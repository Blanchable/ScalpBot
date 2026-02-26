from app.brokers.kalshi_client import Market
from app.feeds.btc_reference_feed import FeedTick
from app.strategy.signal_engine import generate_signal


def test_signal_score_range():
    m = Market("A", bid=49, ask=50, midpoint=49.5, seconds_to_expiry=300)
    tick = FeedTick(spot=65000, momentum_5s=1.2, momentum_15s=1.0, volatility=0.2, updated_at=0)
    sig = generate_signal(m, tick, min_edge=1)
    assert 0 <= sig.score <= 100
