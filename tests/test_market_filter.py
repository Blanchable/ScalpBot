from app.brokers.kalshi_client import Market
from app.strategy.contract_selector import rank_markets


def test_market_filter():
    markets = [
        Market("A", bid=40, ask=42, midpoint=41, seconds_to_expiry=500),
        Market("B", bid=10, ask=20, midpoint=15, seconds_to_expiry=40),
    ]
    ranked = rank_markets(markets, spread_limit=3, min_price=20, max_price=80, no_entry_before_expiry=90)
    assert [m.ticker for m in ranked] == ["A"]
