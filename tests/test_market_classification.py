from datetime import datetime, timezone

from app.brokers.kalshi_client import KalshiClient


def _item(ticker: str, title: str, close_iso: str) -> dict:
    return {"ticker": ticker, "title": title, "close_time": close_iso}


def test_classification_explicit_markers_exclusive():
    c = KalshiClient()
    m15 = _item("KXBTC-15M", "BTC 15 MIN", "2026-01-01T00:15:00Z")
    m1h = _item("KXBTC-1H", "BTC 1 HOUR", "2026-01-01T01:00:00Z")
    assert c._classify_btc_interval(m15) == "15m"
    assert c._classify_btc_interval(m1h) == "1h"


def test_classification_ambiguous_rejected():
    c = KalshiClient()
    amb = _item("KXBTC-15M-1H", "BTC 15 MIN 1 HOUR", "2026-01-01T01:00:00Z")
    assert c._classify_btc_interval(amb) is None


def test_classification_fallback_cadence_rules():
    c = KalshiClient()
    at_hour = _item("KXBTC", "BTC interval", "2026-01-01T10:00:00Z")
    at_15 = _item("KXBTC", "BTC interval", "2026-01-01T10:15:00Z")
    at_30 = _item("KXBTC", "BTC interval", "2026-01-01T10:30:00Z")
    at_45 = _item("KXBTC", "BTC interval", "2026-01-01T10:45:00Z")
    at_10 = _item("KXBTC", "BTC interval", "2026-01-01T10:10:00Z")
    assert c._classify_btc_interval(at_hour) == "1h"
    assert c._classify_btc_interval(at_15) == "15m"
    assert c._classify_btc_interval(at_30) == "15m"
    assert c._classify_btc_interval(at_45) == "15m"
    assert c._classify_btc_interval(at_10) is None


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


def test_list_btc_markets_mode_exclusive(monkeypatch):
    c = KalshiClient()

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markets": [
                    {"ticker": "BTC-15M", "title": "BTC 15 MIN", "yes_bid": 40, "no_bid": 58, "open": True, "close_time": "2026-01-01T10:15:00Z"},
                    {"ticker": "BTC-1H", "title": "BTC 1 HOUR", "yes_bid": 45, "no_bid": 53, "open": True, "close_time": "2026-01-01T11:00:00Z"},
                ]
            }

    async def fake_request(method, path, **kwargs):
        return Resp()

    monkeypatch.setattr(c, "_request", fake_request)

    import asyncio

    m15 = asyncio.run(c.list_btc_markets("15m"))
    m1h = asyncio.run(c.list_btc_markets("1h"))

    assert [m.ticker for m in m15] == ["BTC-15M"]
    assert [m.ticker for m in m1h] == ["BTC-1H"]
