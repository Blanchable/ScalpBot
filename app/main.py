from __future__ import annotations

import sys

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication

from app.config.settings import AppSettings
from app.gui.main_window import MainWindow
from app.storage.db import init_db
from app.utils.logging_setup import setup_logging


def main() -> int:
    load_dotenv()
    settings = AppSettings()
    init_db(settings.db_path)
    setup_logging(settings.log_dir)

    app = QApplication(sys.argv)
    window = MainWindow(settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
