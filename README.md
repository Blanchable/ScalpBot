# Kalshi BTC Scalp Bot (Windows-first)

A desktop GUI trading app for BTC interval scalp workflows on Kalshi with **15m** and **1h** strategy modes.

## Features in this build

- Windows one-click setup + launch (`scripts/setup_and_launch.bat`)
- PySide6 desktop control center
- Strategy mode switch (15m / 1h)
- Environment toggle for `paper` (Kalshi demo) and `production` (Kalshi production)
- Separate credential profiles per environment (API key id + selected `.key` private key file)
- Real authenticated Kalshi REST verification using `GET /trade-api/v2/portfolio/balance`
- Cash balance shown from real Kalshi response (balance cents converted to dollars)
- Kalshi connection status indicator with verification state
- Session dashboard: cash balance, session PnL, trade count, selected market/mode, and polling rates
- Live transparency panel with per-minute polling counts (market, orderbook, open orders)
- Runtime risk/scanning setting fields in GUI (max position size, daily max loss, scan interval)
- Async controller loop with explicit bot states
- SQLite persistence bootstrap with required tables
- Logging setup with rotating file logs
- Environment verification script
- Pytest coverage for critical modules and mocked HTTP auth flows

## Quick start (Windows)

1. Double-click `scripts/setup_and_launch.bat`
2. Wait for first-run setup
3. Select environment (`paper` or `production`)
4. Enter the matching Kalshi API key id for that environment
5. Choose the matching private key `.key` file for that environment
6. Save credentials for that environment profile
7. Choose strategy mode (`15m` or `1h`)
8. Click **Start Bot**
9. Verify the connection line and cash balance update from Kalshi before trading

## Credentials

- `paper` maps to Kalshi demo (`https://demo-api.kalshi.co`).
- `production` maps to Kalshi production (`https://api.elections.kalshi.com`).
- Each environment requires its own API key id and matching RSA private `.key` file.
- The app stores API key ids and key file paths, but does not store private key PEM contents.

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

## Notes

- REST authentication and account balance verification are real Kalshi calls.
- Market discovery and orderbook requests are live Kalshi REST calls.
- Strategy and order lifecycle handling are still intentionally minimal and should be hardened before unattended production use.


## Strategy selection notes

- BTC interval classification is mode-exclusive: explicit 15m markers route only to `15m`, explicit 1h markers route only to `1h`, ambiguous markers are rejected.
- If explicit markers are missing, close-time fallback is used: minute `00` maps to `1h`, minutes `15/30/45` map to `15m`, all other minutes are rejected.
- Entry logic uses side-correct pricing (`buy_yes -> yes_ask`, `buy_no -> no_ask`) and skips entries on stale feed, insufficient edge, low score, or existing open orders.
