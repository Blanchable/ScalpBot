from __future__ import annotations

from dataclasses import dataclass

from app.brokers.kalshi_client import Market
from app.feeds.btc_reference_feed import FeedTick


@dataclass
class Signal:
    score: int
    edge_cents: float
    side: str


def generate_signal(market: Market, tick: FeedTick, min_edge: int) -> Signal:
    directional = max(-1.0, min(1.0, tick.momentum_5s / 3))
    fair_value = market.midpoint + directional * 3
    edge = abs(fair_value - market.midpoint)
    score = int(min(100, 50 + abs(directional) * 30 + max(0, 5 - (market.ask - market.bid)) * 4 + edge * 2))
    side = "buy_yes" if directional >= 0 else "buy_no"
    if edge < min_edge:
        score = min(score, 50)
    return Signal(score=score, edge_cents=edge, side=side)
