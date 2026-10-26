"""Append-only exact event lookup runs, committed in the catalogue transaction.

The authoritative lookup is a binary search of checksummed, sorted 40-byte
records (the existing 32-byte event hash and its canonical manifest rowid).
Bloom filters only reject impossible matches. They never authorize an ACK.
The global filter has a fixed memory budget and is reconstructed from exact
records; only newly committed runs are read after the initial reconstruction.
"""
from __future__ import annotations

import hashlib
import sqlite3
import struct
import time
import threading
from concurrent.futures import Future
from pathlib import Path
from collections.abc import Iterable


RECORD = struct.Struct('>32sQ')
FORMAT = 1
MAX_RUN_ENTRIES = 65536
FILTER_HASHES = 12


def _positions(key: bytes, byte_count: int, hashes: int):
    first, step = struct.unpack_from('>QQ', key)
    step |= 1
    mask = byte_count * 8 - 1
    for i in range(hashes):
        yield (first + i * step) & mask


def _add(bits: bytearray, key: bytes, hashes: int) -> None:
    for position in _positions(key, len(bits), hashes):
        bits[position >> 3] |= 1 << (position & 7)


def _maybe(bits: bytes | bytearray, key: bytes, hashes: int) -> bool:
    return all(bits[p >> 3] & (1 << (p & 7)) for p in _positions(key, len(bits), hashes))


def _add_records(bits: bytearray, payload: bytes, hashes: int) -> None:
    """The same bit positions as _add, without millions of Python iterations."""
    try:
        import numpy as np
    except ImportError:
        for key, _ in RECORD.iter_unpack(payload):
            _add(bits, key, hashes)
        return
    count = len(payload) // RECORD.size
    first = np.ndarray((count,), dtype='>u8', buffer=payload, strides=(RECORD.size,)).astype('uint64')
    step = np.ndarray((count,), dtype='>u8', buffer=payload, offset=8, strides=(RECORD.size,)).astype('uint64') | np.uint64(1)
    target = np.frombuffer(bits, dtype='uint8')
    mask = np.uint64(len(bits) * 8 - 1)
    # uint64 wraparound preserves the low bits used by the power-of-two mask.
    for i in range(hashes):
        positions = (first + np.uint64(i) * step) & mask
        values = (np.uint64(1) << (positions & np.uint64(7))).astype('uint8')
        np.bitwise_or.at(target, (positions >> np.uint64(3)).astype('intp'), values)


def _filter_size(entries: int) -> int:
    return 1 << max(3, ((entries * 24 + 7) // 8 - 1).bit_length())


def _filter_digest(count: int, low: bytes, high: bytes, bits: bytes) -> str:
    return hashlib.sha256(struct.pack('>Q', count) + low + high + bits).hexdigest()


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS event_index_runs (
      run_id INTEGER PRIMARY KEY AUTOINCREMENT, format_version INTEGER NOT NULL,
      entry_count INTEGER NOT NULL, min_key BLOB NOT NULL, max_key BLOB NOT NULL,
      bloom BLOB NOT NULL, filter_sha256 TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
      created_ts_ns INTEGER NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS event_index_run_payload (
      run_id INTEGER PRIMARY KEY, payload BLOB NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS event_index_retirement (
      run_id INTEGER PRIMARY KEY, entry_count INTEGER NOT NULL, max_event_ts_ns INTEGER NOT NULL,
      retired_ts_ns INTEGER NOT NULL, recovery_key TEXT NOT NULL, recovery_sha256 TEXT NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS event_index_retention_state (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), retired_entries INTEGER NOT NULL,
      retired_event_cutoff_ns INTEGER NOT NULL)''')
    connection.execute('INSERT OR IGNORE INTO event_index_retention_state VALUES(1,0,0)')
    # Never allow a cache to miss a new key inserted into an already-seen run.
    for table in ('event_index_runs', 'event_index_run_payload'):
        connection.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_immutable
          BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'immutable event run'); END''')
        # Replaces the pre-retention unconditional delete guard. An explicit
        # retirement receipt is installed in the same transaction as deletion.
        guard=f'{table}_retention_delete_guard'
        if not connection.execute('SELECT 1 FROM sqlite_master WHERE type=\'trigger\' AND name=?',(guard,)).fetchone():
            connection.execute(f'DROP TRIGGER IF EXISTS {table}_no_delete')
            connection.execute(f'''CREATE TRIGGER {guard}
              BEFORE DELETE ON {table} WHEN NOT EXISTS
                (SELECT 1 FROM event_index_retirement WHERE run_id=OLD.run_id)
              BEGIN SELECT RAISE(ABORT,'event run requires verified retention'); END''')


def append_runs(connection: sqlite3.Connection, entries: Iterable[tuple[bytes, int]]) -> list[int]:
    """Caller owns BEGIN/COMMIT with the manifest and delivery rows."""
    if not connection.in_transaction:
        raise RuntimeError('event runs require a catalogue transaction')
    chunk: list[tuple[bytes, int]] = []
    output: list[int] = []
    for key, reference in entries:
        key = bytes(key)
        if len(key) != 32 or not 0 < reference <= 2**63 - 1:
            raise ValueError('invalid event key or canonical manifest reference')
        chunk.append((key, int(reference)))
        if len(chunk) >= MAX_RUN_ENTRIES:
            output.append(_append(connection, chunk))
            chunk.clear()
    if chunk:
        output.append(_append(connection, chunk))
    return output


def _append(connection: sqlite3.Connection, entries: list[tuple[bytes, int]]) -> int:
    values = sorted(set(entries))
    payload = b''.join(RECORD.pack(*value) for value in values)
    bits = bytearray(_filter_size(len(values)))
    _add_records(bits, payload, FILTER_HASHES)
    low, high = values[0][0], values[-1][0]
    cursor = connection.execute('''INSERT INTO event_index_runs
      (format_version,entry_count,min_key,max_key,bloom,filter_sha256,payload_sha256,created_ts_ns)
      VALUES(?,?,?,?,?,?,?,?)''', (FORMAT, len(values), low, high, bytes(bits),
        _filter_digest(len(values), low, high, bits), hashlib.sha256(payload).hexdigest(), time.time_ns()))
    run_id = int(cursor.lastrowid)
    connection.execute('INSERT INTO event_index_run_payload VALUES(?,?)', (run_id, payload))
    return run_id


class ExactEventRuns:
    def __init__(self, connection: sqlite3.Connection, *, global_filter_bytes: int = 32 * 1024 * 1024):
        if global_filter_bytes < 8 or global_filter_bytes & (global_filter_bytes - 1):
            raise ValueError('global filter bytes must be a positive power of two >= 8')
        self.connection = connection
        self.global_filter_bytes = global_filter_bytes
        self._global: bytearray | None = None
        self._through = 0
        self.loaded_entries = 0
        self._retired_at_build = 0
        self._rebuild: Future | None = None
        self._rebuild_stop = threading.Event()
        self._rebuild_thread: threading.Thread | None = None
        self._last_rebuild_started = 0.0
        self.rebuild_error: str | None = None
        self.lookup_calls = 0
        self.directory_scans = 0
        self.scanned_runs = 0

    def prepare(self) -> None:
        self._refresh()

    def close(self) -> None:
        self._rebuild_stop.set()
        if self._rebuild_thread is not None:
            self._rebuild_thread.join(timeout=5)

    def request_rebuild(self) -> None:
        """Rebuild expired negative-cache bits without stalling consumption."""
        if self._rebuild is not None or self._rebuild_stop.is_set():
            return
        filename = str(self.connection.execute('PRAGMA database_list').fetchone()[2])
        if not filename:
            return
        future = self._rebuild = Future()
        self._last_rebuild_started = time.monotonic()
        def build():
            conn = None
            try:
                conn = sqlite3.connect(Path(filename).resolve().as_uri()+'?mode=ro', uri=True, isolation_level=None)
                conn.execute('PRAGMA cache_size=-16000')
                conn.execute('BEGIN')
                rebuilt = ExactEventRuns(conn, global_filter_bytes=self.global_filter_bytes)
                rebuilt._global = bytearray(self.global_filter_bytes)
                maximum = int(conn.execute("SELECT COALESCE((SELECT seq FROM sqlite_sequence WHERE name='event_index_runs'),0)").fetchone()[0])
                retired = int(conn.execute('SELECT retired_entries FROM event_index_retention_state WHERE singleton=1').fetchone()[0])
                for row in conn.execute('SELECT * FROM event_index_runs WHERE run_id<=? ORDER BY run_id', (maximum,)):
                    if self._rebuild_stop.is_set():
                        raise RuntimeError('event cache rebuild stopped')
                    rebuilt._load_run(row)
                conn.execute('COMMIT')
                future.set_result((rebuilt._global, maximum, rebuilt.loaded_entries, retired))
            except Exception as exc:
                future.set_exception(exc)
            finally:
                if conn is not None:
                    conn.close()
        self._rebuild_thread = threading.Thread(target=build, name='crypto-event-cache-rebuild', daemon=True)
        self._rebuild_thread.start()

    @staticmethod
    def _validate_header(row) -> tuple[int, bytes, bytes, bytes]:
        _, version, count, low, high, bits, filter_sha, _, _ = row
        count, low, high, bits = int(count), bytes(low), bytes(high), bytes(bits)
        if (version != FORMAT or not 0 < count <= MAX_RUN_ENTRIES or len(low) != 32
                or len(high) != 32 or low > high or len(bits) != _filter_size(count)
                or _filter_digest(count, low, high, bits) != filter_sha):
            raise RuntimeError('event run metadata integrity failure')
        return count, low, high, bits

    def _payload(self, row) -> bytes:
        count, low, high, _ = self._validate_header(row)
        found = self.connection.execute('SELECT payload FROM event_index_run_payload WHERE run_id=?', (row[0],)).fetchone()
        if found is None:
            raise RuntimeError('event run payload missing')
        payload = bytes(found[0])
        if (len(payload) != count * RECORD.size or hashlib.sha256(payload).hexdigest() != row[7]
                or payload[:32] != low or payload[-RECORD.size:-8] != high):
            raise RuntimeError('event run payload integrity failure')
        return payload

    def _refresh(self) -> None:
        if self.connection.in_transaction:
            # A rolled-back run ID can be reused by SQLite. Never advance an
            # in-memory coverage watermark over an uncommitted transaction.
            raise RuntimeError('event lookup requires committed catalogue state')
        # Run IDs are never reused, even after future retention deletes, due
        # to AUTOINCREMENT. Updates are forbidden by schema triggers.
        if self._rebuild is not None and self._rebuild.done():
            try:
                self._global,self._through,self.loaded_entries,self._retired_at_build = self._rebuild.result()
                self.rebuild_error = None
            except Exception as exc:
                # Existing bits contain a superset, so a failed cache rebuild
                # affects speed only. It can never turn a known key negative.
                self.rebuild_error = type(exc).__name__
            self._rebuild = None
        maximum = int(self.connection.execute("SELECT COALESCE((SELECT seq FROM sqlite_sequence WHERE name='event_index_runs'),0)").fetchone()[0])
        retired = int(self.connection.execute('SELECT retired_entries FROM event_index_retention_state WHERE singleton=1').fetchone()[0])
        if self._global is None or maximum < self._through:
            self._global = bytearray(self.global_filter_bytes)
            self._through = 0
            self.loaded_entries = 0
            self._retired_at_build = retired
        if maximum != self._through:
            cursor = self.connection.execute('SELECT * FROM event_index_runs WHERE run_id>? AND run_id<=? ORDER BY run_id',
                                             (self._through, maximum))
            for row in cursor:
                self._load_run(row)
            self._through = maximum
        if (retired-self._retired_at_build >= max(100000,self.loaded_entries//4)
                and time.monotonic()-self._last_rebuild_started>=600):
            self.request_rebuild()

    def _load_run(self, row) -> None:
        payload = self._payload(row)
        expected_filter = bytearray(_filter_size(int(row[2])))
        previous: tuple[bytes, int] | None = None
        for entry in RECORD.iter_unpack(payload):
            key, reference = entry
            if reference <= 0 or (previous is not None and entry <= previous):
                raise RuntimeError('event run records are not strictly sorted')
            previous = entry
        _add_records(expected_filter, payload, FILTER_HASHES)
        _add_records(self._global, payload, 7)
        if expected_filter != row[5]:
            raise RuntimeError('event run filter does not cover its records')
        self._through = int(row[0])
        self.loaded_entries += int(row[2])

    @staticmethod
    def _search(payload: bytes, key: bytes) -> int | None:
        left, right = 0, len(payload) // RECORD.size
        while left < right:
            middle = (left + right) // 2
            candidate = payload[middle * RECORD.size:middle * RECORD.size + 32]
            if candidate < key:
                left = middle + 1
            else:
                right = middle
        if left < len(payload) // RECORD.size:
            candidate, reference = RECORD.unpack_from(payload, left * RECORD.size)
            if candidate == key:
                return reference
        return None

    def lookup(self, keys: Iterable[bytes]) -> dict[bytes, int]:
        self.lookup_calls += 1
        values = set(bytes(key) for key in keys)
        if any(len(key) != 32 for key in values):
            raise ValueError('event lookup keys must be 32 bytes')
        if not values:
            return {}
        self._refresh()
        assert self._global is not None
        pending = {key for key in values if _maybe(self._global, key, 7)}
        result: dict[bytes, int] = {}
        if not pending:
            return result
        # The directory holds filters, not large payloads. No per-run Python
        # cache grows with retention; SQLite's explicit page-cache budget
        # bounds the directory cache. Exact payloads are fetched only on hits.
        self.directory_scans += 1
        for row in self.connection.execute('SELECT * FROM event_index_runs ORDER BY run_id DESC'):
            self.scanned_runs += 1
            _, low, high, bits = self._validate_header(row)
            candidates = [key for key in pending if low <= key <= high and _maybe(bits, key, FILTER_HASHES)]
            if not candidates:
                continue
            payload = self._payload(row)
            for key in candidates:
                reference = self._search(payload, key)
                if reference is not None:
                    result[key] = reference
                    pending.remove(key)
            if not pending:
                break
        return result
