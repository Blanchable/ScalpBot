# Kalshi BTC Scalp Bot (Windows-first)

A desktop GUI trading bot scaffold for BTC interval scalp trading on Kalshi with **15m** and **1h** strategy modes.

## Features in this build

- Windows one-click setup + launch (`scripts/setup_and_launch.bat`)
- PySide6 desktop control center
- Strategy mode switch (15m / 1h)
- Kalshi connection status indicator with connected account label
- Paper mode default; live mode explicit toggle confirmation
- Credential storage (encrypted local file)
- Async controller loop with explicit bot states
- Market filtering + signal scoring + paper order simulation
- SQLite persistence bootstrap with required tables
- Logging setup with rotating file logs
- Environment verification script
- Pytest coverage for critical modules

## Quick start (Windows)

1. Double-click `scripts/setup_and_launch.bat`
2. Wait for first-run setup
3. Enter your Kalshi API key in GUI and choose your secret `.key` file
4. Save credentials (stores API key + selected key-file path)
5. Choose mode (`15m` or `1h`)
6. Leave live toggle OFF for paper mode
7. Click **Start Bot**

## Manual run (dev)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/bootstrap.py
python scripts/verify_env.py
python -m app.main
```

## Tests

```bash
pytest -q
```

## Project layout

- `app/gui`: PySide6 control center
- `app/core`: state manager and app controller
- `app/brokers`: Kalshi client abstraction + paper broker
- `app/feeds`: BTC reference feed abstraction
- `app/strategy`: market selection, signal engine, risk checks
- `app/storage`: sqlite schema + repositories
- `app/config`: defaults, settings models, secret storage
- `scripts`: setup, bootstrap, env checks, helper bat files
- `tests`: critical smoke/unit tests

## Notes

- Current Kalshi + feed modules are adapter stubs designed for extension.
- This build emphasizes safe scaffolding and transparency for iterative hardening.
