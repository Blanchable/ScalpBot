from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ModuleNotFoundError:  # pragma: no cover
    InvalidSignature = Exception
    hashes = None
    serialization = None
    padding = None


FIFTEEN_MIN_RE = re.compile(r"\b(15\s*-?\s*(M|MIN|MINS|MINUTE|MINUTES))\b", re.IGNORECASE)
ONE_HOUR_RE = re.compile(r"\b((1|60)\s*-?\s*(H|HR|HRS|M|MIN|MINS|HOUR|HOURS)|HOURLY)\b", re.IGNORECASE)


@dataclass
class Market:
    ticker: str
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int
    midpoint: float
    seconds_to_expiry: int
    active: bool = True
    title: str = ""

    @property
    def bid(self) -> int:
        return self.yes_bid

    @bid.setter
    def bid(self, value: int) -> None:
        self.yes_bid = int(value)

    @property
    def ask(self) -> int:
        return self.yes_ask

    @ask.setter
    def ask(self, value: int) -> None:
        self.yes_ask = int(value)


@dataclass
class OrderResult:
    order_id: str
    status: str
    fill_price: float | None = None


class KalshiClient:
    REST_PREFIX = "/trade-api/v2"

    def __init__(self) -> None:
        self.connected = False
        self.account_label = ""
        self.cash_balance = 0.0
        self.connection_verified = False
        self.last_error = ""
        self.environment = "paper"
        self.base_url = "https://demo-api.kalshi.co"
        self.api_key_id = ""
        self._private_key = None
        self._http: Any = None

    def _set_environment(self, environment: str) -> None:
        if environment == "paper":
            self.base_url = "https://demo-api.kalshi.co"
        elif environment == "production":
            self.base_url = "https://api.elections.kalshi.com"
        else:
            raise ValueError(f"Invalid environment: {environment}")
        self.environment = environment

    def _load_private_key(self, pem_text: str):
        if not pem_text.strip():
            raise ValueError("Secret key file is empty")
        if serialization is None:
            raise ValueError("cryptography is required to parse PEM private keys")
        return serialization.load_pem_private_key(pem_text.encode("utf-8"), password=None)

    def _sign_request(self, method: str, path: str, timestamp_ms: str) -> str:
        if self._private_key is None:
            raise ValueError("Private key is not loaded")
        signed_path = path.split("?", 1)[0]
        message = f"{timestamp_ms}{method.upper()}{signed_path}".encode("utf-8")
        if padding is None or hashes is None:
            raise ValueError("cryptography is required for Kalshi request signing")
        signature = self._private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("ascii")

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if not self.api_key_id:
            raise ValueError("API key id is missing")
        timestamp_ms = str(int(time.time() * 1000))
        signature = self._sign_request(method, path, timestamp_ms)
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature,
        }

    async def _request(self, method: str, path: str, *, params=None, json=None, auth: bool = True):
        if httpx is None:
            raise RuntimeError("httpx is required for Kalshi REST calls")
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        headers = self._auth_headers(method, path) if auth else {}
        return await self._http.request(method, path, params=params, json=json, headers=headers)

    def _market_text(self, item: dict) -> str:
        ticker = str(item.get("ticker", ""))
        title = str(item.get("title", ""))
        return f"{ticker} {title}".upper()

    def _extract_close_dt(self, item: dict) -> datetime | None:
        for field in (
            "close_time",
            "expiration_time",
            "expiration_datetime",
            "end_date",
            "close_date",
            "market_close_time",
            "settlement_time",
        ):
            value = item.get(field)
            if not value:
                continue
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                continue
        return None

    def _classify_btc_interval(self, item: dict) -> str | None:
        text = self._market_text(item)
        has_btc = "BTC" in text
        if not has_btc:
            return None

        has_15 = bool(FIFTEEN_MIN_RE.search(text))
        has_1h = bool(ONE_HOUR_RE.search(text))

        if has_15 and not has_1h:
            return "15m"
        if has_1h and not has_15:
            return "1h"
        if has_15 and has_1h:
            return None

        close_dt = self._extract_close_dt(item)
        if close_dt is None:
            return None
        minute = close_dt.astimezone(timezone.utc).minute
        if minute == 0:
            return "1h"
        if minute in {15, 30, 45}:
            return "15m"
        return None

    async def connect(self, api_key: str, api_secret: str, environment: str) -> bool:
        self.last_error = ""
        self.connected = False
        self.connection_verified = False
        self.cash_balance = 0.0
        self.account_label = ""

        try:
            self._set_environment(environment)
            self.api_key_id = api_key.strip()
            self._private_key = self._load_private_key(api_secret)
            response = await self._request("GET", f"{self.REST_PREFIX}/portfolio/balance", auth=True)
            response.raise_for_status()
            payload = response.json()
            balance_cents = payload.get("balance", 0)
            if isinstance(balance_cents, dict):
                balance_cents = balance_cents.get("balance", 0)
            self.cash_balance = float(balance_cents) / 100.0
            self.connected = True
            self.connection_verified = True
            self.account_label = f"{self.api_key_id[:8]}... ({self.environment})"
            return True
        except ValueError as exc:
            self.last_error = f"Credential error: {exc}"
        except TypeError as exc:
            self.last_error = f"Signing error: {exc}"
        except Exception as exc:
            if httpx is not None and isinstance(exc, httpx.TimeoutException):
                self.last_error = "Connection timeout calling Kalshi /portfolio/balance"
            elif httpx is not None and isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code if exc.response is not None else "unknown"
                self.last_error = f"Kalshi authentication rejected with status {status}"
            elif httpx is not None and isinstance(exc, httpx.HTTPError):
                self.last_error = f"Network error connecting to Kalshi: {exc}"
            else:
                self.last_error = f"Unexpected connection error: {exc}"
        return False

    async def disconnect(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self.connected = False
        self.connection_verified = False
        self.cash_balance = 0.0
        self.account_label = ""

    async def get_account_summary(self) -> dict:
        return {
            "cash_balance": self.cash_balance,
            "connected": self.connected,
            "account": self.account_label,
            "verified": self.connection_verified,
            "environment": self.environment,
        }

    async def get_open_orders(self) -> list[dict]:
        response = await self._request("GET", f"{self.REST_PREFIX}/portfolio/orders", auth=True)
        response.raise_for_status()
        orders = response.json().get("orders", [])
        return orders if isinstance(orders, list) else []

    async def place_limit_order(self, market_ticker: str, signal_side: str, qty: int, limit_price: float) -> OrderResult:
        mapping = {
            "buy_yes": ("buy", "yes", "yes_price"),
            "buy_no": ("buy", "no", "no_price"),
        }
        if signal_side not in mapping:
            raise ValueError(f"Unsupported signal side: {signal_side}")
        action, side, price_field = mapping[signal_side]
        payload = {
            "ticker": market_ticker,
            "client_order_id": f"scalpbot-{int(time.time() * 1000)}",
            "type": "limit",
            "action": action,
            "side": side,
            "count": int(qty),
            price_field: int(round(limit_price)),
        }
        response = await self._request("POST", f"{self.REST_PREFIX}/portfolio/orders", json=payload, auth=True)
        response.raise_for_status()
        body = response.json()
        order = body.get("order", body)
        return OrderResult(str(order.get("order_id", "")), str(order.get("status", "submitted")), order.get("fill_price"))

    async def get_orderbook_snapshot(self, ticker: str) -> dict:
        response = await self._request("GET", f"{self.REST_PREFIX}/markets/{ticker}/orderbook", auth=False)
        response.raise_for_status()
        payload = response.json()
        yes_bids = payload.get("orderbook", {}).get("yes", [])
        no_bids = payload.get("orderbook", {}).get("no", [])
        best_yes_bid = max((int(level[0]) for level in yes_bids), default=0)
        best_no_bid = max((int(level[0]) for level in no_bids), default=0)
        best_yes_ask = 100 - best_no_bid if best_no_bid else 100
        best_no_ask = 100 - best_yes_bid if best_yes_bid else 100
        return {
            "best_yes_bid": best_yes_bid,
            "best_yes_ask": best_yes_ask,
            "best_no_bid": best_no_bid,
            "best_no_ask": best_no_ask,
            "raw": payload,
        }

    async def list_btc_markets(self, mode: str) -> list[Market]:
        response = await self._request("GET", f"{self.REST_PREFIX}/markets", params={"status": "open"}, auth=False)
        response.raise_for_status()
        markets = response.json().get("markets", [])
        now = datetime.now(timezone.utc)
        selected: list[Market] = []

        for item in markets:
            if self._classify_btc_interval(item) != mode:
                continue

            yes_bid = int(item.get("yes_bid", 0) or 0)
            no_bid = int(item.get("no_bid", 0) or 0)
            yes_ask = 100 - no_bid if no_bid else 100
            no_ask = 100 - yes_bid if yes_bid else 100
            midpoint = (yes_bid + yes_ask) / 2

            close_dt = self._extract_close_dt(item)
            seconds_to_expiry = 0
            if close_dt is not None:
                seconds_to_expiry = max(0, int((close_dt - now).total_seconds()))

            selected.append(
                Market(
                    ticker=str(item.get("ticker", "")),
                    title=str(item.get("title", "")),
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    no_bid=no_bid,
                    no_ask=no_ask,
                    midpoint=midpoint,
                    seconds_to_expiry=seconds_to_expiry,
                    active=bool(item.get("open", True)),
                )
            )

        return selected
