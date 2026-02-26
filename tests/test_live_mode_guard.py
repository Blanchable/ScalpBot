import asyncio

from app.config.settings import AppSettings
from app.core.controller import AppController
from app.utils.constants import AppState


def test_controller_passes_selected_environment_to_connect(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    controller = AppController(AppSettings(), emit)
    captured = {"env": ""}

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        captured["env"] = environment
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        controller.kalshi.account_label = "PRODKEY1... (production)"
        controller.kalshi.cash_balance = 50.0
        return True

    async def fake_summary() -> dict:
        return {
            "cash_balance": 50.0,
            "connected": True,
            "account": "PRODKEY1... (production)",
            "verified": True,
            "environment": "production",
        }

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)

    async def fake_loop(strategy_mode: str):
        await asyncio.sleep(0.01)

    monkeypatch.setattr(controller, "_loop", fake_loop)

    async def run():
        await controller.start("PRODKEY1", "secret", "production", "15m")
        await asyncio.sleep(0.05)
        controller.state.state = AppState.SCANNING
        await controller.stop()

    asyncio.run(run())

    assert captured["env"] == "production"
    conns = [p for k, p in events if k == "connection"]
    assert conns
    assert conns[0]["connected"] is True
    assert conns[0]["broker_mode"] == "production"
