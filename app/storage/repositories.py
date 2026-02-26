from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class TradeRow:
    market_ticker: str
    strategy_mode: str
    broker_mode: str
    side: str
    qty: int
    entry_price: float
    exit_price: float
    net_pnl: float
    exit_reason: str


class TradeRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add_trade(self, trade: TradeRow) -> None:
        self.conn.execute(
            """
            INSERT INTO trades (market_ticker, strategy_mode, broker_mode, side, qty, entry_price, exit_price, net_pnl, exit_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.market_ticker,
                trade.strategy_mode,
                trade.broker_mode,
                trade.side,
                trade.qty,
                trade.entry_price,
                trade.exit_price,
                trade.net_pnl,
                trade.exit_reason,
            ),
        )
        self.conn.commit()

    def list_recent(self, limit: int = 100) -> list[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return rows
