import asyncio

from app.config.settings import AppSettings
from app.core.controller import AppController
from app.utils.constants import AppState


def test_controller_emits_connected_true_only_after_success(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    controller = AppController(AppSettings(), emit)

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        controller.kalshi.account_label = "ABCD1234... (paper)"
        controller.kalshi.cash_balance = 42.0
        return True

    async def fake_summary() -> dict:
        return {
            "cash_balance": 42.0,
            "connected": True,
            "account": "ABCD1234... (paper)",
            "verified": True,
            "environment": "paper",
        }

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)

    async def fake_loop(strategy_mode: str):
        await asyncio.sleep(0.01)

    monkeypatch.setattr(controller, "_loop", fake_loop)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.05)
        controller.state.state = AppState.SCANNING
        await controller.stop()

    asyncio.run(run())

    connection_events = [payload for kind, payload in events if kind == "connection"]
    assert connection_events
    assert connection_events[0]["connected"] is True


def test_controller_emits_connected_false_on_auth_failure(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    controller = AppController(AppSettings(), emit)

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        controller.kalshi.last_error = "Kalshi authentication rejected"
        return False

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)

    asyncio.run(controller.start("ABCD1234", "secret", "paper", "15m"))

    connection_events = [payload for kind, payload in events if kind == "connection"]
    assert connection_events
    assert connection_events[-1]["connected"] is False
