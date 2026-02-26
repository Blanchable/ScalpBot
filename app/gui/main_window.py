from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.config.settings import AppSettings
from app.config.secrets import SecretStore
from app.core.controller import AppController


class GuiBus(QObject):
    event = Signal(str, dict)


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle("Kalshi BTC Scalp Bot")
        self.resize(1300, 800)
        self.bus = GuiBus()
        self.bus.event.connect(self.on_event)
        self.secret_store = SecretStore(Path("data"))
        self._controller: AppController | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._build_ui()
        self._load_credentials()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        self.status = QLabel("Idle: waiting for user to start")
        layout.addWidget(self.status)

        ctrl = QHBoxLayout()
        form = QFormLayout()
        self.api_key = QLineEdit()
        self.api_secret = QLineEdit()
        self.api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.mode = QComboBox()
        self.mode.addItems(["15m", "1h"])
        self.live = QCheckBox("Enable LIVE mode")
        form.addRow("API Key", self.api_key)
        form.addRow("API Secret", self.api_secret)
        form.addRow("Strategy Mode", self.mode)
        form.addRow("", self.live)
        ctrl.addLayout(form)

        btns = QVBoxLayout()
        self.save_btn = QPushButton("Save Credentials")
        self.start_btn = QPushButton("Start Bot")
        self.stop_btn = QPushButton("Stop Bot")
        self.stop_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_credentials)
        self.start_btn.clicked.connect(self.start_bot)
        self.stop_btn.clicked.connect(self.stop_bot)
        btns.addWidget(self.save_btn)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        ctrl.addLayout(btns)
        layout.addLayout(ctrl)

        self.market_table = QTableWidget(0, 4)
        self.market_table.setHorizontalHeaderLabels(["Ticker", "Bid", "Ask", "Signal Score"])
        layout.addWidget(self.market_table)

        self.trade_table = QTableWidget(0, 4)
        self.trade_table.setHorizontalHeaderLabels(["Ticker", "Entry", "Exit", "PnL"])
        layout.addWidget(self.trade_table)

        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        layout.addWidget(self.logs)

    def _load_credentials(self) -> None:
        api_key, api_secret = self.secret_store.load_credentials()
        self.api_key.setText(api_key)
        self.api_secret.setText(api_secret)

    def save_credentials(self) -> None:
        self.secret_store.save_credentials(self.api_key.text(), self.api_secret.text())
        QMessageBox.information(self, "Saved", "Credentials stored successfully.")

    def _emit(self, kind: str, payload: dict) -> None:
        self.bus.event.emit(kind, payload)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._controller = AppController(self.settings, self._emit)
        self._loop.run_forever()

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def start_bot(self) -> None:
        if self.live.isChecked():
            confirm = QMessageBox.question(self, "Confirm live mode", "Switch to LIVE mode?")
            if confirm != QMessageBox.StandardButton.Yes:
                return
        self._ensure_thread()
        broker_mode = "live" if self.live.isChecked() else "paper"
        coro = self._controller.start(self.api_key.text(), self.api_secret.text(), broker_mode, self.mode.currentText())
        asyncio.run_coroutine_threadsafe(coro, self._loop)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def stop_bot(self) -> None:
        if self._controller and self._loop:
            asyncio.run_coroutine_threadsafe(self._controller.stop(), self._loop)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def on_event(self, kind: str, payload: dict) -> None:
        if kind == "state":
            self.status.setText(f"State: {payload['state']}")
            self.logs.append(f"STATE {payload['state']}")
        elif kind == "status_reason":
            self.status.setText(payload["message"])
            self.logs.append(payload["message"])
        elif kind == "market":
            self.market_table.setRowCount(1)
            self.market_table.setItem(0, 0, QTableWidgetItem(str(payload["ticker"])))
            self.market_table.setItem(0, 1, QTableWidgetItem(str(payload["bid"])))
            self.market_table.setItem(0, 2, QTableWidgetItem(str(payload["ask"])))
            self.market_table.setItem(0, 3, QTableWidgetItem(str(payload["score"])))
        elif kind == "order":
            self.logs.append(f"ORDER {payload}")
        elif kind == "trade":
            row = self.trade_table.rowCount()
            self.trade_table.insertRow(row)
            self.trade_table.setItem(row, 0, QTableWidgetItem(str(payload["ticker"])))
            self.trade_table.setItem(row, 1, QTableWidgetItem(str(payload["entry"])))
            self.trade_table.setItem(row, 2, QTableWidgetItem(str(payload["exit"])))
            self.trade_table.setItem(row, 3, QTableWidgetItem(str(payload["pnl"])))
            self.logs.append(f"TRADE {payload}")
