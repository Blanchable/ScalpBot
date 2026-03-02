from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.defaults import DEFAULT_GLOBAL_SETTINGS, DEFAULT_MODE_SETTINGS
from app.storage.db import init_db


def ensure_settings_file() -> None:
    settings_path = Path("data/settings.json")
    if settings_path.exists():
        return
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"global_settings": DEFAULT_GLOBAL_SETTINGS, "mode_settings": DEFAULT_MODE_SETTINGS}
    settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    Path("data/db").mkdir(parents=True, exist_ok=True)
    Path("data/logs").mkdir(parents=True, exist_ok=True)
    Path("data/exports").mkdir(parents=True, exist_ok=True)
    init_db(Path("data/db/scalpbot.sqlite3"))
    ensure_settings_file()
    print("Bootstrap complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
