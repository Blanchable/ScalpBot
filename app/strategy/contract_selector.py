from __future__ import annotations

from app.brokers.kalshi_client import Market


def rank_markets(
    markets: list[Market],
    spread_limit: int,
    min_price: int,
    max_price: int,
    no_entry_before_expiry: int,
    preferred_mid_low: int,
    preferred_mid_high: int,
) -> list[Market]:
    filtered: list[Market] = []
    preferred_center = (preferred_mid_low + preferred_mid_high) / 2

    for m in markets:
        spread = m.yes_ask - m.yes_bid
        if not m.active:
            continue
        if spread > spread_limit:
            continue
        if not (min_price <= m.midpoint <= max_price):
            continue
        if m.seconds_to_expiry <= no_entry_before_expiry:
            continue
        filtered.append(m)

    return sorted(
        filtered,
        key=lambda x: (
            0 if preferred_mid_low <= x.midpoint <= preferred_mid_high else 1,
            (x.yes_ask - x.yes_bid),
            abs(x.midpoint - preferred_center),
            abs(50 - x.midpoint),
        ),
    )
