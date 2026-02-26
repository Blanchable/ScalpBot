import asyncio

from app.config.settings import AppSettings
from app.core.controller import AppController


def test_controller_emits_connection_event_on_start():
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    controller = AppController(AppSettings(), emit)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.05)
        await controller.stop()

    asyncio.run(run())

    connection_events = [e for e in events if e[0] == "connection"]
    assert connection_events
    assert connection_events[0][1]["connected"] is True
