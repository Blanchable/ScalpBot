from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    mode TEXT NOT NULL,
    strategy_mode TEXT NOT NULL,
    realized_pnl REAL DEFAULT 0,
    unrealized_pnl_peak REAL DEFAULT 0,
    trade_count INTEGER DEFAULT 0,
    win_count INTEGER DEFAULT 0,
    loss_count INTEGER DEFAULT 0,
    end_reason TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    market_ticker TEXT NOT NULL,
    strategy_mode TEXT NOT NULL,
    broker_mode TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    entry_order_id TEXT,
    exit_order_id TEXT,
    entry_time TEXT,
    exit_time TEXT,
    entry_price REAL,
    exit_price REAL,
    gross_pnl REAL DEFAULT 0,
    fees REAL DEFAULT 0,
    net_pnl REAL DEFAULT 0,
    exit_reason TEXT,
    signal_score REAL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER,
    broker_order_id TEXT,
    market_ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    limit_price REAL,
    status TEXT NOT NULL,
    submitted_at TEXT,
    updated_at TEXT,
    fill_price REAL,
    fill_qty INTEGER,
    purpose TEXT
);
CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    timestamp TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    avg_entry REAL NOT NULL,
    mark_price REAL NOT NULL,
    unrealized_pnl REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path)
