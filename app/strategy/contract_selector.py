from __future__ import annotations

from app.brokers.kalshi_client import Market


def rank_markets(markets: list[Market], spread_limit: int, min_price: int, max_price: int, no_entry_before_expiry: int) -> list[Market]:
    filtered = []
    for m in markets:
        spread = m.ask - m.bid
        if not m.active:
            continue
        if spread > spread_limit:
            continue
        if not (min_price <= m.midpoint <= max_price):
            continue
        if m.seconds_to_expiry <= no_entry_before_expiry:
            continue
        filtered.append(m)
    return sorted(filtered, key=lambda x: ((x.ask - x.bid), abs(50 - x.midpoint)))
