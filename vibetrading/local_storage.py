"""Separate-process, bounded local spool -> Parquet and durable fact storage.

No NATS, Core fanout, historical age/policy re-filter or spool GC is involved.
Market delivery coverage commits in the existing Parquet catalogue first.
Control/observation raw bytes and consumer progress commit in a WAL/FULL fact
ledger. A crash between the two databases replays already covered deliveries.
"""
from __future__ import annotations

import base64
import binascii
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import uuid

from .market_spool import SpoolCorruption, SpoolCursor, SpoolReader
from .models import AssetClass, EventType, event_from_dict
from .hot_path import _event_fingerprint
from .parquet_store import ParquetBatchWriter
from .sqlite_store import RuntimeStore


@dataclass(frozen=True)
class StorageBudgets:
    batch_records: int
    batch_bytes: int
    max_payload_bytes: int
    max_writer_buffer_bytes: int
    max_fact_rows: int
    max_fact_db_bytes: int
    poll_interval_ms: int

    def __post_init__(self):
        for name, value in self.__dict__.items():
            if type(value) is not int or value <= 0:
                raise ValueError(name + " requires an explicit positive integer")
        if (self.batch_records > 500 or self.max_payload_bytes > self.batch_bytes
                or self.max_fact_db_bytes < 128 * 1024):
            raise ValueError("invalid storage frame/database byte budgets")


class LocalStorageError(RuntimeError):
    pass


class LocalSourceError(LocalStorageError):
    """Deterministic source preflight failure, before any shared writer mutation."""


def _source_only(exc):
    for _ in range(8):
        if isinstance(exc,OSError): return False
        exc = exc.__cause__ or exc.__context__
        if exc is None: return True
    return False


def fact_storage_reserve(maximum: int, page_size: int = 512) -> int:
    """Conservative main+single-transaction WAL+SHM space, including frames.

    SQLite permits pages down to 512 bytes, which is the conservative default
    for deployment reservation before opening an existing ledger. cache_spill
    is disabled below so one transaction writes each modified page once.
    """
    pages=maximum//page_size
    wal=32+pages*(page_size+24)
    shm=32768*(2+(pages+3999)//4000)
    return maximum+wal+shm+4096


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("non-finite JSON number")


def _document(raw: bytes) -> dict:
    try:
        document = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(document, dict):
            raise ValueError("object required")
        return document
    except (ValueError, TypeError, RecursionError) as exc:
        raise LocalSourceError("invalid local storage JSON") from exc


def _text(value, label, maximum=256, empty=False):
    try:
        invalid = (not isinstance(value,str) or (not value and not empty) or len(value.encode('utf-8')) > maximum
                   or any(ord(char)<32 or ord(char)==127 for char in value))
    except UnicodeError as exc:
        raise LocalSourceError('invalid '+label) from exc
    if invalid:
        raise LocalSourceError("invalid " + label)
    return value


def _market(raw: bytes):
    document = _document(raw)
    identity = _text(document.get("event_id"), "event ID")
    try:
        event = event_from_dict(document)
    except (ValueError, TypeError, KeyError) as exc:
        raise LocalSourceError("invalid historical market event") from exc
    if event.event_id != identity:
        raise LocalSourceError("market identity changed during validation")
    return event


def _observation(raw: bytes, maximum: int):
    envelope = _document(raw)
    if (set(envelope) != {"version", "subject", "msg_id", "payload_base64"}
            or type(envelope["version"]) is not int or envelope["version"] != 1):
        raise LocalSourceError("invalid observation_v1 envelope")
    subject = _text(envelope["subject"], "observation subject")
    if re.fullmatch(r"(?:signal|order|system)\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", subject) is None:
        raise LocalSourceError("invalid observation subject namespace")
    message_id = envelope["msg_id"]
    if message_id is not None:
        _text(message_id, "observation message ID", empty=True)
    encoded = envelope["payload_base64"]
    if not isinstance(encoded, str) or len(encoded) > maximum:
        raise LocalSourceError("observation base64 byte budget exceeded")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise LocalSourceError("invalid observation base64") from exc
    if not payload or len(payload) > maximum:
        raise LocalSourceError("observation decoded payload byte budget exceeded")
    _document(payload)
    return subject, message_id, payload


class _FactLedger:
    """One owning connection/thread. No pruning or deletion API.

    max_fact_db_bytes limits SQLite database pages. WAL can temporarily hold
    one transaction's modified pages plus WAL frame/SHM overhead. Deployment
    reserves fact_storage_reserve(max_fact_db_bytes) separately from spools.
    """

    def __init__(self, path: Path, budgets: StorageBudgets):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = None
        self.path=path
        self._lock = path.with_name(path.name + ".writer.lock").open("a+b")
        self.budgets = budgets
        try:
            self._lock.seek(0, os.SEEK_END)
            if self._lock.tell() == 0:
                self._lock.write(b"0"); self._lock.flush()
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.connection = sqlite3.connect(path, isolation_level=None, timeout=5)
            self.connection.row_factory = sqlite3.Row
            conn = self.connection
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA cache_size=-2000")
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute('PRAGMA cache_spill=OFF')
            self._page_size=int(conn.execute('PRAGMA page_size').fetchone()[0])
            pages = budgets.max_fact_db_bytes // self._page_size
            self._wal_limit=32+pages*(self._page_size+24)
            self._checkpoint_gate()
            actual = int(conn.execute(f"PRAGMA max_page_count={pages}").fetchone()[0])
            if actual > pages:
                raise LocalStorageError("existing fact ledger exceeds declared page budget")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, 1):
                raise LocalStorageError("unsupported local fact ledger schema")
            if version == 1:
                try:
                    generation = conn.execute("SELECT value FROM facts_metadata WHERE key='generation'").fetchone()
                except sqlite3.DatabaseError as exc:
                    raise LocalStorageError("local facts generation metadata missing") from exc
                if generation is None or re.fullmatch(r"[0-9a-f]{32}", generation[0]) is None:
                    raise LocalStorageError("local facts generation is missing/invalid")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS local_fact(
                  fact_id INTEGER PRIMARY KEY,
                  producer TEXT NOT NULL, incarnation TEXT NOT NULL, sequence INTEGER NOT NULL,
                  kind TEXT NOT NULL, subject TEXT NOT NULL, msg_id TEXT,
                  raw_payload BLOB NOT NULL, payload_sha256 TEXT NOT NULL,
                  decoded_payload BLOB, UNIQUE(producer,incarnation,sequence));
                CREATE INDEX IF NOT EXISTS local_fact_subject_id ON local_fact(subject,fact_id);
                CREATE TABLE IF NOT EXISTS facts_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS local_progress(
                  producer TEXT PRIMARY KEY, incarnation TEXT NOT NULL, sequence INTEGER NOT NULL,
                  payload_sha256 TEXT NOT NULL, receipt_kind TEXT NOT NULL, event_id TEXT NOT NULL);
            """)
            if version == 0:
                conn.execute("INSERT OR IGNORE INTO facts_metadata VALUES('generation',?)", (uuid.uuid4().hex,))
                conn.execute("PRAGMA user_version=1")
            if (str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal"
                    or int(conn.execute("PRAGMA synchronous").fetchone()[0]) != 2):
                raise LocalStorageError("local fact writer must use WAL/FULL")
            self._row_count = int(conn.execute("SELECT COUNT(*) FROM local_fact").fetchone()[0])
            if self._row_count > budgets.max_fact_rows:
                raise LocalStorageError("existing local facts exceed row budget")
            self._checkpoint_gate()
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self.connection is not None:
                self.connection.close(); self.connection = None
        finally:
            self._lock.close()

    def progress(self, producer, incarnation):
        row = self.connection.execute("SELECT * FROM local_progress WHERE producer=?", (producer,)).fetchone()
        if row is not None and row["incarnation"] != incarnation:
            raise LocalStorageError("local consumer checkpoint has another incarnation")
        return row

    def _wal_size(self):
        path=self.path.with_name(self.path.name+'-wal')
        return path.stat().st_size if path.exists() else 0

    def _checkpoint_gate(self):
        if self._wal_size()>self._wal_limit:
            raise LocalStorageError('local fact WAL exceeds declared transaction capacity')
        checkpoint=self.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if int(checkpoint[0])!=0:
            raise LocalStorageError('local fact checkpoint blocked; next transaction is forbidden')
        if self._wal_size()!=0:
            raise LocalStorageError('local fact WAL did not truncate before next transaction')
        required=fact_storage_reserve(self.budgets.max_fact_db_bytes,self._page_size)-self.budgets.max_fact_db_bytes
        if shutil.disk_usage(self.path.parent).free<required:
            raise LocalStorageError('insufficient free space for bounded fact WAL/SHM transaction')

    def commit_batch(self, entries):
        if not entries:
            return
        if len(entries) > self.budgets.batch_records:
            raise LocalStorageError("fact/progress batch record budget exceeded")
        self._checkpoint_gate()
        conn = self.connection
        cursor = entries[0][0].cursor
        conn.execute("BEGIN IMMEDIATE")
        inserted = 0
        try:
            prior = self.progress(cursor.producer, cursor.incarnation)
            previous = int(prior["sequence"]) if prior is not None else 0
            for record, kind, event_id, fact in entries:
                if (record.cursor.producer != cursor.producer or record.cursor.incarnation != cursor.incarnation
                        or record.cursor.sequence != previous + 1):
                    raise LocalStorageError("consumer batch would skip/regress sequence or change incarnation")
                previous = record.cursor.sequence
                digest = hashlib.sha256(record.payload).hexdigest()
                if fact is not None:
                    existing = conn.execute("SELECT payload_sha256 FROM local_fact WHERE producer=? AND incarnation=? AND sequence=?",
                        (cursor.producer, cursor.incarnation, record.cursor.sequence)).fetchone()
                    if existing is not None and existing[0] != digest:
                        raise LocalStorageError("fact delivery identity has conflicting bytes")
                    if existing is None:
                        if self._row_count + inserted >= self.budgets.max_fact_rows:
                            raise LocalStorageError("local fact row budget exhausted; source retained")
                        subject, message_id, decoded = fact
                        conn.execute("INSERT INTO local_fact(producer,incarnation,sequence,kind,subject,msg_id,raw_payload,payload_sha256,decoded_payload) VALUES(?,?,?,?,?,?,?,?,?)",
                            (cursor.producer, cursor.incarnation, record.cursor.sequence, kind, subject, message_id,
                             record.payload, digest, decoded))
                        inserted += 1
            record, kind, event_id, _ = entries[-1]
            cursor = record.cursor
            digest = hashlib.sha256(record.payload).hexdigest()
            conn.execute("INSERT INTO local_progress VALUES(?,?,?,?,?,?) ON CONFLICT(producer) DO UPDATE SET "
                "incarnation=excluded.incarnation,sequence=excluded.sequence,payload_sha256=excluded.payload_sha256,"
                "receipt_kind=excluded.receipt_kind,event_id=excluded.event_id",
                (cursor.producer, cursor.incarnation, cursor.sequence, digest, kind, event_id))
            conn.execute("COMMIT")
            self._row_count += inserted
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        self._checkpoint_gate()

    def verify_fact(self, record):
        row = self.connection.execute("SELECT payload_sha256,raw_payload FROM local_fact WHERE producer=? AND incarnation=? AND sequence=?",
            (record.cursor.producer, record.cursor.incarnation, record.cursor.sequence)).fetchone()
        if row is None or row[0] != hashlib.sha256(record.payload).hexdigest() or bytes(row[1]) != record.payload:
            raise LocalStorageError("confirmed control/observation fact is missing or corrupt")


class LocalSpoolStorage:
    """Use only from one dedicated process/thread; step processes one source."""

    def __init__(self, plan, settings, budgets: StorageBudgets | None = None, *, writer=None, store=None):
        self.plan, self.settings = plan, settings
        self.budgets = budgets or StorageBudgets(**plan.storage)
        self._thread = threading.get_ident()
        self._error = None
        self._closed = False
        self._owns_store = store is None and writer is None
        self.store = store or (writer.store if writer is not None else None)
        self.writer = writer
        self._ledger = None
        self._sources = []
        self._source_failures = {}
        self._next = 0
        # This is an attestation cache, not a second permanent event index.
        # A cache miss for a committed ID/new cursor fails closed; no history
        # scan or identity-only alias is allowed to invent content coverage.
        self._fingerprints=OrderedDict()
        try:
            # Claim the role before opening the shared Parquet catalogue for
            # writing. A second local-storage process cannot race startup.
            self._ledger = _FactLedger(settings.state_dir / "local_storage_facts.sqlite3", self.budgets)
            self.store = self.store or RuntimeStore(settings)
            self.store.prepare_event_index()
            self.writer = writer or ParquetBatchWriter(settings, self.store)
            if self.writer.buffer_bytes:
                raise LocalStorageError("storage writer must start without caller-owned pending buffers")
            items = [(item, False) for item in plan.producers]
            if plan.observation is not None:
                items.append((plan.observation, True))
            if not 0 < len(items) <= 17 or len({item.producer for item, _ in items}) != len(items):
                raise LocalStorageError("bounded unique local storage producers required")
            for item, observation in items:
                _text(item.producer,"producer")
                _text(item.incarnation,"incarnation")
                progress = self._ledger.progress(item.producer, item.incarnation)
                sequence = int(progress["sequence"]) if progress is not None else 0
                before = SpoolCursor(item.producer, item.incarnation, max(0, sequence - 1))
                try:
                    reader = SpoolReader(item.spool_root, item.producer, item.incarnation,
                                         after=before, **item.limits.reader_kwargs)
                except SpoolCorruption as exc:
                    if sequence or not _source_only(exc): raise
                    self._quarantine(item,'open',exc)
                    self._sources.append((item,None,observation))
                    continue
                if sequence:
                    rows = reader.read_batch(max_records=1, max_bytes=self.budgets.batch_bytes)
                    if (len(rows) != 1 or rows[0].cursor.sequence != sequence
                            or hashlib.sha256(rows[0].payload).hexdigest() != progress["payload_sha256"]):
                        raise LocalStorageError("consumer checkpoint does not match source spool")
                    if progress["receipt_kind"] == "market":
                        if not self.store.delivery_committed("LOCAL:" + item.producer, item.incarnation, sequence):
                            raise LocalStorageError("consumer checkpoint lacks committed manifest delivery coverage")
                    else:
                        self._ledger.verify_fact(rows[0])
                self._sources.append((item, reader, observation))
        except BaseException:
            self.close()
            raise

    @property
    def source_failures(self):
        return {producer:dict(fault) for producer,fault in self._source_failures.items()}

    def _quarantine(self, item, stage, exc):
        if item.producer not in self._source_failures:
            self._source_failures[item.producer] = {'stage':stage,'error':type(exc).__name__,'reason':str(exc)[:240]}
            logging.getLogger(__name__).error('local storage quarantined producer=%s stage=%s error=%s',item.producer,stage,type(exc).__name__)

    def _check(self):
        if threading.get_ident() != self._thread:
            raise LocalStorageError("local storage connection belongs to another thread")
        if self._closed or self._error is not None:
            raise LocalStorageError("local storage is closed/fail-closed; spool retained") from self._error

    def _aliases(self, deliveries):
        if not deliveries:
            return
        if self.store.compact_delivery_ledger:
            self.store.commit_event_aliases(deliveries)
            return
        # The existing legacy table also records exact delivery aliases. Its
        # unsafe retention prune is disabled; never invent a new ACK proof.
        with self.store.transaction() as conn:
            for stream, incarnation, sequence, identity in deliveries:
                canonical = conn.execute("SELECT d.file_id FROM parquet_delivery d JOIN parquet_manifest m "
                    "ON m.file_id=d.file_id WHERE d.event_id=? LIMIT 1", (identity,)).fetchone()
                if canonical is None:
                    raise LocalStorageError("legacy alias has no canonical manifest")
                conn.execute("INSERT OR IGNORE INTO parquet_delivery VALUES(?,?,?,?,?,?)",
                    (stream, incarnation, sequence, identity, canonical[0], time.time_ns()))

    def _finish_market(self, pending, aliases):
        if not pending:
            return 0
        self.writer.flush_all()
        self._aliases(aliases)
        expected = {(stream, incarnation, record.cursor.sequence) for record, stream, incarnation, _ in pending}
        if self.store.committed_deliveries(expected) != expected:
            raise LocalStorageError("manifest delivery transaction did not cover all local records")
        count = len(pending)
        pending.clear(); aliases.clear()
        return count

    def step(self):
        self._check()
        item, reader, observation = self._sources[self._next]
        self._next = (self._next + 1) % len(self._sources)
        stats = {"records": 0, "market": 0, "facts": 0, "deduplicated": 0}
        if item.producer in self._source_failures: return stats
        preflight, stage = True,'read'
        pending, aliases, entries = [], [], []
        pending_ids = set()
        stream = "LOCAL:" + item.producer
        try:
            rows = reader.read_batch(max_records=self.budgets.batch_records, max_bytes=self.budgets.batch_bytes)
            stage = 'preflight'
            prepared = []
            for record in rows:
                if not record.payload or len(record.payload) > self.budgets.max_payload_bytes:
                    raise LocalSourceError("local record payload byte budget exceeded")
                value = _observation(record.payload, self.budgets.max_payload_bytes) if observation else _market(record.payload)
                prepared.append((record, value))
            market = [(record, value) for record, value in prepared
                      if not observation and value.event_type != EventType.DATA_GAP]
            known_deliveries = self.store.committed_deliveries(
                (stream, item.incarnation, record.cursor.sequence) for record, _ in market)
            known_ids = self.store.committed_event_ids(value.event_id for _, value in market)
            fingerprints={}
            for record,value in market:
                identity=value.event_id
                fingerprint=_event_fingerprint(value)
                previous=fingerprints.get(identity,self._fingerprints.get(identity))
                if previous is not None and previous!=fingerprint:
                    raise LocalSourceError('market event ID has conflicting canonical content')
                key=(stream,item.incarnation,record.cursor.sequence)
                if identity in known_ids and key not in known_deliveries and identity not in self._fingerprints:
                    raise LocalStorageError('committed event content is unverifiable after attestation cache miss')
                fingerprints[identity]=fingerprint
            preflight = False
            for record, value in prepared:
                if observation:
                    subject, message_id, decoded = value
                    entries.append((record, "observation", "", (subject, message_id, decoded)))
                    stats["facts"] += 1
                else:
                    event = value
                    if event.event_type == EventType.DATA_GAP:
                        symbol = re.sub(r"[^A-Z0-9_]+", "_", event.instrument.std_symbol.upper()).strip("_") or "UNKNOWN"
                        prefix = "crypto" if event.instrument.asset_class == AssetClass.CRYPTO_PERP else "md"
                        subject = ".".join((prefix, "data_gap", event.venue.value, event.instrument.asset_class.value, symbol))
                        entries.append((record, "control", event.event_id, (subject, event.event_id, None)))
                        stats["facts"] += 1
                    else:
                        identity = event.event_id
                        key = (stream, item.incarnation, record.cursor.sequence)
                        if identity in self._fingerprints: self._fingerprints.move_to_end(identity)
                        if key in known_deliveries:
                            stats["deduplicated"] += 1
                        elif identity in pending_ids:
                            if not self.writer.add_pending_delivery_alias(identity, stream_name=stream,
                                    stream_generation=item.incarnation, stream_sequence=record.cursor.sequence):
                                raise LocalStorageError("pending canonical delivery alias could not attach")
                            stats["deduplicated"] += 1
                        elif identity in known_ids:
                            aliases.append((*key, identity)); stats["deduplicated"] += 1
                        else:
                            if not self.writer.add(event, stream_name=stream, stream_generation=item.incarnation,
                                                   stream_sequence=record.cursor.sequence, event_id=identity):
                                raise LocalStorageError("unsupported event cannot advance local storage checkpoint")
                            self._fingerprints[identity]=fingerprints[identity]
                            self._fingerprints.move_to_end(identity)
                            while len(self._fingerprints)>self.budgets.batch_records:
                                self._fingerprints.popitem(last=False)
                            pending_ids.add(identity)
                        pending.append((record, stream, item.incarnation, identity))
                        entries.append((record, "market", identity, None))
                        if self.writer.buffer_bytes >= self.budgets.max_writer_buffer_bytes:
                            stats["market"] += self._finish_market(pending, aliases)
                            known_ids.update(pending_ids)
                            pending_ids.clear()
                stats["records"] += 1
            stats["market"] += self._finish_market(pending, aliases)
            # One FULL consumer/fact commit per source batch, after every
            # market delivery has durable manifest coverage. Mixed GAP/facts
            # cannot skip earlier market input or commit a partial checkpoint.
            self._ledger.commit_batch(entries)
            return stats
        except (LocalSourceError,SpoolCorruption) as exc:
            if not preflight or not _source_only(exc):
                self._error = exc
                raise
            self._quarantine(item,stage,exc)
            return stats
        except BaseException as exc:
            self._error = exc
            raise

    def progress(self, producer, incarnation):
        self._check()
        return self._ledger.progress(producer, incarnation)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._ledger is not None:
                self._ledger.close()
        finally:
            if self._owns_store and self.store is not None:
                self.store.close()


def run_local_storage(plan, settings):
    """Synchronous dedicated storage role; all blocking I/O stays here."""
    worker = LocalSpoolStorage(plan, settings)
    try:
        while True:
            stats = worker.step()
            if not stats["records"]:
                time.sleep(worker.budgets.poll_interval_ms / 1000)
    finally:
        worker.close()
