"""Offline monotonic policy shrink; preserves the existing exact index.

Expanding a retired replay window still requires full index recovery. Shrinking
it uses the already larger coverage and changes no event/delivery/receipt rows.
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import time

root = Path('/opt/vibetrading/releases/crypto-relief-20261002')
path = Path('/srv/vibetrading-data/runtime_data/crypto_state/vibetrading.sqlite3')
names = ['crypto-storage', 'crypto-maintenance', 'crypto-collector-binance', 'crypto-collector-bybit', 'crypto-collector-bitget']
containers = json.loads(subprocess.check_output(['docker', 'inspect', *['vibetrading-' + n + '-1' for n in names]]))
assert all(not c['State']['Running'] and not c['State']['Restarting'] for c in containers), 'all crypto writers must be stopped'
before = json.loads((root / 'before.json').read_text())
db = sqlite3.connect(f'file:{path}?mode=rw', uri=True, isolation_level=None)
db.execute('PRAGMA synchronous=FULL')
db.execute('BEGIN EXCLUSIVE')
try:
    metadata = dict(db.execute('SELECT key,value FROM runtime_metadata'))
    assert metadata['collection_generation'] == before['catalogue']['collection_generation']
    assert metadata['event_index_format'] == 'immutable-runs-v1'
    prior = int(metadata['crypto_live_replay_max_age_seconds'])
    assert prior == 172800 and 60 <= 21600 < prior, 'only the expected monotonic shrink is authorized'
    coverage = db.execute('SELECT COUNT(*),SUM(entry_count) FROM event_index_runs').fetchone()
    retention = db.execute('SELECT * FROM event_index_retention_state').fetchone()
    changed = db.execute("UPDATE runtime_metadata SET value='21600',updated_ts_ns=? WHERE key='crypto_live_replay_max_age_seconds' AND value='172800'", (time.time_ns(),)).rowcount
    assert changed == 1
    assert db.execute('SELECT COUNT(*),SUM(entry_count) FROM event_index_runs').fetchone() == coverage
    assert db.execute('SELECT * FROM event_index_retention_state').fetchone() == retention
    db.commit()
except BaseException:
    db.rollback()
    raise
finally:
    db.close()
result = {'old_window_seconds': prior, 'new_window_seconds': 21600, 'collection_generation': metadata['collection_generation'],
          'preserved_exact_index_runs_entries': coverage, 'preserved_retirement_state': retention,
          'changed_metadata_rows': changed, 'all_crypto_writers_stopped': True,
          'reason': 'monotonic shrink within existing larger exact coverage; no event/delivery/receipt changes'}
(root / 'replay-window-shrink.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)
