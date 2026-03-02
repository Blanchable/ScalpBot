import asyncio

from app.brokers.kalshi_client import KalshiClient


class DummyResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self) -> dict:
        return self._payload


def test_connect_success_with_mocked_balance(monkeypatch):
    client = KalshiClient()

    monkeypatch.setattr(client, "_load_private_key", lambda _: object())

    async def fake_request(method: str, path: str, **kwargs):
        return DummyResponse(200, {"balance": 12345})

    monkeypatch.setattr(client, "_request", fake_request)

    ok = asyncio.run(client.connect("APIKEY123", "PEM", "paper"))
    assert ok is True
    assert client.connected is True
    assert client.connection_verified is True
    assert client.cash_balance == 123.45


def test_connect_fails_on_unauthorized(monkeypatch):
    client = KalshiClient()
    monkeypatch.setattr(client, "_load_private_key", lambda _: object())

    async def fake_request(method: str, path: str, **kwargs):
        return DummyResponse(401, {"message": "unauthorized"})

    monkeypatch.setattr(client, "_request", fake_request)

    ok = asyncio.run(client.connect("APIKEY123", "PEM", "paper"))
    assert ok is False
    assert client.connected is False
    assert "error" in client.last_error.lower() or "rejected" in client.last_error.lower()


def test_connect_fails_on_invalid_pem(monkeypatch):
    client = KalshiClient()

    def bad_key(_):
        raise ValueError("invalid pem")

    monkeypatch.setattr(client, "_load_private_key", bad_key)
    ok = asyncio.run(client.connect("APIKEY123", "not-a-pem", "paper"))
    assert ok is False
    assert "credential error" in client.last_error.lower()


def test_connect_fails_on_timeout(monkeypatch):
    client = KalshiClient()
    monkeypatch.setattr(client, "_load_private_key", lambda _: object())

    async def fake_request(method: str, path: str, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(client, "_request", fake_request)

    ok = asyncio.run(client.connect("APIKEY123", "PEM", "paper"))
    assert ok is False
    assert "error" in client.last_error.lower() or "rejected" in client.last_error.lower()
