from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .models import OrderIntent
from .settings import RuntimeSettings, get_settings


class MaintenanceDeferred(RuntimeError):
    """A maintenance read yielded without changing catalogue contents."""


class RuntimeStore:
    """SQLite catalogue.  Manifest and NATS delivery records commit together."""

    _LOCK_RETRY_SECONDS = 120.0

    def __init__(self, settings: RuntimeSettings | None = None, path: Path | None = None) -> None:
        self.settings = settings or get_settings()
        # Explicit, connection-local budgets. Defaults preserve Market's
        # existing SQLite behaviour; only the crypto profile opts in.
        cache_kib = int(self.settings.env.get("SQLITE_CACHE_KIB", "2000"))
        checkpoint_pages = int(self.settings.env.get("SQLITE_WAL_AUTOCHECKPOINT_PAGES", "1000"))
        if not 2000 <= cache_kib <= 131072:
            raise ValueError("SQLITE_CACHE_KIB must be between 2000 and 131072")
        if not 1000 <= checkpoint_pages <= 32768:
            raise ValueError("SQLITE_WAL_AUTOCHECKPOINT_PAGES must be between 1000 and 32768")
        self.path = path or self.settings.state_dir / "vibetrading.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=120000")
        # Setting journal_mode is a database-wide write operation.  Issuing
        # `PRAGMA journal_mode=WAL` on every collector reconnect was enough to
        # make unrelated collectors fail while storage or maintenance wrote a
        # manifest.  Read the current mode first and only change it for a new
        # or pre-WAL catalogue.
        mode = str(self._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if mode != "wal":
            self._run_with_lock_retry(lambda: self._conn.execute("PRAGMA journal_mode=WAL"))
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(f"PRAGMA cache_size=-{cache_kib}")
        self._conn.execute(f"PRAGMA wal_autocheckpoint={checkpoint_pages}")
        self.compact_delivery_ledger = self.settings.env.get("DELIVERY_LEDGER_FORMAT", "legacy").strip().lower() == "compact"
        self._init_schema()

    @staticmethod
    def _event_key(event_id: str) -> bytes:
        return hashlib.sha256(event_id.encode("utf-8")).digest()

    def close(self) -> None:
        self._conn.close()

    def connection_pragmas(self) -> dict[str, int | str]:
        """Read settings from this live writer connection, not a probe connection."""
        with self._lock:
            return {
                "journal_mode": str(self._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "synchronous": int(self._conn.execute("PRAGMA synchronous").fetchone()[0]),
                "cache_size": int(self._conn.execute("PRAGMA cache_size").fetchone()[0]),
                "wal_autocheckpoint_pages": int(self._conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]),
                "page_size": int(self._conn.execute("PRAGMA page_size").fetchone()[0]),
            }

    @contextmanager
    def maintenance_read_guard(
        self, check: Callable[[], None], *, max_seconds: float = 3.0,
    ) -> Iterator[None]:
        """Bound maintenance SELECTs only; never interrupt commit/ACK writes.

        SQLite callbacks run between VM instructions, so an outstanding kernel
        disk read must return before cancellation can take effect.
        """
        check()
        started = last_check = time.monotonic()
        deferred: MaintenanceDeferred | None = None

        def progress() -> int:
            nonlocal last_check, deferred
            now = time.monotonic()
            if now - started >= max_seconds:
                deferred = MaintenanceDeferred("maintenance_read_time_budget")
                return 1
            if now - last_check >= 0.25:
                last_check = now
                try:
                    check()
                except MaintenanceDeferred as exc:
                    deferred = exc
                    return 1
            return 0

        with self._lock:
            if self._conn.in_transaction:
                raise RuntimeError("maintenance read guard cannot wrap a write transaction")
            self._conn.set_progress_handler(progress, 1000)
            try:
                yield
            except sqlite3.OperationalError as exc:
                interrupted = (getattr(exc, "sqlite_errorcode", None) == 9 or str(exc) == "interrupted")
                if deferred is not None and interrupted:
                    raise deferred from exc
                raise
            finally:
                self._conn.set_progress_handler(None, 0)

    def checkpoint_wal_if_large(self, threshold_bytes: int = 256 * 1024 * 1024) -> dict[str, int | bool]:
        """Return space from a large WAL without interrupting live writers."""
        wal_path = Path(str(self.path) + "-wal")
        before = wal_path.stat().st_size if wal_path.exists() else 0
        if before < threshold_bytes:
            return {"attempted": False, "before_bytes": before, "after_bytes": before, "busy": False}
        try:
            connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
            try:
                connection.execute("PRAGMA busy_timeout=5000")
                busy, _, _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            finally:
                connection.close()
        except sqlite3.OperationalError:
            busy = 1
        after = wal_path.stat().st_size if wal_path.exists() else 0
        return {"attempted": True, "before_bytes": before, "after_bytes": after, "busy": bool(busy)}

    @staticmethod
    def _is_locked(exc: sqlite3.OperationalError) -> bool:
        message = str(exc).lower()
        return "locked" in message or "busy" in message

    def _run_with_lock_retry(self, operation: Any, *, rollback_on_lock: bool = True) -> Any:
        """Run a short SQLite write without failing a collector on contention."""
        deadline = time.monotonic() + self._LOCK_RETRY_SECONDS
        while True:
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                if not self._is_locked(exc) or time.monotonic() >= deadline:
                    raise
                # Autocommit statements do not leave a transaction behind,
                # but an interrupted schema operation can.  Clear it before
                # retrying the idempotent operation.
                if rollback_on_lock and self._conn.in_transaction:
                    self._conn.rollback()
                time.sleep(0.25)

    def _write(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._run_with_lock_retry(lambda: self._conn.execute(sql, parameters))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            # Storage and maintenance use independent processes. A completed
            # Parquet file must wait for the short manifest/lease transaction
            # instead of being abandoned as an unregistered orphan.
            deadline = time.monotonic() + 120.0
            while True:
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as exc:
                    if not self._is_locked(exc) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._run_with_lock_retry(lambda: self._conn.execute("COMMIT"), rollback_on_lock=False)

    def _init_schema(self) -> None:
        tables = {
            "parquet_manifest": """
        CREATE TABLE IF NOT EXISTS parquet_manifest (
          path TEXT PRIMARY KEY, file_id TEXT, generation INTEGER NOT NULL DEFAULT 0,
          event_type TEXT NOT NULL, trading_date TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'unknown', venue TEXT NOT NULL,
          rows INTEGER NOT NULL, min_ts_ns INTEGER, max_ts_ns INTEGER, checksum TEXT NOT NULL,
          logical_sha256 TEXT, file_sha256 TEXT, byte_size INTEGER NOT NULL DEFAULT 0,
          compacted_from TEXT, lifecycle_state TEXT NOT NULL DEFAULT 'active',
          upload_status TEXT NOT NULL DEFAULT 'local_only', local_present INTEGER NOT NULL DEFAULT 1,
          remote_key TEXT, remote_manifest_key TEXT, verified_ts_ns INTEGER, local_deleted_ts_ns INTEGER,
          superseded_by TEXT, superseded_ts_ns INTEGER, retry_count INTEGER NOT NULL DEFAULT 0,
          next_retry_ts_ns INTEGER, last_error TEXT, lease_owner TEXT, lease_until_ts_ns INTEGER,
          created_ts_ns INTEGER NOT NULL
        )""",
            "parquet_delivery": """
        CREATE TABLE IF NOT EXISTS parquet_delivery (
          stream_name TEXT NOT NULL, stream_generation TEXT NOT NULL, stream_sequence INTEGER NOT NULL,
          event_id TEXT, file_id TEXT NOT NULL, persisted_ts_ns INTEGER NOT NULL,
          PRIMARY KEY(stream_name,stream_generation,stream_sequence)
        )""",
            "delivery_stream": """
        CREATE TABLE IF NOT EXISTS delivery_stream (
          stream_id INTEGER PRIMARY KEY, stream_name TEXT NOT NULL, stream_generation TEXT NOT NULL,
          ack_floor INTEGER NOT NULL DEFAULT 0, updated_ts_ns INTEGER NOT NULL,
          UNIQUE(stream_name,stream_generation)
        )""",
            "parquet_delivery_compact": """
        CREATE TABLE IF NOT EXISTS parquet_delivery_compact (
          stream_id INTEGER NOT NULL, stream_sequence INTEGER NOT NULL,
          event_key BLOB NOT NULL, file_rowid INTEGER NOT NULL, persisted_ts_ns INTEGER NOT NULL,
          PRIMARY KEY(stream_id,stream_sequence)
        )""",
            "runtime_metadata": "CREATE TABLE IF NOT EXISTS runtime_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts_ns INTEGER NOT NULL)",
            "archive_backup": "CREATE TABLE IF NOT EXISTS archive_backup (backup_id TEXT PRIMARY KEY, remote_key TEXT NOT NULL, sha256 TEXT NOT NULL, created_ts_ns INTEGER NOT NULL)",
            "archive_cache": "CREATE TABLE IF NOT EXISTS archive_cache (file_id TEXT PRIMARY KEY, path TEXT NOT NULL, byte_size INTEGER NOT NULL, accessed_ts_ns INTEGER NOT NULL)",
            "universe_manifest": "CREATE TABLE IF NOT EXISTS universe_manifest (universe_id TEXT PRIMARY KEY,provider TEXT NOT NULL,payload TEXT NOT NULL,valid_from_ts_ns INTEGER NOT NULL,created_ts_ns INTEGER NOT NULL)",
            "order_intents": "CREATE TABLE IF NOT EXISTS order_intents (intent_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,signal_id TEXT NOT NULL,destination TEXT NOT NULL,client_order_id TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,created_ts_ns INTEGER NOT NULL,updated_ts_ns INTEGER NOT NULL)",
            "kill_switch": "CREATE TABLE IF NOT EXISTS kill_switch (scope TEXT PRIMARY KEY,active INTEGER NOT NULL,reason TEXT,updated_by TEXT NOT NULL,updated_ts_ns INTEGER NOT NULL)",
        }
        existing_tables = {
            str(row[0])
            for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for name, ddl in tables.items():
            if name not in existing_tables:
                self._run_with_lock_retry(lambda ddl=ddl: self._conn.execute(ddl))
        # Additive migration for the pre-archive manifest used on the server.
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(parquet_manifest)")}
        for name, ddl in {
            "file_id":"TEXT", "provider":"TEXT NOT NULL DEFAULT 'unknown'", "logical_sha256":"TEXT", "file_sha256":"TEXT",
            "lifecycle_state":"TEXT NOT NULL DEFAULT 'active'", "remote_manifest_key":"TEXT", "verified_ts_ns":"INTEGER",
            "local_deleted_ts_ns":"INTEGER", "retry_count":"INTEGER NOT NULL DEFAULT 0", "next_retry_ts_ns":"INTEGER",
            "last_error":"TEXT", "lease_owner":"TEXT", "lease_until_ts_ns":"INTEGER", "local_present":"INTEGER NOT NULL DEFAULT 1",
            "remote_key":"TEXT", "byte_size":"INTEGER NOT NULL DEFAULT 0", "upload_status":"TEXT NOT NULL DEFAULT 'local_only'",
            "superseded_by":"TEXT", "superseded_ts_ns":"INTEGER",
        }.items():
            if name not in cols:
                self._run_with_lock_retry(
                    lambda name=name, ddl=ddl: self._conn.execute(
                        f"ALTER TABLE parquet_manifest ADD COLUMN {name} {ddl}"
                    )
                )
                cols.add(name)
        # Most connections open an already-migrated catalogue. Avoid taking a
        # database write lock for the no-op migration on every status/read.
        if self._conn.execute("SELECT 1 FROM parquet_manifest WHERE file_id IS NULL OR file_id='' LIMIT 1").fetchone():
            self._write("UPDATE parquet_manifest SET file_id=lower(hex(randomblob(16))) WHERE file_id IS NULL OR file_id='' ")
        indexes = {
            str(row[0])
            for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        for name, ddl in {
            "idx_delivery_event_id": "CREATE INDEX IF NOT EXISTS idx_delivery_event_id ON parquet_delivery(event_id) WHERE event_id <> ''",
            "idx_delivery_compact_event": "CREATE INDEX IF NOT EXISTS idx_delivery_compact_event ON parquet_delivery_compact(event_key)",
            "idx_delivery_compact_prune": "CREATE INDEX IF NOT EXISTS idx_delivery_compact_prune ON parquet_delivery_compact(persisted_ts_ns,stream_id,stream_sequence)",
            "idx_manifest_file_id": "CREATE UNIQUE INDEX IF NOT EXISTS idx_manifest_file_id ON parquet_manifest(file_id)",
            "idx_manifest_partition": "CREATE INDEX IF NOT EXISTS idx_manifest_partition ON parquet_manifest(event_type,trading_date,provider,venue,created_ts_ns)",
            "idx_manifest_archive": "CREATE INDEX IF NOT EXISTS idx_manifest_archive ON parquet_manifest(lifecycle_state,local_present,upload_status,next_retry_ts_ns)",
            # The archive cleanup walks superseded files backwards from a
            # verified replacement. Without this index SQLite scans every
            # superseded row for every verified file in the recursive CTE.
            "idx_manifest_superseded_by": "CREATE INDEX IF NOT EXISTS idx_manifest_superseded_by ON parquet_manifest(superseded_by) WHERE superseded_by IS NOT NULL",
        }.items():
            if name not in indexes:
                self._run_with_lock_retry(lambda ddl=ddl: self._conn.execute(ddl))

    def collection_generation(self) -> str:
        row = self._conn.execute("SELECT value FROM runtime_metadata WHERE key='collection_generation'").fetchone()
        if row:
            return str(row[0])
        value = uuid.uuid4().hex
        self._write("INSERT OR IGNORE INTO runtime_metadata VALUES('collection_generation',?,?)", (value, time.time_ns()))
        return str(self._conn.execute("SELECT value FROM runtime_metadata WHERE key='collection_generation'").fetchone()[0])

    def rotate_collection_generation(self) -> str:
        value = uuid.uuid4().hex
        self._write("INSERT INTO runtime_metadata VALUES('collection_generation',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_ts_ns=excluded.updated_ts_ns", (value,time.time_ns()))
        return value

    def delivery_committed(self, stream: str, generation: str, sequence: int | None) -> bool:
        if sequence is None:
            return False
        if self.compact_delivery_ledger:
            return self._conn.execute(
                """SELECT 1 FROM parquet_delivery_compact AS delivery
                   JOIN delivery_stream AS stream ON stream.stream_id=delivery.stream_id
                   WHERE stream.stream_name=? AND stream.stream_generation=? AND delivery.stream_sequence=?""",
                (stream,generation,int(sequence)),
            ).fetchone() is not None
        return self._conn.execute("SELECT 1 FROM parquet_delivery WHERE stream_name=? AND stream_generation=? AND stream_sequence=?", (stream,generation,int(sequence))).fetchone() is not None

    def is_storage_committed(self, commit_id: str) -> bool:
        """Compatibility check for legacy STREAM:SEQUENCE identifiers."""
        stream, separator, raw_sequence = str(commit_id).rpartition(":")
        if not separator:
            return False
        try:
            sequence = int(raw_sequence)
        except ValueError:
            return False
        if self.compact_delivery_ledger:
            return self._conn.execute(
                """SELECT 1 FROM parquet_delivery_compact AS delivery
                   JOIN delivery_stream AS stream ON stream.stream_id=delivery.stream_id
                   WHERE stream.stream_name=? AND delivery.stream_sequence=? LIMIT 1""",
                (stream, sequence),
            ).fetchone() is not None
        return self._conn.execute("SELECT 1 FROM parquet_delivery WHERE stream_name=? AND stream_sequence=? LIMIT 1",(stream, sequence)).fetchone() is not None

    def event_committed(self, event_id: str) -> bool:
        if not event_id:
            return False
        if self.compact_delivery_ledger:
            return self._conn.execute(
                "SELECT 1 FROM parquet_delivery_compact WHERE event_key=? LIMIT 1",
                (self._event_key(event_id),),
            ).fetchone() is not None
        return self._conn.execute(
            "SELECT 1 FROM parquet_delivery WHERE event_id=? AND event_id<>'' LIMIT 1", (event_id,)
        ).fetchone() is not None

    def committed_deliveries(self, deliveries: Iterable[tuple[str, str, int]]) -> set[tuple[str, str, int]]:
        """Resolve a pull batch with indexed lookups under one SQLite lock."""
        values = [(str(s), str(g), int(q)) for s, g, q in deliveries]
        if not values:
            return set()
        result: set[tuple[str,str,int]] = set()
        with self._lock:
            for stream_name, generation in dict.fromkeys((s,g) for s,g,_ in values):
                sequences=[q for s,g,q in values if s==stream_name and g==generation]
                marks=",".join("?" for _ in sequences)
                if self.compact_delivery_ledger:
                    rows=self._conn.execute(
                        f"""SELECT delivery.stream_sequence FROM parquet_delivery_compact AS delivery
                            JOIN delivery_stream AS stream ON stream.stream_id=delivery.stream_id
                            WHERE stream.stream_name=? AND stream.stream_generation=?
                              AND delivery.stream_sequence IN ({marks})""",
                        (stream_name,generation,*sequences),
                    )
                else:
                    rows=self._conn.execute(
                        f"SELECT stream_sequence FROM parquet_delivery WHERE stream_name=? AND stream_generation=? AND stream_sequence IN ({marks})",
                        (stream_name,generation,*sequences),
                    )
                result.update((stream_name,generation,int(row[0])) for row in rows)
        return result

    def committed_event_ids(self, event_ids: Iterable[str]) -> set[str]:
        values = list(dict.fromkeys(str(value) for value in event_ids if value))
        if not values:
            return set()
        result: set[str] = set()
        # Stay comfortably below SQLite's host-parameter limit.
        with self._lock:
            for start in range(0, len(values), 500):
                chunk = values[start:start + 500]
                marks = ",".join("?" for _ in chunk)
                if self.compact_delivery_ledger:
                    keyed={self._event_key(value):value for value in chunk}
                    key_marks=",".join("?" for _ in keyed)
                    rows=self._conn.execute(
                        f"SELECT event_key FROM parquet_delivery_compact WHERE event_key IN ({key_marks})",
                        tuple(keyed),
                    )
                    result.update(keyed[bytes(row[0])] for row in rows)
                else:
                    rows = self._conn.execute(
                        f"SELECT event_id FROM parquet_delivery WHERE event_id<>'' AND event_id IN ({marks})",
                        chunk,
                    )
                    result.update(str(row[0]) for row in rows)
        return result

    def delivery_ack_floor(self, stream: str, generation: str) -> int:
        row=self._conn.execute(
            "SELECT ack_floor FROM delivery_stream WHERE stream_name=? AND stream_generation=?",
            (stream,generation),
        ).fetchone()
        return int(row[0]) if row else 0

    def record_delivery_ack_floor(self, stream: str, generation: str, sequence: int) -> None:
        if not self.compact_delivery_ledger or sequence <= 0:
            return
        now=time.time_ns()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO delivery_stream(stream_name,stream_generation,ack_floor,updated_ts_ns)
                   VALUES(?,?,?,?)
                   ON CONFLICT(stream_name,stream_generation) DO UPDATE SET
                     ack_floor=MAX(delivery_stream.ack_floor,excluded.ack_floor),
                     updated_ts_ns=excluded.updated_ts_ns""",
                (stream,generation,int(sequence),now),
            )

    def commit_file(self, *, file_id: str, event_type: str, trading_date: str, provider: str, venue: str, path: Path,
                    rows: int, min_ts_ns: int | None, max_ts_ns: int | None, logical_sha256: str, file_sha256: str,
                    byte_size: int, deliveries: Iterable[tuple[str,str,int,str]] = (), compacted_from: Iterable[str] = ()) -> None:
        self.commit_files(({
            "file_id":file_id,"event_type":event_type,"trading_date":trading_date,"provider":provider,
            "venue":venue,"path":path,"rows":rows,"min_ts_ns":min_ts_ns,"max_ts_ns":max_ts_ns,
            "logical_sha256":logical_sha256,"file_sha256":file_sha256,"byte_size":byte_size,
            "deliveries":list(deliveries),"compacted_from":list(compacted_from),
        },))

    def commit_files(self, files: Iterable[dict[str,Any]]) -> None:
        """Atomically register one Parquet flush and all of its deliveries."""
        file_rows=[]
        for source in files:
            row=dict(source)
            row["deliveries"]=list(row.get("deliveries",()))
            row["compacted_from"]=list(row.get("compacted_from",()))
            file_rows.append(row)
        if not file_rows:
            return
        now = time.time_ns()
        with self.transaction() as conn:
            stream_ids:dict[tuple[str,str],int]={}
            for row in file_rows:
                cursor=conn.execute("""INSERT INTO parquet_manifest(file_id,generation,event_type,trading_date,provider,venue,path,rows,min_ts_ns,max_ts_ns,checksum,logical_sha256,file_sha256,byte_size,compacted_from,lifecycle_state,upload_status,local_present,created_ts_ns)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? ,1,?)""", (row['file_id'],0,row['event_type'],row['trading_date'],row['provider'],row['venue'],str(row['path']),int(row['rows']),row['min_ts_ns'],row['max_ts_ns'],row['logical_sha256'],row['logical_sha256'],row['file_sha256'],int(row['byte_size']),json.dumps(row['compacted_from']),"active","local_only",now))
                delivery_rows=row['deliveries']
                if self.compact_delivery_ledger:
                    file_rowid=int(cursor.lastrowid)
                    for stream,generation,_,_ in delivery_rows:
                        key=(stream,generation)
                        if key in stream_ids:continue
                        conn.execute(
                            "INSERT OR IGNORE INTO delivery_stream(stream_name,stream_generation,ack_floor,updated_ts_ns) VALUES(?,?,0,?)",
                            (stream,generation,now),
                        )
                        stream_ids[key]=int(conn.execute(
                            "SELECT stream_id FROM delivery_stream WHERE stream_name=? AND stream_generation=?",key
                        ).fetchone()[0])
                    conn.executemany(
                        "INSERT INTO parquet_delivery_compact VALUES(?,?,?,?,?)",
                        ((stream_ids[(stream,generation)],int(sequence),self._event_key(event_id),file_rowid,now)
                         for stream,generation,sequence,event_id in delivery_rows),
                    )
                else:
                    conn.executemany(
                        "INSERT INTO parquet_delivery VALUES(?,?,?,?,?,?)",
                        ((stream,generation,int(sequence),event_id,row['file_id'],now)
                         for stream,generation,sequence,event_id in delivery_rows),
                    )

    # Compatibility entry point for existing direct writer/compactor callers.
    def record_manifest(self, **values: Any) -> str:
        fid = str(values.get("file_id") or uuid.uuid4().hex)
        self.commit_file(file_id=fid,event_type=values["event_type"],trading_date=values["trading_date"],provider=values.get("provider","unknown"),venue=values["venue"],path=Path(values["path"]),rows=values["rows"],min_ts_ns=values.get("min_ts_ns"),max_ts_ns=values.get("max_ts_ns"),logical_sha256=values.get("content_sha256") or values["checksum"],file_sha256=values.get("file_sha256") or values.get("content_sha256") or "",byte_size=values.get("byte_size",0),deliveries=values.get("deliveries",()),compacted_from=values.get("compacted_from",()))
        return fid

    def local_manifest(self, path: Path) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM parquet_manifest WHERE path=?", (str(path),)).fetchone()
        return dict(row) if row else None

    def list_manifest(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM parquet_manifest" + (" WHERE lifecycle_state='active'" if active_only else "") + " ORDER BY created_ts_ns DESC"
        return [dict(r) for r in self._conn.execute(sql)]

    def compaction_candidates(self, *, older_than_ns: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM parquet_manifest WHERE lifecycle_state='active' AND local_present=1 AND upload_status='local_only' AND created_ts_ns<=? ORDER BY event_type,trading_date,provider,venue,created_ts_ns LIMIT 2048", (older_than_ns,))]

    def commit_compaction(self, output: dict[str, Any], sources: Iterable[dict[str, Any]]) -> None:
        source_rows = list(sources)
        now = time.time_ns()
        compacted_from = [r["file_id"] for r in source_rows]
        with self.transaction() as conn:
            conn.execute("""INSERT INTO parquet_manifest(file_id,generation,event_type,trading_date,provider,venue,path,rows,min_ts_ns,max_ts_ns,checksum,logical_sha256,file_sha256,byte_size,compacted_from,lifecycle_state,upload_status,local_present,created_ts_ns)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""", (
                output["file_id"],0,output["event_type"],output["trading_date"],output.get("provider","unknown"),output["venue"],str(output["path"]),int(output["rows"]),output.get("min_ts_ns"),output.get("max_ts_ns"),output["logical_sha256"],output["logical_sha256"],output["file_sha256"],int(output["byte_size"]),json.dumps(compacted_from),"active","local_only",now,
            ))
            conn.executemany("UPDATE parquet_manifest SET lifecycle_state='superseded',superseded_by=?,superseded_ts_ns=? WHERE file_id=? AND lifecycle_state='active'", ((output["file_id"],now,r["file_id"]) for r in source_rows))

    def acquire_lease(self, file_id: str, owner: str, seconds: int = 300) -> bool:
        now = time.time_ns(); until = now + seconds*1_000_000_000
        with self.transaction() as conn:
            changed = conn.execute("UPDATE parquet_manifest SET lease_owner=?,lease_until_ts_ns=? WHERE file_id=? AND (lease_until_ts_ns IS NULL OR lease_until_ts_ns<? OR lease_owner=?)", (owner,until,file_id,now,owner)).rowcount
        return bool(changed)

    def release_lease(self, file_id: str, owner: str) -> None:
        self._write("UPDATE parquet_manifest SET lease_owner=NULL,lease_until_ts_ns=NULL WHERE file_id=? AND lease_owner=?", (file_id,owner))

    def claim_local_deletions(self, file_ids: Iterable[str], owner: str, seconds: int = 300) -> set[str]:
        """Claim one bounded cleanup batch with a single SQLite commit."""
        now = time.time_ns(); until = now + seconds * 1_000_000_000
        claimed: set[str] = set()
        with self.transaction() as conn:
            for file_id in file_ids:
                changed = conn.execute(
                    "UPDATE parquet_manifest SET lease_owner=?,lease_until_ts_ns=? "
                    "WHERE file_id=? AND local_present=1 AND "
                    "(lease_until_ts_ns IS NULL OR lease_until_ts_ns<? OR lease_owner=?)",
                    (owner, until, file_id, now, owner),
                ).rowcount
                if changed:
                    claimed.add(file_id)
        return claimed

    def finish_local_deletions(self, claimed: Iterable[str], deleted: Iterable[str], owner: str) -> None:
        """Record successful unlinks and release the whole batch atomically."""
        claimed_ids = set(claimed); deleted_ids = claimed_ids.intersection(deleted)
        now = time.time_ns()
        with self.transaction() as conn:
            conn.executemany(
                "UPDATE parquet_manifest SET local_present=0,local_deleted_ts_ns=?,"
                "lease_owner=NULL,lease_until_ts_ns=NULL WHERE file_id=? AND lease_owner=?",
                ((now, file_id, owner) for file_id in deleted_ids),
            )
            conn.executemany(
                "UPDATE parquet_manifest SET lease_owner=NULL,lease_until_ts_ns=NULL "
                "WHERE file_id=? AND lease_owner=?",
                ((file_id, owner) for file_id in claimed_ids - deleted_ids),
            )

    def archive_candidates(self, *, older_than_ns: int, force: bool = False, limit: int = 16) -> list[dict[str, Any]]:
        sql = "SELECT * FROM parquet_manifest WHERE lifecycle_state='active' AND local_present=1 AND upload_status IN ('local_only','upload_failed') AND (next_retry_ts_ns IS NULL OR next_retry_ts_ns<=?)"
        args: list[Any] = [time.time_ns()]
        if not force: sql += " AND created_ts_ns<=?"; args.append(older_than_ns)
        sql += " ORDER BY created_ts_ns LIMIT ?"; args.append(limit)
        return [dict(r) for r in self._conn.execute(sql,args)]

    def mark_remote_verified(self, file_id: str, remote_key: str, remote_manifest_key: str, size: int, sha: str) -> None:
        self._write("UPDATE parquet_manifest SET upload_status='remote_verified',remote_key=?,remote_manifest_key=?,byte_size=?,file_sha256=?,verified_ts_ns=?,last_error=NULL,next_retry_ts_ns=NULL WHERE file_id=?", (remote_key,remote_manifest_key,size,sha,time.time_ns(),file_id))

    def mark_upload_error(self, file_id: str, error: str) -> None:
        row=self._conn.execute("SELECT retry_count FROM parquet_manifest WHERE file_id=?",(file_id,)).fetchone(); n=int(row[0] if row else 0)+1
        self._write("UPDATE parquet_manifest SET upload_status='upload_failed',retry_count=?,next_retry_ts_ns=?,last_error=? WHERE file_id=?",(n,time.time_ns()+min(3600,30*2**min(n,7))*1_000_000_000,error[:2000],file_id))

    def deletion_candidates(self, *, older_than_ns: int, force: bool, limit: int = 32) -> list[dict[str, Any]]:
        # A compacted source is safe to remove once any replacement at the end
        # of its compaction chain has both its data object and manifest sidecar
        # verified remotely.  Sources themselves intentionally are not uploaded.
        cutoff=time.time_ns() if force else older_than_ns
        sql="""
        WITH RECURSIVE safe_files(file_id) AS (
          SELECT file_id
          FROM parquet_manifest
          WHERE upload_status='remote_verified'
            AND remote_manifest_key IS NOT NULL
            AND verified_ts_ns<=?
          UNION
          SELECT source.file_id
          FROM safe_files AS replacement
          JOIN parquet_manifest AS source INDEXED BY idx_manifest_superseded_by
            ON source.superseded_by=replacement.file_id
          WHERE source.lifecycle_state='superseded' AND source.superseded_by IS NOT NULL
        )
        SELECT manifest.*
        FROM parquet_manifest AS manifest
        JOIN safe_files ON safe_files.file_id=manifest.file_id
        WHERE manifest.local_present=1
          AND (manifest.lease_until_ts_ns IS NULL OR manifest.lease_until_ts_ns<?)
        ORDER BY COALESCE(manifest.verified_ts_ns,manifest.superseded_ts_ns,manifest.created_ts_ns)
        LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql,(cutoff,time.time_ns(),limit))]

    def mark_local_deleted(self, file_id: str) -> None:
        self._write("UPDATE parquet_manifest SET local_present=0,local_deleted_ts_ns=? WHERE file_id=?", (time.time_ns(),file_id))

    def prune_delivery_history(self, *, retain_seconds: int = 24 * 60 * 60, limit: int = 5_000) -> int:
        """Bound SQLite growth after the remote recovery copy is verified.

        The caller supplies the retention window. Compact-ledger rows are
        removed only below the persisted contiguous ACK floor and only after
        the matching Parquet object and sidecar are remotely verified.
        Keep each deletion small because this runs in a writer transaction
        shared with storage and collector metadata.
        """
        cutoff=time.time_ns()-max(1,int(retain_seconds))*1_000_000_000
        before=self._conn.total_changes
        with self.transaction() as conn:
            if self.compact_delivery_ledger:
                conn.execute(
                    """
                    DELETE FROM parquet_delivery_compact
                    WHERE rowid IN (
                      SELECT delivery.rowid
                      FROM parquet_delivery_compact AS delivery
                      JOIN delivery_stream AS stream ON stream.stream_id=delivery.stream_id
                      JOIN parquet_manifest AS manifest ON manifest.rowid=delivery.file_rowid
                      WHERE delivery.persisted_ts_ns<=?
                        AND delivery.stream_sequence<=stream.ack_floor
                        AND manifest.upload_status='remote_verified'
                        AND manifest.remote_manifest_key IS NOT NULL
                      ORDER BY delivery.persisted_ts_ns
                      LIMIT ?
                    )
                    """,
                    (cutoff,max(1,int(limit))),
                )
                return self._conn.total_changes-before
            conn.execute(
                """
                WITH RECURSIVE safe_files(file_id) AS (
                  SELECT file_id
                  FROM parquet_manifest
                  WHERE upload_status='remote_verified' AND remote_manifest_key IS NOT NULL
                  UNION
                  SELECT source.file_id
                  FROM safe_files AS replacement
                  JOIN parquet_manifest AS source INDEXED BY idx_manifest_superseded_by
                    ON source.superseded_by=replacement.file_id
                  WHERE source.lifecycle_state='superseded' AND source.superseded_by IS NOT NULL
                )
                DELETE FROM parquet_delivery
                WHERE rowid IN (
                  SELECT delivery.rowid
                  FROM parquet_delivery AS delivery
                  JOIN safe_files ON safe_files.file_id=delivery.file_id
                  WHERE delivery.persisted_ts_ns<=?
                  ORDER BY delivery.persisted_ts_ns
                  LIMIT ?
                )
                """,
                (cutoff,max(1,int(limit))),
            )
        return self._conn.total_changes-before

    def delivery_ledger_stats(self) -> dict[str,int|str]:
        table="parquet_delivery_compact" if self.compact_delivery_ledger else "parquet_delivery"
        rows=int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        ack_floor=0
        if self.compact_delivery_ledger:
            ack_floor=int(self._conn.execute("SELECT COALESCE(MAX(ack_floor),0) FROM delivery_stream").fetchone()[0])
        return {"format":"compact" if self.compact_delivery_ledger else "legacy","rows":rows,"max_ack_floor":ack_floor}

    def backup_page_count(self) -> int:
        return int(self._conn.execute("PRAGMA page_count").fetchone()[0])

    def backup_page_size(self) -> int:
        return int(self._conn.execute("PRAGMA page_size").fetchone()[0])

    def backup_to(
        self,
        destination: Path,
        *,
        pages: int = -1,
        sleep_seconds: float = 0.05,
        progress: Callable[[int, int, int], None] | None = None,
    ) -> None:
        destination.parent.mkdir(parents=True,exist_ok=True); other=sqlite3.connect(destination)
        try: self._conn.backup(other, pages=pages, progress=progress, sleep=max(0.0,sleep_seconds))
        finally: other.close()

    def last_archive_backup_ts_ns(self) -> int | None:
        row=self._conn.execute("SELECT MAX(created_ts_ns) FROM archive_backup").fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def record_archive_backup(self, backup_id: str, remote_key: str, sha256: str, created_ts_ns: int) -> None:
        self._write("INSERT OR REPLACE INTO archive_backup VALUES(?,?,?,?)",(backup_id,remote_key,sha256,int(created_ts_ns)))

    def touch_archive_cache(self, file_id: str, path: Path, byte_size: int) -> None:
        self._write("INSERT INTO archive_cache VALUES(?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET path=excluded.path,byte_size=excluded.byte_size,accessed_ts_ns=excluded.accessed_ts_ns",(file_id,str(path),int(byte_size),time.time_ns()))

    def archive_cache_rows(self) -> list[dict[str,Any]]:
        return [dict(row) for row in self._conn.execute("SELECT * FROM archive_cache ORDER BY accessed_ts_ns")]

    def remove_archive_cache(self, file_id: str) -> None:
        self._write("DELETE FROM archive_cache WHERE file_id=?",(file_id,))

    def status_summary(self) -> dict[str, Any]:
        active=self._conn.execute("SELECT COUNT(*) n,COALESCE(SUM(rows),0) rows,COALESCE(SUM(byte_size),0) bytes FROM parquet_manifest WHERE lifecycle_state='active'").fetchone()
        remote=self._conn.execute("SELECT COUNT(*) n,COALESCE(SUM(byte_size),0) bytes FROM parquet_manifest WHERE upload_status='remote_verified'").fetchone()
        due=self._conn.execute("SELECT COUNT(*) n,MIN(created_ts_ns) oldest FROM parquet_manifest WHERE lifecycle_state='active' AND local_present=1 AND upload_status!='remote_verified'").fetchone()
        orders={r['status']:int(r['n']) for r in self._conn.execute("SELECT status,COUNT(*) n FROM order_intents GROUP BY status")}
        grouped=[dict(row) for row in self._conn.execute("SELECT provider,event_type,COUNT(*) files,COALESCE(SUM(rows),0) rows,COALESCE(SUM(byte_size),0) bytes FROM parquet_manifest WHERE lifecycle_state='active' GROUP BY provider,event_type ORDER BY provider,event_type")]
        reclaimable=self._conn.execute("SELECT COUNT(*) files,COALESCE(SUM(byte_size),0) bytes FROM parquet_manifest WHERE local_present=1 AND upload_status='remote_verified' AND remote_manifest_key IS NOT NULL").fetchone()
        recent=self._conn.execute("SELECT COALESCE(SUM(byte_size),0) bytes FROM parquet_manifest WHERE lifecycle_state='active' AND created_ts_ns>=?",(time.time_ns()-3_600_000_000_000,)).fetchone()
        oldest=due['oldest']; upload_lag=max(0.0,(time.time_ns()-int(oldest))/1_000_000_000) if oldest else 0.0
        return {"manifest_files":int(active['n']),"manifest_rows":int(active['rows']),"manifest_bytes":int(active['bytes']),"by_provider_event":grouped,"remote_verified_files":int(remote['n']),"remote_verified_bytes":int(remote['bytes']),"upload_queue":int(due['n']),"oldest_upload_ts_ns":oldest,"upload_lag_seconds":round(upload_lag,1),"reclaimable_local_files":int(reclaimable['files']),"reclaimable_local_bytes":int(reclaimable['bytes']),"recent_local_write_bytes_1h":int(recent['bytes']),"orders":orders,"sqlite_path":str(self.path),"collection_generation":self.collection_generation()}

    def write_universe(self, universe_id: str, provider: str, payload: dict[str, Any], valid_from_ts_ns: int) -> None:
        # The id is a stable hash of the resolved universe.  Reconnects often
        # resolve identical input, so replacing the row only creates needless
        # cross-container writer contention.
        self._write("INSERT OR IGNORE INTO universe_manifest VALUES(?,?,?,?,?)",(universe_id,provider,json.dumps(payload,ensure_ascii=False),valid_from_ts_ns,time.time_ns()))
    def record_order_intent(self, intent: OrderIntent) -> None:
        now=time.time_ns(); self._write("INSERT OR IGNORE INTO order_intents VALUES(?,?,?,?,?,?,?,?,?)",(intent.intent_id,intent.job_id,intent.signal_id,intent.destination.value,intent.client_order_id,intent.status.value,json.dumps(intent.model_dump(mode='json')),intent.created_ts_ns,now))
    def update_order_intent(self, intent_id: str, status: str, payload: dict[str, Any] | None = None) -> None:
        row=self._conn.execute("SELECT payload FROM order_intents WHERE intent_id=?",(intent_id,)).fetchone(); body=json.loads(row[0]) if row else {}; body.update(payload or {}); body['status']=status; self._write("UPDATE order_intents SET status=?,payload=?,updated_ts_ns=? WHERE intent_id=?",(status,json.dumps(body),time.time_ns(),intent_id))
    def set_kill_switch(self, scope: str, active: bool, reason: str, updated_by: str) -> None:
        self._write("INSERT OR REPLACE INTO kill_switch VALUES(?,?,?,?,?)",(scope,int(active),reason,updated_by,time.time_ns()))
    def is_killed(self, scope: str) -> bool:
        row=self._conn.execute("SELECT active FROM kill_switch WHERE scope=?",(scope,)).fetchone(); return bool(row and row[0])
