import hashlib
import sqlite3
import pytest
from storage.event_index_runs import ExactEventRuns, append_runs, ensure_schema
from vibetrading.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore

def test_larger_negative_filter_avoids_scans_without_changing_exact_results(tmp_path):
    conn=sqlite3.connect(tmp_path/'runs.db',isolation_level=None)
    ensure_schema(conn)
    digest=lambda n:hashlib.sha256(str(n).encode()).digest()
    known={digest(n):n+1 for n in range(400)}
    conn.execute('BEGIN');append_runs(conn,known.items());conn.execute('COMMIT')
    small=ExactEventRuns(conn,global_filter_bytes=128)
    large=ExactEventRuns(conn,global_filter_bytes=8192)
    for n in range(400,1000):
        assert small.lookup([digest(n)])==large.lookup([digest(n)])=={}
    assert small.directory_scans>400
    assert large.directory_scans<10
    assert small.lookup(known)==large.lookup(known)==known
    conn.close()

def make_settings(tmp_path,budget):
    path=tmp_path/'.env';path.write_text(f'VIBETRADING_DATA_ROOT={tmp_path}\nDELIVERY_LEDGER_FORMAT=compact\nCRYPTO_EVENT_INDEX_FORMAT=immutable-runs-v1\nCRYPTO_EVENT_GLOBAL_FILTER_BYTES={budget}\n',encoding='utf-8')
    return RuntimeSettings.from_env(path)

def test_runtime_store_respects_explicit_bounded_filter_capacity(tmp_path):
    store=RuntimeStore(make_settings(tmp_path,8192))
    store.prepare_event_index()
    assert store.event_index_progress()['global_filter_bytes']==8192
    store.close()

@pytest.mark.parametrize('budget',[0,1000,256*1024*1024])
def test_invalid_or_excessive_filter_memory_is_rejected(tmp_path,budget):
    with pytest.raises(ValueError,match='CRYPTO_EVENT_GLOBAL_FILTER_BYTES'):
        RuntimeStore(make_settings(tmp_path,budget))
