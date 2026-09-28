from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import OrderIntent
from .settings import RuntimeSettings, get_settings


class RuntimeStore:
    """SQLite catalogue.  Manifest and NATS delivery records commit together."""

    def __init__(self, settings: RuntimeSettings | None = None, path: Path | None = None) -> None:
        self.settings = settings or get_settings()
        self.path = path or self.settings.state_dir / "vibetrading.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _init_schema(self) -> None:
        self._conn.executescript("""
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
        );
        CREATE TABLE IF NOT EXISTS parquet_delivery (
          stream_name TEXT NOT NULL, stream_generation TEXT NOT NULL, stream_sequence INTEGER NOT NULL,
          event_id TEXT, file_id TEXT NOT NULL, persisted_ts_ns INTEGER NOT NULL,
          PRIMARY KEY(stream_name,stream_generation,stream_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_delivery_event_id ON parquet_delivery(event_id) WHERE event_id <> '';
        CREATE TABLE IF NOT EXISTS runtime_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts_ns INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS archive_backup (backup_id TEXT PRIMARY KEY, remote_key TEXT NOT NULL, sha256 TEXT NOT NULL, created_ts_ns INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS archive_cache (file_id TEXT PRIMARY KEY, path TEXT NOT NULL, byte_size INTEGER NOT NULL, accessed_ts_ns INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS universe_manifest (universe_id TEXT PRIMARY KEY,provider TEXT NOT NULL,payload TEXT NOT NULL,valid_from_ts_ns INTEGER NOT NULL,created_ts_ns INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS order_intents (intent_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,signal_id TEXT NOT NULL,destination TEXT NOT NULL,client_order_id TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,created_ts_ns INTEGER NOT NULL,updated_ts_ns INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS kill_switch (scope TEXT PRIMARY KEY,active INTEGER NOT NULL,reason TEXT,updated_by TEXT NOT NULL,updated_ts_ns INTEGER NOT NULL);
        """)
        # Additive migration for the pre-archive manifest used on the server.
        for name, ddl in {
            "file_id":"TEXT", "provider":"TEXT NOT NULL DEFAULT 'unknown'", "logical_sha256":"TEXT", "file_sha256":"TEXT",
            "lifecycle_state":"TEXT NOT NULL DEFAULT 'active'", "remote_manifest_key":"TEXT", "verified_ts_ns":"INTEGER",
            "local_deleted_ts_ns":"INTEGER", "retry_count":"INTEGER NOT NULL DEFAULT 0", "next_retry_ts_ns":"INTEGER",
            "last_error":"TEXT", "lease_owner":"TEXT", "lease_until_ts_ns":"INTEGER", "local_present":"INTEGER NOT NULL DEFAULT 1",
            "remote_key":"TEXT", "byte_size":"INTEGER NOT NULL DEFAULT 0", "upload_status":"TEXT NOT NULL DEFAULT 'local_only'",
            "superseded_by":"TEXT", "superseded_ts_ns":"INTEGER",
        }.items():
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(parquet_manifest)")}
            if name not in cols:
                self._conn.execute(f"ALTER TABLE parquet_manifest ADD COLUMN {name} {ddl}")
        self._conn.execute("UPDATE parquet_manifest SET file_id=lower(hex(randomblob(16))) WHERE file_id IS NULL OR file_id='' ")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_manifest_file_id ON parquet_manifest(file_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_manifest_partition ON parquet_manifest(event_type,trading_date,provider,venue,created_ts_ns)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_manifest_archive ON parquet_manifest(lifecycle_state,local_present,upload_status,next_retry_ts_ns)")

    def collection_generation(self) -> str:
        row = self._conn.execute("SELECT value FROM runtime_metadata WHERE key='collection_generation'").fetchone()
        if row:
            return str(row[0])
        value = uuid.uuid4().hex
        self._conn.execute("INSERT OR IGNORE INTO runtime_metadata VALUES('collection_generation',?,?)", (value, time.time_ns()))
        return str(self._conn.execute("SELECT value FROM runtime_metadata WHERE key='collection_generation'").fetchone()[0])

    def rotate_collection_generation(self) -> str:
        value = uuid.uuid4().hex
        self._conn.execute("INSERT INTO runtime_metadata VALUES('collection_generation',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_ts_ns=excluded.updated_ts_ns", (value,time.time_ns()))
        return value

    def delivery_committed(self, stream: str, generation: str, sequence: int | None) -> bool:
        if sequence is None:
            return False
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
        return self._conn.execute(
            "SELECT 1 FROM parquet_delivery WHERE stream_name=? AND stream_sequence=? LIMIT 1",
            (stream, sequence),
        ).fetchone() is not None

    def event_committed(self, event_id: str) -> bool:
        if not event_id:
            return False
        return self._conn.execute(
            "SELECT 1 FROM parquet_delivery WHERE event_id=? AND event_id<>'' LIMIT 1", (event_id,)
        ).fetchone() is not None

    def committed_deliveries(self, deliveries: Iterable[tuple[str, str, int]]) -> set[tuple[str, str, int]]:
        """Resolve a pull batch with indexed lookups under one SQLite lock."""
        values = [(str(s), str(g), int(q)) for s, g, q in deliveries]
        if not values:
            return set()
        with self._lock:
            return {
                item for item in values
                if self._conn.execute(
                    "SELECT 1 FROM parquet_delivery WHERE stream_name=? AND stream_generation=? AND stream_sequence=?",
                    item,
                ).fetchone() is not None
            }

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
                rows = self._conn.execute(
                    f"SELECT event_id FROM parquet_delivery WHERE event_id<>'' AND event_id IN ({marks})",
                    chunk,
                )
                result.update(str(row[0]) for row in rows)
        return result

    def commit_file(self, *, file_id: str, event_type: str, trading_date: str, provider: str, venue: str, path: Path,
                    rows: int, min_ts_ns: int | None, max_ts_ns: int | None, logical_sha256: str, file_sha256: str,
                    byte_size: int, deliveries: Iterable[tuple[str,str,int,str]] = (), compacted_from: Iterable[str] = ()) -> None:
        now = time.time_ns()
        with self.transaction() as conn:
            conn.execute("""INSERT INTO parquet_manifest(file_id,generation,event_type,trading_date,provider,venue,path,rows,min_ts_ns,max_ts_ns,checksum,logical_sha256,file_sha256,byte_size,compacted_from,lifecycle_state,upload_status,local_present,created_ts_ns)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? ,1,?)""", (file_id,0,event_type,trading_date,provider,venue,str(path),int(rows),min_ts_ns,max_ts_ns,logical_sha256,logical_sha256,file_sha256,int(byte_size),json.dumps(list(compacted_from)),"active","local_only",now))
            conn.executemany(
                "INSERT INTO parquet_delivery VALUES(?,?,?,?,?,?)",
                ((stream,generation,int(sequence),event_id,file_id,now) for stream,generation,sequence,event_id in deliveries),
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
        return [dict(r) for r in self._conn.execute("SELECT * FROM parquet_manifest WHERE lifecycle_state='active' AND local_present=1 AND upload_status='local_only' AND created_ts_ns<=? ORDER BY event_type,trading_date,provider,venue,created_ts_ns", (older_than_ns,))]

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
        self._conn.execute("UPDATE parquet_manifest SET lease_owner=NULL,lease_until_ts_ns=NULL WHERE file_id=? AND lease_owner=?", (file_id,owner))

    def archive_candidates(self, *, older_than_ns: int, force: bool = False, limit: int = 16) -> list[dict[str, Any]]:
        sql = "SELECT * FROM parquet_manifest WHERE lifecycle_state='active' AND local_present=1 AND upload_status IN ('local_only','upload_failed') AND (next_retry_ts_ns IS NULL OR next_retry_ts_ns<=?)"
        args: list[Any] = [time.time_ns()]
        if not force: sql += " AND created_ts_ns<=?"; args.append(older_than_ns)
        sql += " ORDER BY created_ts_ns LIMIT ?"; args.append(limit)
        return [dict(r) for r in self._conn.execute(sql,args)]

    def mark_remote_verified(self, file_id: str, remote_key: str, remote_manifest_key: str, size: int, sha: str) -> None:
        self._conn.execute("UPDATE parquet_manifest SET upload_status='remote_verified',remote_key=?,remote_manifest_key=?,byte_size=?,file_sha256=?,verified_ts_ns=?,last_error=NULL,next_retry_ts_ns=NULL WHERE file_id=?", (remote_key,remote_manifest_key,size,sha,time.time_ns(),file_id))

    def mark_upload_error(self, file_id: str, error: str) -> None:
        row=self._conn.execute("SELECT retry_count FROM parquet_manifest WHERE file_id=?",(file_id,)).fetchone(); n=int(row[0] if row else 0)+1
        self._conn.execute("UPDATE parquet_manifest SET upload_status='upload_failed',retry_count=?,next_retry_ts_ns=?,last_error=? WHERE file_id=?",(n,time.time_ns()+min(3600,30*2**min(n,7))*1_000_000_000,error[:2000],file_id))

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
          FROM parquet_manifest AS source
          JOIN safe_files AS replacement ON source.superseded_by=replacement.file_id
          WHERE source.lifecycle_state='superseded'
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
        self._conn.execute("UPDATE parquet_manifest SET local_present=0,local_deleted_ts_ns=? WHERE file_id=?", (time.time_ns(),file_id))

    def prune_delivery_history(self, *, retain_seconds: int = 7 * 86400, limit: int = 100_000) -> int:
        """Bound SQLite growth after the remote recovery copy is verified.

        Seven days is deliberately much longer than the 20-minute JetStream
        ACK/redelivery window and the collector's 24-hour maximum backfill.
        SQLite reuses the freed pages, keeping steady-state growth bounded.
        """
        cutoff=time.time_ns()-max(1,int(retain_seconds))*1_000_000_000
        before=self._conn.total_changes
        with self.transaction() as conn:
            conn.execute(
                """
                WITH RECURSIVE safe_files(file_id) AS (
                  SELECT file_id
                  FROM parquet_manifest
                  WHERE upload_status='remote_verified' AND remote_manifest_key IS NOT NULL
                  UNION
                  SELECT source.file_id
                  FROM parquet_manifest AS source
                  JOIN safe_files AS replacement ON source.superseded_by=replacement.file_id
                  WHERE source.lifecycle_state='superseded'
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

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True,exist_ok=True); other=sqlite3.connect(destination)
        try: self._conn.backup(other)
        finally: other.close()

    def status_summary(self) -> dict[str, Any]:
        active=self._conn.execute("SELECT COUNT(*) n,COALESCE(SUM(rows),0) rows,COALESCE(SUM(byte_size),0) bytes FROM parquet_manifest WHERE lifecycle_state='active'").fetchone()
        remote=self._conn.execute("SELECT COUNT(*) n,COALESCE(SUM(byte_size),0) bytes FROM parquet_manifest WHERE upload_status='remote_verified'").fetchone()
        due=self._conn.execute("SELECT COUNT(*) n,MIN(created_ts_ns) oldest FROM parquet_manifest WHERE lifecycle_state='active' AND local_present=1 AND upload_status!='remote_verified'").fetchone()
        orders={r['status']:int(r['n']) for r in self._conn.execute("SELECT status,COUNT(*) n FROM order_intents GROUP BY status")}
        return {"manifest_files":int(active['n']),"manifest_rows":int(active['rows']),"manifest_bytes":int(active['bytes']),"remote_verified_files":int(remote['n']),"remote_verified_bytes":int(remote['bytes']),"upload_queue":int(due['n']),"oldest_upload_ts_ns":due['oldest'],"orders":orders,"sqlite_path":str(self.path),"collection_generation":self.collection_generation()}

    def write_universe(self, universe_id: str, provider: str, payload: dict[str, Any], valid_from_ts_ns: int) -> None:
        self._conn.execute("INSERT OR REPLACE INTO universe_manifest VALUES(?,?,?,?,?)",(universe_id,provider,json.dumps(payload,ensure_ascii=False),valid_from_ts_ns,time.time_ns()))
    def record_order_intent(self, intent: OrderIntent) -> None:
        now=time.time_ns(); self._conn.execute("INSERT OR IGNORE INTO order_intents VALUES(?,?,?,?,?,?,?,?,?)",(intent.intent_id,intent.job_id,intent.signal_id,intent.destination.value,intent.client_order_id,intent.status.value,json.dumps(intent.model_dump(mode='json')),intent.created_ts_ns,now))
    def update_order_intent(self, intent_id: str, status: str, payload: dict[str, Any] | None = None) -> None:
        row=self._conn.execute("SELECT payload FROM order_intents WHERE intent_id=?",(intent_id,)).fetchone(); body=json.loads(row[0]) if row else {}; body.update(payload or {}); body['status']=status; self._conn.execute("UPDATE order_intents SET status=?,payload=?,updated_ts_ns=? WHERE intent_id=?",(status,json.dumps(body),time.time_ns(),intent_id))
    def set_kill_switch(self, scope: str, active: bool, reason: str, updated_by: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO kill_switch VALUES(?,?,?,?,?)",(scope,int(active),reason,updated_by,time.time_ns()))
    def is_killed(self, scope: str) -> bool:
        row=self._conn.execute("SELECT active FROM kill_switch WHERE scope=?",(scope,)).fetchone(); return bool(row and row[0])
