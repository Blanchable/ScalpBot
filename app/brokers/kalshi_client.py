from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Market:
    ticker: str
    bid: int
    ask: int
    midpoint: float
    seconds_to_expiry: int
    active: bool = True


class KalshiClient:
    def __init__(self) -> None:
        self.connected = False
        self.account_label = ""
        self.cash_balance = 0.0
        self.connection_verified = False
        self.last_error = ""

    async def connect(self, api_key: str, api_secret: str, live: bool) -> bool:
        self.last_error = ""
        # IMPORTANT: this project currently includes a paper simulator and not full Kalshi
        # signed live-auth integration. Do not pretend live is connected.
        if live:
            self.connected = False
            self.connection_verified = False
            self.account_label = ""
            self.cash_balance = 0.0
            self.last_error = "Live mode not connected: Kalshi signed API auth is not implemented yet. Use PAPER mode."
            return False

        # Paper-mode simulation only.
        self.connected = bool(api_key and api_secret)
        self.connection_verified = self.connected
        if self.connected:
            self.account_label = f"{api_key[:4]}... (PAPER-SIM)"
            self.cash_balance = 10000.0
        else:
            self.account_label = ""
            self.cash_balance = 0.0
            self.last_error = "Missing API key or secret key file contents."
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False
        self.account_label = ""
        self.cash_balance = 0.0
        self.connection_verified = False

    async def get_account_summary(self) -> dict:
        return {
            "cash_balance": self.cash_balance,
            "connected": self.connected,
            "account": self.account_label,
            "verified": self.connection_verified,
        }

    async def get_market_strike_snapshot(self, mode: str) -> dict:
        return {"mode": mode, "strike": 65000.0, "source": "paper-sim"}

    async def get_orderbook_snapshot(self, mode: str) -> dict:
        return {"mode": mode, "best_bid": 47, "best_ask": 49, "source": "paper-sim"}

    async def get_open_orders(self) -> list[dict]:
        return []

    async def list_btc_markets(self, mode: str) -> list[Market]:
        base = "KXBTCD-"
        return [
            Market(ticker=f"{base}{mode}-ATM", bid=47, ask=49, midpoint=48.0, seconds_to_expiry=900 if mode == "15m" else 3600),
            Market(ticker=f"{base}{mode}-UP", bid=62, ask=64, midpoint=63.0, seconds_to_expiry=900 if mode == "15m" else 3600),
        ]
