from pathlib import Path

from app.storage.db import connect, init_db
from app.storage.repositories import TradeRepository, TradeRow


def test_db_write(tmp_path: Path):
    db = tmp_path / "test.sqlite3"
    init_db(db)
    conn = connect(db)
    repo = TradeRepository(conn)
    repo.add_trade(TradeRow("T", "15m", "paper", "buy_yes", 1, 50, 53, 3, "tp"))
    rows = repo.list_recent()
    assert len(rows) == 1
