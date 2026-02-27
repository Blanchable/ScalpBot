from __future__ import annotations

from app.brokers.kalshi_client import Market


def validate_market(
    market: Market,
    spread_limit: int,
    min_price: int,
    max_price: int,
    no_entry_before_expiry: int,
) -> tuple[bool, str]:
    if not market.active:
        return False, "inactive"
    if market.yes_bid <= 0 or market.yes_ask <= 0:
        return False, "missing pricing"
    spread = market.yes_ask - market.yes_bid
    if spread < 0:
        return False, "invalid spread"
    if spread > spread_limit:
        return False, f"spread too wide ({spread} > {spread_limit})"
    if not (min_price <= market.midpoint <= max_price):
        return False, f"midpoint out of range ({market.midpoint:.1f})"
    if market.seconds_to_expiry <= no_entry_before_expiry:
        return False, f"too close to expiry ({market.seconds_to_expiry}s <= {no_entry_before_expiry}s)"
    return True, "ok"


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
        ok, _ = validate_market(m, spread_limit, min_price, max_price, no_entry_before_expiry)
        if not ok:
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
