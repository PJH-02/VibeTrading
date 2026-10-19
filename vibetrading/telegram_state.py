"""Telegram-owned inbox, delivery receipts and bounded outbound messages.

No connection is shared between threads and no trading journal is opened RW.
The controller holds a process lease; each short transaction closes before HTTP.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time


class OutboxFull(RuntimeError):
    pass


class TelegramState:
    def __init__(self, path: Path, *, max_pending: int = 1000, max_outbox_bytes: int = 4*1024*1024) -> None:
        self.path = Path(path)
        self.max_pending = max(1, int(max_pending))
        self.max_outbox_bytes = max(1, int(max_outbox_bytes))
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            db = sqlite3.connect(self.path, timeout=1, uri=True)
            try:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS inbox(update_id INTEGER PRIMARY KEY,payload TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL,received_ns INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS cursor(id INTEGER PRIMARY KEY CHECK(id=1),offset INTEGER NOT NULL);
                    INSERT OR IGNORE INTO cursor VALUES(1,0);
                    CREATE TABLE IF NOT EXISTS outbox(identity TEXT PRIMARY KEY,chat_id TEXT NOT NULL,text TEXT NOT NULL,bytes INTEGER NOT NULL,notification_id TEXT,attempts INTEGER NOT NULL DEFAULT 0,next_ns INTEGER NOT NULL DEFAULT 0,created_ns INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS delivered(identity TEXT PRIMARY KEY,notification_id TEXT,delivered_ns INTEGER NOT NULL);
                    CREATE UNIQUE INDEX IF NOT EXISTS delivered_notification ON delivered(notification_id) WHERE notification_id IS NOT NULL;
                """)
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def offset(self) -> int:
        with self.transaction() as db:
            return int(db.execute("SELECT offset FROM cursor WHERE id=1").fetchone()[0])

    def receive(self, update: dict) -> None:
        identity = update.get("update_id")
        if type(identity) is not int or identity < 0:
            raise ValueError("invalid Telegram update_id")
        payload = json.dumps(update, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode()) > 65536:
            raise ValueError("Telegram update exceeds inbox frame limit")
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.transaction() as db:
            previous = db.execute("SELECT digest FROM inbox WHERE update_id=?", (identity,)).fetchone()
            if previous and previous[0] != digest:
                raise ValueError("Telegram update identity has conflicting content")
            if not previous:
                if identity < db.execute("SELECT offset FROM cursor WHERE id=1").fetchone()[0]:
                    return
                if db.execute("SELECT COUNT(*) FROM inbox WHERE state='RECEIVED'").fetchone()[0] >= self.max_pending:
                    raise OutboxFull("Telegram inbox is full")
                db.execute("INSERT INTO inbox VALUES(?,?,?,'RECEIVED',?)", (identity, payload, digest, time.time_ns()))
            # Telegram offsets are not contiguous integers; advance only after
            # this and every earlier update in the returned batch is durable.
            db.execute("UPDATE cursor SET offset=MAX(offset,?) WHERE id=1", (identity+1,))

    def pending(self) -> list[dict]:
        with self.transaction() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM inbox WHERE state='RECEIVED' ORDER BY update_id LIMIT 100")]

    def complete(self, identity: int) -> None:
        with self.transaction() as db:
            db.execute("UPDATE inbox SET state='DONE' WHERE update_id=?", (identity,))
            db.execute("DELETE FROM delivered WHERE identity=? AND notification_id IS NULL", ("reply:"+str(identity),))
            # Retain a bounded audit tail; acknowledged updates below offset
            # cannot be returned by a correctly behaving Telegram poller.
            db.execute("DELETE FROM inbox WHERE state='DONE' AND update_id < (SELECT COALESCE(MIN(update_id),0) FROM (SELECT update_id FROM inbox WHERE state='DONE' ORDER BY update_id DESC LIMIT 10000))")

    def enqueue(self, identity: str, chat_id: str, text: str, notification_id: str | None = None) -> bool:
        size = len(text.encode("utf-8"))
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM delivered WHERE identity=?", (identity,)).fetchone() or db.execute("SELECT 1 FROM outbox WHERE identity=?", (identity,)).fetchone():
                return False
            count, total = db.execute("SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM outbox").fetchone()
            if count >= self.max_pending or total + size > self.max_outbox_bytes:
                raise OutboxFull("Telegram outbox capacity exceeded")
            db.execute("INSERT INTO outbox(identity,chat_id,text,bytes,notification_id,created_ns) VALUES(?,?,?,?,?,?)", (identity,str(chat_id),text,size,notification_id,time.time_ns()))
            return True

    def next_message(self) -> dict | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM outbox WHERE next_ns<=? ORDER BY created_ns,identity LIMIT 1", (time.time_ns(),)).fetchone()
            return dict(row) if row else None

    def delivered(self, identity: str, notification_id: str | None = None) -> None:
        with self.transaction() as db:
            retain = notification_id is not None or not identity.startswith("notice:")
            if notification_id is None and identity.startswith("reply:"):
                row = db.execute("SELECT state FROM inbox WHERE update_id=?", (identity[6:],)).fetchone()
                retain = row is None or row[0] != "DONE"
            if retain:
                db.execute("INSERT OR IGNORE INTO delivered VALUES(?,?,?)", (identity,notification_id,time.time_ns()))
            db.execute("DELETE FROM outbox WHERE identity=?", (identity,))

    def retry(self, identity: str, delay_seconds: float) -> None:
        with self.transaction() as db:
            db.execute("UPDATE outbox SET attempts=attempts+1,next_ns=? WHERE identity=?", (time.time_ns()+int(max(1,delay_seconds)*1e9),identity))

    def pending_notifications(self, journal_path: Path) -> list[dict]:
        if not journal_path.exists():
            return []
        with self.transaction() as db:
            db.execute("ATTACH DATABASE ? AS etf", (journal_path.resolve().as_uri()+"?mode=ro",))
            if not db.execute("SELECT 1 FROM etf.sqlite_master WHERE type='table' AND name='notifications'").fetchone():
                return []
            rows = db.execute("""SELECT n.id,n.kind,n.payload FROM etf.notifications n
                WHERE n.sent_ns IS NULL AND NOT EXISTS(SELECT 1 FROM delivered d WHERE d.notification_id=n.id)
                AND NOT EXISTS(SELECT 1 FROM outbox o WHERE o.notification_id=n.id)
                ORDER BY n.created_ns,n.id LIMIT 50""")
            return [{"id":r[0],"kind":r[1],"payload":json.loads(r[2])} for r in rows]
