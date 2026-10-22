"""Exact crypto delivery recovery blocks, committed with their canonical runs.

Ranges are never recovery evidence. Each record names one stream incarnation,
sequence, event hash, canonical manifest reference, and persistence timestamp.
Remote blocks contain the stream and manifest mapping needed to interpret those
references independently of the original SQLite file.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import struct
import time
import zlib
from collections import OrderedDict
from pathlib import Path
from threading import Lock

from .event_index_runs import MAX_RUN_ENTRIES, append_runs


RECORD = struct.Struct('>QQ32sQQ')
SCHEMA = 'crypto-delivery-recovery-v1'
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024

# Only negative pruning evidence survives maintenance's connection reopen.
# This cache never contains payloads or authorizes deletion. A newly eligible
# block is always fetched, checksummed and checked against current proofs.
_UNCONFIRMED_BOUNDS = OrderedDict()
_BOUNDS_LOCK = Lock()
_MAX_CACHED_BLOCKS = 256
_MAX_CACHED_STREAMS = 8
# Negative archive hints only; a recovered file always triggers full validation.
_UNAVAILABLE_MANIFESTS = OrderedDict()
_MAX_CACHED_MANIFEST_BLOCKS = 64
_MAX_CACHED_MANIFEST_REFS = 1024


def _database_identity(conn):
    main = next((row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), '')
    if not main:
        return None
    try:
        path = Path(main).resolve(strict=True)
        stat = path.stat()
    except OSError:
        return None
    if not stat.st_ino:
        return None
    return (hashlib.sha256(str(path).encode()).digest(), stat.st_dev, stat.st_ino)


def _floors_cover(conn, required):
    # A renewed consumer can lower a previously confirmed floor. Always query
    # it inside the same transaction as the bounded deletion or negative skip.
    for sid, maximum in required.items():
        floor = conn.execute('''SELECT confirmed.ack_floor FROM delivery_stream stream
          JOIN delivery_confirmed_floor confirmed ON confirmed.stream_name=stream.stream_name
            AND confirmed.stream_generation=stream.stream_generation
            AND confirmed.durable='crypto-parquet-storage'
          WHERE stream.stream_id=? AND stream.stream_name='CRYPTO_DATA' ''', (sid,)).fetchone()
        if floor is None or int(floor[0]) < maximum:
            return False
    return True


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS crypto_delivery_recovery (
      block_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL UNIQUE,
      payload BLOB NOT NULL, payload_sha256 TEXT NOT NULL, row_count INTEGER NOT NULL,
      min_persisted_ts_ns INTEGER NOT NULL, max_persisted_ts_ns INTEGER NOT NULL,
      remote_key TEXT, remote_sha256 TEXT, remote_bytes INTEGER, verified_ts_ns INTEGER,
      prune_cursor INTEGER NOT NULL DEFAULT 0, pruned_ts_ns INTEGER)''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS crypto_recovery_immutable
      BEFORE UPDATE OF run_id,payload,payload_sha256,row_count,min_persisted_ts_ns,max_persisted_ts_ns
      ON crypto_delivery_recovery BEGIN SELECT RAISE(ABORT,'immutable recovery coverage'); END''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_crypto_recovery_upload
      ON crypto_delivery_recovery(verified_ts_ns,block_id)''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_crypto_recovery_prune
      ON crypto_delivery_recovery(max_persisted_ts_ns,block_id) WHERE pruned_ts_ns IS NULL''')


def attach_coverage(conn: sqlite3.Connection, run_id: int, deliveries) -> int:
    if not conn.in_transaction:
        raise RuntimeError('recovery coverage requires the catalogue transaction')
    rows = sorted(set(tuple(row) for row in deliveries), key=lambda row: (row[0], row[1]))
    if not 0 < len(rows) <= MAX_RUN_ENTRIES:
        raise ValueError('invalid recovery block size')
    if len({row[:2] for row in rows}) != len(rows):
        raise ValueError('ambiguous recovery delivery identity')
    if any(len(bytes(row[2])) != 32 or min(row[0], row[1], row[3], row[4]) <= 0 for row in rows):
        raise ValueError('invalid recovery delivery fields')
    raw = b''.join(RECORD.pack(*row) for row in rows)
    packed,sha = zlib.compress(raw, 1),hashlib.sha256(raw).hexdigest()
    decoded = decode_records(packed, sha, len(rows))
    exact = {(row[2], row[3]) for row in decoded}
    from .event_index_runs import RECORD as EVENT_RECORD
    existing = conn.execute('SELECT payload FROM event_index_run_payload WHERE run_id=?', (run_id,)).fetchone()
    if existing is None or set(EVENT_RECORD.iter_unpack(bytes(existing[0]))) != exact:
        raise RuntimeError('recovery coverage differs from exact event run')
    cursor = conn.execute('''INSERT INTO crypto_delivery_recovery
      (run_id,payload,payload_sha256,row_count,min_persisted_ts_ns,max_persisted_ts_ns)
      VALUES(?,?,?,?,?,?)''', (run_id, packed, sha,
                             len(rows), min(row[4] for row in rows), max(row[4] for row in rows)))
    return int(cursor.lastrowid)


def append_delivery_runs(conn: sqlite3.Connection, deliveries) -> None:
    rows = list(deliveries)
    for start in range(0, len(rows), MAX_RUN_ENTRIES):
        chunk = rows[start:start + MAX_RUN_ENTRIES]
        runs = append_runs(conn, ((row[2], row[3]) for row in chunk))
        if len(runs) != 1:
            raise RuntimeError('one recovery block requires one exact run')
        attach_coverage(conn, runs[0], chunk)


def _inflate(payload: bytes, limit: int) -> bytes:
    decoder = zlib.decompressobj()
    raw = decoder.decompress(payload, limit + 1)
    if len(raw) > limit or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise RuntimeError('invalid or oversized recovery payload')
    return raw


def decode_records(payload: bytes, sha: str, count: int):
    if not 0 < int(count) <= MAX_RUN_ENTRIES:
        raise RuntimeError('invalid recovery row count')
    raw = _inflate(bytes(payload), int(count) * RECORD.size)
    if len(raw) != count * RECORD.size or hashlib.sha256(raw).hexdigest() != sha:
        raise RuntimeError('recovery payload checksum or size mismatch')
    rows = list(RECORD.iter_unpack(raw))
    previous = None
    for sid, seq, key, file_ref, persisted in rows:
        if min(sid, seq, file_ref, persisted) <= 0 or (previous is not None and (sid, seq) <= previous):
            raise RuntimeError('invalid or duplicate recovery identity')
        previous = (sid, seq)
    return rows


def remote_manifests(conn: sqlite3.Connection, references) -> dict[str, dict] | None:
    """Include original canonical manifests and verified supersession chains."""
    result = {}
    for reference in set(references):
        row = conn.execute('SELECT rowid AS manifest_rowid,* FROM parquet_manifest WHERE rowid=?', (reference,)).fetchone()
        seen = set()
        for _ in range(64):
            if row is None:
                return None
            value = dict(row)
            file_id = str(value.get('file_id') or '')
            if not file_id or file_id in seen:
                return None
            seen.add(file_id)
            result[str(value['manifest_rowid'])] = value
            if (value['upload_status'] == 'remote_verified' and value.get('remote_key')
                    and value.get('remote_manifest_key') and len(str(value.get('file_sha256') or '')) == 64):
                break
            if value.get('lifecycle_state') != 'superseded' or not value.get('superseded_by'):
                return None
            row = conn.execute('SELECT rowid AS manifest_rowid,* FROM parquet_manifest WHERE file_id=?',
                               (value['superseded_by'],)).fetchone()
        else:
            return None
    return result


def prepare_document(conn: sqlite3.Connection, block_id: int, generation: str) -> tuple[bytes, str] | None:
    identity = _database_identity(conn)
    cache_key = (identity, block_id) if identity else None
    with _BOUNDS_LOCK:
        references = _UNAVAILABLE_MANIFESTS.get(cache_key)
        if references is not None:
            _UNAVAILABLE_MANIFESTS.move_to_end(cache_key)
    if references is not None and remote_manifests(conn, references) is None:
        return None
    block = conn.execute('SELECT * FROM crypto_delivery_recovery WHERE block_id=?', (block_id,)).fetchone()
    if block is None or block['verified_ts_ns'] is not None:
        return None
    rows = decode_records(block['payload'], block['payload_sha256'], block['row_count'])
    manifests = remote_manifests(conn, (row[3] for row in rows))
    if manifests is None:
        references = tuple({row[3] for row in rows})
        if cache_key is not None and len(references) <= _MAX_CACHED_MANIFEST_REFS:
            with _BOUNDS_LOCK:
                _UNAVAILABLE_MANIFESTS[cache_key] = references
                _UNAVAILABLE_MANIFESTS.move_to_end(cache_key)
                while len(_UNAVAILABLE_MANIFESTS) > _MAX_CACHED_MANIFEST_BLOCKS:
                    _UNAVAILABLE_MANIFESTS.popitem(last=False)
        return None
    with _BOUNDS_LOCK:
        _UNAVAILABLE_MANIFESTS.pop(cache_key, None)
    streams = {}
    for sid in {row[0] for row in rows}:
        stream = conn.execute('SELECT stream_name,stream_generation FROM delivery_stream WHERE stream_id=?', (sid,)).fetchone()
        if stream is None or stream[0] != 'CRYPTO_DATA' or not stream[1]:
            raise RuntimeError('recovery stream incarnation is unavailable')
        streams[str(sid)] = list(stream)
    document = {'schema': SCHEMA, 'catalogue_generation': generation, 'block_id': block_id,
                'run_id': block['run_id'], 'payload': base64.b64encode(block['payload']).decode('ascii'),
                'payload_sha256': block['payload_sha256'], 'row_count': block['row_count'],
                'streams': streams, 'manifests': manifests}
    raw = json.dumps(document, sort_keys=True, separators=(',', ':')).encode()
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise RuntimeError('recovery document exceeds bounded memory budget')
    packed = zlib.compress(raw, 1)
    return packed, hashlib.sha256(packed).hexdigest()


def read_document(packed: bytes, sha: str) -> tuple[dict, list]:
    if hashlib.sha256(packed).hexdigest() != sha:
        raise RuntimeError('remote recovery document checksum mismatch')
    document = json.loads(_inflate(packed, MAX_DOCUMENT_BYTES))
    if document.get('schema') != SCHEMA or not document.get('catalogue_generation'):
        raise RuntimeError('unsupported recovery document identity')
    rows = decode_records(base64.b64decode(document['payload'], validate=True),
                          document['payload_sha256'], document['row_count'])
    manifests = document['manifests']
    by_file_id = {value['file_id']: value for value in manifests.values()}
    for sid, _, _, reference, _ in rows:
        stream = document['streams'].get(str(sid))
        if not stream or len(stream) != 2 or stream[0] != 'CRYPTO_DATA' or not stream[1]:
            raise RuntimeError('recovery stream mapping missing')
        value = manifests.get(str(reference))
        visited = set()
        for _ in range(64):
            if value is None or value['file_id'] in visited:
                raise RuntimeError('recovery canonical manifest mapping missing')
            visited.add(value['file_id'])
            if (value.get('upload_status') == 'remote_verified' and value.get('remote_key')
                    and value.get('remote_manifest_key') and len(str(value.get('file_sha256') or '')) == 64):
                break
            if value.get('lifecycle_state') != 'superseded':
                raise RuntimeError('canonical file has no verified remote coverage')
            value = by_file_id.get(value.get('superseded_by'))
        else:
            raise RuntimeError('invalid canonical supersession chain')
    return document, rows


def record_verified(conn: sqlite3.Connection, block_id: int, generation: str, packed: bytes, receipt) -> None:
    from .storage_backend import ArchiveObject
    if not isinstance(receipt, ArchiveObject) or not receipt.key or receipt.size_bytes != len(packed):
        raise RuntimeError('invalid remote recovery receipt')
    document, _ = read_document(packed, receipt.sha256)
    if document['block_id'] != block_id or document['catalogue_generation'] != generation:
        raise RuntimeError('remote recovery receipt identity mismatch')
    block = conn.execute('SELECT * FROM crypto_delivery_recovery WHERE block_id=?', (block_id,)).fetchone()
    if (block is None or block['run_id'] != document['run_id']
            or block['payload_sha256'] != document['payload_sha256'] or block['row_count'] != document['row_count']):
        raise RuntimeError('remote recovery receipt coverage mismatch')
    conn.execute('''UPDATE crypto_delivery_recovery SET remote_key=?,remote_sha256=?,remote_bytes=?,verified_ts_ns=?
      WHERE block_id=?''', (receipt.key, receipt.sha256, receipt.size_bytes, time.time_ns(), block_id))


def restore_document(store, packed: bytes, sha: str) -> dict[str, int]:
    """Restore exact catalogue coverage; never changes NATS or authorizes ACKs.

    The caller supplies a separately verified remote object's SHA. Recovery
    preserves manifest reference numbers, rejects collisions, and marks local
    Parquet copies absent. Source absolute paths are never used for writes.
    """
    if store.event_index_format != 'immutable-runs-v1':
        raise RuntimeError('recovery requires the immutable crypto catalogue')
    document, rows = read_document(packed, sha)
    marker = f'recovered_delivery_document:{sha}'
    with store.transaction() as conn:
        if conn.execute('SELECT 1 FROM runtime_metadata WHERE key=?', (marker,)).fetchone():
            return {'restored_deliveries': 0, 'already_restored': 1}
        generation = conn.execute("SELECT value FROM runtime_metadata WHERE key='collection_generation'").fetchone()
        if generation is not None and generation[0] != document['catalogue_generation']:
            raise RuntimeError('recovery catalogue generation collision')
        conn.execute("INSERT OR IGNORE INTO runtime_metadata VALUES('collection_generation',?,?)",
                     (document['catalogue_generation'], time.time_ns()))
        for sid, identity in document['streams'].items():
            existing = conn.execute('SELECT stream_name,stream_generation FROM delivery_stream WHERE stream_id=?', (int(sid),)).fetchone()
            if existing is not None and list(existing) != identity:
                raise RuntimeError('recovery stream identity collision')
            conn.execute('INSERT OR IGNORE INTO delivery_stream VALUES(?,?,?,0,?)',
                         (int(sid), *identity, time.time_ns()))
            actual = conn.execute('SELECT stream_id FROM delivery_stream WHERE stream_name=? AND stream_generation=?', identity).fetchone()
            if actual is None or int(actual[0]) != int(sid):
                raise RuntimeError('recovery stream reference collision')
        columns = [str(row[1]) for row in conn.execute('PRAGMA table_info(parquet_manifest)')]
        for reference, source in document['manifests'].items():
            if int(reference) <= 0 or int(source['manifest_rowid']) != int(reference):
                raise RuntimeError('recovery manifest reference mismatch')
            existing = conn.execute('SELECT file_id,file_sha256,rows FROM parquet_manifest WHERE rowid=?', (int(reference),)).fetchone()
            if existing is not None:
                if tuple(existing) != (source['file_id'], source['file_sha256'], source['rows']):
                    raise RuntimeError('recovery canonical manifest collision')
                continue
            values = {name: source[name] for name in columns if name in source}
            # No external path or lease can become a local filesystem action.
            path_id = hashlib.sha256(str(source['file_id']).encode()).hexdigest()
            values.update(path=str(store.settings.parquet.data_root / 'recovered' / f'{path_id}.parquet'),
                          local_present=0, lease_owner=None, lease_until_ts_ns=None)
            names = list(values)
            conn.execute(f'INSERT INTO parquet_manifest(rowid,{",".join(names)}) VALUES({",".join("?" for _ in range(len(names)+1))})',
                         [int(reference), *(values[name] for name in names)])
        inserted = []
        for row in rows:
            existing = conn.execute('SELECT stream_id,stream_sequence,event_key,file_rowid,persisted_ts_ns '
                                    'FROM parquet_delivery_compact WHERE stream_id=? AND stream_sequence=?', row[:2]).fetchone()
            if existing is not None:
                if tuple(existing) != tuple(row):
                    raise RuntimeError('recovery delivery collision')
                continue
            conn.execute('INSERT INTO parquet_delivery_compact VALUES(?,?,?,?,?)', row)
            inserted.append(row)
        append_delivery_runs(conn, inserted)
        conn.execute('INSERT INTO runtime_metadata VALUES(?,?,?)', (marker, SCHEMA, time.time_ns()))
    return {'restored_deliveries': len(inserted), 'already_restored': 0}


def prune_verified(conn: sqlite3.Connection, *, cutoff_ns: int, limit: int, candidate_limit: int = 16) -> int:
    """Caller owns the writer transaction; exact rows and progress commit together."""
    if not conn.in_transaction:
        raise RuntimeError('recovery pruning requires a transaction')
    marker = conn.execute("SELECT value FROM runtime_metadata WHERE key='crypto_recovery_prune_scan_cursor'").fetchone()
    after = int(marker[0]) if marker else 0
    identity = _database_identity(conn)
    # Index-only discovery is essential: metadata stored after a multi-MB
    # SQLite BLOB can still traverse its overflow pages even if the BLOB is
    # not in SELECT. Proof fields are fetched only for non-negative candidates.
    query = '''SELECT block_id FROM crypto_delivery_recovery INDEXED BY idx_crypto_recovery_prune
      WHERE pruned_ts_ns IS NULL AND max_persisted_ts_ns<=?
        AND block_id IN (SELECT block_id FROM crypto_delivery_recovery INDEXED BY idx_crypto_recovery_upload
                         WHERE verified_ts_ns IS NOT NULL)
        AND block_id>? ORDER BY block_id LIMIT ?'''
    candidate_limit = max(16, min(128, int(candidate_limit)))
    candidates = conn.execute(query, (cutoff_ns, after, candidate_limit)).fetchall()
    if not candidates:
        candidates = conn.execute(query, (cutoff_ns, 0, candidate_limit)).fetchall()
    removed = 0
    cold_blocked = 0

    def scanned(block_id):
        # This marker has no deletion authority. Rotate past unverifiable
        # generations, retaining their rows, and retry them on the next wrap.
        # It commits/rolls back with exact deletion and the per-block cursor.
        conn.execute("INSERT INTO runtime_metadata VALUES('crypto_recovery_prune_scan_cursor',?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_ts_ns=excluded.updated_ts_ns",
                     (str(block_id), time.time_ns()))

    for candidate in candidates:
        block_id = int(candidate['block_id'])
        cache_key = (identity, block_id) if identity else None
        with _BOUNDS_LOCK:
            cached = _UNCONFIRMED_BOUNDS.get(cache_key)
            if cached is not None:
                _UNCONFIRMED_BOUNDS.move_to_end(cache_key)
        if cached is not None and not _floors_cover(conn, dict(cached)):
            scanned(block_id)
            continue
        # Even a cache hit whose floor recovered must validate actual bytes.
        block = conn.execute('SELECT * FROM crypto_delivery_recovery WHERE block_id=?', (block_id,)).fetchone()
        if block is None:
            raise RuntimeError('recovery block disappeared inside the writer transaction')
        if (block['verified_ts_ns'] is None or block['remote_key'] is None
                or block['remote_sha256'] is None or (block['remote_bytes'] or 0) <= 0):
            scanned(block_id)
            continue
        rows = decode_records(block['payload'], block['payload_sha256'], block['row_count'])
        required = {}
        for sid, seq, _, _, _ in rows:
            required[sid] = max(required.get(sid, 0), seq)
        if not _floors_cover(conn, required):
            if cache_key is not None and len(required) <= _MAX_CACHED_STREAMS:
                with _BOUNDS_LOCK:
                    _UNCONFIRMED_BOUNDS[cache_key] = tuple(required.items())
                    _UNCONFIRMED_BOUNDS.move_to_end(cache_key)
                    while len(_UNCONFIRMED_BOUNDS) > _MAX_CACHED_BLOCKS:
                        _UNCONFIRMED_BOUNDS.popitem(last=False)
            scanned(int(block['block_id']))
            cold_blocked += 1
            # A restart may need to learn many large historical blocks. Keep
            # that first pass at the original bounded cold-read batch size.
            if cold_blocked >= 16:
                break
            continue
        with _BOUNDS_LOCK:
            _UNCONFIRMED_BOUNDS.pop(cache_key, None)
        if remote_manifests(conn, (row[3] for row in rows)) is None:
            scanned(int(block['block_id']))
            continue
        cursor = int(block['prune_cursor'])
        if not 0 <= cursor <= len(rows):
            raise RuntimeError('invalid recovery prune cursor')
        chunk = rows[cursor:cursor + max(0, limit - removed)]
        for row in chunk:
            changed = conn.execute('''DELETE FROM parquet_delivery_compact
              WHERE stream_id=? AND stream_sequence=? AND event_key=? AND file_rowid=? AND persisted_ts_ns=?''', row).rowcount
            if changed != 1:
                raise RuntimeError('recovery coverage does not match the delivery being deleted')
        cursor += len(chunk)
        conn.execute('UPDATE crypto_delivery_recovery SET prune_cursor=?,pruned_ts_ns=? WHERE block_id=?',
                     (cursor, time.time_ns() if cursor == len(rows) else None, block['block_id']))
        # Finish a partially deleted block before advancing the scan. This
        # preserves bounded delete throughput even when a block is > limit.
        scanned(int(block['block_id']) if cursor == len(rows) else int(block['block_id']) - 1)
        removed += len(chunk)
        if removed >= limit:
            break
    return removed


def retire_runs(conn: sqlite3.Connection, *, now_ns: int, max_age_seconds: int, limit: int) -> dict:
    """Retire only fully pruned, independently recoverable historical runs.

    The immutable live replay contract is enforced by RuntimeStore/StorageWorker.
    Canonical file event-time bounds are conservative (may delay retirement),
    never inferred from a sequence range or the run's creation timestamp.
    """
    if not conn.in_transaction:
        raise RuntimeError('event retirement requires a transaction')
    cutoff=now_ns-max(60,int(max_age_seconds))*10**9
    prior=conn.execute("SELECT value FROM runtime_metadata WHERE key='crypto_event_retirement_cursor'").fetchone()
    after=int(prior[0]) if prior else 0
    query='''SELECT block_id FROM crypto_delivery_recovery INDEXED BY idx_crypto_recovery_upload
      WHERE verified_ts_ns IS NOT NULL
        AND block_id NOT IN (SELECT block_id FROM crypto_delivery_recovery INDEXED BY idx_crypto_recovery_prune
                             WHERE pruned_ts_ns IS NULL)
        AND block_id>? ORDER BY block_id LIMIT ?'''
    blocks=conn.execute(query,(after,max(16,limit*4))).fetchall()
    if not blocks:
        blocks=conn.execute(query,(0,max(16,limit*4))).fetchall()
    retired=entries=0
    last_examined=None
    for candidate in blocks:
        last_examined=candidate['block_id']
        block=conn.execute('SELECT * FROM crypto_delivery_recovery WHERE block_id=?', (last_examined,)).fetchone()
        if (block is None or block['pruned_ts_ns'] is None or block['prune_cursor']!=block['row_count']
                or block['verified_ts_ns'] is None or block['remote_key'] is None or block['remote_sha256'] is None):
            continue
        index=conn.execute('SELECT * FROM event_index_runs WHERE run_id=?',(block['run_id'],)).fetchone()
        if index is None:
            raise RuntimeError('recovery block lost its exact run before retirement')
        from .event_index_runs import ExactEventRuns, RECORD as EVENT_RECORD
        payload=ExactEventRuns(conn)._payload(index)
        references={record[1] for record in EVENT_RECORD.iter_unpack(payload)}
        maximum=0
        for reference in references:
            row=conn.execute('SELECT max_ts_ns FROM parquet_manifest WHERE rowid=?',(reference,)).fetchone()
            if row is None or row[0] is None or int(row[0])<=0 or int(row[0])>cutoff:
                maximum=0
                break
            maximum=max(maximum,int(row[0]))
        if maximum<=0 or remote_manifests(conn,references) is None:
            continue
        conn.execute('INSERT INTO event_index_retirement VALUES(?,?,?,?,?,?)',
                     (block['run_id'],index['entry_count'],maximum,now_ns,block['remote_key'],block['remote_sha256']))
        conn.execute('DELETE FROM event_index_run_payload WHERE run_id=?',(block['run_id'],))
        conn.execute('DELETE FROM event_index_runs WHERE run_id=?',(block['run_id'],))
        conn.execute('DELETE FROM crypto_delivery_recovery WHERE block_id=?',(block['block_id'],))
        conn.execute('''UPDATE event_index_retention_state
          SET retired_entries=retired_entries+?,retired_event_cutoff_ns=MAX(retired_event_cutoff_ns,?) WHERE singleton=1''',
                     (index['entry_count'],maximum))
        retired+=1
        entries+=int(index['entry_count'])
        if retired>=limit:
            break
    if last_examined is not None:
        conn.execute("INSERT INTO runtime_metadata VALUES('crypto_event_retirement_cursor',?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_ts_ns=excluded.updated_ts_ns",
                     (str(last_examined),now_ns))
    return {'retired_runs':retired,'retired_entries':entries,'candidate_count':len(blocks),'limit':limit}
