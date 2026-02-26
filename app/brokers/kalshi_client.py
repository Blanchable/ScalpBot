from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ModuleNotFoundError:  # pragma: no cover
    hashes = None
    serialization = None
    padding = None

logger = logging.getLogger("APP.KALSHI")

BTC_15M_SERIES_TICKER = "KXBTC15M"
# TODO: confirm the exact live 1h BTC series ticker in your Kalshi account and update here if needed.
BTC_1H_SERIES_TICKER = "KXBTC1H"
CLOSE_WINDOW_15M_SECONDS = 120
CLOSE_WINDOW_1H_SECONDS = 300


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
    close_time: datetime | None = None
    series_ticker: str = ""

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

    def _extract_close_dt(self, item: dict) -> datetime | None:
        for field in (
            "close_time",
            "close_date",
            "close_datetime",
            "expiration_time",
            "expiration_datetime",
            "end_date",
            "market_close_time",
            "settlement_time",
        ):
            value = item.get(field)
            if not value:
                continue
            try:
                close_dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                continue
            return close_dt.astimezone(timezone.utc)
        return None

    def _series_for_mode(self, mode: str) -> str:
        if mode == "15m":
            return BTC_15M_SERIES_TICKER
        if mode == "1h":
            return BTC_1H_SERIES_TICKER
        raise ValueError(f"Unsupported mode: {mode}")

    def _close_window_seconds(self, mode: str) -> int:
        return CLOSE_WINDOW_15M_SECONDS if mode == "15m" else CLOSE_WINDOW_1H_SECONDS

    def _compute_target_close(self, mode: str, now: datetime) -> datetime:
        current = now.astimezone(timezone.utc)
        if mode == "15m":
            minute_bucket = ((current.minute // 15) + 1) * 15
            if minute_bucket == 60:
                return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            return current.replace(minute=minute_bucket, second=0, microsecond=0)
        if mode == "1h":
            return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        raise ValueError(f"Unsupported mode: {mode}")

    def _parse_quote(self, item: dict) -> tuple[int, int, int, int] | None:
        yes_bid_raw = item.get("yes_bid")
        yes_ask_raw = item.get("yes_ask")
        no_bid_raw = item.get("no_bid")
        no_ask_raw = item.get("no_ask")

        yes_bid = int(yes_bid_raw) if yes_bid_raw is not None else None
        yes_ask = int(yes_ask_raw) if yes_ask_raw is not None else None
        no_bid = int(no_bid_raw) if no_bid_raw is not None else None
        no_ask = int(no_ask_raw) if no_ask_raw is not None else None

        if yes_bid is None and no_ask is not None:
            yes_bid = 100 - no_ask
        if yes_ask is None and no_bid is not None:
            yes_ask = 100 - no_bid
        if no_bid is None and yes_ask is not None:
            no_bid = 100 - yes_ask
        if no_ask is None and yes_bid is not None:
            no_ask = 100 - yes_bid

        if yes_bid is None or yes_ask is None or no_bid is None or no_ask is None:
            return None
        spread = yes_ask - yes_bid
        if yes_ask < yes_bid or spread < 0 or spread > 99:
            return None
        return yes_bid, yes_ask, no_bid, no_ask

    def _market_from_item(self, item: dict, now: datetime) -> Market | None:
        quote = self._parse_quote(item)
        if quote is None:
            return None
        close_dt = self._extract_close_dt(item)
        if close_dt is None:
            return None
        yes_bid, yes_ask, no_bid, no_ask = quote
        midpoint = (yes_bid + yes_ask) / 2
        return Market(
            ticker=str(item.get("ticker", "")),
            title=str(item.get("title", "")),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            midpoint=midpoint,
            seconds_to_expiry=max(0, int((close_dt - now).total_seconds())),
            active=bool(item.get("open", True)),
            close_time=close_dt,
            series_ticker=str(item.get("series_ticker", "")),
        )

    async def resolve_active_btc_market(self, mode: str, now: datetime | None = None) -> Market | None:
        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        target_close = self._compute_target_close(mode, now_utc)
        series = self._series_for_mode(mode)
        window = self._close_window_seconds(mode)
        params = {
            "status": "open",
            "series_ticker": series,
            "min_close_ts": int((target_close - timedelta(seconds=window)).timestamp()),
            "max_close_ts": int((target_close + timedelta(seconds=window)).timestamp()),
            "limit": 20,
        }
        logger.info("Resolving %s BTC market in series %s for target close %s", mode, series, target_close.isoformat())

        response = await self._request("GET", f"{self.REST_PREFIX}/markets", params=params, auth=False)
        response.raise_for_status()
        items = response.json().get("markets", [])
        candidates: list[tuple[float, Market]] = []
        for item in items:
            if str(item.get("series_ticker", "")) != series:
                continue
            market = self._market_from_item(item, now_utc)
            if market is None or not market.active:
                continue
            if market.close_time is None:
                continue
            distance = abs((market.close_time - target_close).total_seconds())
            candidates.append((distance, market))

        if not candidates:
            logger.info("No active BTC %s market found near target close %s", mode, target_close.isoformat())
            return None

        candidates.sort(key=lambda x: x[0])
        resolved = candidates[0][1]
        logger.info("Resolved market %s closing at %s", resolved.ticker, resolved.close_time.isoformat() if resolved.close_time else "unknown")
        return resolved

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
        resolved = await self.resolve_active_btc_market(mode)
        if resolved is None:
            logger.info("No active BTC %s market resolved", mode)
            return []
        return [resolved]
