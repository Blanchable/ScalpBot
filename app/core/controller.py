from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Callable

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None

from app.brokers.kalshi_client import KalshiClient, Market
from app.config.settings import AppSettings
from app.core.state_manager import StateManager
from app.feeds.btc_reference_feed import BTCReferenceFeed
from app.storage.db import connect, init_db
from app.storage.repositories import TradeRepository, TradeRow
from app.strategy.contract_selector import validate_market
from app.strategy.risk_rules import RiskEngine, RiskSnapshot
from app.strategy.signal_engine import Signal, generate_signal
from app.utils.constants import AppState

logger = logging.getLogger("APP.CONTROLLER")


@dataclass
class PendingOrderState:
    order_id: str
    ticker: str
    side: str
    size: int
    limit_price_cents: int
    created_at_ts: float
    reprice_attempts: int = 0
    attempts: int = 1
    exit_reason: str = ""
    trigger_mark_cents: int = 0
    min_allowed_exit_cents: int = 0
    guarded: bool = False


@dataclass
class PositionState:
    ticker: str
    side: str
    entry_price_cents: int
    size: int
    opened_at_ts: float
    entry_order_id: str | None
    entry_filled_count: int
    is_open: bool
    mode: str = "15m"
    broker_mode: str = "paper"
    entry_time_iso: str = ""
    exit_order_id: str | None = None
    exit_reason: str | None = None
    realized_pnl_cents: int | None = None
    peak_unrealized_pnl_cents: int | None = None
    current_mark_cents: int | None = None
    unrealized_pnl_dollars: float = 0.0
    entry_spread_cents_at_fill: int = 0
    polls_since_entry: int = 0
    stop_hits_in_a_row: int = 0
    target_hits_in_a_row: int = 0
    best_mark_cents: int = 0
    worst_mark_cents: int = 0
    trail_armed: bool = False
    moved_to_breakeven: bool = False
    adverse_fill_window_end_ts: float = 0.0
    target_ready_ts: float = 0.0
    stop_ready_ts: float = 0.0
    breakeven_floor_cents: int = 0


class AppController:
    def __init__(self, settings: AppSettings, emit: Callable[[str, dict], None]) -> None:
        self.settings = settings
        self.emit = emit
        self.state = StateManager()
        self.kalshi = KalshiClient()
        self.feed = BTCReferenceFeed(stale_seconds=settings.global_settings.feed_stale_seconds)
        self._task: asyncio.Task | None = None
        self._stop_requested = False
        self._running = False
        self._active_strategy_mode = ""
        self._resolved_market: Market | None = None
        self._pending_entry: PendingOrderState | None = None
        self._pending_exit: PendingOrderState | None = None
        self._position: PositionState | None = None
        self.active_positions: dict[str, PositionState] = {}
        self._resting_bot_orders: dict[str, dict] = {}
        self._processed_entry_order_ids: set[str] = set()
        self._ticker_trade_stats: dict[str, dict] = {}
        self._current_contract_ticker: str = ""
        self._invalid_market_counts = {"missing_pricing": 0, "spread": 0, "midpoint": 0}
        self._skip_repeat = {"ticker": "", "reason": "", "count": 0}
        self._resolver_repeat = {"msg": "", "count": 0}
        gs = settings.global_settings
        self.risk = RiskEngine(gs.daily_max_loss, gs.max_consecutive_losses, gs.max_simultaneous_positions)
        self.snapshot = RiskSnapshot()
        self.session_realized_pnl = 0.0
        self.session_unrealized_pnl = 0.0
        self.trade_count = 0
        self.polling_counts = {"market": 0, "orderbook": 0, "open_orders": 0}
        self._polling_window_start = time.time()
        self._broker_mode = "paper"
        self._trade_repo: TradeRepository | None = None
        self._db_conn = None

    def _position_key(self, ticker: str, side: str) -> str:
        return f"{ticker}:{side}"

    def _sync_snapshot_positions(self) -> None:
        self.snapshot.open_positions = len(self.active_positions)
        self.snapshot.realized_pnl = self.session_realized_pnl

    def _refresh_market_time(self, market: Market | None, now_utc: datetime) -> bool:
        if market is None:
            return False
        if market.close_time is None:
            if market.seconds_to_expiry > 0:
                market.close_time = now_utc + timedelta(seconds=market.seconds_to_expiry)
            else:
                self.emit("status_reason", {"message": f"resolver_snapshot warning: market {market.ticker} missing close_time; forcing re-resolve"})
                self._resolved_market = None
                return False
        # Recompute seconds_to_expiry each loop so expiry checks use live time, not cached values.
        market.refresh_time_fields(now_utc)
        return True

    def _emit_skip_reason(self, source: str, ticker: str, reason: str) -> None:
        same = self._skip_repeat["ticker"] == ticker and self._skip_repeat["reason"] == reason
        if same:
            self._skip_repeat["count"] += 1
            if self._skip_repeat["count"] % 10 != 0:
                return
        else:
            self._skip_repeat = {"ticker": ticker, "reason": reason, "count": 1}
        self.emit("status_reason", {"message": f"{source}: ticker={ticker} reason={reason} (x{self._skip_repeat['count']})"})

    def _emit_resolver_reason(self, message: str) -> None:
        if message == self._resolver_repeat["msg"]:
            self._resolver_repeat["count"] += 1
            if self._resolver_repeat["count"] % 10 != 0:
                return
        else:
            self._resolver_repeat = {"msg": message, "count": 1}
        self.emit("status_reason", {"message": message})

    def _reset_market_invalid_counts(self) -> None:
        self._invalid_market_counts = {"missing_pricing": 0, "spread": 0, "midpoint": 0}

    def _apply_market_invalid_counter(self, key: str, market: Market, mode_cfg) -> None:
        self._invalid_market_counts[key] += 1
        near_expiry_window = max(mode_cfg.rollover_before_expiry_seconds, mode_cfg.flatten_before_expiry_seconds, 120)
        threshold_map = {
            "missing_pricing": mode_cfg.invalidate_after_missing_pricing_polls,
            "spread": mode_cfg.invalidate_after_spread_reject_polls,
            "midpoint": mode_cfg.invalidate_after_midpoint_reject_polls,
        }
        threshold = threshold_map[key]
        if market.seconds_to_expiry <= near_expiry_window and self._invalid_market_counts[key] >= threshold:
            # Force clear dead near-expiry contracts to avoid endless skip spam on stale markets.
            self.emit("status_reason", {"message": f"validation summary: ticker={market.ticker} missing_pricing x{self._invalid_market_counts['missing_pricing']}, midpoint_out_of_range x{self._invalid_market_counts['midpoint']}, spread_too_wide x{self._invalid_market_counts['spread']}"})
            self.invalidate_market_cache(f"near-expiry invalid market ({key})")
            self._reset_market_invalid_counts()

    def invalidate_market_cache(self, reason: str = "mode change") -> None:
        if self._resolved_market is not None:
            logger.info("Invalidating cached market %s due to %s", self._resolved_market.ticker, reason)
        self._resolved_market = None

    def _configure_market_resolver(self) -> None:
        gs = self.settings.global_settings
        self.kalshi.configure_resolver(
            btc_15m_series_ticker=gs.btc_15m_series_ticker,
            btc_1h_series_ticker=gs.btc_1h_series_ticker,
            max_delta_15m=gs.resolver_max_delta_seconds_15m,
            max_delta_1h=gs.resolver_max_delta_seconds_1h,
        )

    async def start(self, api_key: str, api_secret: str, broker_mode: str, strategy_mode: str) -> None:
        if self._running:
            return
        self.state.transition(AppState.STARTING)
        self.emit("state", {"state": self.state.state})
        self._configure_market_resolver()

        ok = await self.kalshi.connect(api_key, api_secret, environment=broker_mode)
        if not ok:
            self.state.transition(AppState.HALTED)
            reason = self.kalshi.last_error or "Authentication failed"
            self.emit("status_reason", {"message": reason})
            self.emit("connection", {"connected": False, "account": "", "cash_balance": 0.0, "verified": False})
            return

        summary = await self.kalshi.get_account_summary()
        self.emit("connection", {"connected": True, "account": summary["account"], "cash_balance": summary["cash_balance"], "strategy_mode": strategy_mode, "broker_mode": broker_mode, "verified": summary.get("verified", False)})
        self.emit("status_reason", {"message": f"Connected to Kalshi {broker_mode}: {self.kalshi.account_label}"})
        self.emit("market_mode", {"strategy_mode": strategy_mode})
        self.emit("session_metrics", {"session_pnl": self.session_realized_pnl, "session_unrealized_pnl": self.session_unrealized_pnl, "trade_count": self.trade_count, "open_positions": len(self.active_positions)})
        self._active_strategy_mode = strategy_mode
        self._broker_mode = broker_mode
        self.invalidate_market_cache("start")
        self._pending_entry = None
        self._pending_exit = None
        self._stop_requested = False
        self._position = None
        self.active_positions = {}
        self._processed_entry_order_ids.clear()
        init_db(self.settings.db_path)
        self._db_conn = connect(self.settings.db_path)
        self._trade_repo = TradeRepository(self._db_conn)
        self._running = True
        self._task = asyncio.create_task(self._loop(strategy_mode, broker_mode))

    async def stop(self) -> None:
        if not self._running:
            return
        self.state.transition(AppState.STOPPING)
        self.emit("state", {"state": self.state.state})
        self._running = False
        if self._task:
            await self._task
        if self.settings.global_settings.cancel_bot_resting_orders_on_stop:
            await self._cancel_stale_or_old_ticker_orders(current_ticker=None, force_all=True)
        await self.kalshi.disconnect()
        self.invalidate_market_cache("stop")
        if self._db_conn is not None:
            self._db_conn.close()
            self._db_conn = None
            self._trade_repo = None
        self.emit("connection", {"connected": False, "account": "", "cash_balance": 0.0, "verified": False})
        self.state.transition(AppState.IDLE)
        self.emit("state", {"state": self.state.state})

    def _emit_polling_metrics_if_due(self) -> None:
        elapsed = time.time() - self._polling_window_start
        if elapsed < 1:
            return
        factor = 60 / elapsed
        self.emit("polling_stats", {"strike_per_min": round(self.polling_counts["market"] * factor, 2), "orderbook_per_min": round(self.polling_counts["orderbook"] * factor, 2), "open_orders_per_min": round(self.polling_counts["open_orders"] * factor, 2)})
        self.polling_counts = {"market": 0, "orderbook": 0, "open_orders": 0}
        self._polling_window_start = time.time()

    def _is_http_error(self, exc: Exception) -> bool:
        return httpx is not None and isinstance(exc, httpx.HTTPError)

    def _filter_resting_orders_for_ticker(self, orders: list[dict], ticker: str) -> list[dict]:
        filtered: list[dict] = []
        for o in orders:
            status = str(o.get("status", "")).lower()
            remaining = int(o.get("remaining_count", o.get("count", 0)) or 0)
            if status == "resting" and str(o.get("ticker", "")) == ticker and remaining > 0:
                filtered.append(o)
        return filtered

    async def _cancel_stale_or_old_ticker_orders(self, current_ticker: str | None, force_all: bool = False) -> None:
        timeout = self.settings.global_settings.resting_order_timeout_seconds
        now_ts = time.time()
        for oid, meta in list(self._resting_bot_orders.items()):
            stale = (now_ts - meta.get("created_at", now_ts)) > timeout
            wrong_ticker = current_ticker is not None and meta.get("ticker") != current_ticker
            if force_all or stale or wrong_ticker:
                try:
                    ok = await self.kalshi.cancel_order(oid)
                except Exception as exc:
                    self.emit("status_reason", {"message": f"Auto-cancel failed for {oid}: {exc}"})
                    ok = False
                if ok:
                    self.emit("status_reason", {"message": f"Auto-canceled stale bot order {oid} on {meta.get('ticker')}"})
                self._resting_bot_orders.pop(oid, None)

    def _emit_market_payload(self, market: Market, signal_score: int, preferred_side: str, quote_source: str, quote_stale: bool) -> None:
        self.emit("market", {
            "ticker": market.ticker,
            "yes_bid": market.yes_bid,
            "yes_ask": market.yes_ask,
            "no_bid": market.no_bid,
            "no_ask": market.no_ask,
            "bid": market.yes_bid,
            "ask": market.yes_ask,
            "preferred_side": preferred_side,
            "score": signal_score,
            "strategy_mode": self._active_strategy_mode,
            "quote_source": quote_source,
            "quote_stale": quote_stale,
        })
        self.emit("market_mode", {"strategy_mode": self._active_strategy_mode, "selected_market": market.ticker})

    async def _resolve_market(self, strategy_mode: str, now_utc: datetime) -> Market | None:
        if self._resolved_market is not None and not self._refresh_market_time(self._resolved_market, now_utc):
            self._resolved_market = None
        strict_rollover = False
        current_ticker = self._resolved_market.ticker if self._resolved_market else None
        if self._resolved_market and self._resolved_market.seconds_to_expiry <= self.settings.mode_settings[strategy_mode].rollover_before_expiry_seconds:
            strict_rollover = True
            self.emit("status_reason", {"message": f"Rollover: current ticker {self._resolved_market.ticker} within {self.settings.mode_settings[strategy_mode].rollover_before_expiry_seconds}s of expiry"})
            await self._cancel_stale_or_old_ticker_orders(current_ticker=self._resolved_market.ticker, force_all=True)
            self._resolved_market = None
        if self._resolved_market is None:
            try:
                self._resolved_market = await self.kalshi.resolve_btc_target_market(
                    strategy_mode,
                    now_utc,
                    current_ticker=current_ticker,
                    strict_rollover=strict_rollover,
                )
            except TypeError:
                self._resolved_market = await self.kalshi.resolve_btc_target_market(strategy_mode, now_utc)
            self.polling_counts["market"] += 1
            if self._resolved_market:
                self._emit_resolver_reason(self.kalshi.last_market_resolution_reason)
                if self._current_contract_ticker and self._current_contract_ticker != self._resolved_market.ticker:
                    self.emit("status_reason", {"message": f"Rollover complete: {self._current_contract_ticker} -> {self._resolved_market.ticker}"})
                    self._reset_market_invalid_counts()
                    if self._pending_entry and self._pending_entry.ticker != self._resolved_market.ticker:
                        self._pending_entry = None
                self._current_contract_ticker = self._resolved_market.ticker
        return self._resolved_market

    def _position_mark_and_unrealized(self, market: Market) -> tuple[int, int]:
        assert self._position is not None
        if self._position.side == "yes":
            mark = market.yes_bid
        else:
            mark = market.no_bid
        pnl_per = mark - self._position.entry_price_cents
        return mark, pnl_per * self._position.size

    def _normalize_fill_state(self, status: dict, submitted_size: int) -> tuple[int, int]:
        status_l = str(status.get("status", "")).lower()
        submitted = int(status.get("submitted_count", status.get("count", submitted_size)) or submitted_size)
        remaining = int(status.get("remaining_count", 0) or 0)
        filled_raw = status.get("filled_count")
        if filled_raw is not None:
            filled = int(filled_raw or 0)
        else:
            filled = max(0, submitted - remaining)
        if filled <= 0 and status_l in {"executed", "filled"} and remaining == 0:
            filled = submitted
        return filled, remaining

    def _persist_closed_trade(self, position: PositionState, exit_price: int) -> None:
        if self._trade_repo is None:
            return
        trade = TradeRow(
            market_ticker=position.ticker,
            strategy_mode=position.mode,
            broker_mode=position.broker_mode,
            side=position.side,
            qty=position.entry_filled_count,
            entry_price=position.entry_price_cents,
            exit_price=exit_price,
            net_pnl=(position.realized_pnl_cents or 0) / 100.0,
            exit_reason=position.exit_reason or "exit",
            entry_order_id=position.entry_order_id,
            exit_order_id=position.exit_order_id,
            entry_time=position.entry_time_iso,
            exit_time=datetime.now(timezone.utc).isoformat(),
        )
        self._trade_repo.add_trade(trade)
        self.emit("status_reason", {"message": f"Trade persisted: ticker={position.ticker} pnl={(position.realized_pnl_cents or 0) / 100.0:.2f}"})

    def _record_contract_exit(self, ticker: str, side: str, score: int, pnl: float, reason: str) -> None:
        stats = self._ticker_trade_stats.setdefault(ticker, {"trade_count": 0, "last_exit_time": 0.0, "last_exit_reason": "", "last_signal_side": side, "signal_reset": True})
        stats["trade_count"] += 1
        stats["last_exit_time"] = time.time()
        stats["last_exit_reason"] = reason
        stats["last_signal_side"] = side
        stats["signal_reset"] = score <= self.settings.mode_settings[self._active_strategy_mode].signal_reset_score_floor
        stats["neutral_until"] = time.time() + self.settings.mode_settings[self._active_strategy_mode].neutral_reset_seconds_after_loss if reason == "stop_loss" else 0.0

    def _entry_block_reason(self, market_ticker: str, signal: Signal | None = None) -> str | None:
        cfg = self.settings.mode_settings[self._active_strategy_mode]
        stats = self._ticker_trade_stats.get(market_ticker)
        if not stats:
            return None
        now_ts = time.time()
        if now_ts < stats.get("last_exit_time", 0.0) + cfg.cooldown_after_exit_seconds:
            return "cooldown"
        if stats.get("last_exit_reason") == "stop_loss" and now_ts < stats.get("last_exit_time", 0.0) + cfg.cooldown_after_loss_seconds:
            return "loss_cooldown"
        if stats.get("trade_count", 0) >= cfg.max_trades_per_contract:
            return "per_contract_cap"
        if stats.get("last_exit_reason") == "stop_loss" and now_ts < stats.get("neutral_until", 0.0):
            side_flip = signal is not None and signal.side != "none" and signal.side != stats.get("last_signal_side")
            if not side_flip and not stats.get("signal_reset", False):
                return "neutral_reset_after_loss"
        if cfg.require_signal_reset_before_reentry:
            signal_reset = stats.get("signal_reset", True)
            side_flip = signal is not None and signal.side != "none" and signal.side != stats.get("last_signal_side")
            if signal is not None and signal.score <= cfg.signal_reset_score_floor:
                stats["signal_reset"] = True
                signal_reset = True
            if not signal_reset and not side_flip:
                return "signal_not_reset"
        return None

    def _entry_limit_price(self, market: Market, side: str, mode_cfg: ModeSettings) -> int:
        if mode_cfg.entry_style != "maker_first":
            return market.yes_ask if side == "buy_yes" else market.no_ask
        if side == "buy_yes":
            maker_price = min(market.yes_bid + mode_cfg.entry_improve_cents, market.yes_ask - 1)
            return max(1, min(99, maker_price if maker_price >= market.yes_bid else market.yes_ask))
        maker_price = min(market.no_bid + mode_cfg.entry_improve_cents, market.no_ask - 1)
        return max(1, min(99, maker_price if maker_price >= market.no_bid else market.no_ask))

    def _should_exit(self, market: Market, now_ts: float, raw_pnl_cents: int) -> str | None:
        assert self._position is not None
        cfg = self.settings.mode_settings[self._active_strategy_mode]
        if market.seconds_to_expiry <= cfg.flatten_before_expiry_seconds:
            return "flatten_before_expiry"

        elapsed = int(now_ts - self._position.opened_at_ts)
        if now_ts <= self._position.adverse_fill_window_end_ts and raw_pnl_cents <= -cfg.adverse_fill_max_loss_cents:
            return "adverse_fill"

        target_armed = elapsed >= cfg.target_activation_seconds
        stop_armed = elapsed >= cfg.stop_activation_seconds and elapsed >= cfg.min_hold_seconds
        if target_armed and self._position.target_ready_ts == 0:
            self._position.target_ready_ts = now_ts
            self.emit("status_reason", {"message": f"exit_manager: target armed for {self._position.ticker}"})
        if stop_armed and self._position.stop_ready_ts == 0:
            self._position.stop_ready_ts = now_ts
            self.emit("status_reason", {"message": f"exit_manager: stop armed for {self._position.ticker}"})

        effective_stop = cfg.stop_loss_cents + self._position.entry_spread_cents_at_fill + cfg.stop_buffer_beyond_spread_cents
        effective_target = cfg.profit_target_cents + cfg.profit_buffer_beyond_spread_cents
        mark = market.yes_bid if self._position.side == "yes" else market.no_bid
        self._position.best_mark_cents = max(self._position.best_mark_cents, mark)
        self._position.worst_mark_cents = min(self._position.worst_mark_cents, mark)

        if cfg.enable_trailing_winners:
            if not self._position.moved_to_breakeven and raw_pnl_cents >= cfg.breakeven_trigger_cents:
                self._position.moved_to_breakeven = True
                self._position.breakeven_floor_cents = self._position.entry_price_cents + cfg.breakeven_lock_cents
                self.emit("status_reason", {"message": f"exit_manager: breakeven flip on {self._position.ticker} floor={self._position.breakeven_floor_cents}"})
            if self._position.moved_to_breakeven and mark <= self._position.breakeven_floor_cents:
                return "breakeven_stop"
            if not self._position.trail_armed and raw_pnl_cents >= cfg.trail_arm_cents:
                self._position.trail_armed = True
                self.emit("status_reason", {"message": f"exit_manager: trailing armed for {self._position.ticker}"})
            if cfg.hard_take_profit_cents > 0 and raw_pnl_cents >= cfg.hard_take_profit_cents:
                return "hard_take_profit"
            if self._position.trail_armed and (self._position.best_mark_cents - mark) >= cfg.trail_distance_cents:
                return "trailing_stop"
        elif target_armed:
            if raw_pnl_cents >= effective_target:
                self._position.target_hits_in_a_row += 1
            else:
                self._position.target_hits_in_a_row = 0
            if self._position.target_hits_in_a_row >= cfg.exit_confirm_polls_target:
                return "profit_target"

        if stop_armed:
            if raw_pnl_cents <= -effective_stop:
                self._position.stop_hits_in_a_row += 1
            else:
                self._position.stop_hits_in_a_row = 0
            if self._position.stop_hits_in_a_row >= cfg.exit_confirm_polls_stop:
                return "stop_loss"

        if elapsed >= cfg.time_stop_seconds:
            return "time_stop"
        return None
        # Effective stop includes entry spread so spread-cross loss is not treated as adverse move.
        effective_stop = cfg.stop_loss_cents + self._position.entry_spread_cents_at_fill + cfg.stop_buffer_beyond_spread_cents
        effective_target = cfg.profit_target_cents + cfg.profit_buffer_beyond_spread_cents
        if raw_pnl_cents <= -effective_stop:
            self._position.stop_hits_in_a_row += 1
        else:
            self._position.stop_hits_in_a_row = 0
        if raw_pnl_cents >= effective_target:
            self._position.target_hits_in_a_row += 1
        else:
            self._position.target_hits_in_a_row = 0
        if self._position.stop_hits_in_a_row >= cfg.exit_confirm_polls:
            return "stop_loss"
        if self._position.target_hits_in_a_row >= cfg.exit_confirm_polls:
            return "profit_target"
        if elapsed >= cfg.time_stop_seconds:
            return "time_stop"
        return None

    async def _submit_exit_order(self, market: Market, reason: str) -> None:
        assert self._position is not None
        if self._pending_exit is not None:
            # Prevent overlapping exits for same position.
            self.emit("status_reason", {"message": "Exit already pending, suppressing duplicate exit submission"})
            return
        side = "sell_yes" if self._position.side == "yes" else "sell_no"
        limit = market.yes_bid if self._position.side == "yes" else market.no_bid
        cfg = self.settings.mode_settings[self._active_strategy_mode]
        stop_like = reason in {"stop_loss", "adverse_fill"}
        target_like = reason in {"profit_target", "trailing_stop", "breakeven_stop", "hard_take_profit"}
        slippage_cap = cfg.max_stop_exit_slippage_cents if stop_like else cfg.max_target_exit_slippage_cents
        trigger_mark = limit
        min_allowed = max(1, trigger_mark - slippage_cap)
        order = await self.kalshi.place_limit_order(market.ticker, side, self._position.size, limit)
        self._pending_exit = PendingOrderState(
            order_id=order.order_id,
            ticker=market.ticker,
            side=side,
            size=self._position.size,
            limit_price_cents=limit,
            created_at_ts=time.time(),
            exit_reason=reason,
            trigger_mark_cents=trigger_mark,
            min_allowed_exit_cents=min_allowed,
        )
        self._position.exit_order_id = order.order_id
        self._position.exit_reason = reason
        self.emit("status_reason", {"message": f"Exit submitted: ticker={market.ticker} side={side} reason={reason} entry={self._position.entry_price_cents} trigger_mark={trigger_mark} slippage_cap={slippage_cap} submitted={limit} order_id={order.order_id}"})

    async def _manage_pending_entry(self, market: Market) -> None:
        if self._pending_entry is None:
            return
        
        try:
            status = await self.kalshi.get_order_status(self._pending_entry.order_id)
        except Exception as exc:
            self.emit("status_reason", {"message": f"Pending entry status unavailable: {exc}"})
            return

        filled = int(status.get("filled_count", 0))
        filled, remaining = self._normalize_fill_state(status, self._pending_entry.size)
        self.emit("status_reason", {"message": f"Pending entry {self._pending_entry.order_id}: filled={filled} remaining={remaining} status={status.get('status')}"})
        if filled > 0 and self._pending_entry.order_id not in self._processed_entry_order_ids and self._position is None:
            entry_price = self._pending_entry.limit_price_cents
            if self._pending_entry.side == "buy_yes":
                entry_price = int(status.get("yes_price") or entry_price)
                pos_side = "yes"
            else:
                entry_price = int(status.get("no_price") or entry_price)
                pos_side = "no"
            self._position = PositionState(
                ticker=self._pending_entry.ticker,
                side=pos_side,
                entry_price_cents=entry_price,
                size=filled,
                opened_at_ts=time.time(),
                entry_order_id=self._pending_entry.order_id,
                entry_filled_count=filled,
                is_open=True,
                mode=self._active_strategy_mode,
                broker_mode=self._broker_mode,
                entry_time_iso=datetime.now(timezone.utc).isoformat(),
                entry_spread_cents_at_fill=(market.yes_ask - market.yes_bid) if pos_side == "yes" else (market.no_ask - market.no_bid),
                best_mark_cents=market.yes_bid if pos_side == "yes" else market.no_bid,
                worst_mark_cents=market.yes_bid if pos_side == "yes" else market.no_bid,
                adverse_fill_window_end_ts=time.time() + self.settings.mode_settings[self._active_strategy_mode].adverse_fill_guard_seconds,
            )
            self.active_positions[self._position_key(self._position.ticker, self._position.side)] = self._position
            self._sync_snapshot_positions()
            self._processed_entry_order_ids.add(self._pending_entry.order_id)
            self.emit("status_reason", {"message": f"Position open: {self._position.ticker} {self._position.side} size={filled} entry={entry_price}"})
            self.emit("position_opened", {"ticker": self._position.ticker, "side": self._position.side, "qty": self._position.size, "entry": self._position.entry_price_cents, "order_id": self._position.entry_order_id})

        timeout = self.settings.mode_settings[self._active_strategy_mode].entry_order_timeout_seconds
        expired = (time.time() - self._pending_entry.created_at_ts) > timeout
        if remaining <= 0 or str(status.get("status", "")).lower() in {"executed", "filled", "canceled", "rejected"}:
            self._pending_entry = None
            return
        if expired or market.seconds_to_expiry <= self.settings.mode_settings[self._active_strategy_mode].rollover_before_expiry_seconds:
            await self.kalshi.cancel_order(self._pending_entry.order_id)
            cfg = self.settings.mode_settings[self._active_strategy_mode]
            if expired and cfg.entry_style == "maker_first" and self._pending_entry.attempts < cfg.max_entry_attempts:
                allow_requote = True
                revalidate_reason = ""
                cross_price = market.yes_ask if self._pending_entry.side == "buy_yes" else market.no_ask
                drift = cross_price - self._pending_entry.limit_price_cents
                signal = None
                if cfg.require_signal_revalidation_on_requote:
                    tick = await self.feed.get_tick()
                    signal = generate_signal(market, tick, cfg, self._active_strategy_mode)
                    if tick.is_stale or not signal.should_trade or signal.side != self._pending_entry.side or signal.score < cfg.min_signal_score:
                        allow_requote = False
                        revalidate_reason = "stale signal on maker timeout"
                    elif signal.edge_cents < signal.round_trip_threshold:
                        allow_requote = False
                        revalidate_reason = "edge no longer clears friction"
                if drift > cfg.max_requote_drift_cents:
                    allow_requote = False
                    revalidate_reason = "price drift exceeded requote cap"
                self.emit("status_reason", {"message": f"entry_requote: old={self._pending_entry.limit_price_cents} new={cross_price} drift={drift} side={self._pending_entry.side} score={(signal.score if signal else 'na')} edge={(signal.edge_cents if signal else 'na')} allowed={allow_requote}"})
                if allow_requote:
                    refreshed = await self.kalshi.place_limit_order(self._pending_entry.ticker, self._pending_entry.side, self._pending_entry.size, cross_price)
                    self._pending_entry = PendingOrderState(
                        order_id=refreshed.order_id,
                        ticker=self._pending_entry.ticker,
                        side=self._pending_entry.side,
                        size=self._pending_entry.size,
                        limit_price_cents=cross_price,
                        created_at_ts=time.time(),
                        attempts=self._pending_entry.attempts + 1,
                    )
                    self.emit("status_reason", {"message": f"Entry requoted after maker timeout: {refreshed.order_id} @ {cross_price}"})
                else:
                    self.emit("status_reason", {"message": f"Entry canceled: {revalidate_reason or 'stale signal on maker timeout'}"})
                    self._pending_entry = None
            else:
                self.emit("status_reason", {"message": f"Entry canceled timeout/rollover: {self._pending_entry.order_id}"})
                self._pending_entry = None

    async def _manage_pending_exit(self, market: Market) -> None:
        if self._pending_exit is None or self._position is None:
            return
        
        try:
            status = await self.kalshi.get_order_status(self._pending_exit.order_id)
        except Exception as exc:
            self.emit("status_reason", {"message": f"Pending exit status unavailable: {exc}"})
            return

        filled = int(status.get("filled_count", 0))
        filled, remaining = self._normalize_fill_state(status, self._pending_exit.size)
        if filled > 0:
            self._position.size = max(0, self._position.size - filled)
            self.emit("status_reason", {"message": f"Partial exit fill: {filled}, remaining position={self._position.size}"})
        if self._position.size <= 0 or remaining <= 0 or str(status.get("status", "")).lower() in {"executed", "filled"}:
            exit_price = self._pending_exit.limit_price_cents
            if self._pending_exit.side == "sell_yes":
                exit_price = int(status.get("yes_price") or exit_price)
            else:
                exit_price = int(status.get("no_price") or exit_price)
            hold_s = int(time.time() - self._position.opened_at_ts)
            realized = (exit_price - self._position.entry_price_cents) * self._position.entry_filled_count
            self._position.realized_pnl_cents = realized
            self.emit("trade", {"ticker": self._position.ticker, "side": self._position.side, "entry": self._position.entry_price_cents, "exit": exit_price, "size": self._position.entry_filled_count, "pnl": realized / 100.0, "reason": self._position.exit_reason or "exit", "hold_time_seconds": hold_s})
            self.session_realized_pnl += realized / 100.0
            self.trade_count += 1
            self._record_contract_exit(self._position.ticker, f"buy_{self._position.side}", 0, realized / 100.0, self._position.exit_reason or "exit")
            self._persist_closed_trade(self._position, exit_price)
            self.active_positions.pop(self._position_key(self._position.ticker, self._position.side), None)
            self.session_unrealized_pnl = sum(p.unrealized_pnl_dollars for p in self.active_positions.values())
            self._sync_snapshot_positions()
            self.emit("session_metrics", {"session_pnl": self.session_realized_pnl, "session_unrealized_pnl": self.session_unrealized_pnl, "trade_count": self.trade_count, "open_positions": len(self.active_positions)})
            self._position = None
            self._pending_exit = None
            return

        cfg = self.settings.mode_settings[self._active_strategy_mode]
        reason = self._pending_exit.exit_reason or "exit"
        stop_like = reason in {"stop_loss", "adverse_fill"}
        target_like = reason in {"profit_target", "trailing_stop", "breakeven_stop", "hard_take_profit"}
        timeout = cfg.stop_exit_timeout_seconds if stop_like else (cfg.target_exit_timeout_seconds if target_like else cfg.exit_order_timeout_seconds)
        if (time.time() - self._pending_exit.created_at_ts) > timeout:
            await self.kalshi.cancel_order(self._pending_exit.order_id)
            current_bid = market.yes_bid if self._position.side == "yes" else market.no_bid
            if current_bid < self._pending_exit.min_allowed_exit_cents:
                self._pending_exit.guarded = True
                self.emit("status_reason", {"message": f"exit_manager: repricing blocked by slippage cap reason={reason} trigger={self._pending_exit.trigger_mark_cents} min_allowed={self._pending_exit.min_allowed_exit_cents} current_bid={current_bid}"})
                self._pending_exit.created_at_ts = time.time()
                return
            if stop_like and not cfg.allow_stop_exit_reprice:
                self.emit("status_reason", {"message": f"exit_manager: stop reprice suppressed at cap; submitting guarded flatten @ {current_bid}"})
                self._pending_exit = None
                await self._submit_exit_order(market, reason)
                return
            max_reprices = cfg.stop_exit_max_reprice_attempts if stop_like else (cfg.target_exit_max_reprice_attempts if target_like else cfg.max_exit_reprice_attempts)
            if self._pending_exit.reprice_attempts < max_reprices:
                self._pending_exit.reprice_attempts += 1
                self._pending_exit = None
                await self._submit_exit_order(market, reason)
            else:
                self.emit("status_reason", {"message": "exit_manager: timeout and max reprices reached"})

    async def _loop(self, strategy_mode: str, broker_mode: str) -> None:
        self.state.transition(AppState.SCANNING)
        self.emit("state", {"state": self.state.state})

        while self._running:
            now_utc = datetime.now(timezone.utc)
            mode_cfg = self.settings.mode_settings[strategy_mode]
            self._sync_snapshot_positions()
            self._emit_polling_metrics_if_due()
            tick = await self.feed.get_tick()
            if tick.is_stale:
                self.emit("status_reason", {"message": "BTC reference feed stale"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            try:
                market = await self._resolve_market(strategy_mode, now_utc)
            except Exception as exc:
                self.emit("status_reason", {"message": f"Resolver error: {exc}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if market is None:
                self._emit_resolver_reason(self.kalshi.last_market_resolution_reason or f"No active BTC {strategy_mode} target market")
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue
            if not self._refresh_market_time(market, now_utc):
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            quote_source = "snapshot-fallback"
            quote_stale = True
            try:
                book = await self.kalshi.get_orderbook_snapshot(market.ticker)
                self.polling_counts["orderbook"] += 1
                market.yes_bid = int(book.get("best_yes_bid", market.yes_bid))
                market.yes_ask = int(book.get("best_yes_ask", market.yes_ask))
                market.no_bid = int(book.get("best_no_bid", market.no_bid))
                market.no_ask = int(book.get("best_no_ask", market.no_ask))
                market.midpoint = (market.yes_bid + market.yes_ask) / 2
                quote_source = "live-orderbook"
                quote_stale = False
            except Exception as exc:
                self.emit("status_reason", {"message": f"Orderbook unavailable for {market.ticker}: {exc}"})

            if market.yes_bid <= 0 or market.no_bid <= 0:
                self._emit_skip_reason("live_orderbook", market.ticker, "missing pricing")
                self._apply_market_invalid_counter("missing_pricing", market, mode_cfg)
            self._emit_market_payload(market, 0, "none", quote_source, quote_stale)

            if self._pending_exit is not None:
                await self._manage_pending_exit(market)
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if self._position is not None and self._position.is_open:
                mark, unrealized = self._position_mark_and_unrealized(market)
                self._position.current_mark_cents = mark
                self._position.unrealized_pnl_dollars = unrealized / 100.0
                self.session_unrealized_pnl = sum(p.unrealized_pnl_dollars for p in self.active_positions.values())
                raw_pnl_cents = int(unrealized / max(1, self._position.size))
                self._position.polls_since_entry += 1
                if self._position.peak_unrealized_pnl_cents is None:
                    self._position.peak_unrealized_pnl_cents = unrealized
                else:
                    self._position.peak_unrealized_pnl_cents = max(self._position.peak_unrealized_pnl_cents, unrealized)
                cfg = self.settings.mode_settings[self._active_strategy_mode]
                effective_stop = cfg.stop_loss_cents + self._position.entry_spread_cents_at_fill + cfg.stop_buffer_beyond_spread_cents
                effective_target = cfg.profit_target_cents + cfg.profit_buffer_beyond_spread_cents
                self.emit("status_reason", {"message": f"Position mark: ticker={self._position.ticker} side={self._position.side} entry={self._position.entry_price_cents} mark={mark} raw_pnl_cents={raw_pnl_cents} eff_stop={effective_stop} eff_target={effective_target} hold_s={int(time.time()-self._position.opened_at_ts)} stop_hits={self._position.stop_hits_in_a_row} target_hits={self._position.target_hits_in_a_row}"})
                self.emit("position_mark", {"ticker": self._position.ticker, "side": self._position.side, "entry": self._position.entry_price_cents, "mark": mark, "qty": self._position.size, "unrealized_pnl": self._position.unrealized_pnl_dollars})
                self.emit("session_metrics", {"session_pnl": self.session_realized_pnl, "session_unrealized_pnl": self.session_unrealized_pnl, "trade_count": self.trade_count, "open_positions": len(self.active_positions)})
                exit_reason = self._should_exit(market, time.time(), raw_pnl_cents)
                if exit_reason:
                    await self._submit_exit_order(market, exit_reason)
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if self._pending_entry is not None:
                await self._manage_pending_entry(market)
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if self._stop_requested or self.state.state == AppState.STOPPING:
                self.emit("status_reason", {"message": "Stop requested: suppressing new entries"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            can_enter, reason = self.risk.can_enter(self.snapshot)
            if not can_enter:
                self.emit("status_reason", {"message": f"Scanning paused: {reason}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if len(self.active_positions) >= self.settings.global_settings.max_simultaneous_positions:
                self.emit("status_reason", {"message": f"Skipping entry: max open positions reached ({len(self.active_positions)}/{self.settings.global_settings.max_simultaneous_positions})"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if any(p.ticker == market.ticker for p in self.active_positions.values()):
                self.emit("status_reason", {"message": f"Skipping entry: active position already exists for ticker {market.ticker}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            try:
                open_orders = await self.kalshi.get_open_orders(ticker=market.ticker, status="resting")
            except TypeError:
                open_orders = await self.kalshi.get_open_orders()
            except Exception as exc:
                self.emit("status_reason", {"message": f"Skipping entry: unable to verify resting orders ({exc})"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue
            self.polling_counts["open_orders"] += 1

            blocking_orders = self._filter_resting_orders_for_ticker(open_orders, market.ticker)
            if blocking_orders:
                self.emit("status_reason", {"message": "Skipping entry: open orders already exist for ticker"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            ok_market, reason = validate_market(market, mode_cfg.spread_filter_cents, mode_cfg.min_price_cents, mode_cfg.max_price_cents, mode_cfg.no_entry_before_expiry_seconds)
            if not ok_market:
                self._emit_skip_reason("validation", market.ticker, reason)
                reason_l = reason.lower()
                if "missing" in reason_l:
                    self._apply_market_invalid_counter("missing_pricing", market, mode_cfg)
                elif "spread" in reason_l:
                    self._apply_market_invalid_counter("spread", market, mode_cfg)
                elif "midpoint" in reason_l:
                    self._apply_market_invalid_counter("midpoint", market, mode_cfg)
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue
            self._reset_market_invalid_counts()

            signal = generate_signal(market, tick, mode_cfg, strategy_mode)
            stats = self._ticker_trade_stats.get(market.ticker)
            if stats and signal.score <= mode_cfg.signal_reset_score_floor:
                stats["signal_reset"] = True
            self._emit_market_payload(market, signal.score, signal.side, quote_source, quote_stale)
            self.emit("status_reason", {"message": f"Signal diagnostics: side={signal.side} yes_edge={signal.yes_edge:.2f} no_edge={signal.no_edge:.2f} threshold={signal.round_trip_threshold:.2f} entry_style={mode_cfg.entry_style} yes={market.yes_bid}/{market.yes_ask} no={market.no_bid}/{market.no_ask}"})

            if not signal.should_trade or signal.score < mode_cfg.min_signal_score:
                self.emit("status_reason", {"message": f"Skipping entry: {signal.reasons[0] if signal.reasons else 'score gate'}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            block_reason = self._entry_block_reason(market.ticker, signal)
            if block_reason:
                msg_map = {"cooldown": "cooldown", "loss_cooldown": "cooldown_after_loss", "neutral_reset_after_loss": "neutral reset after loss", "per_contract_cap": "per-contract cap", "signal_not_reset": "signal not reset"}
                self.emit("status_reason", {"message": f"Skipping entry: {msg_map.get(block_reason, block_reason)}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            side = signal.side
            limit_price = self._entry_limit_price(market, side, mode_cfg)
            if self._stop_requested or self.state.state == AppState.STOPPING:
                self.emit("status_reason", {"message": "Stop requested: skipped entry submit"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            try:
                order = await self.kalshi.place_limit_order(market.ticker, side, 1, limit_price)
            except Exception as exc:
                self.emit("order", {"ticker": market.ticker, "side": side, "price": limit_price, "status": f"rejected: {exc}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            self._pending_entry = PendingOrderState(order_id=order.order_id, ticker=market.ticker, side=side, size=1, limit_price_cents=limit_price, created_at_ts=time.time(), attempts=1)
            st = self._ticker_trade_stats.setdefault(market.ticker, {"trade_count": 0, "last_exit_time": 0.0, "last_exit_reason": "", "last_signal_side": side, "signal_reset": True})
            st["last_signal_side"] = side
            st["signal_reset"] = False
            if order.order_id:
                self._resting_bot_orders[order.order_id] = {"ticker": market.ticker, "created_at": time.time(), "side": side}
            self.emit("order", {"ticker": market.ticker, "side": side, "price": limit_price, "status": order.status, "order_id": order.order_id})
            self.emit("status_reason", {"message": f"Pending entry submitted: {order.order_id}"})

            await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
