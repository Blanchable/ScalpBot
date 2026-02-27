from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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

from app.utils.time_utils import next_quarter_hour_utc, next_top_of_hour_utc, parse_api_datetime, seconds_until, utc_now

logger = logging.getLogger("APP.KALSHI")

BTC_15M_SERIES_TICKER = "KXBTC15M"
BTC_1H_SERIES_TICKER = ""
DEFAULT_RESOLVER_MAX_DELTA_SECONDS_15M = 1200
DEFAULT_RESOLVER_MAX_DELTA_SECONDS_1H = 4200
MAX_MARKET_SCAN_ITEMS = 3000


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
    raw_status: str = ""

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
        self.last_market_resolution_reason = ""
        self.environment = "paper"
        self.base_url = "https://demo-api.kalshi.co"
        self.api_key_id = ""
        self._private_key = None
        self._http: Any = None
        self.btc_15m_series_ticker = BTC_15M_SERIES_TICKER
        self.btc_1h_series_ticker = BTC_1H_SERIES_TICKER
        self.resolver_max_delta_seconds_15m = DEFAULT_RESOLVER_MAX_DELTA_SECONDS_15M
        self.resolver_max_delta_seconds_1h = DEFAULT_RESOLVER_MAX_DELTA_SECONDS_1H

    def configure_resolver(self, *, btc_15m_series_ticker: str, btc_1h_series_ticker: str, max_delta_15m: int, max_delta_1h: int) -> None:
        self.btc_15m_series_ticker = (btc_15m_series_ticker or BTC_15M_SERIES_TICKER).strip()
        self.btc_1h_series_ticker = (btc_1h_series_ticker or "").strip()
        self.resolver_max_delta_seconds_15m = int(max_delta_15m)
        self.resolver_max_delta_seconds_1h = int(max_delta_1h)

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

    def _select_series_for_mode(self, mode: str) -> str:
        if mode == "15m":
            return self.btc_15m_series_ticker
        if mode == "1h":
            return self.btc_1h_series_ticker
        raise ValueError(f"Unsupported mode: {mode}")

    def _target_close_for_mode(self, mode: str, now: datetime) -> datetime:
        if mode == "15m":
            return next_quarter_hour_utc(now)
        if mode == "1h":
            return next_top_of_hour_utc(now)
        raise ValueError(f"Unsupported mode: {mode}")

    def _max_delta_for_mode(self, mode: str) -> int:
        return self.resolver_max_delta_seconds_15m if mode == "15m" else self.resolver_max_delta_seconds_1h

    async def _fetch_series_open_markets(self, series_ticker: str) -> list[dict]:
        all_markets: list[dict] = []
        cursor: str | None = None
        while len(all_markets) < MAX_MARKET_SCAN_ITEMS:
            params: dict[str, Any] = {"series_ticker": series_ticker, "status": "open", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            response = await self._request("GET", f"{self.REST_PREFIX}/markets", params=params, auth=False)
            response.raise_for_status()
            payload = response.json()
            markets = payload.get("markets", [])
            if isinstance(markets, list):
                all_markets.extend(markets)
            cursor = payload.get("cursor")
            if not cursor:
                break
        return all_markets[:MAX_MARKET_SCAN_ITEMS]

    @staticmethod
    def _to_cents(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(round(float(value) * 100))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _read_cents(item: dict, key: str) -> int | None:
        direct = item.get(key)
        if direct is not None:
            try:
                return int(direct)
            except (TypeError, ValueError):
                return None
        dollar_key = f"{key}_dollars"
        if dollar_key in item:
            return KalshiClient._to_cents(item.get(dollar_key))
        return None

    def _parse_market_from_list_item(self, item: dict, now: datetime) -> Market | None:
        close_dt = parse_api_datetime(str(item.get("close_time", "")))
        if close_dt is None:
            return None

        yes_bid = self._read_cents(item, "yes_bid")
        yes_ask = self._read_cents(item, "yes_ask")
        no_bid = self._read_cents(item, "no_bid")
        no_ask = self._read_cents(item, "no_ask")

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
        if yes_ask < yes_bid:
            return None
        spread = yes_ask - yes_bid
        if spread < 0 or spread > 99:
            return None

        status = str(item.get("status", "")).lower()
        midpoint = (yes_bid + yes_ask) / 2
        return Market(
            ticker=str(item.get("ticker", "")),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            midpoint=midpoint,
            seconds_to_expiry=max(0, seconds_until(close_dt, now)),
            active=status == "active",
            title=str(item.get("title", "")),
            close_time=close_dt,
            series_ticker=str(item.get("series_ticker", "")),
            raw_status=status,
        )

    def _choose_nearest_market(self, markets: list[Market], target_close: datetime, max_delta_seconds: int) -> Market | None:
        if not markets:
            return None
        nearest = min(markets, key=lambda m: abs((m.close_time - target_close).total_seconds()) if m.close_time else 10**9)
        if nearest.close_time is None:
            return None
        delta = abs((nearest.close_time - target_close).total_seconds())
        if delta > max_delta_seconds:
            self.last_market_resolution_reason = (
                f"Closest active market {nearest.ticker} delta {int(delta)}s exceeded threshold {max_delta_seconds}s"
            )
            return None
        return nearest

    async def resolve_btc_target_market(self, mode: str, now: datetime | None = None) -> Market | None:
        self.last_market_resolution_reason = ""
        now_utc = (now or utc_now()).astimezone(timezone.utc)
        try:
            series_ticker = self._select_series_for_mode(mode)
        except ValueError as exc:
            self.last_market_resolution_reason = str(exc)
            return None

        if mode == "1h" and not series_ticker:
            self.last_market_resolution_reason = "1h series ticker is not configured. Set global_settings.btc_1h_series_ticker."
            return None

        target_close = self._target_close_for_mode(mode, now_utc)
        max_delta = self._max_delta_for_mode(mode)

        items = await self._fetch_series_open_markets(series_ticker)
        parsed: list[Market] = []
        statuses: dict[str, int] = {}
        missing_close = 0
        unpriced = 0
        ticker_samples: list[str] = []
        for item in items:
            status = str(item.get("status", "")).lower()
            statuses[status or "unknown"] = statuses.get(status or "unknown", 0) + 1
            market = self._parse_market_from_list_item(item, now_utc)
            if len(ticker_samples) < 5:
                ticker_samples.append(f"{item.get('ticker','?')}@{item.get('close_time','?')}")
            if market is None:
                if parse_api_datetime(str(item.get("close_time", ""))) is None:
                    missing_close += 1
                else:
                    unpriced += 1
                continue
            parsed.append(market)

        active = [m for m in parsed if m.raw_status == "active"]
        chosen = self._choose_nearest_market(active, target_close, max_delta)

        if chosen is None:
            base = (
                f"Resolver miss mode={mode} series={series_ticker} target={target_close.isoformat()} "
                f"returned={len(items)} parsed={len(parsed)} active={len(active)} "
                f"statuses={statuses} missing_close={missing_close} unpriced={unpriced} sample={ticker_samples}"
            )
            if self.last_market_resolution_reason:
                self.last_market_resolution_reason = f"{base}; {self.last_market_resolution_reason}"
            else:
                self.last_market_resolution_reason = f"{base}; no active market near target"
            return None

        self.last_market_resolution_reason = (
            f"Resolved mode={mode} series={series_ticker} ticker={chosen.ticker} close={chosen.close_time.isoformat()} "
            f"seconds_to_expiry={chosen.seconds_to_expiry} bid={chosen.yes_bid} ask={chosen.yes_ask} mid={chosen.midpoint:.1f}"
        )
        return chosen

    async def resolve_active_btc_market(self, mode: str, now: datetime | None = None) -> Market | None:
        return await self.resolve_btc_target_market(mode, now)

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
            "quote_ts": time.time(),
        }

    async def list_btc_markets(self, mode: str) -> list[Market]:
        resolved = await self.resolve_btc_target_market(mode)
        if resolved is None:
            return []
        return [resolved]
