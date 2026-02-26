import asyncio

from app.brokers.kalshi_client import Market, OrderResult
from app.config.settings import AppSettings
from app.core.controller import AppController
from app.feeds.btc_reference_feed import FeedTick
from app.strategy.signal_engine import Signal


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

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=5, momentum_15s=4, momentum_60s=6, volatility=1, updated_at=1, is_stale=False)

    async def fake_markets(mode: str):
        return [Market(ticker="KXBTCD-15m-ATM", yes_bid=47, yes_ask=49, no_bid=51, no_ask=53, midpoint=48, seconds_to_expiry=400)]

    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 47, "best_yes_ask": 49, "best_no_bid": 51, "best_no_ask": 53}

    async def fake_open_orders():
        return []

    async def fake_place_limit_order(ticker: str, signal_side: str, qty: int, limit_price: float):
        return OrderResult(order_id="abc", status="submitted", fill_price=limit_price)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
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


def test_controller_skips_new_entry_when_open_orders_exist(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    controller = AppController(settings, emit)

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary() -> dict:
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=6, momentum_15s=5, momentum_60s=6, volatility=1, updated_at=1, is_stale=False)

    async def fake_markets(mode: str):
        return [Market(ticker="T", yes_bid=47, yes_ask=49, no_bid=51, no_ask=53, midpoint=48, seconds_to_expiry=400)]

    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 47, "best_yes_ask": 49, "best_no_bid": 51, "best_no_ask": 53}

    async def fake_open_orders():
        return [{"id": "existing"}]

    called = {"order": 0}

    async def fake_place_limit_order(*args, **kwargs):
        called["order"] += 1
        return OrderResult(order_id="x", status="submitted", fill_price=None)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "list_btc_markets", fake_markets)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", fake_orderbook)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place_limit_order)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.5)
        await controller.stop()

    asyncio.run(run())

    assert called["order"] == 0
    reasons = [payload for kind, payload in events if kind == "status_reason"]
    assert any("open orders" in r["message"].lower() for r in reasons)


def test_controller_uses_no_ask_limit_for_buy_no(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    controller = AppController(settings, emit)

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary() -> dict:
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=-6, momentum_15s=-5, momentum_60s=-7, volatility=1, updated_at=1, is_stale=False)

    async def fake_markets(mode: str):
        return [Market(ticker="T", yes_bid=52, yes_ask=53, no_bid=47, no_ask=48, midpoint=52, seconds_to_expiry=400)]

    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 52, "best_yes_ask": 53, "best_no_bid": 47, "best_no_ask": 48}

    async def fake_open_orders():
        return []

    captured = {"price": None, "side": None}

    async def fake_place_limit_order(ticker: str, signal_side: str, qty: int, limit_price: float):
        captured["price"] = limit_price
        captured["side"] = signal_side
        return OrderResult(order_id="x", status="submitted", fill_price=limit_price)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "list_btc_markets", fake_markets)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", fake_orderbook)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place_limit_order)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "1h")
        await asyncio.sleep(0.6)
        await controller.stop()

    asyncio.run(run())

    assert captured["side"] == "buy_no"
    assert captured["price"] == 48


def test_controller_skips_trade_on_stale_feed(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    controller = AppController(settings, emit)

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary() -> dict:
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def stale_tick():
        return FeedTick(spot=65000, momentum_5s=0, momentum_15s=0, momentum_60s=0, volatility=1, updated_at=1, is_stale=True)

    called = {"order": 0}

    async def fake_place_limit_order(*args, **kwargs):
        called["order"] += 1
        return OrderResult(order_id="x", status="submitted", fill_price=None)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", stale_tick)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place_limit_order)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.5)
        await controller.stop()

    asyncio.run(run())

    assert called["order"] == 0
    reasons = [payload for kind, payload in events if kind == "status_reason"]
    assert any("stale" in r["message"].lower() for r in reasons)


def test_controller_emits_bid_ask_preview_when_all_candidates_filtered(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    controller = AppController(settings, emit)

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary() -> dict:
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=1, momentum_15s=1, momentum_60s=1, volatility=1, updated_at=1, is_stale=False)

    async def fake_markets(mode: str):
        # Expires too soon, so it will be filtered out by rank_markets.
        return [Market(ticker="BTC-PREVIEW", yes_bid=49, yes_ask=51, no_bid=49, no_ask=51, midpoint=50, seconds_to_expiry=1)]

    async def fake_open_orders():
        return []

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "list_btc_markets", fake_markets)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.4)
        await controller.stop()

    asyncio.run(run())

    market_events = [payload for kind, payload in events if kind == "market"]
    assert market_events
    assert market_events[0]["ticker"] == "BTC-PREVIEW"
    assert market_events[0]["bid"] == 49
    assert market_events[0]["ask"] == 51
    reasons = [payload for kind, payload in events if kind == "status_reason"]
    assert any("preview" in r["message"].lower() for r in reasons)
