from app.brokers.kalshi_client import Market
from app.strategy.contract_selector import rank_markets


def test_market_filter_prefers_preferred_band():
    markets = [
        Market("A", yes_bid=40, yes_ask=42, no_bid=58, no_ask=60, midpoint=41, seconds_to_expiry=500),
        Market("B", yes_bid=54, yes_ask=56, no_bid=44, no_ask=46, midpoint=55, seconds_to_expiry=500),
    ]
    ranked = rank_markets(
        markets,
        spread_limit=3,
        min_price=20,
        max_price=80,
        no_entry_before_expiry=90,
        preferred_mid_low=50,
        preferred_mid_high=60,
    )
    assert [m.ticker for m in ranked] == ["B", "A"]
