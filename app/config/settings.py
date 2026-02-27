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
    resolver_soft_spread_cents: int = 6
    rollover_before_expiry_seconds: int = 90
    entry_order_timeout_seconds: int = 10
    exit_order_timeout_seconds: int = 8
    max_exit_reprice_attempts: int = 1
    stop_activation_seconds: int = 3
    min_hold_seconds: int = 4
    exit_confirm_polls: int = 2
    stop_buffer_beyond_spread_cents: int = 1
    profit_buffer_beyond_spread_cents: int = 0
    min_round_trip_edge_cents: int = 4
    require_edge_multiple_of_spread: bool = True
    spread_edge_multiplier: float = 1.5
    max_entry_price_deviation_from_mark_cents: int = 2
    entry_style: str = "maker_first"
    entry_improve_cents: int = 0
    entry_requote_seconds: int = 2
    max_entry_attempts: int = 2
    cooldown_after_exit_seconds: int = 8
    max_trades_per_contract: int = 3
    require_signal_reset_before_reentry: bool = True
    signal_reset_score_floor: int = 45


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
    quote_stale_seconds: int = 5
    btc_15m_series_ticker: str = "KXBTC15M"
    btc_1h_series_ticker: str = "KXBTC1H"
    resolver_max_delta_seconds_15m: int = 1200
    resolver_max_delta_seconds_1h: int = 4200
    resting_order_timeout_seconds: int = 20
    cancel_bot_resting_orders_on_stop: bool = True


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
