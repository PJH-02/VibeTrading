"""Small local operational ledger for collector outage and backpressure intervals."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class CollectionGapLedger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path=path

    def _connect(self) -> sqlite3.Connection:
        db=sqlite3.connect(self.path,timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS gaps (id INTEGER PRIMARY KEY, provider TEXT NOT NULL, symbols TEXT NOT NULL, started_ns INTEGER NOT NULL, ended_ns INTEGER, reason TEXT NOT NULL)")
        return db

    def start(self, provider: str, symbols: str, reason: str, started_ns: int | None = None) -> None:
        with self._connect() as db:
            row=db.execute("SELECT 1 FROM gaps WHERE provider=? AND ended_ns IS NULL",(provider,)).fetchone()
            if row is None:
                db.execute("INSERT INTO gaps(provider,symbols,started_ns,reason) VALUES(?,?,?,?)",(provider,symbols,int(started_ns or time.time_ns()),reason))

    def end(self, provider: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE gaps SET ended_ns=? WHERE provider=? AND ended_ns IS NULL",(time.time_ns(),provider))
