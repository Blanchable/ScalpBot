from datetime import datetime, timezone

from app.brokers.kalshi_client import KalshiClient


def test_15m_resolver_selects_nearest_next_quarter(monkeypatch):
    c = KalshiClient()
    c.configure_resolver(btc_15m_series_ticker="KXBTC15M", btc_1h_series_ticker="KXBTC1H", max_delta_15m=1200, max_delta_1h=4200)

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markets": [
                    {
                        "ticker": "KXBTC15M-1015",
                        "series_ticker": "KXBTC15M",
                        "status": "active",
                        "yes_bid": 48,
                        "yes_ask": 50,
                        "close_time": "2026-01-01T10:15:00Z",
                    },
                    {
                        "ticker": "KXBTC15M-1030",
                        "series_ticker": "KXBTC15M",
                        "status": "active",
                        "yes_bid": 48,
                        "yes_ask": 52,
                        "close_time": "2026-01-01T10:30:00Z",
                    },
                ]
            }

    async def fake_request(method, path, **kwargs):
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    m = asyncio.run(c.resolve_btc_target_market("15m", datetime(2026, 1, 1, 10, 7, tzinfo=timezone.utc)))
    assert m is not None
    assert m.ticker == "KXBTC15M-1015"


def test_resolver_ignores_inactive_and_not_literal_open(monkeypatch):
    c = KalshiClient()
    c.configure_resolver(btc_15m_series_ticker="KXBTC15M", btc_1h_series_ticker="KXBTC1H", max_delta_15m=1200, max_delta_1h=4200)

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markets": [
                    {
                        "ticker": "OLD",
                        "series_ticker": "KXBTC15M",
                        "status": "inactive",
                        "yes_bid": 48,
                        "yes_ask": 50,
                        "close_time": "2026-01-01T10:15:00Z",
                    },
                    {
                        "ticker": "GOOD",
                        "series_ticker": "KXBTC15M",
                        "status": "active",
                        "yes_bid": 49,
                        "yes_ask": 51,
                        "close_time": "2026-01-01T10:15:00Z",
                    },
                ]
            }

    async def fake_request(method, path, **kwargs):
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    m = asyncio.run(c.resolve_btc_target_market("15m", datetime(2026, 1, 1, 10, 7, tzinfo=timezone.utc)))
    assert m is not None
    assert m.ticker == "GOOD"


def test_resolver_none_with_reason_when_outside_threshold(monkeypatch):
    c = KalshiClient()
    c.configure_resolver(btc_15m_series_ticker="KXBTC15M", btc_1h_series_ticker="KXBTC1H", max_delta_15m=10, max_delta_1h=4200)

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markets": [
                    {
                        "ticker": "FAR",
                        "series_ticker": "KXBTC15M",
                        "status": "active",
                        "yes_bid": 48,
                        "yes_ask": 50,
                        "close_time": "2026-01-01T10:30:00Z",
                    }
                ]
            }

    async def fake_request(method, path, **kwargs):
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    m = asyncio.run(c.resolve_btc_target_market("15m", datetime(2026, 1, 1, 10, 7, tzinfo=timezone.utc)))
    assert m is None
    assert "exceeded threshold" in c.last_market_resolution_reason


def test_parsing_prefers_yes_ask_and_fallback_only_when_missing():
    c = KalshiClient()
    now = datetime(2026, 1, 1, 10, 7, tzinfo=timezone.utc)
    market_direct = c._parse_market_from_list_item(
        {
            "ticker": "D",
            "status": "active",
            "yes_bid": 48,
            "yes_ask": 50,
            "no_bid": 0,
            "close_time": "2026-01-01T10:15:00Z",
        },
        now,
    )
    assert market_direct is not None
    assert market_direct.yes_ask == 50

    market_fallback = c._parse_market_from_list_item(
        {
            "ticker": "F",
            "status": "active",
            "yes_bid": 48,
            "no_bid": 49,
            "close_time": "2026-01-01T10:15:00Z",
        },
        now,
    )
    assert market_fallback is not None
    assert market_fallback.yes_ask == 51


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



def test_fetch_open_markets_handles_cursor_pagination(monkeypatch):
    c = KalshiClient()
    calls = []

    class Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    async def fake_request(method, path, **kwargs):
        params = kwargs.get("params", {})
        calls.append(params)
        if "cursor" not in params:
            return Resp({"markets": [{"ticker": "A"}], "cursor": "NEXT"})
        return Resp({"markets": [{"ticker": "B"}]})

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    items = asyncio.run(c._fetch_series_open_markets("KXBTC15M"))
    assert [x["ticker"] for x in items] == ["A", "B"]
    assert calls[0]["series_ticker"] == "KXBTC15M"
    assert calls[1]["cursor"] == "NEXT"



def test_resolver_prefers_future_not_within_rollover_buffer(monkeypatch):
    c = KalshiClient()
    c.configure_resolver(btc_15m_series_ticker="KXBTC15M", btc_1h_series_ticker="KXBTC1H", max_delta_15m=3600, max_delta_1h=4200)

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markets": [
                    {"ticker": "SOON", "series_ticker": "KXBTC15M", "status": "active", "yes_bid": 49, "yes_ask": 50, "close_time": "2026-01-01T10:15:00Z"},
                    {"ticker": "NEXT", "series_ticker": "KXBTC15M", "status": "active", "yes_bid": 49, "yes_ask": 50, "close_time": "2026-01-01T10:30:00Z"},
                ]
            }

    async def fake_request(method, path, **kwargs):
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    # At 10:14:00, SOON is 60s to expiry so resolver should prefer NEXT with rollover buffer.
    m = asyncio.run(c.resolve_btc_target_market("15m", datetime(2026, 1, 1, 10, 14, 0, tzinfo=timezone.utc)))
    assert m is not None
    assert m.ticker == "NEXT"


def test_resolver_strict_rollover_excludes_current_ticker(monkeypatch):
    c = KalshiClient()
    c.configure_resolver(btc_15m_series_ticker="KXBTC15M", btc_1h_series_ticker="KXBTC1H", max_delta_15m=3600, max_delta_1h=4200)

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markets": [
                    {"ticker": "CURR", "series_ticker": "KXBTC15M", "status": "active", "yes_bid": 49, "yes_ask": 50, "close_time": "2026-01-01T10:15:00Z"},
                    {"ticker": "NEXT", "series_ticker": "KXBTC15M", "status": "active", "yes_bid": 49, "yes_ask": 50, "close_time": "2026-01-01T10:30:00Z"},
                ]
            }

    async def fake_request(method, path, **kwargs):
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    m = asyncio.run(c.resolve_btc_target_market("15m", datetime(2026, 1, 1, 10, 14, 0, tzinfo=timezone.utc), current_ticker="CURR", strict_rollover=True))
    assert m is not None
    assert m.ticker == "NEXT"


def test_resolver_strict_rollover_returns_none_when_no_future_beyond_buffer(monkeypatch):
    c = KalshiClient()
    c.configure_resolver(btc_15m_series_ticker="KXBTC15M", btc_1h_series_ticker="KXBTC1H", max_delta_15m=3600, max_delta_1h=4200)

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markets": [
                    {"ticker": "CURR", "series_ticker": "KXBTC15M", "status": "active", "yes_bid": 49, "yes_ask": 50, "close_time": "2026-01-01T10:15:00Z"},
                ]
            }

    async def fake_request(method, path, **kwargs):
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    m = asyncio.run(c.resolve_btc_target_market("15m", datetime(2026, 1, 1, 10, 14, 0, tzinfo=timezone.utc), current_ticker="CURR", strict_rollover=True))
    assert m is None
    assert "Strict rollover" in c.last_market_resolution_reason
