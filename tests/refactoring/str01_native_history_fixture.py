"""Synthetic yet native-schema CSV bundle for the STR-01 Paper acceptance tests.

The fixture writes the same complete manifest and close-only native files that
``CsvStrategyHistory`` consumes.  It never injects precomputed features: all
signals in the tests are produced from these daily/minute bars plus real raw
market events delivered through the existing runtime.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from storage.etf_csv_history import CSV_FILES, ETF_HISTORY_ROLES, atomic_json

DEFAULT_DAY = "2026-10-13"
ETF_BASE = {"069500": 100.0, "122630": 200.0, "229200": 150.0, "251340": 50.0}


def _daily_rows(days, symbol, base, phase):
    index = np.arange(len(days), dtype=float)
    close = base * np.exp(0.0003 * np.sin(index / 9 + phase))
    open_ = close * np.exp(0.0005 * np.cos(index / 5 + phase))
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    return pd.DataFrame({
        "date": [str(item.date()) for item in days],
        "symbol": symbol,
        "source": "STR01_FIXTURE",
        "open": open_, "high": high, "low": low, "close": close,
        "volume": 1_000_000.0, "trading_value": "",
    })


def _index_rows(days):
    n = len(days)
    us_ret = 0.0005 * np.sin(np.arange(n, dtype=float) / 11)
    us_ret[0] = 0.0
    us_ret[-1] = 0.0
    us_close = 4000.0 * np.exp(np.cumsum(us_ret))
    index_open = np.zeros(n)
    index_close = np.zeros(n)
    index_open[0] = 200.0
    index_close[0] = index_open[0] * np.exp(0.0002 * np.cos(0.0))
    for i in range(1, n):
        index_open[i] = index_close[i - 1] * np.exp(
            0.5 * us_ret[i - 1] + 0.0001 * np.sin(i / 3))
        index_close[i] = index_open[i] * np.exp(0.0002 * np.cos(i / 5))
    index = pd.DataFrame({
        "date": [str(item.date()) for item in days],
        "symbol": "KOSPI200", "source": "STR01_FIXTURE",
        "open": index_open, "high": np.maximum(index_open, index_close) * 1.001,
        "low": np.minimum(index_open, index_close) * 0.999,
        "close": index_close, "volume": 0.0, "trading_value": "",
    })
    us = pd.DataFrame({
        "date": [str(item.date()) for item in days],
        "symbol": "SP500", "source": "STR01_FIXTURE",
        "open": us_close, "high": us_close * 1.001,
        "low": us_close * 0.999, "close": us_close,
        "volume": 0.0, "trading_value": "",
    })
    return index, us, float(index_close[-1])


def _minute_rows(days, daily_by_symbol, symbol, phase):
    loop = []
    for day in days:
        daily = daily_by_symbol[symbol][daily_by_symbol[symbol].date == str(day.date())].iloc[0]
        stamp = pd.date_range(f"{day.date()} 09:01", f"{day.date()} 15:00", freq="1min")
        previous = float(daily["open"])
        for offset, item in enumerate(stamp):
            drift = 0.0008 * np.sin(2 * np.pi * offset / len(stamp) + phase)
            close = previous * (1 + drift)
            open_ = previous
            high = max(open_, close) * 1.0002
            low = min(open_, close) * 0.9998
            volume = float(1000 + offset)
            loop.append({
                "timestamp": str(item), "date": str(day.date()), "symbol": symbol,
                "source": "STR01_FIXTURE", "trading_value": close * volume,
                "timestamp_semantics": "bar_end", "open": open_, "high": high,
                "low": low, "close": close, "volume": volume,
            })
            previous = close
    return pd.DataFrame(loop)


def _write(root: Path, key: str, frame: pd.DataFrame, files: dict):
    path = root / CSV_FILES[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    files[key] = {
        "path": CSV_FILES[key], "sha256": digest, "rows": len(frame),
        "last_date": str(frame["date"].max()), "provider": "STR01_FIXTURE",
        "bytes": path.stat().st_size, "updated_ns": 1,
    }


def build_native_history(root, *, day=DEFAULT_DAY, daily_sessions=270, minute_sessions=15):
    """Create a complete native bundle and return the exact causal facts."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    day_stamp = pd.Timestamp(day)
    days = pd.bdate_range(end=day_stamp - pd.Timedelta(days=1), periods=daily_sessions)
    if not len(days):
        raise ValueError("fixture requires at least one prior business day")
    daily = {symbol: _daily_rows(days, symbol, base, index * .7)
             for index, (symbol, base) in enumerate(ETF_BASE.items())}
    index, us, last_index_close = _index_rows(days)
    files = {}
    for symbol, (daily_key, minute_key) in ETF_HISTORY_ROLES.items():
        _write(root, daily_key, daily[symbol], files)
    _write(root, "KOSPI200_daily", index, files)
    _write(root, "SP500_daily", us, files)

    minute_days = days[-max(1, min(len(days), max(minute_sessions, 15))):]
    if minute_sessions >= 15:
        minute_days = days[-15:]
    else:
        minute_days = days[-minute_sessions:]
    for index_number, (symbol, (_, minute_key)) in enumerate(ETF_HISTORY_ROLES.items()):
        frame = _minute_rows(minute_days, daily, symbol, index_number * .9)
        _write(root, minute_key, frame, files)

    manifest = {
        "version": 1, "created_ns": 1, "provider": "STR01_FIXTURE",
        "files": files,
    }
    atomic_json(root / "manifest.json", manifest)
    manifest_sha256 = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    return {
        "root": root, "day": str(day_stamp.date()),
        "manifest_sha256": manifest_sha256,
        "days": [str(item.date()) for item in days],
        "daily": daily, "index": index, "us": us,
        "last_index_close": last_index_close,
        "c3_index_open": last_index_close * np.exp(.05),
    }


def bar_rows(day, symbol, *, clock="09:01", open_=100.0, close=None,
             high=None, low=None, volume=1000.0, count=1, step_seconds=60):
    """Return real Bar1m-shaped rows from the first requested minute."""
    start = pd.Timestamp(f"{day} {clock}", tz="Asia/Seoul")
    close = open_ if close is None else close
    out = []
    for offset in range(count):
        end = start + pd.Timedelta(seconds=step_seconds * offset)
        if offset:
            out_end = end
        else:
            out_end = end
        open_row = out_end - pd.Timedelta(seconds=step_seconds)
        hi = max(open_, close) if high is None else high
        lo = min(open_, close) if low is None else low
        out.append({
            "open_ts_ns": int(open_row.value), "close_ts_ns": int(out_end.value),
            "open": open_, "high": hi, "low": lo, "close": close,
            "volume": volume,
        })
    return out
