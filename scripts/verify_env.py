from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check_import(name: str) -> None:
    importlib.import_module(name)


def main() -> int:
    check_import("PySide6")
    check_import("httpx")
    check_import("pydantic")
    db = Path("data/db/scalpbot.sqlite3")
    logs = Path("data/logs")
    exports = Path("data/exports")
    assert db.parent.exists(), "db dir missing"
    logs.mkdir(parents=True, exist_ok=True)
    exports.mkdir(parents=True, exist_ok=True)
    logs.joinpath("write_test.log").write_text("ok", encoding="utf-8")
    print("Environment verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
