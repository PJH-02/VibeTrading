from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.verify_crypto_replay_integrity import ReplayCoverage, audit_catalogue


RUN = '1' * 32
ORDER = [(0, 2), (0, 0), (1, 0), (0, 1), (1, 1)]


def coverage():
    inspection = {'trace_duration_ns': 2_000_000_000, 'trace_sha256': 'a' * 64}
    trace = [(1_700_000_000, 2, b''), (0, 0, b''), (900_000_000, 1, b'')]
    result = {'run_id': RUN, 'multiplier': 2, 'duration_seconds': 1.5,
              'kind': 'complete', 'publish_errors': 0, 'scheduled': 5, 'sent': 5,
              'last_stream_sequence': 5, 'max_schedule_lag_ms': 1, 'max_publish_ms': 2,
              'trace_sha256': 'a' * 64}
    return ReplayCoverage(result, trace, inspection)


def event_id(cycle, ordinal):
    return f'replay:{RUN}:{cycle}:{ordinal}'


def test_partial_cycle_and_out_of_order_publication_have_exact_coverage():
    audit = coverage()
    for sequence, (cycle, ordinal) in enumerate(ORDER, 1):
        audit.add(event_id(cycle, ordinal), 'CRYPTO_DATA', sequence, 2)
    rows = [(seq, hashlib.sha256(event_id(*pair).encode()).digest(), 1)
            for seq, pair in enumerate(ORDER, 1)]
    audit.verify_ledger(rows, lambda reference: 2)
    assert audit.count == audit.expected == 5
    assert len(audit.events) == 1
    assert len(audit.sequences) == 5


@pytest.mark.parametrize('event,stream,sequence,reference', [
    (event_id(1, 2), 'CRYPTO_DATA', 1, 2),  # outside partial last cycle
    (event_id(0, 0), 'MARKET_DATA', 1, 2),
    (event_id(0, 0), 'CRYPTO_DATA', 6, 2),
    (event_id(0, 0), 'CRYPTO_DATA', 1, 0),
    ('replay:' + '2' * 32 + ':0:0', 'CRYPTO_DATA', 1, 2),
])
def test_unplanned_canonical_records_are_rejected(event, stream, sequence, reference):
    with pytest.raises(RuntimeError):
        coverage().add(event, stream, sequence, reference)


@pytest.mark.parametrize('event,sequence', [(event_id(0, 0), 2), (event_id(0, 1), 1)])
def test_duplicate_event_or_delivery_is_rejected(event, sequence):
    audit = coverage()
    audit.add(event_id(0, 0), 'CRYPTO_DATA', 1, 2)
    with pytest.raises(RuntimeError, match='duplicate canonical'):
        audit.add(event, 'CRYPTO_DATA', sequence, 2)


@pytest.fixture
def catalogue(tmp_path):
    root = tmp_path / 'r2-load-gate' / 'trial'
    root.mkdir(parents=True)
    prefix = 'vibetrading/crypto/qualification/trial'
    remote = root / 'fixture.parquet'
    pq.write_table(pa.table({
        'event_id': [event_id(*pair) for pair in ORDER],
        'nats_stream': ['CRYPTO_DATA'] * 5,
        'nats_stream_sequence': list(range(1, 6)),
    }), remote)
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
      CREATE TABLE delivery_stream(stream_id INTEGER PRIMARY KEY,stream_name TEXT,stream_generation TEXT);
      CREATE TABLE parquet_manifest(file_id TEXT,lifecycle_state TEXT,superseded_by TEXT,
        upload_status TEXT,verified_ts_ns INTEGER,remote_key TEXT,remote_manifest_key TEXT,
        file_sha256 TEXT,byte_size INTEGER,rows INTEGER);
      CREATE TABLE parquet_delivery_compact(stream_id INTEGER,stream_sequence INTEGER,event_key BLOB,
        file_rowid INTEGER,PRIMARY KEY(stream_id,stream_sequence));
      INSERT INTO delivery_stream VALUES(4,'CRYPTO_DATA','test-generation');
      INSERT INTO parquet_manifest(rowid,file_id,lifecycle_state,superseded_by) VALUES(1,'old','superseded','new');
    ''')
    sha = hashlib.sha256(remote.read_bytes()).hexdigest()
    key, sidecar = prefix + '/new.parquet', prefix + '/new.json'
    document = {'file_id': 'new', 'key': key, 'bytes': remote.stat().st_size, 'sha256': sha, 'rows': 5}
    objects = {key: remote.read_bytes(), sidecar: json.dumps(document).encode()}
    conn.execute('INSERT INTO parquet_manifest VALUES(?,?,?,?,?,?,?,?,?,?)',
                 ('new', 'active', None, 'remote_verified', 123, key, sidecar, sha, remote.stat().st_size, 5))
    conn.executemany('INSERT INTO parquet_delivery_compact VALUES(4,?,?,1)',
                     ((seq, hashlib.sha256(event_id(*pair).encode()).digest()) for seq, pair in enumerate(ORDER, 1)))
    backend = SimpleNamespace(download_file=lambda object_key, destination: destination.write_bytes(objects[object_key]))
    receipt = SimpleNamespace(generation='test-generation')
    yield conn, root, prefix, objects, backend, receipt
    conn.close()


def test_remote_parquet_sidecar_ledger_and_compaction_reference_agree(catalogue):
    conn, root, prefix, _, backend, receipt = catalogue
    before = conn.total_changes
    result = audit_catalogue(conn, coverage(), 0, receipt, backend, root, prefix, 0)
    assert result['canonical_rows'] == result['exact_delivery_rows'] == 5
    assert result['canonical_files'] == 1
    assert result['remote_full_sha_verified_bytes'] > 0
    assert conn.total_changes == before  # audit does not alter or prune the catalogue
    assert not list(root.glob('integrity-*'))


@pytest.mark.parametrize('fault', ['missing_delivery', 'wrong_event', 'missing_reference', 'corrupt_remote',
                                  'wrong_sidecar', 'unverified', 'wrong_generation', 'insufficient_space',
                                  'duplicate_canonical'])
def test_terminal_audit_fails_closed_on_missing_or_inconsistent_evidence(catalogue, monkeypatch, fault):
    conn, root, prefix, objects, backend, receipt = catalogue
    if fault == 'missing_delivery':
        conn.execute('DELETE FROM parquet_delivery_compact WHERE stream_sequence=5')
    elif fault == 'wrong_event':
        conn.execute('UPDATE parquet_delivery_compact SET event_key=? WHERE stream_sequence=1', (b'x' * 32,))
    elif fault == 'missing_reference':
        conn.execute('UPDATE parquet_delivery_compact SET file_rowid=99 WHERE stream_sequence=1')
    elif fault == 'corrupt_remote':
        objects[prefix + '/new.parquet'] = b'corrupt'
    elif fault == 'wrong_sidecar':
        document = json.loads(objects[prefix + '/new.json'])
        document['file_id'] = 'different'
        objects[prefix + '/new.json'] = json.dumps(document).encode()
    elif fault == 'unverified':
        conn.execute("UPDATE parquet_manifest SET upload_status='local_only' WHERE rowid=2")
    elif fault == 'wrong_generation':
        receipt.generation = 'different-generation'
    elif fault == 'insufficient_space':
        monkeypatch.setattr(shutil, 'disk_usage', lambda path: SimpleNamespace(free=0))
    elif fault == 'duplicate_canonical':
        remote = root / 'fixture.parquet'
        table = pq.read_table(remote).to_pydict()
        table['event_id'][1] = table['event_id'][0]
        pq.write_table(pa.table(table), remote)
        payload = remote.read_bytes()
        sha = hashlib.sha256(payload).hexdigest()
        objects[prefix + '/new.parquet'] = payload
        document = json.loads(objects[prefix + '/new.json'])
        document.update(sha256=sha, bytes=len(payload))
        objects[prefix + '/new.json'] = json.dumps(document).encode()
        conn.execute('UPDATE parquet_manifest SET file_sha256=?,byte_size=? WHERE rowid=2', (sha, len(payload)))
    with pytest.raises(RuntimeError):
        audit_catalogue(conn, coverage(), 0, receipt, backend, root, prefix, 0)
    assert not list(root.glob('integrity-*'))
