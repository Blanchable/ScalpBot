import asyncio

from app.config.settings import AppSettings
from app.core.controller import AppController


def test_production_environment_connects_with_separate_mode():
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    controller = AppController(AppSettings(), emit)

    async def run():
        await controller.start("PRODKEY1", "secret", "production", "15m")
        await asyncio.sleep(0.05)
        await controller.stop()

    asyncio.run(run())

    conns = [p for k, p in events if k == "connection"]
    assert conns
    assert conns[0]["connected"] is True
    assert conns[0]["broker_mode"] == "production"
