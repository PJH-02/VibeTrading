"""Small local operational ledger for collector outage and backpressure intervals."""
from __future__ import annotations

import sqlite3
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class CollectionGapLedger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path=path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            if str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            # Reserve the writer before reading/updating a gap. A deferred
            # read -> write upgrade can fail immediately with SQLITE_BUSY
            # despite busy_timeout when another exchange writes concurrently.
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS gaps (id INTEGER PRIMARY KEY, provider TEXT NOT NULL, symbols TEXT NOT NULL, started_ns INTEGER NOT NULL, ended_ns INTEGER, reason TEXT NOT NULL, details_json TEXT, unsent_events INTEGER NOT NULL DEFAULT 0, unsent_bytes INTEGER NOT NULL DEFAULT 0)")
            columns={str(row[1]) for row in db.execute("PRAGMA table_info(gaps)")}
            for name,ddl in (("details_json","TEXT"),("unsent_events","INTEGER NOT NULL DEFAULT 0"),("unsent_bytes","INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:db.execute(f"ALTER TABLE gaps ADD COLUMN {name} {ddl}")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            # sqlite3's connection context manager commits but does not close.
            db.close()

    def start(self, provider: str, symbols: str, reason: str, started_ns: int | None = None,
              details:dict|None=None,unsent_events:int=0,unsent_bytes:int=0) -> None:
        with self._connect() as db:
            row=db.execute("SELECT id FROM gaps WHERE provider=? AND ended_ns IS NULL",(provider,)).fetchone()
            if row is None:
                db.execute("INSERT INTO gaps(provider,symbols,started_ns,reason,details_json,unsent_events,unsent_bytes) VALUES(?,?,?,?,?,?,?)",
                           (provider,symbols,int(started_ns or time.time_ns()),reason,json.dumps(details or {},sort_keys=True),int(unsent_events),int(unsent_bytes)))
            elif details or unsent_events or unsent_bytes:
                db.execute("UPDATE gaps SET details_json=?,unsent_events=MAX(unsent_events,?),unsent_bytes=MAX(unsent_bytes,?) WHERE id=?",
                           (json.dumps(details or {},sort_keys=True),int(unsent_events),int(unsent_bytes),int(row[0])))

    def end(self, provider: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE gaps SET ended_ns=? WHERE provider=? AND ended_ns IS NULL",(time.time_ns(),provider))
