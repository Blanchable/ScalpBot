from __future__ import annotations

from dataclasses import dataclass
from itertools import count


@dataclass
class SimOrder:
    order_id: str
    status: str
    fill_price: float | None = None


class PaperBroker:
    def __init__(self) -> None:
        self._ids = count(1)

    async def place_limit_order(self, market: str, side: str, qty: int, limit_price: float) -> SimOrder:
        order_id = f"P-{next(self._ids)}"
        return SimOrder(order_id=order_id, status="filled", fill_price=limit_price)

    async def cancel_all(self) -> None:
        return None
