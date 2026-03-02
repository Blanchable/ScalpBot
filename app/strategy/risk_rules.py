from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskSnapshot:
    realized_pnl: float = 0.0
    consecutive_losses: int = 0
    open_positions: int = 0


class RiskEngine:
    def __init__(self, daily_max_loss: float, max_consecutive_losses: int, max_positions: int) -> None:
        self.daily_max_loss = daily_max_loss
        self.max_consecutive_losses = max_consecutive_losses
        self.max_positions = max_positions

    def can_enter(self, snapshot: RiskSnapshot) -> tuple[bool, str]:
        if snapshot.realized_pnl <= -abs(self.daily_max_loss):
            return False, "Daily max loss reached"
        if snapshot.consecutive_losses >= self.max_consecutive_losses:
            return False, "Consecutive loss limit reached"
        if snapshot.open_positions >= self.max_positions:
            return False, "Max simultaneous positions reached"
        return True, "OK"
