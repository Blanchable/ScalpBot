from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from app.brokers.kalshi_client import KalshiClient
from app.brokers.paper_broker import PaperBroker
from app.config.settings import AppSettings
from app.feeds.btc_reference_feed import BTCReferenceFeed
from app.strategy.contract_selector import rank_markets
from app.strategy.risk_rules import RiskEngine, RiskSnapshot
from app.strategy.signal_engine import generate_signal
from app.utils.constants import AppState
from app.core.state_manager import StateManager

logger = logging.getLogger("APP.CONTROLLER")


class AppController:
    def __init__(self, settings: AppSettings, emit: Callable[[str, dict], None]) -> None:
        self.settings = settings
        self.emit = emit
        self.state = StateManager()
        self.kalshi = KalshiClient()
        self.paper = PaperBroker()
        self.feed = BTCReferenceFeed()
        self._task: asyncio.Task | None = None
        self._running = False
        gs = settings.global_settings
        self.risk = RiskEngine(gs.daily_max_loss, gs.max_consecutive_losses, gs.max_simultaneous_positions)
        self.snapshot = RiskSnapshot()

    async def start(self, api_key: str, api_secret: str, broker_mode: str, strategy_mode: str) -> None:
        if self._running:
            return
        self.state.transition(AppState.STARTING)
        self.emit("state", {"state": self.state.state})
        ok = await self.kalshi.connect(api_key, api_secret, live=broker_mode == "live")
        if not ok:
            self.state.transition(AppState.HALTED)
            self.emit("status_reason", {"message": "Halted: credentials invalid"})
            self.emit("connection", {"connected": False, "account": ""})
            return
        self.emit("connection", {"connected": True, "account": self.kalshi.account_label})
        self.emit("status_reason", {"message": f"Connected to Kalshi account: {self.kalshi.account_label}"})
        self._running = True
        self._task = asyncio.create_task(self._loop(strategy_mode, broker_mode))

    async def stop(self) -> None:
        if not self._running:
            return
        self.state.transition(AppState.STOPPING)
        self.emit("state", {"state": self.state.state})
        self._running = False
        await self.paper.cancel_all()
        if self._task:
            await self._task
        await self.kalshi.disconnect()
        self.emit("connection", {"connected": False, "account": ""})
        self.state.transition(AppState.IDLE)
        self.emit("state", {"state": self.state.state})

    async def _loop(self, strategy_mode: str, broker_mode: str) -> None:
        self.state.transition(AppState.SCANNING)
        self.emit("state", {"state": self.state.state})
        while self._running:
            mode_cfg = self.settings.mode_settings[strategy_mode]
            can_enter, reason = self.risk.can_enter(self.snapshot)
            if not can_enter:
                self.emit("status_reason", {"message": f"Scanning paused: {reason}"})
                await asyncio.sleep(1)
                continue
            tick = await self.feed.get_tick()
            markets = await self.kalshi.list_btc_markets(strategy_mode)
            ranked = rank_markets(
                markets,
                mode_cfg.spread_filter_cents,
                mode_cfg.min_price_cents,
                mode_cfg.max_price_cents,
                mode_cfg.no_entry_before_expiry_seconds,
            )
            if not ranked:
                self.emit("status_reason", {"message": "Scanning: no candidate passed filters"})
                await asyncio.sleep(1)
                continue
            chosen = ranked[0]
            signal = generate_signal(chosen, tick, mode_cfg.min_edge_after_friction_cents)
            self.emit("market", {"ticker": chosen.ticker, "bid": chosen.bid, "ask": chosen.ask, "score": signal.score})
            if signal.score >= mode_cfg.min_signal_score:
                self.state.transition(AppState.PENDING_ENTRY)
                self.emit("state", {"state": self.state.state})
                order = await self.paper.place_limit_order(chosen.ticker, signal.side, 1, chosen.ask)
                self.emit("order", {"ticker": chosen.ticker, "side": signal.side, "price": chosen.ask, "status": order.status})
                self.emit("trade", {"ticker": chosen.ticker, "entry": chosen.ask, "exit": chosen.ask + 1, "pnl": 1.0, "reason": "tp"})
                self.state.transition(AppState.SCANNING)
                self.emit("state", {"state": self.state.state})
            await asyncio.sleep(1)
