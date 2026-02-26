from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
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

from app.config.secrets import SecretStore
from app.config.settings import AppSettings
from app.core.controller import AppController


class GuiBus(QObject):
    event = Signal(str, dict)


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle("Kalshi BTC Scalp Bot")
        self.resize(1400, 900)
        self.bus = GuiBus()
        self.bus.event.connect(self.on_event)
        self.secret_store = SecretStore(Path("data"))
        self._controller: AppController | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._controller_ready = threading.Event()
        self._build_ui()
        self._load_credentials()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        self.status = QLabel("Idle: waiting for user to start")
        self.connection_status = QLabel("Kalshi: Disconnected [paper]")
        self.market_mode_status = QLabel("Market Mode: not connected")
        layout.addWidget(self.status)
        layout.addWidget(self.connection_status)
        layout.addWidget(self.market_mode_status)

        metrics = QGridLayout()
        self.cash_balance_label = QLabel("Cash Balance: $0.00")
        self.session_pnl_label = QLabel("Session PnL: $0.00")
        self.trade_count_label = QLabel("Trades: 0")
        self.polling_label = QLabel("Poll/min — strike: 0 | orderbook: 0 | open orders: 0")
        metrics.addWidget(self.cash_balance_label, 0, 0)
        metrics.addWidget(self.session_pnl_label, 0, 1)
        metrics.addWidget(self.trade_count_label, 0, 2)
        metrics.addWidget(self.polling_label, 1, 0, 1, 3)
        layout.addLayout(metrics)

        top = QHBoxLayout()
        form = QFormLayout()
        self.api_key = QLineEdit()
        self.key_file_input = QLineEdit(str(self.secret_store.key_path))
        self.key_file_input.setReadOnly(True)
        self.browse_key_btn = QPushButton("Browse .key...")
        self.browse_key_btn.clicked.connect(self.browse_secret_key_file)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_file_input)
        key_row.addWidget(self.browse_key_btn)
        key_row_widget = QWidget()
        key_row_widget.setLayout(key_row)

        self.mode = QComboBox()
        self.mode.addItems(["15m", "1h"])
        self.environment = QComboBox()
        self.environment.addItems(["paper", "production"])
        self.environment.currentTextChanged.connect(self.on_environment_changed)
        form.addRow("API Key", self.api_key)
        form.addRow("Secret Key File", key_row_widget)
        form.addRow("Environment", self.environment)
        form.addRow("Strategy Mode", self.mode)

        self.max_pos_input = QLineEdit(str(self.settings.global_settings.max_position_size))
        self.daily_loss_input = QLineEdit(str(self.settings.global_settings.daily_max_loss))
        self.scan_interval_input = QLineEdit(str(self.settings.global_settings.scan_interval_seconds))
        form.addRow("Max Position Size", self.max_pos_input)
        form.addRow("Daily Max Loss", self.daily_loss_input)
        form.addRow("Scan Interval (sec)", self.scan_interval_input)
        top.addLayout(form)

        btns = QVBoxLayout()
        self.save_btn = QPushButton("Save Credentials")
        self.start_btn = QPushButton("Start Bot")
        self.stop_btn = QPushButton("Stop Bot")
        self.stop_btn.setEnabled(False)
        self.start_btn.setEnabled(True)
        self.save_btn.clicked.connect(self.save_credentials)
        self.start_btn.clicked.connect(self.start_bot)
        self.stop_btn.clicked.connect(self.stop_bot)
        btns.addWidget(self.save_btn)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        top.addLayout(btns)
        layout.addLayout(top)

        self.market_table = QTableWidget(0, 4)
        self.market_table.setHorizontalHeaderLabels(["Ticker", "Bid", "Ask", "Signal Score"])
        layout.addWidget(self.market_table)

        self.trade_table = QTableWidget(0, 4)
        self.trade_table.setHorizontalHeaderLabels(["Ticker", "Entry", "Exit", "PnL"])
        layout.addWidget(self.trade_table)

        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Structured logs appear here (state, connection, orders, trades, risk, polling)...")
        layout.addWidget(self.logs)

    def browse_secret_key_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select Secret Key File",
            str(self.secret_store.key_path.parent),
            "Key Files (*.key)",
        )
        if not selected:
            return
        try:
            self.secret_store.set_key_file(Path(selected))
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid key file", str(exc))
            return
        self.key_file_input.setText(str(self.secret_store.key_path))

    def on_environment_changed(self, environment: str) -> None:
        self.secret_store.load_credentials(environment=environment)
        self._load_credentials()

    def _load_credentials(self) -> None:
        api_key, _ = self.secret_store.load_credentials(environment=self.environment.currentText())
        self.api_key.setText(api_key)
        self.key_file_input.setText(str(self.secret_store.key_path))

    def _apply_runtime_settings(self) -> bool:
        try:
            self.settings.global_settings.max_position_size = int(self.max_pos_input.text())
            self.settings.global_settings.daily_max_loss = float(self.daily_loss_input.text())
            self.settings.global_settings.scan_interval_seconds = max(0.2, float(self.scan_interval_input.text()))
            return True
        except ValueError:
            QMessageBox.warning(self, "Invalid settings", "Please enter valid numeric values in settings fields.")
            return False

    def save_credentials(self) -> None:
        self.secret_store.save_credentials(self.api_key.text().strip(), environment=self.environment.currentText())
        QMessageBox.information(self, "Saved", "API key and key-file path stored successfully.")

    def _emit(self, kind: str, payload: dict) -> None:
        self.bus.event.emit(kind, payload)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._controller = AppController(self.settings, self._emit)
        self._controller_ready.set()
        self._loop.run_forever()

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive() and self._controller is not None and self._loop is not None:
            return
        self._controller_ready.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._controller_ready.wait(timeout=3)

    def start_bot(self) -> None:
        if not self._apply_runtime_settings():
            return
        selected_environment = self.environment.currentText()

        try:
            api_secret = self.secret_store.read_secret_key(environment=selected_environment)
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "Missing key file", str(exc))
            return

        self._ensure_thread()
        if self._controller is None or self._loop is None:
            QMessageBox.warning(self, "Startup error", "Controller thread failed to initialize. Please restart app.")
            return
        broker_mode = selected_environment
        coro = self._controller.start(self.api_key.text().strip(), api_secret, broker_mode, self.mode.currentText())
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop_bot(self) -> None:
        if self._controller and self._loop:
            asyncio.run_coroutine_threadsafe(self._controller.stop(), self._loop)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def on_event(self, kind: str, payload: dict) -> None:
        if kind == "state":
            self.status.setText(f"State: {payload['state']}")
            self.logs.append(f"STATE {payload['state']}")
        elif kind == "connection":
            if payload.get("connected"):
                verified = payload.get("verified", False)
                verify_text = "verified" if verified else "unverified"
                env = payload.get("broker_mode", payload.get("environment", "paper"))
                self.connection_status.setText(f"Kalshi: Connected [{env}] ({payload.get('account', 'Unknown')}, {verify_text})")
                self.cash_balance_label.setText(f"Cash Balance: ${payload.get('cash_balance', 0.0):,.2f}")
                self.start_btn.setEnabled(False)
                self.stop_btn.setEnabled(True)
            else:
                self.connection_status.setText(f"Kalshi: Disconnected [{self.environment.currentText()}]")
                self.cash_balance_label.setText("Cash Balance: $0.00")
                self.start_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
        elif kind == "market_mode":
            mode = payload.get("strategy_mode", "?")
            chosen = payload.get("selected_market", "awaiting selection")
            self.market_mode_status.setText(f"Market Mode: {mode} | Selected: {chosen}")
        elif kind == "session_metrics":
            self.session_pnl_label.setText(f"Session PnL: ${payload.get('session_pnl', 0.0):,.2f}")
            self.trade_count_label.setText(f"Trades: {payload.get('trade_count', 0)}")
        elif kind == "polling_stats":
            self.polling_label.setText(
                "Poll/min — strike: "
                f"{payload.get('strike_per_min', 0)} | orderbook: {payload.get('orderbook_per_min', 0)} "
                f"| open orders: {payload.get('open_orders_per_min', 0)}"
            )
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
