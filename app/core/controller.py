from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Callable

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None

from app.brokers.kalshi_client import KalshiClient, Market
from app.config.settings import AppSettings
from app.core.state_manager import StateManager
from app.feeds.btc_reference_feed import BTCReferenceFeed
from app.strategy.contract_selector import validate_market
from app.strategy.risk_rules import RiskEngine, RiskSnapshot
from app.strategy.signal_engine import generate_signal
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
    exit_order_id: str | None = None
    exit_reason: str | None = None
    realized_pnl_cents: int | None = None
    peak_unrealized_pnl_cents: int | None = None


class AppController:
    def __init__(self, settings: AppSettings, emit: Callable[[str, dict], None]) -> None:
        self.settings = settings
        self.emit = emit
        self.state = StateManager()
        self.kalshi = KalshiClient()
        self.feed = BTCReferenceFeed(stale_seconds=settings.global_settings.feed_stale_seconds)
        self._task: asyncio.Task | None = None
        self._running = False
        self._active_strategy_mode = ""
        self._resolved_market: Market | None = None
        self._pending_entry: PendingOrderState | None = None
        self._pending_exit: PendingOrderState | None = None
        self._position: PositionState | None = None
        self._resting_bot_orders: dict[str, dict] = {}
        gs = settings.global_settings
        self.risk = RiskEngine(gs.daily_max_loss, gs.max_consecutive_losses, gs.max_simultaneous_positions)
        self.snapshot = RiskSnapshot()
        self.session_realized_pnl = 0.0
        self.trade_count = 0
        self.polling_counts = {"market": 0, "orderbook": 0, "open_orders": 0}
        self._polling_window_start = time.time()

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
        self.emit("session_metrics", {"session_pnl": self.session_realized_pnl, "trade_count": self.trade_count})
        self._active_strategy_mode = strategy_mode
        self.invalidate_market_cache("start")
        self._pending_entry = None
        self._pending_exit = None
        self._position = None
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

    async def _resolve_market(self, strategy_mode: str) -> Market | None:
        if self._resolved_market and self._resolved_market.seconds_to_expiry <= self.settings.mode_settings[strategy_mode].rollover_before_expiry_seconds:
            self.emit("status_reason", {"message": f"Rollover: current ticker {self._resolved_market.ticker} within {self.settings.mode_settings[strategy_mode].rollover_before_expiry_seconds}s of expiry"})
            await self._cancel_stale_or_old_ticker_orders(current_ticker=self._resolved_market.ticker, force_all=True)
            self._resolved_market = None
        if self._resolved_market is None:
            self._resolved_market = await self.kalshi.resolve_btc_target_market(strategy_mode)
            self.polling_counts["market"] += 1
            if self._resolved_market:
                self.emit("status_reason", {"message": self.kalshi.last_market_resolution_reason})
        return self._resolved_market

    def _position_mark_and_unrealized(self, market: Market) -> tuple[int, int]:
        assert self._position is not None
        if self._position.side == "yes":
            mark = market.yes_bid
        else:
            mark = market.no_bid
        pnl_per = mark - self._position.entry_price_cents
        return mark, pnl_per * self._position.size

    def _should_exit(self, market: Market, now_ts: float, unrealized_per_contract: int) -> str | None:
        assert self._position is not None
        cfg = self.settings.mode_settings[self._active_strategy_mode]
        if market.seconds_to_expiry <= cfg.flatten_before_expiry_seconds:
            return "flatten_before_expiry"
        if unrealized_per_contract <= -cfg.stop_loss_cents:
            return "stop_loss"
        if unrealized_per_contract >= cfg.profit_target_cents:
            return "profit_target"
        if (now_ts - self._position.opened_at_ts) >= cfg.time_stop_seconds:
            return "time_stop"
        return None

    async def _submit_exit_order(self, market: Market, reason: str) -> None:
        assert self._position is not None
        side = "sell_yes" if self._position.side == "yes" else "sell_no"
        limit = market.yes_bid if self._position.side == "yes" else market.no_bid
        order = await self.kalshi.place_limit_order(market.ticker, side, self._position.size, limit)
        self._pending_exit = PendingOrderState(order_id=order.order_id, ticker=market.ticker, side=side, size=self._position.size, limit_price_cents=limit, created_at_ts=time.time())
        self._position.exit_order_id = order.order_id
        self._position.exit_reason = reason
        self.emit("status_reason", {"message": f"Exit triggered: {reason}, submitted {side} @ {limit}"})

    async def _manage_pending_entry(self, market: Market) -> None:
        if self._pending_entry is None:
            return
        
        try:
            status = await self.kalshi.get_order_status(self._pending_entry.order_id)
        except Exception as exc:
            self.emit("status_reason", {"message": f"Pending entry status unavailable: {exc}"})
            return

        filled = int(status.get("filled_count", 0))
        remaining = int(status.get("remaining_count", 0))
        self.emit("status_reason", {"message": f"Pending entry {self._pending_entry.order_id}: filled={filled} remaining={remaining} status={status.get('status')}"})
        if filled > 0 and self._position is None:
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
            )
            self.emit("status_reason", {"message": f"Position open: {self._position.ticker} {self._position.side} size={filled} entry={entry_price}"})

        timeout = self.settings.mode_settings[self._active_strategy_mode].entry_order_timeout_seconds
        expired = (time.time() - self._pending_entry.created_at_ts) > timeout
        if remaining <= 0 or str(status.get("status", "")).lower() in {"executed", "filled", "canceled", "rejected"}:
            self._pending_entry = None
            return
        if expired or market.seconds_to_expiry <= self.settings.mode_settings[self._active_strategy_mode].rollover_before_expiry_seconds:
            await self.kalshi.cancel_order(self._pending_entry.order_id)
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
        remaining = int(status.get("remaining_count", 0))
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
            self.emit("session_metrics", {"session_pnl": self.session_realized_pnl, "trade_count": self.trade_count})
            self._position = None
            self._pending_exit = None
            return

        timeout = self.settings.mode_settings[self._active_strategy_mode].exit_order_timeout_seconds
        if (time.time() - self._pending_exit.created_at_ts) > timeout:
            await self.kalshi.cancel_order(self._pending_exit.order_id)
            if self._pending_exit.reprice_attempts < self.settings.mode_settings[self._active_strategy_mode].max_exit_reprice_attempts:
                self._pending_exit.reprice_attempts += 1
                await self._submit_exit_order(market, self._position.exit_reason or "reprice")
            else:
                self.emit("status_reason", {"message": "Exit order timeout and max reprices reached"})

    async def _loop(self, strategy_mode: str, broker_mode: str) -> None:
        self.state.transition(AppState.SCANNING)
        self.emit("state", {"state": self.state.state})

        while self._running:
            mode_cfg = self.settings.mode_settings[strategy_mode]
            can_enter, reason = self.risk.can_enter(self.snapshot)
            if not can_enter:
                self.emit("status_reason", {"message": f"Scanning paused: {reason}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            self._emit_polling_metrics_if_due()
            tick = await self.feed.get_tick()
            if tick.is_stale:
                self.emit("status_reason", {"message": "BTC reference feed stale"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            try:
                market = await self._resolve_market(strategy_mode)
            except Exception as exc:
                self.emit("status_reason", {"message": f"Resolver error: {exc}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if market is None:
                self.emit("status_reason", {"message": self.kalshi.last_market_resolution_reason or f"No active BTC {strategy_mode} target market"})
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

            self._emit_market_payload(market, 0, "none", quote_source, quote_stale)

            if self._pending_exit is not None:
                await self._manage_pending_exit(market)
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if self._position is not None and self._position.is_open:
                mark, unrealized = self._position_mark_and_unrealized(market)
                per_contract = int(unrealized / max(1, self._position.size))
                if self._position.peak_unrealized_pnl_cents is None:
                    self._position.peak_unrealized_pnl_cents = unrealized
                else:
                    self._position.peak_unrealized_pnl_cents = max(self._position.peak_unrealized_pnl_cents, unrealized)
                self.emit("status_reason", {"message": f"Position mark: side={self._position.side} mark={mark} unrealized_cents={unrealized}"})
                exit_reason = self._should_exit(market, time.time(), per_contract)
                if exit_reason:
                    await self._submit_exit_order(market, exit_reason)
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if self._pending_entry is not None:
                await self._manage_pending_entry(market)
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
                self.emit("status_reason", {"message": f"Resolved market skipped: {reason}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            signal = generate_signal(market, tick, mode_cfg, strategy_mode)
            self._emit_market_payload(market, signal.score, signal.side, quote_source, quote_stale)

            if not signal.should_trade or signal.score < mode_cfg.min_signal_score:
                self.emit("status_reason", {"message": f"Skipping entry: {signal.reasons[0] if signal.reasons else 'score gate'}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            side = signal.side
            limit_price = market.yes_ask if side == "buy_yes" else market.no_ask
            try:
                order = await self.kalshi.place_limit_order(market.ticker, side, 1, limit_price)
            except Exception as exc:
                self.emit("order", {"ticker": market.ticker, "side": side, "price": limit_price, "status": f"rejected: {exc}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            self._pending_entry = PendingOrderState(order_id=order.order_id, ticker=market.ticker, side=side, size=1, limit_price_cents=limit_price, created_at_ts=time.time())
            if order.order_id:
                self._resting_bot_orders[order.order_id] = {"ticker": market.ticker, "created_at": time.time(), "side": side}
            self.emit("order", {"ticker": market.ticker, "side": side, "price": limit_price, "status": order.status, "order_id": order.order_id})
            self.emit("status_reason", {"message": f"Pending entry submitted: {order.order_id}"})

            await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
