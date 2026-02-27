import asyncio
import time
from datetime import datetime, timedelta, timezone

from app.brokers.kalshi_client import Market, OrderResult
from app.config.settings import AppSettings
from app.core.controller import AppController, PendingOrderState, PositionState
from app.feeds.btc_reference_feed import FeedTick
from app.strategy.signal_engine import Signal
from app.utils.constants import AppState


def test_controller_emits_session_and_polling_metrics(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    settings.mode_settings["1h"].entry_style = "cross_now"
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

    async def fake_resolve(mode: str, now=None):
        return Market(ticker="KXBTCD-15m-ATM", yes_bid=47, yes_ask=49, no_bid=51, no_ask=53, midpoint=48, seconds_to_expiry=400)

    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 47, "best_yes_ask": 49, "best_no_bid": 51, "best_no_ask": 53}

    async def fake_open_orders():
        return []

    async def fake_place_limit_order(ticker: str, signal_side: str, qty: int, limit_price: float):
        return OrderResult(order_id="abc", status="submitted", fill_price=limit_price)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
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
    settings.mode_settings["1h"].entry_style = "cross_now"
    controller = AppController(settings, emit)

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary() -> dict:
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=6, momentum_15s=5, momentum_60s=6, volatility=1, updated_at=1, is_stale=False)

    async def fake_resolve(mode: str, now=None):
        return Market(ticker="T", yes_bid=47, yes_ask=49, no_bid=51, no_ask=53, midpoint=48, seconds_to_expiry=400)

    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 47, "best_yes_ask": 49, "best_no_bid": 51, "best_no_ask": 53}

    async def fake_open_orders():
        return [{"id": "existing", "status": "resting", "ticker": "T", "remaining_count": 1}]

    called = {"order": 0}

    async def fake_place_limit_order(*args, **kwargs):
        called["order"] += 1
        return OrderResult(order_id="x", status="submitted", fill_price=None)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
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
    settings.mode_settings["1h"].entry_style = "cross_now"
    controller = AppController(settings, emit)

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary() -> dict:
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=-6, momentum_15s=-5, momentum_60s=-7, volatility=1, updated_at=1, is_stale=False)

    async def fake_resolve(mode: str, now=None):
        return Market(ticker="T", yes_bid=52, yes_ask=53, no_bid=47, no_ask=48, midpoint=52, seconds_to_expiry=400)

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
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
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

    async def fake_resolve(mode: str, now=None):
        # Expires too soon, so it will be filtered out by rank_markets.
        return Market(ticker="BTC-PREVIEW", yes_bid=49, yes_ask=51, no_bid=49, no_ask=51, midpoint=50, seconds_to_expiry=1)

    async def fake_open_orders():
        return []

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.4)
        await controller.stop()

    asyncio.run(run())

    reasons = [payload for kind, payload in events if kind == "status_reason"]
    assert any("validation:" in r["message"].lower() for r in reasons)



def test_controller_mode_change_invalidates_cached_market():
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    controller = AppController(settings, emit)
    controller._resolved_market = Market(ticker="OLD", yes_bid=49, yes_ask=50, no_bid=50, no_ask=51, midpoint=49.5, seconds_to_expiry=120)

    controller.invalidate_market_cache("mode changed to 1h")

    assert controller._resolved_market is None



def test_controller_emits_resolver_specific_status_when_unresolved(monkeypatch):
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

    async def fake_resolve(mode: str, now=None):
        controller.kalshi.last_market_resolution_reason = "Resolver miss mode=15m series=KXBTC15M target=..."
        return None

    async def fake_open_orders():
        return []

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.4)
        await controller.stop()

    asyncio.run(run())

    reasons = [payload["message"] for kind, payload in events if kind == "status_reason"]
    assert any("resolver miss" in msg.lower() for msg in reasons)


def test_controller_validation_reason_for_near_expiry(monkeypatch):
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

    async def fake_resolve(mode: str, now=None):
        return Market(ticker="EXP", yes_bid=49, yes_ask=50, no_bid=50, no_ask=51, midpoint=49.5, seconds_to_expiry=1)

    async def fake_open_orders():
        return []

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.4)
        await controller.stop()

    asyncio.run(run())

    reasons = [payload["message"] for kind, payload in events if kind == "status_reason"]
    assert any("validation:" in msg.lower() and "expiry" in msg.lower() for msg in reasons)


def test_controller_1h_blank_series_reports_configuration_error(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    settings.global_settings.btc_1h_series_ticker = ""
    controller = AppController(settings, emit)

    async def fake_connect(api_key: str, api_secret: str, environment: str) -> bool:
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary() -> dict:
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=1, momentum_15s=1, momentum_60s=1, volatility=1, updated_at=1, is_stale=False)

    async def fake_open_orders():
        return []

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "1h")
        await asyncio.sleep(0.4)
        await controller.stop()

    asyncio.run(run())

    reasons = [payload["message"] for kind, payload in events if kind == "status_reason"]
    assert any("1h series ticker is not configured" in msg.lower() for msg in reasons)



def test_snapshot_wide_but_live_ok_not_rejected(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    controller = AppController(settings, emit)

    async def fake_connect(*args, **kwargs):
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary():
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=6, momentum_15s=6, momentum_60s=6, volatility=1, updated_at=1, is_stale=False)

    async def fake_resolve(mode: str, now=None):
        return Market(ticker="T", yes_bid=10, yes_ask=50, no_bid=50, no_ask=90, midpoint=30, seconds_to_expiry=500)

    async def fake_open_orders():
        return []

    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 49, "best_yes_ask": 52, "best_no_bid": 48, "best_no_ask": 51, "quote_ts": 1}

    async def fake_place(*args, **kwargs):
        return OrderResult(order_id="o", status="submitted", fill_price=None)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", fake_orderbook)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place)

    async def run():
        await controller.start("k", "s", "paper", "15m")
        await asyncio.sleep(0.4)
        await controller.stop()

    asyncio.run(run())

    reasons = [p["message"].lower() for k, p in events if k == "status_reason"]
    assert not any("spread too wide" in r for r in reasons)


def test_market_payload_marks_snapshot_fallback_stale(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    settings.global_settings.quote_stale_seconds = 0
    controller = AppController(settings, emit)

    async def fake_connect(*args, **kwargs):
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary():
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=1, momentum_15s=1, momentum_60s=1, volatility=1, updated_at=0, is_stale=False)

    async def fake_resolve(mode: str, now=None):
        return Market(ticker="T", yes_bid=49, yes_ask=50, no_bid=50, no_ask=51, midpoint=49.5, seconds_to_expiry=500)

    async def fake_open_orders():
        return []

    async def bad_orderbook(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", bad_orderbook)

    async def run():
        await controller.start("k", "s", "paper", "15m")
        await asyncio.sleep(0.3)
        await controller.stop()

    asyncio.run(run())

    market_events = [p for k, p in events if k == "market"]
    assert market_events
    assert market_events[-1]["quote_source"] == "snapshot-fallback"
    assert market_events[-1]["quote_stale"] is True



def test_unrelated_resting_order_does_not_block(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    controller = AppController(settings, emit)

    async def fake_connect(*args, **kwargs):
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary():
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=7, momentum_15s=6, momentum_60s=7, volatility=1, updated_at=1, is_stale=False)

    async def fake_resolve(mode: str, now=None):
        return Market(ticker="T", yes_bid=45, yes_ask=46, no_bid=54, no_ask=55, midpoint=45.5, seconds_to_expiry=500)

    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 45, "best_yes_ask": 46, "best_no_bid": 54, "best_no_ask": 55, "quote_ts": 1}

    async def fake_open_orders(*args, **kwargs):
        return [{"id": "manual", "status": "resting", "ticker": "OTHER", "remaining_count": 1}]

    placed = {"n": 0}

    async def fake_place(*args, **kwargs):
        placed["n"] += 1
        return OrderResult(order_id="o", status="submitted", fill_price=None)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", fake_orderbook)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place)

    async def run():
        await controller.start("k", "s", "paper", "15m")
        await asyncio.sleep(0.4)
        await controller.stop()

    asyncio.run(run())
    assert placed["n"] >= 1


def test_auto_cancel_old_bot_order_on_rollover(monkeypatch):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    controller = AppController(settings, emit)
    controller._resting_bot_orders = {"oid": {"ticker": "OLD", "created_at": 0, "side": "buy_yes"}}

    canceled = {"ids": []}

    async def fake_cancel(order_id: str):
        canceled["ids"].append(order_id)
        return True

    monkeypatch.setattr(controller.kalshi, "cancel_order", fake_cancel)

    asyncio.run(controller._cancel_stale_or_old_ticker_orders(current_ticker="NEW"))
    assert "oid" in canceled["ids"]


def test_executed_entry_becomes_active_position_and_blocks_reentry(monkeypatch, tmp_path):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.db_path = tmp_path / "bot.sqlite3"
    settings.global_settings.scan_interval_seconds = 0.1
    settings.global_settings.max_simultaneous_positions = 1
    controller = AppController(settings, emit)

    async def fake_connect(*args, **kwargs):
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary():
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=9, momentum_15s=8, momentum_60s=7, volatility=1, updated_at=1, is_stale=False)

    async def fake_resolve(mode: str, now=None):
        return Market(ticker="T", yes_bid=52, yes_ask=53, no_bid=47, no_ask=48, midpoint=52, seconds_to_expiry=500)

    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 52, "best_yes_ask": 53, "best_no_bid": 47, "best_no_ask": 48}

    async def fake_open_orders(*args, **kwargs):
        return []

    calls = {"place": 0}

    async def fake_place_limit_order(*args, **kwargs):
        calls["place"] += 1
        return OrderResult(order_id="entry-1", status="submitted", fill_price=None)

    async def fake_get_order_status(order_id: str):
        return {"order_id": order_id, "status": "executed", "count": 1, "remaining_count": 0, "filled_count": 1, "yes_price": 53}

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", fake_orderbook)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place_limit_order)
    monkeypatch.setattr(controller.kalshi, "get_order_status", fake_get_order_status)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.55)
        await controller.stop()

    asyncio.run(run())

    assert calls["place"] == 1
    assert len(controller.active_positions) == 1
    assert any(k == "position_opened" for k, _ in events)


def test_trade_emits_on_close_and_persists(monkeypatch, tmp_path):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.db_path = tmp_path / "bot.sqlite3"
    settings.global_settings.scan_interval_seconds = 0.1
    settings.mode_settings["15m"].profit_target_cents = 1
    settings.mode_settings["15m"].flatten_before_expiry_seconds = 1
    settings.mode_settings["15m"].stop_activation_seconds = 0
    settings.mode_settings["15m"].min_hold_seconds = 0
    settings.mode_settings["15m"].exit_confirm_polls = 1
    settings.mode_settings["15m"].enable_trailing_winners = False
    settings.mode_settings["15m"].target_activation_seconds = 0
    controller = AppController(settings, emit)

    async def fake_connect(*args, **kwargs):
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary():
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=9, momentum_15s=8, momentum_60s=7, volatility=1, updated_at=1, is_stale=False)

    async def fake_resolve(mode: str, now=None):
        return Market(ticker="T", yes_bid=60, yes_ask=61, no_bid=39, no_ask=40, midpoint=60, seconds_to_expiry=500)

    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 60, "best_yes_ask": 61, "best_no_bid": 39, "best_no_ask": 40}

    async def fake_open_orders(*args, **kwargs):
        return []

    async def fake_cancel_order(order_id: str):
        return True

    def place_side_effect(ticker: str, side: str, qty: int, limit_price: float):
        if side.startswith("buy"):
            return OrderResult(order_id="entry-1", status="submitted", fill_price=None)
        return OrderResult(order_id="exit-1", status="submitted", fill_price=None)

    async def fake_place_limit_order(ticker: str, side: str, qty: int, limit_price: float):
        return place_side_effect(ticker, side, qty, limit_price)

    async def fake_get_order_status(order_id: str):
        if order_id == "entry-1":
            return {"order_id": "entry-1", "status": "executed", "count": 1, "remaining_count": 0, "filled_count": 1, "yes_price": 58}
        return {"order_id": "exit-1", "status": "executed", "count": 1, "remaining_count": 0, "filled_count": 1, "yes_price": 60}

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", fake_orderbook)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "cancel_order", fake_cancel_order)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place_limit_order)
    monkeypatch.setattr(controller.kalshi, "get_order_status", fake_get_order_status)

    monkeypatch.setattr("app.core.controller.generate_signal", lambda *args, **kwargs: Signal(score=99, edge_cents=5.0, side="buy_yes", should_trade=True, limit_price=61, fair_yes=65.0, reasons=[]))

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.85)
        await controller.stop()

    asyncio.run(run())

    trade_events = [payload for kind, payload in events if kind == "trade"]
    assert len(trade_events) == 1
    assert trade_events[0]["pnl"] == 0.02
    assert controller.trade_count == 1
    assert controller.session_realized_pnl == 0.02

    from app.storage.db import connect
    from app.storage.repositories import TradeRepository

    conn = connect(settings.db_path)
    rows = TradeRepository(conn).list_recent(limit=10)
    assert len(rows) == 1


def test_idempotent_executed_poll_does_not_duplicate_position(monkeypatch, tmp_path):
    events = []

    def emit(kind: str, payload: dict):
        events.append((kind, payload))

    settings = AppSettings()
    settings.db_path = tmp_path / "bot.sqlite3"
    settings.global_settings.scan_interval_seconds = 0.1
    controller = AppController(settings, emit)

    async def fake_connect(*args, **kwargs):
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True

    async def fake_summary():
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}

    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=9, momentum_15s=8, momentum_60s=7, volatility=1, updated_at=1, is_stale=False)

    async def fake_resolve(mode: str, now=None):
        return Market(ticker="T", yes_bid=52, yes_ask=53, no_bid=47, no_ask=48, midpoint=52, seconds_to_expiry=500)

    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 52, "best_yes_ask": 53, "best_no_bid": 47, "best_no_ask": 48}

    async def fake_open_orders(*args, **kwargs):
        return []

    async def fake_place_limit_order(*args, **kwargs):
        return OrderResult(order_id="entry-1", status="submitted", fill_price=None)

    polls = {"n": 0}

    async def fake_get_order_status(order_id: str):
        polls["n"] += 1
        return {"order_id": order_id, "status": "executed", "count": 1, "remaining_count": 0, "filled_count": 1, "yes_price": 53}

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", fake_orderbook)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place_limit_order)
    monkeypatch.setattr(controller.kalshi, "get_order_status", fake_get_order_status)

    async def run():
        await controller.start("ABCD1234", "secret", "paper", "15m")
        await asyncio.sleep(0.75)
        await controller.stop()

    asyncio.run(run())

    opened = [p for k, p in events if k == "position_opened"]
    assert len(opened) == 1
    assert len(controller.active_positions) == 1


def test_stop_not_armed_during_activation_window():
    settings = AppSettings()
    controller = AppController(settings, lambda *_: None)
    controller._active_strategy_mode = "15m"
    cfg = settings.mode_settings["15m"]
    cfg.stop_activation_seconds = 10
    cfg.min_hold_seconds = 10
    controller._position = PositionState(
        ticker="T",
        side="yes",
        entry_price_cents=60,
        size=1,
        opened_at_ts=time.time(),
        entry_order_id="e",
        entry_filled_count=1,
        is_open=True,
        entry_spread_cents_at_fill=2,
    )
    reason = controller._should_exit(Market("T", 56, 58, 42, 44, 57, 300), time.time(), -4)
    assert reason is None


def test_stop_requires_consecutive_confirm_polls():
    settings = AppSettings()
    controller = AppController(settings, lambda *_: None)
    controller._active_strategy_mode = "15m"
    cfg = settings.mode_settings["15m"]
    cfg.stop_activation_seconds = 0
    cfg.min_hold_seconds = 0
    cfg.exit_confirm_polls = 2
    cfg.exit_confirm_polls_target = 2
    cfg.enable_trailing_winners = False
    controller._position = PositionState(
        ticker="T", side="yes", entry_price_cents=60, size=1, opened_at_ts=time.time()-20,
        entry_order_id="e", entry_filled_count=1, is_open=True, entry_spread_cents_at_fill=1,
    )
    assert controller._should_exit(Market("T", 55, 57, 43, 45, 56, 300), time.time(), -5) is None
    assert controller._should_exit(Market("T", 55, 57, 43, 45, 56, 300), time.time(), -5) == "stop_loss"


def test_profit_target_requires_consecutive_confirm_polls():
    settings = AppSettings()
    controller = AppController(settings, lambda *_: None)
    controller._active_strategy_mode = "15m"
    cfg = settings.mode_settings["15m"]
    cfg.stop_activation_seconds = 0
    cfg.min_hold_seconds = 0
    cfg.exit_confirm_polls = 2
    cfg.exit_confirm_polls_target = 2
    cfg.enable_trailing_winners = False
    controller._position = PositionState(
        ticker="T", side="yes", entry_price_cents=50, size=1, opened_at_ts=time.time()-20,
        entry_order_id="e", entry_filled_count=1, is_open=True, entry_spread_cents_at_fill=1,
    )
    assert controller._should_exit(Market("T", 54, 55, 45, 46, 54.5, 300), time.time(), 4) is None
    assert controller._should_exit(Market("T", 54, 55, 45, 46, 54.5, 300), time.time(), 4) == "profit_target"


def test_entry_blocked_during_cooldown_and_loss_cooldown():
    settings = AppSettings()
    controller = AppController(settings, lambda *_: None)
    controller._active_strategy_mode = "15m"
    cfg = settings.mode_settings["15m"]
    cfg.cooldown_after_exit_seconds = 100
    cfg.cooldown_after_loss_seconds = 200
    controller._ticker_trade_stats["T"] = {
        "trade_count": 1,
        "last_exit_time": time.time(),
        "last_exit_reason": "stop_loss",
        "last_signal_side": "buy_yes",
        "signal_reset": True,
    }
    assert controller._entry_block_reason("T") == "cooldown"
    controller._ticker_trade_stats["T"]["last_exit_time"] = time.time() - 110
    assert controller._entry_block_reason("T") == "loss_cooldown"


def test_entry_blocked_by_per_contract_cap_and_signal_reset():
    settings = AppSettings()
    controller = AppController(settings, lambda *_: None)
    controller._active_strategy_mode = "15m"
    cfg = settings.mode_settings["15m"]
    cfg.max_trades_per_contract = 1
    controller._ticker_trade_stats["T"] = {
        "trade_count": 1,
        "last_exit_time": time.time() - 1000,
        "last_exit_reason": "profit_target",
        "last_signal_side": "buy_yes",
        "signal_reset": False,
    }
    assert controller._entry_block_reason("T") == "per_contract_cap"
    controller._ticker_trade_stats["T"]["trade_count"] = 0
    sig = Signal(score=70, edge_cents=6, side="buy_yes", should_trade=True, limit_price=55, fair_yes=60)
    assert controller._entry_block_reason("T", sig) == "signal_not_reset"


def test_no_new_entry_after_stopping(monkeypatch):
    events = []
    def emit(kind: str, payload: dict):
        events.append((kind, payload))
    settings = AppSettings()
    settings.global_settings.scan_interval_seconds = 0.2
    controller = AppController(settings, emit)

    async def fake_connect(*args, **kwargs):
        controller.kalshi.connected = True
        controller.kalshi.connection_verified = True
        return True
    async def fake_summary():
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}
    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=9, momentum_15s=8, momentum_60s=7, volatility=1, updated_at=1, is_stale=False)
    async def fake_resolve(mode: str, now=None):
        return Market(ticker="T", yes_bid=52, yes_ask=53, no_bid=47, no_ask=48, midpoint=52, seconds_to_expiry=500)
    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 52, "best_yes_ask": 53, "best_no_bid": 47, "best_no_ask": 48}
    async def fake_open_orders(*args, **kwargs):
        return []
    called={"n":0, "after_stop":0}
    async def fake_place(*args, **kwargs):
        called["n"] += 1
        if controller._stop_requested:
            called["after_stop"] += 1
        return OrderResult(order_id="x", status="submitted", fill_price=None)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", fake_orderbook)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place)

    async def run():
        await controller.start("ABCD", "sec", "paper", "15m")
        await asyncio.sleep(0.05)
        controller._stop_requested = True
        await asyncio.sleep(0.25)
        await controller.stop()

    asyncio.run(run())
    assert called["after_stop"] == 0


def test_maker_first_entry_uses_bid_improved_limit(monkeypatch):
    events=[]
    def emit(k,p): events.append((k,p))
    settings=AppSettings()
    settings.global_settings.scan_interval_seconds=0.2
    settings.mode_settings["15m"].entry_style = "maker_first"
    settings.mode_settings["15m"].entry_improve_cents = 0
    controller=AppController(settings, emit)

    async def fake_connect(*args, **kwargs):
        controller.kalshi.connected=True
        controller.kalshi.connection_verified=True
        return True
    async def fake_summary():
        return {"cash_balance": 1.0, "connected": True, "account": "A", "verified": True, "environment": "paper"}
    async def fake_tick():
        return FeedTick(spot=65000, momentum_5s=9, momentum_15s=8, momentum_60s=7, volatility=1, updated_at=1, is_stale=False)
    async def fake_resolve(mode: str, now=None):
        return Market(ticker="T", yes_bid=52, yes_ask=54, no_bid=46, no_ask=48, midpoint=53, seconds_to_expiry=500)
    async def fake_orderbook(ticker: str):
        return {"best_yes_bid": 52, "best_yes_ask": 54, "best_no_bid": 46, "best_no_ask": 48}
    async def fake_open_orders(*args, **kwargs):
        return []
    placed={"price":None}
    async def fake_place(ticker, side, qty, limit_price):
        placed["price"] = limit_price
        return OrderResult(order_id="e1", status="submitted", fill_price=None)

    monkeypatch.setattr(controller.kalshi, "connect", fake_connect)
    monkeypatch.setattr(controller.kalshi, "get_account_summary", fake_summary)
    monkeypatch.setattr(controller.feed, "get_tick", fake_tick)
    monkeypatch.setattr(controller.kalshi, "resolve_btc_target_market", fake_resolve)
    monkeypatch.setattr(controller.kalshi, "get_orderbook_snapshot", fake_orderbook)
    monkeypatch.setattr(controller.kalshi, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place)
    monkeypatch.setattr("app.core.controller.generate_signal", lambda *args, **kwargs: Signal(score=99, edge_cents=8.0, side="buy_yes", should_trade=True, limit_price=54, fair_yes=62.0, reasons=[]))

    async def run():
        await controller.start("ABCD", "sec", "paper", "15m")
        await asyncio.sleep(0.35)
        await controller.stop()

    asyncio.run(run())
    assert placed["price"] == 52


def test_refresh_market_time_recomputes_seconds():
    settings = AppSettings()
    controller = AppController(settings, lambda *_: None)
    now = datetime.now(timezone.utc)
    m = Market("T", 50, 51, 49, 50, 50.5, seconds_to_expiry=999, close_time=now + timedelta(seconds=8))
    assert controller._refresh_market_time(m, now) is True
    assert 7 <= m.seconds_to_expiry <= 8


def test_near_expiry_missing_pricing_invalidates_market():
    events = []
    settings = AppSettings()
    controller = AppController(settings, lambda k, p: events.append((k, p)))
    controller._active_strategy_mode = "15m"
    market = Market("T", 0, 50, 0, 50, 50, seconds_to_expiry=80)
    controller._resolved_market = market
    cfg = settings.mode_settings["15m"]
    for _ in range(cfg.invalidate_after_missing_pricing_polls):
        controller._apply_market_invalid_counter("missing_pricing", market, cfg)
    assert controller._resolved_market is None


def test_near_expiry_spread_invalidates_market():
    settings = AppSettings()
    controller = AppController(settings, lambda *_: None)
    controller._active_strategy_mode = "15m"
    market = Market("T", 40, 60, 40, 60, 50, seconds_to_expiry=80)
    controller._resolved_market = market
    cfg = settings.mode_settings["15m"]
    for _ in range(cfg.invalidate_after_spread_reject_polls):
        controller._apply_market_invalid_counter("spread", market, cfg)
    assert controller._resolved_market is None


def test_only_one_pending_exit_allowed(monkeypatch):
    settings = AppSettings()
    controller = AppController(settings, lambda *_: None)
    controller._active_strategy_mode = "15m"
    controller._position = PositionState("T", "yes", 55, 1, time.time(), "e", 1, True)
    calls = {"n": 0}

    async def fake_place(*args, **kwargs):
        calls["n"] += 1
        return OrderResult(order_id=f"x{calls['n']}", status="submitted")

    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place)
    m = Market("T", 54, 55, 45, 46, 54.5, 200)
    asyncio.run(controller._submit_exit_order(m, "stop_loss"))
    asyncio.run(controller._submit_exit_order(m, "stop_loss"))
    assert calls["n"] == 1


def test_stop_exit_slippage_guard_blocks_worse_reprice(monkeypatch):
    events = []
    settings = AppSettings()
    controller = AppController(settings, lambda k, p: events.append((k, p)))
    controller._active_strategy_mode = "15m"
    cfg = settings.mode_settings["15m"]
    cfg.allow_stop_exit_reprice = False
    cfg.max_stop_exit_slippage_cents = 2
    cfg.stop_exit_timeout_seconds = 0
    controller._position = PositionState("T", "yes", 55, 1, time.time(), "e", 1, True)
    controller._pending_exit = PendingOrderState(
        order_id="exit1",
        ticker="T",
        side="sell_yes",
        size=1,
        limit_price_cents=53,
        created_at_ts=time.time() - 5,
        exit_reason="stop_loss",
        trigger_mark_cents=53,
        min_allowed_exit_cents=51,
    )

    async def fake_status(order_id: str):
        return {"order_id": order_id, "status": "resting", "count": 1, "remaining_count": 1, "filled_count": 0}

    async def fake_cancel(order_id: str):
        return True

    async def fake_place(*args, **kwargs):
        raise AssertionError("should not reprice beyond cap")

    monkeypatch.setattr(controller.kalshi, "get_order_status", fake_status)
    monkeypatch.setattr(controller.kalshi, "cancel_order", fake_cancel)
    monkeypatch.setattr(controller.kalshi, "place_limit_order", fake_place)

    m = Market("T", 49, 50, 50, 51, 49.5, 150)
    asyncio.run(controller._manage_pending_exit(m))
    assert controller._pending_exit is not None
    assert controller._pending_exit.guarded is True
    assert any("slippage cap" in e[1]["message"] for e in events if e[0] == "status_reason")
