from datetime import datetime, timezone

from app.brokers.kalshi_client import BTC_15M_SERIES_TICKER, BTC_1H_SERIES_TICKER, KalshiClient


def test_15m_boundary_resolution_and_nearest_contract(monkeypatch):
    c = KalshiClient()
    captured = {}

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markets": [
                    {
                        "ticker": "KXBTC15M-1015",
                        "series_ticker": BTC_15M_SERIES_TICKER,
                        "yes_bid": 48,
                        "yes_ask": 50,
                        "open": True,
                        "close_time": "2026-01-01T10:15:00Z",
                    },
                    {
                        "ticker": "KXBTC15M-1030",
                        "series_ticker": BTC_15M_SERIES_TICKER,
                        "yes_bid": 49,
                        "yes_ask": 51,
                        "open": True,
                        "close_time": "2026-01-01T10:30:00Z",
                    },
                ]
            }

    async def fake_request(method, path, **kwargs):
        captured["params"] = kwargs.get("params", {})
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    market = asyncio.run(c.resolve_active_btc_market("15m", datetime(2026, 1, 1, 10, 7, tzinfo=timezone.utc)))
    assert market is not None
    assert market.ticker == "KXBTC15M-1015"
    assert captured["params"]["series_ticker"] == BTC_15M_SERIES_TICKER


def test_15m_rollover_selects_next_interval(monkeypatch):
    c = KalshiClient()

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markets": [
                    {
                        "ticker": "KXBTC15M-1030",
                        "series_ticker": BTC_15M_SERIES_TICKER,
                        "yes_bid": 48,
                        "yes_ask": 50,
                        "open": True,
                        "close_time": "2026-01-01T10:30:00Z",
                    }
                ]
            }

    async def fake_request(method, path, **kwargs):
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    market = asyncio.run(c.resolve_active_btc_market("15m", datetime(2026, 1, 1, 10, 16, tzinfo=timezone.utc)))
    assert market is not None
    assert market.ticker == "KXBTC15M-1030"


def test_1h_boundary_resolution_uses_1h_series(monkeypatch):
    c = KalshiClient()
    captured = {}

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markets": [
                    {
                        "ticker": "BTC1H-1100",
                        "series_ticker": BTC_1H_SERIES_TICKER,
                        "yes_bid": 47,
                        "yes_ask": 49,
                        "open": True,
                        "close_time": "2026-01-01T11:00:00Z",
                    }
                ]
            }

    async def fake_request(method, path, **kwargs):
        captured["params"] = kwargs.get("params", {})
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    market = asyncio.run(c.resolve_active_btc_market("1h", datetime(2026, 1, 1, 10, 7, tzinfo=timezone.utc)))
    assert market is not None
    assert market.ticker == "BTC1H-1100"
    assert captured["params"]["series_ticker"] == BTC_1H_SERIES_TICKER


def test_mode_exclusivity_series_query(monkeypatch):
    c = KalshiClient()
    calls = []

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"markets": []}

    async def fake_request(method, path, **kwargs):
        calls.append(kwargs.get("params", {}).get("series_ticker"))
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    asyncio.run(c.resolve_active_btc_market("15m", datetime(2026, 1, 1, 10, 7, tzinfo=timezone.utc)))
    asyncio.run(c.resolve_active_btc_market("1h", datetime(2026, 1, 1, 10, 7, tzinfo=timezone.utc)))
    assert calls == [BTC_15M_SERIES_TICKER, BTC_1H_SERIES_TICKER]


def test_yes_ask_prefers_direct_field_not_reciprocal():
    c = KalshiClient()
    quote = c._parse_quote({"yes_bid": 48, "yes_ask": 50})
    assert quote is not None
    yes_bid, yes_ask, _, _ = quote
    assert yes_bid == 48
    assert yes_ask == 50


def test_malformed_quote_rejected():
    c = KalshiClient()
    assert c._parse_quote({"yes_bid": 52, "yes_ask": 50, "no_bid": 50, "no_ask": 48}) is None
    assert c._parse_quote({"yes_bid": 50}) is None


def test_orderbook_derives_asks_from_opposite_bid(monkeypatch):
    c = KalshiClient()

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"orderbook": {"yes": [[47, 10]], "no": [[52, 20]]}}

    async def fake_request(method, path, **kwargs):
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    book = asyncio.run(c.get_orderbook_snapshot("TICK"))
    assert book["best_yes_bid"] == 47
    assert book["best_no_bid"] == 52
    assert book["best_yes_ask"] == 48
    assert book["best_no_ask"] == 53
