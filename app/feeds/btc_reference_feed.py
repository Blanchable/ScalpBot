from __future__ import annotations

from dataclasses import dataclass
from random import uniform
from time import time


@dataclass
class FeedTick:
    spot: float
    momentum_5s: float
    momentum_15s: float
    volatility: float
    updated_at: float


class BTCReferenceFeed:
    def __init__(self) -> None:
        self._last = FeedTick(spot=65000.0, momentum_5s=0.0, momentum_15s=0.0, volatility=0.0, updated_at=time())

    async def get_tick(self) -> FeedTick:
        move = uniform(-20, 20)
        spot = max(1000, self._last.spot + move)
        self._last = FeedTick(
            spot=spot,
            momentum_5s=move / 10,
            momentum_15s=move / 15,
            volatility=abs(move) / 100,
            updated_at=time(),
        )
        return self._last
