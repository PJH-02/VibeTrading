from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"), allow_nan=False)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(encode(data), encoding="utf-8")
    os.replace(temp, path)


class RunLock:
    """OS-backed exclusive lock; automatically released after process death."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if os.fstat(self.handle.fileno()).st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError("Another liquidity strategy process holds this account lock") from None
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()


class Journal:
    def __init__(self, path: Path, run_id: str, raw_events: bool = True) -> None:
        self.path, self.run_id, self.raw_events = path, run_id, raw_events
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, run_id TEXT, received_mono REAL, received_wall_ms INTEGER, kind TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, run_id TEXT, received_mono REAL, body TEXT);
            CREATE TABLE IF NOT EXISTS orders(client_id TEXT PRIMARY KEY, run_id TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS executions(exec_id TEXT PRIMARY KEY, run_id TEXT, received_mono REAL, body TEXT);
            CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, started_wall_ms INTEGER, mode TEXT, config TEXT, summary TEXT);
        """)
        self.db.commit()

    def event(self, kind: str, now: float, body: dict, wall_ms: int | None = None) -> None:
        if self.raw_events or kind not in {"public"}:
            self.db.execute("INSERT INTO events(run_id,received_mono,received_wall_ms,kind,body) VALUES(?,?,?,?,?)",
                            (self.run_id, now, wall_ms or int(time.time() * 1000), kind, encode(body)))

    def decision(self, now: float, decision) -> None:
        self.db.execute("INSERT INTO decisions(run_id,received_mono,body) VALUES(?,?,?)", (self.run_id, now, encode(asdict(decision))))

    def order(self, order) -> None:
        self.db.execute("INSERT INTO orders VALUES(?,?,?) ON CONFLICT(client_id) DO UPDATE SET body=excluded.body",
                        (order.client_id, self.run_id, encode(asdict(order))))
        # Intents and order state are durable BEFORE any exchange mutation.
        self.db.commit()

    def execution(self, row: dict, now: float) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO executions VALUES(?,?,?,?)", (row["execId"], self.run_id, now, encode(row)))
        return cur.rowcount > 0

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()


class RiskLedger:
    """Daily start equity and account high-water mark survive session restarts."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY, start TEXT, halted INTEGER)")
        self.db.execute("CREATE TABLE IF NOT EXISTS controls(name TEXT PRIMARY KEY, value TEXT)")
        self.db.commit()

    def observe(self, day: str, equity) -> tuple:
        from decimal import Decimal
        row = self.db.execute("SELECT start,halted FROM days WHERE day=?", (day,)).fetchone()
        if not row:
            self.db.execute("INSERT INTO days VALUES(?,?,0)", (day, str(equity)))
        high = self.db.execute("SELECT value FROM controls WHERE name='high_watermark'").fetchone()
        watermark = max(equity, Decimal(high[0]) if high else equity)
        self.db.execute("INSERT INTO controls VALUES('high_watermark',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (str(watermark),))
        account_halt = self.db.execute("SELECT value FROM controls WHERE name='halt_reason'").fetchone()
        self.db.commit()
        return Decimal(row[0]) if row else equity, watermark, bool((row and row[1]) or account_halt)

    def halt(self, day: str, reason: str) -> None:
        self.db.execute("UPDATE days SET halted=1 WHERE day=?", (day,))
        self.db.execute("INSERT INTO controls VALUES('halt_reason',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (reason,))
        self.db.commit()

    def close(self) -> None:
        self.db.close()
