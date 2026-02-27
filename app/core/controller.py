from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone

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
        self.emit(
            "connection",
            {
                "connected": True,
                "account": summary["account"],
                "cash_balance": summary["cash_balance"],
                "strategy_mode": strategy_mode,
                "broker_mode": broker_mode,
                "verified": summary.get("verified", False),
            },
        )
        self.emit("status_reason", {"message": f"Connected to Kalshi {broker_mode}: {self.kalshi.account_label}"})
        self.emit("market_mode", {"strategy_mode": strategy_mode})
        self.emit("session_metrics", {"session_pnl": self.session_realized_pnl, "trade_count": self.trade_count})
        self._active_strategy_mode = strategy_mode
        self.invalidate_market_cache("start")
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
        self.emit(
            "polling_stats",
            {
                "strike_per_min": round(self.polling_counts["market"] * factor, 2),
                "orderbook_per_min": round(self.polling_counts["orderbook"] * factor, 2),
                "open_orders_per_min": round(self.polling_counts["open_orders"] * factor, 2),
            },
        )
        self.polling_counts = {"market": 0, "orderbook": 0, "open_orders": 0}
        self._polling_window_start = time.time()

    def _is_http_error(self, exc: Exception) -> bool:
        return httpx is not None and isinstance(exc, httpx.HTTPError)

    def _should_reresolve_market(self, strategy_mode: str) -> bool:
        if self._resolved_market is None:
            return True
        if strategy_mode != self._active_strategy_mode:
            return True
        if not self._resolved_market.active:
            return True
        if self._resolved_market.close_time and datetime.now(timezone.utc) >= self._resolved_market.close_time:
            return True
        return False

    async def _resolve_market(self, strategy_mode: str) -> Market | None:
        if self._should_reresolve_market(strategy_mode):
            previous = self._resolved_market.ticker if self._resolved_market else "none"
            self._resolved_market = await self.kalshi.resolve_btc_target_market(strategy_mode)
            self.polling_counts["market"] += 1
            self._active_strategy_mode = strategy_mode
            if self._resolved_market:
                logger.info("Resolved market switched from %s to %s", previous, self._resolved_market.ticker)
                self.emit("status_reason", {"message": self.kalshi.last_market_resolution_reason})
        return self._resolved_market

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

            tick = await self.feed.get_tick()
            if tick.is_stale:
                self.emit("status_reason", {"message": "Skipping entry: BTC feed stale"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            try:
                market = await self._resolve_market(strategy_mode)
                open_orders = await self.kalshi.get_open_orders()
                self.polling_counts["open_orders"] += 1
            except Exception as exc:
                if self._is_http_error(exc):
                    self.invalidate_market_cache("market resolve error")
                    self.emit("status_reason", {"message": f"Kalshi request error: {exc}"})
                    await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                    continue
                self.emit("status_reason", {"message": f"Resolver error: {exc}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if market is None:
                self.emit(
                    "status_reason",
                    {"message": self.kalshi.last_market_resolution_reason or f"No active BTC {strategy_mode} target market"},
                )
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            ok_market, reason = validate_market(
                market,
                mode_cfg.spread_filter_cents,
                mode_cfg.min_price_cents,
                mode_cfg.max_price_cents,
                mode_cfg.no_entry_before_expiry_seconds,
            )
            if not ok_market:
                self.emit("status_reason", {"message": f"Resolved market skipped: {reason}"})
                if "expiry" in reason:
                    self.invalidate_market_cache("near expiry rollover")
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            chosen = market
            try:
                book = await self.kalshi.get_orderbook_snapshot(chosen.ticker)
                self.polling_counts["orderbook"] += 1
            except Exception as exc:
                if self._is_http_error(exc):
                    self.invalidate_market_cache("orderbook fetch failure")
                    self.emit("status_reason", {"message": f"Orderbook error for {chosen.ticker}: {exc}"})
                    await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                    continue
                raise

            self._emit_polling_metrics_if_due()
            chosen.yes_bid = int(book.get("best_yes_bid", chosen.yes_bid))
            chosen.yes_ask = int(book.get("best_yes_ask", chosen.yes_ask))
            chosen.no_bid = int(book.get("best_no_bid", chosen.no_bid))
            chosen.no_ask = int(book.get("best_no_ask", chosen.no_ask))

            signal = generate_signal(chosen, tick, mode_cfg, strategy_mode)
            self.emit(
                "market",
                {
                    "ticker": chosen.ticker,
                    "bid": chosen.yes_bid,
                    "ask": chosen.yes_ask,
                    "score": signal.score,
                    "strategy_mode": strategy_mode,
                    "open_orders": len(open_orders),
                    "edge": round(signal.edge_cents, 2),
                    "side": signal.side,
                },
            )
            self.emit("market_mode", {"strategy_mode": strategy_mode, "selected_market": chosen.ticker})

            if open_orders:
                self.emit("status_reason", {"message": "Skipping entry: open orders already exist"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if not signal.should_trade:
                self.emit("status_reason", {"message": f"Skipping entry: {signal.reasons[0] if signal.reasons else 'no signal'}"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            if signal.score < mode_cfg.min_signal_score:
                self.emit("status_reason", {"message": f"Skipping entry: signal score {signal.score} below threshold"})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            self.state.transition(AppState.PENDING_ENTRY)
            self.emit("state", {"state": self.state.state})
            try:
                order = await self.kalshi.place_limit_order(chosen.ticker, signal.side, 1, signal.limit_price)
            except Exception as exc:
                if not (self._is_http_error(exc) or isinstance(exc, ValueError)):
                    raise
                self.emit(
                    "order",
                    {
                        "ticker": chosen.ticker,
                        "side": signal.side,
                        "price": signal.limit_price,
                        "status": f"rejected: {exc}",
                        "edge": round(signal.edge_cents, 2),
                    },
                )
                self.state.transition(AppState.SCANNING)
                self.emit("state", {"state": self.state.state})
                await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
                continue

            self.emit(
                "order",
                {
                    "ticker": chosen.ticker,
                    "side": signal.side,
                    "price": signal.limit_price,
                    "status": order.status,
                    "order_id": order.order_id,
                    "edge": round(signal.edge_cents, 2),
                },
            )

            pnl = 0.0
            self.session_realized_pnl += pnl
            self.trade_count += 1
            self.emit(
                "trade",
                {
                    "ticker": chosen.ticker,
                    "entry": signal.limit_price,
                    "exit": order.fill_price if order.fill_price is not None else signal.limit_price,
                    "pnl": pnl,
                    "reason": "live_order_submitted",
                },
            )
            self.emit("session_metrics", {"session_pnl": self.session_realized_pnl, "trade_count": self.trade_count})
            self.state.transition(AppState.SCANNING)
            self.emit("state", {"state": self.state.state})

            await asyncio.sleep(self.settings.global_settings.scan_interval_seconds)
