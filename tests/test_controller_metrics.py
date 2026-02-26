import asyncio

from app.config.settings import AppSettings
from app.core.controller import AppController


def test_controller_emits_session_and_polling_metrics():
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    controller = AppController(settings, emit)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(1.25)
        await controller.stop()

    asyncio.run(run())

    session_metrics = [e for e in events if e[0] == "session_metrics"]
    polling_stats = [e for e in events if e[0] == "polling_stats"]
    market_mode = [e for e in events if e[0] == "market_mode"]

    assert session_metrics
    assert polling_stats
    assert market_mode
    assert polling_stats[0][1]["strike_per_min"] > 0
