import asyncio
import hashlib
import sqlite3
import subprocess
import sys
import os
import time

import pytest

from runtime.bus import BusMessage, InMemoryEventBus
from storage.delivery_recovery import restore_document
from storage.event_index_runs import ExactEventRuns, append_runs, ensure_schema
from storage.parquet_store import ParquetBatchWriter
from storage.sqlite_store import RuntimeStore
from storage.storage_backend import archive_backend
from storage.storage_worker import StorageWorker
from test_crypto_delivery_recovery import ENV, archive_all, confirm, seeded
from test_storage_pipeline import _event, _settings


FLAGS = 'CRYPTO_EVENT_RUN_RETENTION_ENABLED=true\nCRYPTO_ENFORCE_LIVE_EVENT_WINDOW=true\n'


def enable(store, tmp_path):
    store.close()
    return RuntimeStore(_settings(tmp_path, ENV + FLAGS))


def test_retirement_preserves_remote_exact_recovery_and_refuses_old_replay(tmp_path, monkeypatch):
    store, maintenance, event, clock = seeded(tmp_path, monkeypatch)
    archive_all(store, maintenance)
    confirm(store)
    receipts = list(store._conn.execute('SELECT remote_key,remote_sha256 FROM crypto_delivery_recovery ORDER BY block_id'))
    store = enable(store, tmp_path)
    statements = []
    store._conn.set_trace_callback(statements.append)
    assert store.retire_crypto_event_runs()['retired_runs'] == 0  # delivery not yet safely pruned
    discoveries = [sql for sql in statements if 'SELECT' in sql.upper() and 'FROM crypto_delivery_recovery' in sql]
    assert discoveries and all('SELECT block_id FROM' in sql for sql in discoveries)
    bytecode = store._conn.execute('EXPLAIN ' + discoveries[0]).fetchall()
    root_page = store._conn.execute("SELECT rootpage FROM sqlite_master WHERE name='crypto_delivery_recovery'").fetchone()[0]
    table_cursors = {row[2] for row in bytecode if row[1] == 'OpenRead' and row[3] == root_page}
    assert not any(row[1] == 'Column' and row[2] in table_cursors for row in bytecode)
    store._conn.set_trace_callback(None)
    assert store.prune_delivery_history() == 3
    assert store.event_committed(event.event_id)
    assert store.retire_crypto_event_runs()['retired_runs'] == 2
    assert not store.event_committed(event.event_id)
    assert store._conn.execute('SELECT COUNT(*) FROM crypto_delivery_recovery').fetchone()[0] == 0
    assert store._conn.execute('SELECT COUNT(*) FROM event_index_run_payload').fetchone()[0] == 0
    assert store._conn.execute('SELECT COUNT(*) FROM event_index_retirement').fetchone()[0] == 2
    assert store.crypto_live_cutoff_ns() > event.source_ts_ns
    acks = []
    class Raw:
        async def ack(self): acks.append(True)
    bus = InMemoryEventBus()
    bus.messages.append(BusMessage(event.model_dump(mode='json'), 'crypto.trade.test', event.event_id,
                                   stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=20, _raw=Raw()))
    with pytest.raises(RuntimeError, match='historical_replay_requires_index_restore'):
        asyncio.run(StorageWorker(bus, ParquetBatchWriter(store.settings, store), domain='crypto').run(.1))
    assert not acks and not store.delivery_committed('CRYPTO_DATA', 'g', 20)
    # New live events still work after the cache contains stale positive bits.
    fresh = _event().model_copy(update={'event_id': 'fresh-after-retirement', 'source_ts_ns': clock[0]})
    bus = InMemoryEventBus()
    bus.messages.append(BusMessage(fresh.model_dump(mode='json'), 'crypto.trade.test', fresh.event_id,
                                   stream_name='CRYPTO_DATA', stream_generation='g', stream_sequence=21))
    asyncio.run(StorageWorker(bus, ParquetBatchWriter(store.settings, store), domain='crypto').run(.1))
    assert store.event_committed(fresh.event_id)
    recovery_dir = tmp_path / 'offline-replay'
    recovery_dir.mkdir()
    recovered = RuntimeStore(_settings(recovery_dir, ENV))
    restored = 0
    for number, receipt in enumerate(receipts):
        file = tmp_path / f'archived-{number}'
        archive_backend(store.settings).download_file(receipt[0], file)
        restored += restore_document(recovered, file.read_bytes(), receipt[1])['restored_deliveries']
    assert restored == 3 and recovered.event_committed(event.event_id)
    recovered.close()
    store.close()


def test_retirement_does_not_use_delivery_age_as_event_age(tmp_path, monkeypatch):
    store, maintenance, _, clock = seeded(tmp_path, monkeypatch)
    # Unknown/recent event bounds prevent retirement even with old delivery.
    store._conn.execute('UPDATE parquet_manifest SET max_ts_ns=?', (clock[0],))
    archive_all(store, maintenance)
    confirm(store)
    store = enable(store, tmp_path)
    assert store.prune_delivery_history() == 3
    assert store.retire_crypto_event_runs()['retired_runs'] == 0
    store._conn.execute('UPDATE parquet_manifest SET max_ts_ns=NULL')
    assert store.retire_crypto_event_runs()['retired_runs'] == 0
    store.close()


def test_operational_retirement_batch_can_drain_more_than_sixteen_blocks(tmp_path, monkeypatch):
    store, maintenance, event, clock = seeded(tmp_path, monkeypatch)
    now = clock[0]
    clock[0] = now - 3 * 86400 * 10**9
    for sequence in range(11, 51):
        store.commit_event_aliases([('CRYPTO_DATA', 'g', sequence, event.event_id)])
    clock[0] = now
    store.settings.env['CRYPTO_RECOVERY_ARCHIVE_BATCH'] = '64'
    for row in store.list_manifest():
        maintenance._upload(row)
    assert maintenance._archive_crypto_recovery()['uploaded'] == 42
    confirm(store, floor=50)
    store = enable(store, tmp_path)
    assert sum(store.prune_delivery_history(limit=100) for _ in range(3)) == 43
    assert store.retire_crypto_event_runs(limit=64)['retired_runs'] == 42
    store.close()


@pytest.mark.parametrize('extra', ['', 'CRYPTO_ENFORCE_LIVE_EVENT_WINDOW=true\nMARKET_EVENT_MAX_AGE_SECONDS=999999\n'])
def test_retired_replay_contract_cannot_be_disabled_or_widened(tmp_path, extra):
    store = RuntimeStore(_settings(tmp_path, ENV + FLAGS))
    store.close()
    with pytest.raises(RuntimeError, match='replay window|replay window enforcement'):
        RuntimeStore(_settings(tmp_path, ENV + extra))


def test_background_filter_rebuild_merges_new_commits_without_false_negatives(tmp_path):
    path = tmp_path / 'runs.db'
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute('PRAGMA journal_mode=WAL')
    ensure_schema(conn)
    key = lambda value: hashlib.sha256(str(value).encode()).digest()
    conn.execute('BEGIN')
    append_runs(conn, [(key(i), i+1) for i in range(100)])
    conn.execute('COMMIT')
    index = ExactEventRuns(conn, global_filter_bytes=128)
    assert len(index.lookup([key(i) for i in range(100)])) == 100
    # Simulate the already-tested retirement transaction, leaving stale bits.
    conn.execute("INSERT INTO event_index_retirement VALUES(1,100,1,1,'verified','sha')")
    conn.execute('DELETE FROM event_index_run_payload WHERE run_id=1')
    conn.execute('DELETE FROM event_index_runs WHERE run_id=1')
    conn.execute('UPDATE event_index_retention_state SET retired_entries=100')
    index.request_rebuild()
    assert index._rebuild is not None
    index._rebuild.result(timeout=5)
    # This commit occurs after the snapshot completes, before installing it.
    conn.execute('BEGIN')
    append_runs(conn, [(key(200), 201)])
    conn.execute('COMMIT')
    assert index.lookup([key(0), key(200)]) == {key(200): 201}
    assert index.loaded_entries == 1
    assert index.rebuild_error is None
    index.close()
    conn.close()


@pytest.mark.parametrize('commit', [False, True])
def test_process_exit_during_retirement_is_atomic(tmp_path, monkeypatch, commit):
    store, maintenance, event, _ = seeded(tmp_path, monkeypatch)
    archive_all(store, maintenance)
    confirm(store)
    store = enable(store, tmp_path)
    assert store.prune_delivery_history() == 3
    store.close()
    code = '''
import os,sys,time
from pathlib import Path
from runtime.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore
from storage.delivery_recovery import retire_runs
s=RuntimeStore(RuntimeSettings.from_env(Path(sys.argv[1])))
s._conn.execute('BEGIN IMMEDIATE')
assert retire_runs(s._conn,now_ns=time.time_ns(),max_age_seconds=172800,limit=4)['retired_runs']==2
if sys.argv[2]=='True':s._conn.execute('COMMIT')
os._exit(0)
'''
    subprocess.run([sys.executable, '-c', code, str(tmp_path/'.env'), str(commit)], check=True,
                   env=dict(os.environ, PYTHONPATH=os.pathsep.join((os.getcwd(), os.path.join(os.getcwd(), "src")))))
    reopened = RuntimeStore(_settings(tmp_path, ENV+FLAGS))
    assert reopened.event_committed(event.event_id) is (not commit)
    assert reopened._conn.execute('SELECT COUNT(*) FROM crypto_delivery_recovery').fetchone()[0] == (0 if commit else 2)
    assert reopened._conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    reopened.close()
