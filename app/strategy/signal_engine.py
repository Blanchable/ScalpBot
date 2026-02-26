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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def generate_signal(market: Market, tick: FeedTick, mode_cfg: ModeSettings, mode: str) -> Signal:
    reasons: list[str] = []
    if tick.is_stale:
        reasons.append("Feed stale")
        return Signal(0, 0.0, "none", False, market.yes_ask, market.midpoint, reasons)

    spread = market.yes_ask - market.yes_bid
    if spread > mode_cfg.spread_filter_cents:
        reasons.append("Spread above threshold")

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

    min_edge = mode_cfg.min_edge_after_friction_cents
    side = "none"
    edge = max(yes_edge, no_edge)
    limit_price = market.yes_ask
    should_trade = False

    if yes_edge > no_edge and yes_edge >= min_edge and aligned:
        side = "buy_yes"
        edge = yes_edge
        limit_price = market.yes_ask
        should_trade = True
        reasons.append("YES edge passes threshold")
    elif no_edge > yes_edge and no_edge >= min_edge and aligned:
        side = "buy_no"
        edge = no_edge
        limit_price = market.no_ask
        should_trade = True
        reasons.append("NO edge passes threshold")
    else:
        reasons.append("No side has enough edge after friction")

    in_preferred = mode_cfg.preferred_mid_low <= market.midpoint <= mode_cfg.preferred_mid_high
    spread_quality = max(0.0, mode_cfg.spread_filter_cents - spread + 1)
    edge_quality = max(0.0, edge)
    score = int(_clamp(20 + edge_quality * 12 + momentum_strength * 9 + spread_quality * 6 + (8 if in_preferred else 0), 0, 100))

    return Signal(score, float(edge), side, should_trade, int(limit_price), float(fair_yes), reasons)
