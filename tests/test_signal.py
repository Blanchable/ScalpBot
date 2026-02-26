from app.brokers.kalshi_client import Market
from app.config.settings import ModeSettings
from app.feeds.btc_reference_feed import FeedTick
from app.strategy.signal_engine import generate_signal


def mode_settings() -> ModeSettings:
    return ModeSettings(
        spread_filter_cents=2,
        min_price_cents=15,
        max_price_cents=85,
        preferred_mid_low=25,
        preferred_mid_high=75,
        min_edge_after_friction_cents=2,
        profit_target_cents=3,
        stop_loss_cents=2,
        time_stop_seconds=90,
        no_entry_before_expiry_seconds=90,
        flatten_before_expiry_seconds=45,
        cooldown_after_loss_seconds=60,
        min_signal_score=65,
    )


def test_signal_stale_feed_no_trade():
    m = Market("A", yes_bid=49, yes_ask=50, no_bid=50, no_ask=51, midpoint=49.5, seconds_to_expiry=300)
    tick = FeedTick(spot=65000, momentum_5s=2, momentum_15s=2, momentum_60s=3, volatility=1, updated_at=0, is_stale=True)
    sig = generate_signal(m, tick, mode_settings(), "15m")
    assert sig.should_trade is False


def test_signal_buy_yes_uses_yes_ask():
    m = Market("A", yes_bid=45, yes_ask=46, no_bid=54, no_ask=55, midpoint=45.5, seconds_to_expiry=300)
    tick = FeedTick(spot=65000, momentum_5s=8, momentum_15s=7, momentum_60s=9, volatility=1, updated_at=0, is_stale=False)
    sig = generate_signal(m, tick, mode_settings(), "15m")
    assert 0 <= sig.score <= 100
    assert sig.side == "buy_yes"
    assert sig.limit_price == m.yes_ask


def test_signal_buy_no_uses_no_ask():
    m = Market("A", yes_bid=52, yes_ask=53, no_bid=47, no_ask=48, midpoint=52.5, seconds_to_expiry=300)
    tick = FeedTick(spot=65000, momentum_5s=-8, momentum_15s=-7, momentum_60s=-9, volatility=1, updated_at=0, is_stale=False)
    sig = generate_signal(m, tick, mode_settings(), "1h")
    assert 0 <= sig.score <= 100
    assert sig.side == "buy_no"
    assert sig.limit_price == m.no_ask


def test_signal_insufficient_edge_no_trade():
    m = Market("A", yes_bid=49, yes_ask=50, no_bid=50, no_ask=51, midpoint=50, seconds_to_expiry=300)
    tick = FeedTick(spot=65000, momentum_5s=0.1, momentum_15s=0.1, momentum_60s=0.1, volatility=2, updated_at=0, is_stale=False)
    sig = generate_signal(m, tick, mode_settings(), "15m")
    assert sig.should_trade is False
