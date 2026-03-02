from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import time

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None


@dataclass
class FeedTick:
    spot: float
    momentum_5s: float
    momentum_15s: float
    momentum_60s: float
    volatility: float
    updated_at: float
    is_stale: bool


class BTCReferenceFeed:
    def __init__(self, stale_seconds: int = 5, min_poll_interval: float = 1.0) -> None:
        self.stale_seconds = stale_seconds
        self.min_poll_interval = min_poll_interval
        self._http = httpx.AsyncClient(timeout=8.0) if httpx else None
        now = time()
        self._last_good_update = 0.0
        self._last_attempt = 0.0
        self._history: deque[tuple[float, float]] = deque(maxlen=240)
        self._last = FeedTick(
            spot=0.0,
            momentum_5s=0.0,
            momentum_15s=0.0,
            momentum_60s=0.0,
            volatility=0.0,
            updated_at=now,
            is_stale=True,
        )

    async def _fetch_spot(self) -> float:
        if self._http is None:
            raise RuntimeError("httpx is required for BTC feed")
        # Public unauthenticated endpoint.
        response = await self._http.get("https://api.coinbase.com/v2/prices/BTC-USD/spot")
        response.raise_for_status()
        payload = response.json()
        amount = payload.get("data", {}).get("amount")
        return float(amount)

    def _value_at_or_before(self, target_ts: float) -> float:
        if not self._history:
            return self._last.spot
        candidate = self._history[0][1]
        for ts, value in self._history:
            if ts <= target_ts:
                candidate = value
            else:
                break
        return candidate

    def _compute_volatility(self) -> float:
        if len(self._history) < 2:
            return 0.0
        window = list(self._history)[-30:]
        diffs = [abs(window[i][1] - window[i - 1][1]) for i in range(1, len(window))]
        return sum(diffs) / len(diffs) if diffs else 0.0

    async def get_tick(self) -> FeedTick:
        now = time()
        should_poll = (now - self._last_attempt) >= self.min_poll_interval

        if should_poll:
            self._last_attempt = now
            try:
                spot = await self._fetch_spot()
                self._history.append((now, spot))
                self._last_good_update = now
            except Exception:
                pass

        stale = self._last_good_update <= 0 or (now - self._last_good_update) > self.stale_seconds
        if not self._history:
            self._last = FeedTick(
                spot=self._last.spot,
                momentum_5s=0.0,
                momentum_15s=0.0,
                momentum_60s=0.0,
                volatility=0.0,
                updated_at=self._last_good_update or now,
                is_stale=True,
            )
            return self._last

        spot_now = self._history[-1][1]
        m5 = spot_now - self._value_at_or_before(now - 5)
        m15 = spot_now - self._value_at_or_before(now - 15)
        m60 = spot_now - self._value_at_or_before(now - 60)
        vol = self._compute_volatility()
        self._last = FeedTick(
            spot=spot_now,
            momentum_5s=m5,
            momentum_15s=m15,
            momentum_60s=m60,
            volatility=vol,
            updated_at=self._last_good_update,
            is_stale=stale,
        )
        return self._last
