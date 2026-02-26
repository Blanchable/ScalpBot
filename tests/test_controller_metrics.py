import asyncio

from app.brokers.kalshi_client import Market, OrderResult
from app.config.settings import AppSettings
from app.core.controller import AppController


def test_controller_emits_session_and_polling_metrics(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    controller = AppController(settings, emit)

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        controller.kalshi.account_label = "ABCD1234... (paper)"
        controller.kalshi.cash_balance = 100.0
        return True

    async def fake_summary() -> dict:
        return {
            "cash_balance": 100.0,
            "connected": True,
            "account": "ABCD1234... (paper)",
            "verified": True,
            "environment": "paper",
        }

    async def fake_markets(mode: str):
        return [Market(ticker="KXBTCD-15m-ATM", bid=47, ask=49, midpoint=48, seconds_to_expiry=400)]

    async def fake_orderbook(ticker: str):
        return {"best_bid": 47, "best_ask": 49}

    async def fake_open_orders():
        return []

    async def fake_place_limit_order(ticker: str, signal_side: str, qty: int, limit_price: float):
        return OrderResult(order_id="abc", status="submitted", fill_price=limit_price)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.kalshi, "list_btc_markets", fake_markets)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", fake_orderbook)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place_limit_order)

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
