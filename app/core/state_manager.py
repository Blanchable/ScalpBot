from __future__ import annotations

from app.utils.constants import AppState


ALLOWED_TRANSITIONS = {
    AppState.IDLE: {AppState.STARTING},
    AppState.STARTING: {AppState.SCANNING, AppState.HALTED, AppState.ERROR},
    AppState.SCANNING: {AppState.PENDING_ENTRY, AppState.STOPPING, AppState.HALTED, AppState.ERROR},
    AppState.PENDING_ENTRY: {AppState.IN_POSITION, AppState.SCANNING, AppState.STOPPING, AppState.HALTED, AppState.ERROR},
    AppState.IN_POSITION: {AppState.EXITING, AppState.STOPPING, AppState.HALTED, AppState.ERROR},
    AppState.EXITING: {AppState.SCANNING, AppState.STOPPING, AppState.HALTED, AppState.ERROR},
    AppState.STOPPING: {AppState.IDLE, AppState.HALTED, AppState.ERROR},
    AppState.HALTED: {AppState.IDLE},
    AppState.ERROR: {AppState.IDLE, AppState.HALTED},
}


class StateManager:
    def __init__(self) -> None:
        self.state = AppState.IDLE

    def transition(self, new_state: AppState) -> None:
        allowed = ALLOWED_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(f"Invalid transition {self.state} -> {new_state}")
        self.state = new_state
