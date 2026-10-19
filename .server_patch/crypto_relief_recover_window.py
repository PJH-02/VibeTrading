"""Recover every retired exact record before expanding the live window."""
import hashlib
import json
from pathlib import Path
import time
import sys
sys.path.insert(0, '/app')

from vibetrading.delivery_recovery import read_document, restore_document
from vibetrading.settings import get_settings
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.storage_backend import archive_backend

settings = get_settings()
store = RuntimeStore(settings)
backend = archive_backend(settings)
proof = json.loads((settings.state_dir / 'migration-offline-proof.json').read_text())
assert proof['all_crypto_writers_stopped']
generation = store.collection_generation()
assert generation == proof['generation']
before = store._conn.execute('SELECT COUNT(*),SUM(entry_count) FROM event_index_runs').fetchone()
marker = store._conn.execute("SELECT value FROM runtime_metadata WHERE key='window_recovery_baseline_entries'").fetchone()
baseline = int(marker[0]) if marker else int(before[1])
if marker is None:
    store._write("INSERT INTO runtime_metadata VALUES('window_recovery_baseline_entries',?,?)", (str(baseline), time.time_ns()))
state = store._conn.execute('SELECT retired_entries,retired_event_cutoff_ns FROM event_index_retention_state').fetchone()
receipts = store._conn.execute('SELECT run_id,entry_count,recovery_key,recovery_sha256 FROM event_index_retirement ORDER BY run_id').fetchall()
assert sum(r['entry_count'] for r in receipts) == state['retired_entries'], 'missing retirement receipt'
assert len(receipts) < 4096, 'recovery must remain bounded'
restored = 0
documents = []
scratch = Path('/tmp/retired-window')
scratch.mkdir()
try:
    for index, r in enumerate(receipts):
        path = scratch / f'{index}.zlib'
        backend.download_file(r['recovery_key'], path)
        packed = path.read_bytes()
        doc, rows = read_document(packed, r['recovery_sha256'])
        assert doc['catalogue_generation'] == generation and doc['run_id'] == r['run_id']
        exact = {(row[2], row[3]) for row in rows}
        assert len(exact) == r['entry_count'], 'retirement exact coverage differs'
        result = restore_document(store, packed, r['recovery_sha256'])
        restored += result['restored_deliveries']
        documents.append({'run_id': r['run_id'], 'entries': r['entry_count'], 'rows': len(rows),
                          'sha256': r['recovery_sha256'], 'key': r['recovery_key'], **result})
        path.unlink()
    after = store._conn.execute('SELECT COUNT(*),SUM(entry_count) FROM event_index_runs').fetchone()
    assert after[1] == baseline + state['retired_entries'], 'full retired index coverage was not recovered'
    with store.transaction() as conn:
        assert conn.execute("SELECT value FROM runtime_metadata WHERE key='crypto_live_replay_max_age_seconds'").fetchone()[0] == '21600'
        assert tuple(conn.execute('SELECT retired_entries,retired_event_cutoff_ns FROM event_index_retention_state').fetchone()) == tuple(state)
        conn.execute("UPDATE runtime_metadata SET value='43200',updated_ts_ns=? WHERE key='crypto_live_replay_max_age_seconds'", (time.time_ns(),))
        # Every retirement receipt has been recovered, so no missing earlier
        # exact coverage remains. Preserve the cumulative retirement history.
        conn.execute('UPDATE event_index_retention_state SET retired_event_cutoff_ns=0 WHERE singleton=1')
    result = {'generation': generation, 'old_window_seconds': 21600, 'new_window_seconds': 43200,
              'exact_entries_before': baseline, 'exact_entries_after': after[1],
              'retired_entries_recovered': state['retired_entries'], 'restored_deliveries': restored,
              'documents': documents, 'nats_modified': False, 'data_files_deleted': False}
    (settings.state_dir / 'replay-window-recovery.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'documents'}), flush=True)
finally:
    store.close()
    backend.close()
