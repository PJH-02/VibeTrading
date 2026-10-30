import os
import sqlite3
import subprocess
import sys

import pytest

from scripts.prepare_crypto_event_index import prepare
from storage.parquet_store import ParquetBatchWriter
from storage.sqlite_store import RuntimeStore
from test_storage_pipeline import _event, _settings


def test_offline_migration_preserves_delivery_and_manifest_references(tmp_path):
    old = _settings(tmp_path, 'DELIVERY_LEDGER_FORMAT=compact\n')
    source = tmp_path / 'source.db'
    store = RuntimeStore(old, source)
    writer = ParquetBatchWriter(old, store)
    event = _event()
    writer.add(event, stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=7, event_id=event.event_id)
    writer.flush_all()
    store.commit_event_aliases([('CRYPTO_DATA', 'g', 8, event.event_id)])
    manifest = store.list_manifest()
    store.close()
    target = tmp_path / 'new.db'
    report = prepare(source, target)
    assert report['delivery_rows'] == 2
    updated = _settings(tmp_path, 'DELIVERY_LEDGER_FORMAT=compact\nCRYPTO_EVENT_INDEX_FORMAT=immutable-runs-v1\n')
    converted = RuntimeStore(updated, target)
    assert converted.event_committed(event.event_id)
    assert converted.delivery_committed('CRYPTO_DATA', 'g', 7)
    assert converted.delivery_committed('CRYPTO_DATA', 'g', 8)
    assert converted.list_manifest() == manifest
    assert converted._conn.execute("SELECT 1 FROM sqlite_master WHERE name='idx_delivery_compact_event'").fetchone() is None
    converted.close()
    with pytest.raises(RuntimeError, match='differs from configuration'):
        RuntimeStore(old, target)
    original = RuntimeStore(old, source)
    assert original.event_committed(event.event_id)
    assert original._conn.execute("SELECT 1 FROM sqlite_master WHERE name='idx_delivery_compact_event'").fetchone()
    original.close()


def test_old_catalogue_cannot_silently_switch_to_runs(tmp_path):
    settings = _settings(tmp_path, 'DELIVERY_LEDGER_FORMAT=compact\n')
    store = RuntimeStore(settings)
    path = store.path
    store.close()
    changed = _settings(tmp_path, 'DELIVERY_LEDGER_FORMAT=compact\nCRYPTO_EVENT_INDEX_FORMAT=immutable-runs-v1\n')
    with pytest.raises(RuntimeError, match='verified offline migration'):
        RuntimeStore(changed, path)


def test_run_failure_rolls_back_manifest_and_delivery(tmp_path):
    settings = _settings(tmp_path, 'DELIVERY_LEDGER_FORMAT=compact\nCRYPTO_EVENT_INDEX_FORMAT=immutable-runs-v1\n')
    store = RuntimeStore(settings)
    store._conn.execute("CREATE TRIGGER fail_run BEFORE INSERT ON event_index_runs BEGIN SELECT RAISE(ABORT,'run failure'); END")
    writer = ParquetBatchWriter(settings, store)
    event = _event()
    writer.add(event, stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=1, event_id=event.event_id)
    with pytest.raises(sqlite3.IntegrityError, match='run failure'):
        writer.flush_all()
    assert store.list_manifest() == []
    assert not store.event_committed(event.event_id)
    assert not store.delivery_committed('CRYPTO_DATA', 'g', 1)
    store.close()


@pytest.mark.parametrize('commit', [False, True])
def test_process_exit_respects_run_transaction_boundary(tmp_path, commit):
    from storage.event_index_runs import ExactEventRuns
    path = tmp_path / 'crash.db'
    code = '''
import sqlite3,sys,os
from storage.event_index_runs import ensure_schema,append_runs
c=sqlite3.connect(sys.argv[1],isolation_level=None)
c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA synchronous=FULL')
ensure_schema(c);c.execute('BEGIN IMMEDIATE');append_runs(c,[(b'x'*32,7)])
if sys.argv[2]=='True':c.execute('COMMIT')
os._exit(0)
'''
    subprocess.run([sys.executable, '-c', code, str(path), str(commit)], check=True,
                   env=dict(os.environ, PYTHONPATH=os.pathsep.join((os.getcwd(), os.path.join(os.getcwd(), "src")))))
    conn = sqlite3.connect(path, isolation_level=None)
    assert ExactEventRuns(conn).lookup([b'x' * 32]) == ({b'x' * 32: 7} if commit else {})
    assert conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    conn.close()
