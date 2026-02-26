import asyncio

from app.config.settings import AppSettings
from app.core.controller import AppController


def test_live_mode_reports_not_implemented_and_does_not_connect():
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    controller = AppController(AppSettings(), emit)

    async def run():
        await controller.start("ABCD1234", "secret", "live", "15m")

    asyncio.run(run())

    reasons = [p for k, p in events if k == "status_reason"]
    conns = [p for k, p in events if k == "connection"]
    assert reasons
    assert "not connected" in reasons[-1]["message"].lower()
    assert conns
    assert conns[-1]["connected"] is False
