from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.config.defaults import DEFAULT_GLOBAL_SETTINGS, DEFAULT_MODE_SETTINGS


@dataclass
class ModeSettings:
    spread_filter_cents: int
    min_price_cents: int
    max_price_cents: int
    preferred_mid_low: int
    preferred_mid_high: int
    min_edge_after_friction_cents: int
    profit_target_cents: int
    stop_loss_cents: int
    time_stop_seconds: int
    no_entry_before_expiry_seconds: int
    flatten_before_expiry_seconds: int
    cooldown_after_loss_seconds: int
    min_signal_score: int


@dataclass
class GlobalSettings:
    broker_mode: str = "paper"
    strategy_mode: str = "15m"
    max_simultaneous_positions: int = 1
    max_entry_reprice_attempts: int = 1
    entry_order_timeout_ms: int = 1500
    max_entry_slippage_cents: int = 1
    allow_marketable_limit_on_stop: bool = True
    allow_mean_reversion: bool = False
    daily_max_loss: float = 50.0
    session_max_loss: float = 25.0
    max_consecutive_losses: int = 3
    max_position_size: int = 1
    max_notional_exposure: float = 100.0
    feed_stale_seconds: int = 5
    scan_interval_seconds: float = 1.0
    btc_15m_series_ticker: str = "KXBTC15M"
    btc_1h_series_ticker: str = ""
    resolver_max_delta_seconds_15m: int = 1200
    resolver_max_delta_seconds_1h: int = 4200


@dataclass
class AppSettings:
    data_dir: Path = Path("data")
    db_path: Path = Path("data/db/scalpbot.sqlite3")
    log_dir: Path = Path("data/logs")
    export_dir: Path = Path("data/exports")
    global_settings: GlobalSettings = field(default_factory=lambda: GlobalSettings(**DEFAULT_GLOBAL_SETTINGS))
    mode_settings: dict[str, ModeSettings] = field(
        default_factory=lambda: {k: ModeSettings(**v) for k, v in DEFAULT_MODE_SETTINGS.items()}
    )
