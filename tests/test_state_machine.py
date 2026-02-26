import pytest

from app.core.state_manager import StateManager
from app.utils.constants import AppState


def test_valid_transition():
    sm = StateManager()
    sm.transition(AppState.STARTING)
    sm.transition(AppState.SCANNING)
    assert sm.state == AppState.SCANNING


def test_invalid_transition():
    sm = StateManager()
    with pytest.raises(ValueError):
        sm.transition(AppState.IN_POSITION)
