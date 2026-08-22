#!/usr/bin/env python3
"""Preliminary backtest for the frozen SPY 1-minute momentum scalp rules.

This tests the SPY underlying signal. It does not model historical SPY option
contracts, bid and ask spreads, delta, implied volatility, theta, or option fills.
"""
from __future__ import annotations

import json
import math
import os
import time as time_module
from dataclasses import asdict, dataclass, replace
from datetime import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

OUT = Path(os.environ.get("BACKTEST_OUT", "results"))
SYMBOL = "SPY"
SOURCE = "Yahoo Finance chart API"


@dataclass(frozen=True)
class Config:
    ema_period: int = 9
    ema_slope_lookback: int = 3
    breakout_lookback: int = 3
    volume_lookback: int = 20
    volume_multiplier: float = 1.25
    max_ema_distance_pct: float = 0.0012
    entry_buffer: float = 0.01
    stop_buffer: float = 0.01
    min_risk_pct: float = 0.0004
    max_risk_pct: float = 0.0018
    target_r: float = 1.0
    pending_bars: int = 2
    progress_check_minutes: int = 3
    minimum_progress_r: float = 0.25
    max_hold_minutes: int = 8
    max_trades_per_day: int = 5
    max_stop_losses_per_day: int = 2
    daily_loss_limit_r: float = 2.0
    cooldown_minutes: int = 5
    stop_cooldown_minutes: int = 15
    ema_reset_each_session: bool = False


def fetch_yahoo_1m(days: int = 29) -> tuple[pd.DataFrame, dict]:
    now_utc = pd.Timestamp.now(tz="UTC")
    end = now_utc.ceil("D")
    start = end - pd.Timedelta(days=days)
    frames: list[pd.DataFrame] = []
    attempts: list[dict] = []
    cursor = start
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 SPY-scalp-backtest/1.0"})

    while cursor < end:
        chunk_end = min(cursor + pd.Timedelta(days=6), end)
        params = {
            "period1": int(cursor.timestamp()),
            "period2": int(chunk_end.timestamp()),
            "interval": "1m",
            "includePrePost": "false",
            "events": "div,splits",
        }
        urls = [
            f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}?{urlencode(params)}",
            f"https://query2.finance.yahoo.com/v8/finance/chart/{SYMBOL}?{urlencode(params)}",
        ]
        payload = None
        last_error = None
        for retry in range(4):
            for url in urls:
                try:
                    response = session.get(url, timeout=45)
                    attempts.append({
                        "start": cursor.isoformat(),
                        "end": chunk_end.isoformat(),
                        "status": response.status_code,
                        "host": url.split("/")[2],
                        "retry": retry,
                    })
                    if response.status_code == 200:
                        candidate = response.json()
                        error = candidate.get("chart", {}).get("error")
                        result = candidate.get("chart", {}).get("result")
                        if not error and result:
                            payload = result[0]
                            break
                        last_error = str(error)
                    else:
                        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                except Exception as exc:
                    last_error = repr(exc)
            if payload is not None:
                break
            time_module.sleep(2 ** retry)
        if payload is None:
            raise RuntimeError(f"Failed to download {cursor} through {chunk_end}: {last_error}")

        timestamps = payload.get("timestamp") or []
        quote_blocks = payload.get("indicators", {}).get("quote") or []
        if timestamps and quote_blocks:
            quote = quote_blocks[0]
            frame = pd.DataFrame({
                "timestamp": pd.to_datetime(timestamps, unit="s", utc=True),
                "Open": quote.get("open"),
                "High": quote.get("high"),
                "Low": quote.get("low"),
                "Close": quote.get("close"),
                "Volume": quote.get("volume"),
            })
            frames.append(frame)
        cursor = chunk_end
        time_module.sleep(0.5)

    if not frames:
        raise RuntimeError("Yahoo returned no one-minute bars")
    raw = pd.concat(frames, ignore_index=True)
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True).dt.tz_convert("America/New_York")
    raw = raw.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    metadata = {
        "source": SOURCE,
        "symbol": SYMBOL,
        "requested_start_utc": start.isoformat(),
        "requested_end_utc": end.isoformat(),
        "download_attempts": attempts,
        "downloaded_rows_before_validation": int(len(raw)),
    }
    return raw, metadata


def validate_sessions(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    required = ["timestamp", "Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in raw.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    data = raw.copy()
    for column in ["Open", "High", "Low", "Close", "Volume"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    quality = {
        "raw_rows": int(len(data)),
        "duplicate_timestamps": int(data["timestamp"].duplicated().sum()),
        "rows_with_null_required_values": int(data[required].isna().any(axis=1).sum()),
    }
    valid_ohlc = (
        data[["Open", "High", "Low", "Close"]].gt(0).all(axis=1)
        & data["Volume"].ge(0)
        & data["High"].ge(data[["Open", "Close", "Low"]].max(axis=1))
        & data["Low"].le(data[["Open", "Close", "High"]].min(axis=1))
    )
    quality["invalid_ohlc_or_volume_rows"] = int((~valid_ohlc).sum())
    data = data.loc[valid_ohlc].dropna(subset=required).copy()
    data = data.drop_duplicates("timestamp", keep="first").sort_values("timestamp")
    data["date"] = data["timestamp"].dt.date
    data["clock"] = data["timestamp"].dt.time
    data = data[(data["clock"] >= time(9, 30)) & (data["clock"] <= time(15, 59))].copy()

    expected_clocks = set(pd.date_range("2000-01-03 09:30", "2000-01-03 15:59", freq="1min").time)
    rows: list[dict] = []
    complete_dates = []
    for session_date, group in data.groupby("date", sort=True):
        clocks = set(group["clock"])
        complete = len(group) == 390 and clocks == expected_clocks
        rows.append({
            "date": str(session_date),
            "bars": int(len(group)),
            "first_bar": str(group["clock"].min()),
            "last_bar": str(group["clock"].max()),
            "complete": bool(complete),
            "missing_bar_count": int(len(expected_clocks - clocks)),
        })
        if complete:
            complete_dates.append(session_date)
    session_quality = pd.DataFrame(rows)
    clean = data[data["date"].isin(complete_dates)].reset_index(drop=True)
    quality.update({
        "regular_sessions_seen": int(len(session_quality)),
        "complete_sessions": int(len(complete_dates)),
        "excluded_incomplete_or_short_sessions": int((~session_quality["complete"]).sum()) if not session_quality.empty else 0,
        "clean_rows": int(len(clean)),
        "first_clean_timestamp": clean["timestamp"].min().isoformat() if not clean.empty else None,
        "last_clean_timestamp": clean["timestamp"].max().isoformat() if not clean.empty else None,
    })
    if len(complete_dates) < 5:
        raise RuntimeError(f"Only {len(complete_dates)} complete sessions remained; at least five are required")
    return clean, session_quality, quality


def add_indicators(data: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    frame = data.copy()
    if cfg.ema_reset_each_session:
        frame["ema"] = frame.groupby("date", sort=False)["Close"].transform(
            lambda s: s.ewm(span=cfg.ema_period, adjust=False, min_periods=cfg.ema_period).mean()
        )
    else:
        frame["ema"] = frame["Close"].ewm(span=cfg.ema_period, adjust=False, min_periods=cfg.ema_period).mean()
    frame["ema_lag3"] = frame.groupby("date", sort=False)["ema"].shift(cfg.ema_slope_lookback)
    frame["prior_volume_avg"] = frame.groupby("date", sort=False)["Volume"].transform(
        lambda s: s.shift(1).rolling(cfg.volume_lookback, min_periods=cfg.volume_lookback).mean()
    )
    frame["volume_ratio"] = frame["Volume"] / frame["prior_volume_avg"]
    frame["prior_3_high"] = frame.groupby("date", sort=False)["High"].transform(
        lambda s: s.shift(1).rolling(cfg.breakout_lookback, min_periods=cfg.breakout_lookback).max()
    )
    frame["prior_3_low"] = frame.groupby("date", sort=False)["Low"].transform(
        lambda s: s.shift(1).rolling(cfg.breakout_lookback, min_periods=cfg.breakout_lookback).min()
    )
    bar_range = frame["High"] - frame["Low"]
    frame["close_location"] = np.where(bar_range > 0, (frame["Close"] - frame["Low"]) / bar_range, 0.5)
    frame["ema_distance_pct"] = (frame["Close"] - frame["ema"]).abs() / frame["ema"]
    return frame


def signal_window(ts: pd.Timestamp) -> bool:
    t = ts.time()
    return (time(9, 45) <= t < time(11, 30)) or (time(13, 30) <= t < time(15, 30))


def entry_window(ts: pd.Timestamp) -> bool:
    t = ts.time()
    return (time(9, 45) <= t <= time(11, 30)) or (time(13, 30) <= t <= time(15, 30))


def make_signal(bar: pd.Series, i: int, cfg: Config) -> Optional[dict]:
    needed = ["ema", "ema_lag3", "volume_ratio", "prior_3_high", "prior_3_low", "close_location"]
    if any(pd.isna(bar[name]) for name in needed) or not signal_window(bar["timestamp"]):
        return None
    if bar["volume_ratio"] < cfg.volume_multiplier or bar["ema_distance_pct"] > cfg.max_ema_distance_pct:
        return None
    call = (
        bar["Close"] > bar["ema"]
        and bar["ema"] > bar["ema_lag3"]
        and bar["Close"] > bar["prior_3_high"]
        and bar["close_location"] >= 0.75
    )
    put = (
        bar["Close"] < bar["ema"]
        and bar["ema"] < bar["ema_lag3"]
        and bar["Close"] < bar["prior_3_low"]
        and bar["close_location"] <= 0.25
    )
    if call == put:
        return None
    side = "CALL" if call else "PUT"
    trigger = float(bar["High"] + cfg.entry_buffer) if call else float(bar["Low"] - cfg.entry_buffer)
    stop = float(bar["Low"] - cfg.stop_buffer) if call else float(bar["High"] + cfg.stop_buffer)
    risk = trigger - stop if call else stop - trigger
    risk_pct = risk / trigger
    if not cfg.min_risk_pct <= risk_pct <= cfg.max_risk_pct:
        return None
    return {
        "date": str(bar["date"]), "side": side, "signal_index": int(i),
        "signal_time": bar["timestamp"], "expires_index": int(i + cfg.pending_bars),
        "trigger": trigger, "stop": stop, "planned_risk": float(risk),
        "volume_ratio": float(bar["volume_ratio"]), "ema": float(bar["ema"]),
        "ema_slope_pct": float(bar["ema"] / bar["ema_lag3"] - 1),
        "ema_distance_pct": float(bar["ema_distance_pct"]),
        "close_location": float(bar["close_location"]),
        "session_segment": "MORNING" if bar["timestamp"].time() < time(11, 30) else "AFTERNOON",
    }


def entry_fill(order: dict, bar: pd.Series) -> Optional[tuple[float, float]]:
    if order["side"] == "CALL":
        if bar["Open"] >= order["trigger"]:
            return float(bar["Open"]), float(bar["Open"] - order["trigger"])
        if bar["High"] >= order["trigger"]:
            return float(order["trigger"]), 0.0
    else:
        if bar["Open"] <= order["trigger"]:
            return float(bar["Open"]), float(order["trigger"] - bar["Open"])
        if bar["Low"] <= order["trigger"]:
            return float(order["trigger"]), 0.0
    return None


def close_trade(position: dict, bar: pd.Series, price: float, reason: str) -> dict:
    pnl_points = price - position["entry"] if position["side"] == "CALL" else position["entry"] - price
    r_multiple = pnl_points / position["initial_risk"]
    return {
        "date": str(position["entry_time"].date()), "side": position["side"],
        "signal_time": position["signal_time"].isoformat(), "entry_time": position["entry_time"].isoformat(),
        "exit_time": bar["timestamp"].isoformat(), "session_segment": position["session_segment"],
        "entry": float(position["entry"]), "original_stop": float(position["original_stop"]),
        "target": float(position["target"]), "exit": float(price),
        "initial_risk_points": float(position["initial_risk"]),
        "initial_risk_pct": float(position["initial_risk"] / position["entry"]),
        "pnl_points": float(pnl_points), "r_multiple": float(r_multiple),
        "exit_reason": reason, "bars_after_entry": int(position["bars_after_entry"]),
        "max_favorable_excursion_r": float(position["mfe_points"] / position["initial_risk"]),
        "max_adverse_excursion_r": float(position["mae_points"] / position["initial_risk"]),
        "volume_ratio": float(position["volume_ratio"]), "ema_slope_pct": float(position["ema_slope_pct"]),
        "ema_distance_pct": float(position["ema_distance_pct"]),
        "entry_gap_slippage_points": float(position["gap_slippage"]),
    }


def run_backtest(data: pd.DataFrame, cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = add_indicators(data, cfg)
    trades: list[dict] = []
    signals: list[dict] = []
    for session_date, group in frame.groupby("date", sort=True):
        day = group.reset_index(drop=True)
        pending: Optional[dict] = None
        position: Optional[dict] = None
        completed = 0
        stop_losses = 0
        realized_r = 0.0
        cooldown_until: Optional[pd.Timestamp] = None
        for i, bar in day.iterrows():
            ts = bar["timestamp"]
            if position is not None and position.get("scheduled_exit_reason"):
                trade = close_trade(position, bar, float(bar["Open"]), position["scheduled_exit_reason"])
                trades.append(trade); completed += 1; realized_r += trade["r_multiple"]
                if trade["r_multiple"] <= -0.999:
                    stop_losses += 1; cooldown_until = ts + pd.Timedelta(minutes=cfg.stop_cooldown_minutes)
                else:
                    cooldown_until = ts + pd.Timedelta(minutes=cfg.cooldown_minutes)
                position = None; pending = None
            if position is not None and ts.time() >= time(15, 45):
                trade = close_trade(position, bar, float(bar["Open"]), "FORCED_1545")
                trades.append(trade); completed += 1; realized_r += trade["r_multiple"]
                cooldown_until = ts + pd.Timedelta(minutes=cfg.cooldown_minutes)
                position = None; pending = None

            if position is not None:
                side = position["side"]; stop = position["stop"]; target = position["target"]; trade = None
                if side == "CALL":
                    if bar["Open"] <= stop: trade = close_trade(position, bar, float(bar["Open"]), "STOP_GAP")
                    elif bar["Low"] <= stop: trade = close_trade(position, bar, float(stop), "STOP")
                    elif bar["Open"] >= target: trade = close_trade(position, bar, float(bar["Open"]), "TARGET_GAP")
                    elif bar["High"] >= target: trade = close_trade(position, bar, float(target), "TARGET")
                    favorable = max(0.0, float(bar["High"] - position["entry"])); adverse = max(0.0, float(position["entry"] - bar["Low"]))
                else:
                    if bar["Open"] >= stop: trade = close_trade(position, bar, float(bar["Open"]), "STOP_GAP")
                    elif bar["High"] >= stop: trade = close_trade(position, bar, float(stop), "STOP")
                    elif bar["Open"] <= target: trade = close_trade(position, bar, float(bar["Open"]), "TARGET_GAP")
                    elif bar["Low"] <= target: trade = close_trade(position, bar, float(target), "TARGET")
                    favorable = max(0.0, float(position["entry"] - bar["Low"])); adverse = max(0.0, float(bar["High"] - position["entry"]))
                position["mfe_points"] = max(position["mfe_points"], favorable)
                position["mae_points"] = max(position["mae_points"], adverse)
                if trade is not None:
                    trades.append(trade); completed += 1; realized_r += trade["r_multiple"]
                    if trade["r_multiple"] <= -0.999:
                        stop_losses += 1; cooldown_until = ts + pd.Timedelta(minutes=cfg.stop_cooldown_minutes)
                    else:
                        cooldown_until = ts + pd.Timedelta(minutes=cfg.cooldown_minutes)
                    position = None; pending = None
                else:
                    position["bars_after_entry"] += 1
                    if position["bars_after_entry"] >= cfg.progress_check_minutes and position["mfe_points"] < cfg.minimum_progress_r * position["initial_risk"] and not position.get("progress_checked"):
                        position["progress_checked"] = True
                        position["scheduled_exit_reason"] = "NO_025R_PROGRESS_AFTER_3M"
                    if position["bars_after_entry"] >= cfg.max_hold_minutes:
                        position["scheduled_exit_reason"] = "MAX_HOLD"

            if position is None and pending is not None:
                if i > pending["expires_index"] or not entry_window(ts):
                    signals.append({**pending, "status": "EXPIRED_UNFILLED"}); pending = None
                elif i > pending["signal_index"]:
                    fill = entry_fill(pending, bar)
                    if fill is not None:
                        entry, gap_slippage = fill
                        risk = entry - pending["stop"] if pending["side"] == "CALL" else pending["stop"] - entry
                        risk_pct = risk / entry
                        if not cfg.min_risk_pct <= risk_pct <= cfg.max_risk_pct:
                            signals.append({**pending, "status": "REJECTED_GAP_RISK", "actual_entry": entry, "actual_risk": risk}); pending = None
                        else:
                            target = entry + cfg.target_r * risk if pending["side"] == "CALL" else entry - cfg.target_r * risk
                            position = {
                                "side": pending["side"], "signal_time": pending["signal_time"], "entry_time": ts,
                                "entry": float(entry), "stop": float(pending["stop"]), "original_stop": float(pending["stop"]),
                                "target": float(target), "initial_risk": float(risk), "bars_after_entry": 0,
                                "mfe_points": 0.0, "mae_points": 0.0, "volume_ratio": pending["volume_ratio"],
                                "ema_slope_pct": pending["ema_slope_pct"], "ema_distance_pct": pending["ema_distance_pct"],
                                "session_segment": pending["session_segment"], "gap_slippage": float(gap_slippage),
                                "progress_checked": False, "scheduled_exit_reason": None,
                            }
                            signals.append({**pending, "status": "FILLED", "actual_entry": entry, "actual_risk": risk}); pending = None
                            trade = None
                            if position["side"] == "CALL":
                                if bar["Low"] <= position["stop"]: trade = close_trade(position, bar, position["stop"], "ENTRY_BAR_STOP")
                                elif bar["High"] >= position["target"]: trade = close_trade(position, bar, position["target"], "ENTRY_BAR_TARGET")
                                position["mfe_points"] = max(0.0, float(bar["High"] - position["entry"])); position["mae_points"] = max(0.0, float(position["entry"] - bar["Low"]))
                            else:
                                if bar["High"] >= position["stop"]: trade = close_trade(position, bar, position["stop"], "ENTRY_BAR_STOP")
                                elif bar["Low"] <= position["target"]: trade = close_trade(position, bar, position["target"], "ENTRY_BAR_TARGET")
                                position["mfe_points"] = max(0.0, float(position["entry"] - bar["Low"])); position["mae_points"] = max(0.0, float(bar["High"] - position["entry"]))
                            if trade is not None:
                                trades.append(trade); completed += 1; realized_r += trade["r_multiple"]
                                if trade["r_multiple"] <= -0.999:
                                    stop_losses += 1; cooldown_until = ts + pd.Timedelta(minutes=cfg.stop_cooldown_minutes)
                                else:
                                    cooldown_until = ts + pd.Timedelta(minutes=cfg.cooldown_minutes)
                                position = None

            locked = (
                completed >= cfg.max_trades_per_day or stop_losses >= cfg.max_stop_losses_per_day
                or realized_r <= -cfg.daily_loss_limit_r or (cooldown_until is not None and ts < cooldown_until)
            )
            if position is None and pending is None and not locked:
                new_signal = make_signal(bar, i, cfg)
                if new_signal is not None: pending = new_signal
        if pending is not None: signals.append({**pending, "status": "SESSION_END_UNFILLED"})
        if position is not None:
            last = day.iloc[-1]
            trades.append(close_trade(position, last, float(last["Close"]), "SESSION_END_DEFENSIVE"))

    trades_df = pd.DataFrame(trades); signals_df = pd.DataFrame(signals)
    if not trades_df.empty:
        trades_df["entry_time"] = pd.to_datetime(trades_df["entry_time"], utc=True).dt.tz_convert("America/New_York")
        trades_df["exit_time"] = pd.to_datetime(trades_df["exit_time"], utc=True).dt.tz_convert("America/New_York")
        trades_df = trades_df.sort_values("entry_time").reset_index(drop=True)
        trades_df["cumulative_r"] = trades_df["r_multiple"].cumsum()
        trades_df["peak_r"] = trades_df["cumulative_r"].cummax().clip(lower=0)
        trades_df["drawdown_r"] = trades_df["cumulative_r"] - trades_df["peak_r"]
    return trades_df, signals_df


def longest_streak(values: pd.Series) -> int:
    best = current = 0
    for value in values:
        if value: current += 1; best = max(best, current)
        else: current = 0
    return best


def metrics(trades: pd.DataFrame, sessions: int, seed: int = 20260822) -> dict:
    base = {"sessions": int(sessions), "trades": int(len(trades))}
    if trades.empty: return base
    r = trades["r_multiple"].astype(float)
    gross_profit = float(r[r > 0].sum()); gross_loss = float(-r[r < 0].sum())
    rng = np.random.default_rng(seed)
    boot = np.array([rng.choice(r.to_numpy(), size=len(r), replace=True).mean() for _ in range(20000)])
    split_index = max(1, int(len(r) * 0.60))
    base.update({
        "trades_per_session": float(len(r) / sessions), "trades_per_5_session_week": float(len(r) / sessions * 5),
        "win_rate": float((r > 0).mean()), "average_r": float(r.mean()), "median_r": float(r.median()),
        "total_r": float(r.sum()), "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else None,
        "average_winner_r": float(r[r > 0].mean()) if (r > 0).any() else None,
        "average_loser_r": float(r[r < 0].mean()) if (r < 0).any() else None,
        "max_drawdown_r": float(trades["drawdown_r"].min()), "max_consecutive_losses": int(longest_streak(r < 0)),
        "bootstrap_average_r_95pct_low": float(np.quantile(boot, 0.025)),
        "bootstrap_average_r_95pct_high": float(np.quantile(boot, 0.975)),
        "first_60pct_average_r": float(r.iloc[:split_index].mean()),
        "last_40pct_average_r": float(r.iloc[split_index:].mean()) if split_index < len(r) else None,
        "first_60pct_trades": int(split_index), "last_40pct_trades": int(len(r) - split_index),
    })
    return base


def subgroup_metrics(trades: pd.DataFrame, key: str) -> list[dict]:
    if trades.empty: return []
    result = []
    for value, group in trades.groupby(key, dropna=False):
        r = group["r_multiple"].astype(float); loss = float(-r[r < 0].sum())
        result.append({key: str(value), "trades": int(len(group)), "win_rate": float((r > 0).mean()),
                       "average_r": float(r.mean()), "total_r": float(r.sum()),
                       "profit_factor": float(r[r > 0].sum() / loss) if loss > 0 else None})
    return result


def make_charts(trades: pd.DataFrame, sensitivity: pd.DataFrame) -> None:
    if not trades.empty:
        plt.figure(figsize=(10, 5)); plt.plot(np.arange(1, len(trades) + 1), trades["cumulative_r"]); plt.axhline(0, linewidth=1)
        plt.xlabel("Trade number"); plt.ylabel("Cumulative R"); plt.title("SPY 1-minute scalp cumulative R"); plt.tight_layout()
        plt.savefig(OUT / "equity_curve.png", dpi=160); plt.close()
        plt.figure(figsize=(10, 5)); plt.plot(np.arange(1, len(trades) + 1), trades["drawdown_r"]); plt.axhline(0, linewidth=1)
        plt.xlabel("Trade number"); plt.ylabel("Drawdown R"); plt.title("SPY 1-minute scalp drawdown"); plt.tight_layout()
        plt.savefig(OUT / "drawdown.png", dpi=160); plt.close()
    if not sensitivity.empty:
        pivot = sensitivity.pivot_table(index="volume_multiplier", columns="target_r", values="average_r", aggfunc="mean")
        plt.figure(figsize=(8, 5))
        for column in pivot.columns: plt.plot(pivot.index, pivot[column], marker="o", label=f"Target {column}R")
        plt.axhline(0, linewidth=1); plt.xlabel("Relative volume threshold"); plt.ylabel("Mean R across hold variants")
        plt.title("Parameter sensitivity"); plt.legend(); plt.tight_layout(); plt.savefig(OUT / "sensitivity.png", dpi=160); plt.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw, download_metadata = fetch_yahoo_1m(); raw.to_csv(OUT / "raw_download.csv", index=False)
    clean, session_quality, data_quality = validate_sessions(raw)
    clean.to_csv(OUT / "clean_1m_bars.csv", index=False); session_quality.to_csv(OUT / "session_quality.csv", index=False)
    primary_cfg = Config(); trades, signals = run_backtest(clean, primary_cfg)
    trades.to_csv(OUT / "primary_trade_ledger.csv", index=False); signals.to_csv(OUT / "primary_signal_ledger.csv", index=False)
    sensitivity_rows: list[dict] = []
    for volume in [1.10, 1.25, 1.40]:
        for target in [0.75, 1.00, 1.25]:
            for hold in [5, 8, 12]:
                cfg = replace(primary_cfg, volume_multiplier=volume, target_r=target, max_hold_minutes=hold)
                variant_trades, _ = run_backtest(clean, cfg); summary = metrics(variant_trades, data_quality["complete_sessions"])
                sensitivity_rows.append({"volume_multiplier": volume, "target_r": target, "max_hold_minutes": hold,
                    **{k: summary.get(k) for k in ["trades", "trades_per_session", "win_rate", "average_r", "total_r",
                    "profit_factor", "max_drawdown_r", "bootstrap_average_r_95pct_low", "bootstrap_average_r_95pct_high",
                    "first_60pct_average_r", "last_40pct_average_r"]}})
    sensitivity = pd.DataFrame(sensitivity_rows); sensitivity.to_csv(OUT / "sensitivity.csv", index=False)
    reset_cfg = replace(primary_cfg, ema_reset_each_session=True)
    reset_trades, _ = run_backtest(clean, reset_cfg); reset_trades.to_csv(OUT / "session_reset_ema_trade_ledger.csv", index=False)
    primary = metrics(trades, data_quality["complete_sessions"]); reset_summary = metrics(reset_trades, data_quality["complete_sessions"])
    ci_low = primary.get("bootstrap_average_r_95pct_low")
    if primary.get("trades", 0) >= 100 and primary.get("trades_per_session", 0) >= 2 and primary.get("average_r", -99) > 0.05 and (primary.get("profit_factor") or 0) >= 1.15 and ci_low is not None and ci_low > 0:
        verdict = "PROMISING_UNDERLYING_SIGNAL_PROCEED_TO_LONGER_AND_OPTIONS_BACKTEST"
    elif primary.get("average_r", -99) > 0 and (primary.get("profit_factor") or 0) > 1:
        verdict = "POSITIVE_BUT_INSUFFICIENT_SAMPLE_DO_NOT_TRADE_LIVE"
    else:
        verdict = "FAILED_PRELIMINARY_SCREEN_DO_NOT_TRADE_LIVE"
    payload = {
        "verdict": verdict,
        "scope_warning": "This is a recent one-minute SPY underlying signal screening test, not a historical options-contract backtest.",
        "configuration": asdict(primary_cfg), "download_metadata": download_metadata, "data_quality": data_quality,
        "primary": primary, "primary_by_side": subgroup_metrics(trades, "side"),
        "primary_by_session_segment": subgroup_metrics(trades, "session_segment"),
        "primary_by_exit_reason": subgroup_metrics(trades, "exit_reason"),
        "session_reset_ema_sensitivity": reset_summary, "sensitivity": sensitivity_rows,
    }
    (OUT / "summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    report = ["# SPY 1-minute Momentum Scalp Backtest", "", f"**Verdict:** {verdict}", "",
        "This is an underlying SPY signal screening test. It does not model actual option contracts, option bid and ask spreads, delta, implied volatility, theta, or option fills.", "",
        "## Data quality", f"- Source: {SOURCE}", f"- Complete regular sessions: {data_quality['complete_sessions']}",
        f"- Date range: {data_quality['first_clean_timestamp']} through {data_quality['last_clean_timestamp']}",
        f"- Excluded incomplete or shortened sessions: {data_quality['excluded_incomplete_or_short_sessions']}", "",
        "## Frozen primary rules", f"- Trades: {primary.get('trades')}", f"- Trades per session: {primary.get('trades_per_session')}",
        f"- Win rate: {primary.get('win_rate')}", f"- Average R: {primary.get('average_r')}",
        f"- Profit factor: {primary.get('profit_factor')}", f"- Total R: {primary.get('total_r')}",
        f"- Maximum drawdown: {primary.get('max_drawdown_r')} R",
        f"- Bootstrap 95% interval for average R: [{primary.get('bootstrap_average_r_95pct_low')}, {primary.get('bootstrap_average_r_95pct_high')}]",
        f"- First 60% average R: {primary.get('first_60pct_average_r')}", f"- Last 40% average R: {primary.get('last_40pct_average_r')}", "",
        "## Interpretation", "A positive recent underlying result is only a screening result. A longer independent sample and an exact contract-level options backtest are required."]
    (OUT / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    make_charts(trades, sensitivity); print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
