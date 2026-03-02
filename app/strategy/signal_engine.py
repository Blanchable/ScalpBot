from __future__ import annotations

from dataclasses import dataclass, field

from app.brokers.kalshi_client import Market
from app.config.settings import ModeSettings
from app.feeds.btc_reference_feed import FeedTick


@dataclass
class Signal:
    score: int
    edge_cents: float
    side: str
    should_trade: bool
    limit_price: int
    fair_yes: float
    reasons: list[str] = field(default_factory=list)
    yes_edge: float = 0.0
    no_edge: float = 0.0
    round_trip_threshold: float = 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def generate_signal(market: Market, tick: FeedTick, mode_cfg: ModeSettings, mode: str) -> Signal:
    reasons: list[str] = []
    if tick.is_stale:
        reasons.append("Feed stale")
        return Signal(0, 0.0, "none", False, market.yes_ask, market.midpoint, reasons)

    yes_spread = max(0, market.yes_ask - market.yes_bid)
    no_spread = max(0, market.no_ask - market.no_bid)

    vol = max(tick.volatility, 0.5)
    short = tick.momentum_5s
    longer = tick.momentum_15s if mode == "15m" else tick.momentum_60s

    norm_short = short / vol
    norm_long = longer / vol
    aligned = (norm_short >= 0 and norm_long >= 0) or (norm_short <= 0 and norm_long <= 0)
    momentum_strength = abs(norm_short) + 0.7 * abs(norm_long)

    if not aligned and abs(norm_short) < 2.2:
        reasons.append("Momentum disagreement")

    probability_shift = _clamp(1.8 * norm_short + 1.0 * norm_long, -10.0, 10.0)
    fair_yes = _clamp(market.midpoint + probability_shift, 1.0, 99.0)
    fair_no = 100.0 - fair_yes

    yes_edge = fair_yes - market.yes_ask
    no_edge = fair_no - market.no_ask

    # Require edge to exceed realistic round-trip friction, not just paper midpoint edge.
    side = "none"
    edge = max(yes_edge, no_edge)
    limit_price = market.yes_ask
    should_trade = False
    round_trip_threshold = float(mode_cfg.min_round_trip_edge_cents)

    def eligible(edge_cents: float, spread: int, ask: int, bid: int) -> tuple[bool, float]:
        threshold = float(mode_cfg.min_round_trip_edge_cents)
        if mode_cfg.require_edge_multiple_of_spread:
            threshold = max(threshold, float(mode_cfg.spread_edge_multiplier * spread))
        ask_dev = max(0, ask - bid)
        if ask_dev > mode_cfg.max_entry_price_deviation_from_mark_cents:
            return False, threshold
        return edge_cents >= threshold, threshold

    yes_ok, yes_thr = eligible(yes_edge, yes_spread, market.yes_ask, market.yes_bid)
    no_ok, no_thr = eligible(no_edge, no_spread, market.no_ask, market.no_bid)

    if yes_edge > no_edge and yes_ok and aligned:
        side = "buy_yes"
        edge = yes_edge
        limit_price = market.yes_ask
        should_trade = True
        round_trip_threshold = yes_thr
        reasons.append("YES edge clears round-trip friction")
    elif no_edge > yes_edge and no_ok and aligned:
        side = "buy_no"
        edge = no_edge
        limit_price = market.no_ask
        should_trade = True
        round_trip_threshold = no_thr
        reasons.append("NO edge clears round-trip friction")
    else:
        reasons.append("Insufficient edge over round-trip friction")

    spread = yes_spread if side == "buy_yes" else no_spread
    in_preferred = mode_cfg.preferred_mid_low <= market.midpoint <= mode_cfg.preferred_mid_high
    spread_quality = max(0.0, mode_cfg.spread_filter_cents - spread + 1)
    edge_quality = max(0.0, edge)
    score = int(_clamp(20 + edge_quality * 12 + momentum_strength * 9 + spread_quality * 6 + (8 if in_preferred else 0), 0, 100))

    return Signal(
        score,
        float(edge),
        side,
        should_trade,
        int(limit_price),
        float(fair_yes),
        reasons,
        yes_edge=float(yes_edge),
        no_edge=float(no_edge),
        round_trip_threshold=float(round_trip_threshold),
    )
