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
        self.environment = "paper"

    async def connect(self, api_key: str, api_secret: str, environment: str) -> bool:
        self.last_error = ""
        self.environment = environment
        self.connected = bool(api_key and api_secret)
        self.connection_verified = self.connected

        if self.connected:
            suffix = "PAPER-SIM" if environment == "paper" else "PRODUCTION-SIM"
            self.account_label = f"{api_key[:4]}... ({suffix})"
            self.cash_balance = 10000.0 if environment == "paper" else 5000.0
        else:
            self.account_label = ""
            self.cash_balance = 0.0
            self.last_error = f"Failed to connect to {environment}: missing API key or secret key file contents."
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
            "environment": self.environment,
        }

    async def get_market_strike_snapshot(self, mode: str) -> dict:
        return {"mode": mode, "strike": 65000.0, "source": f"{self.environment}-sim"}

    async def get_orderbook_snapshot(self, mode: str) -> dict:
        return {"mode": mode, "best_bid": 47, "best_ask": 49, "source": f"{self.environment}-sim"}

    async def get_open_orders(self) -> list[dict]:
        return []

    async def list_btc_markets(self, mode: str) -> list[Market]:
        base = "KXBTCD-"
        return [
            Market(ticker=f"{base}{mode}-ATM", bid=47, ask=49, midpoint=48.0, seconds_to_expiry=900 if mode == "15m" else 3600),
            Market(ticker=f"{base}{mode}-UP", bid=62, ask=64, midpoint=63.0, seconds_to_expiry=900 if mode == "15m" else 3600),
        ]
