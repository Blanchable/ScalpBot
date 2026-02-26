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

    async def connect(self, api_key: str, api_secret: str, live: bool) -> bool:
        self.connected = bool(api_key and api_secret)
        if self.connected:
            mode = "LIVE" if live else "PAPER"
            self.account_label = f"{api_key[:4]}... ({mode})"
            self.cash_balance = 5000.0 if live else 10000.0
        else:
            self.account_label = ""
            self.cash_balance = 0.0
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False
        self.account_label = ""
        self.cash_balance = 0.0

    async def get_account_summary(self) -> dict:
        return {"cash_balance": self.cash_balance, "connected": self.connected, "account": self.account_label}

    async def get_market_strike_snapshot(self, mode: str) -> dict:
        return {"mode": mode, "strike": 65000.0}

    async def get_orderbook_snapshot(self, mode: str) -> dict:
        return {"mode": mode, "best_bid": 47, "best_ask": 49}

    async def get_open_orders(self) -> list[dict]:
        return []

    async def list_btc_markets(self, mode: str) -> list[Market]:
        base = "KXBTCD-"
        return [
            Market(ticker=f"{base}{mode}-ATM", bid=47, ask=49, midpoint=48.0, seconds_to_expiry=900 if mode == "15m" else 3600),
            Market(ticker=f"{base}{mode}-UP", bid=62, ask=64, midpoint=63.0, seconds_to_expiry=900 if mode == "15m" else 3600),
        ]
