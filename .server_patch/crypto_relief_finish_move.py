"""Resume the verified disk copy after the offline recovery helper finishes."""
import ast
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

ROOT = Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
OLD = Path('/opt/vibetrading/releases/crypto-relief-20261002')
DATA = Path('/srv/vibetrading-data/runtime_data')
STATE = Path('/srv/vibetrading-crypto-state')
ENV = Path('/etc/vibetrading/compose.env')
CRYPTO = ['crypto-storage', 'crypto-maintenance', 'crypto-collector-binance', 'crypto-collector-bitget', 'crypto-collector-bybit']
# Reuse only the pure helper definitions from our saved cutover script, never
# its stop/copy/recovery actions. The completed exact copy is not repeated.
parsed = ast.parse((ROOT / 'crypto_relief_move_db.py').read_text())
definitions = [n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name in ('run', 'compose', 'jsz', 'invariants', 'tree')]
exec(compile(ast.Module(body=definitions, type_ignores=[]), '<cutover-helpers>', 'exec'))
recovery = json.loads((STATE / 'replay-window-recovery.json').read_text())
assert recovery['new_window_seconds'] == 43200
frozen = json.loads((ROOT / 'nats-frozen.json').read_text())
before = json.loads((OLD / 'before.json').read_text())
release = json.loads((ROOT / '.deploy-release.json').read_text())
lines = ENV.read_text().splitlines()
(ROOT / 'compose.env.before').write_text('\n'.join(lines) + '\n')
(ROOT / 'compose.env.before').chmod(0o600)
lines = ['VIBETRADING_HOST_NATS_ROOT=/srv/vibetrading-data/nats' if v.startswith('VIBETRADING_HOST_NATS_ROOT=') else v for v in lines]
lines = [v for v in lines if not v.startswith('VIBETRADING_HOST_CRYPTO_STATE_ROOT=')]
lines.append('VIBETRADING_HOST_CRYPTO_STATE_ROOT=/srv/vibetrading-crypto-state')
ENV.write_text('\n'.join(lines) + '\n')
ENV.chmod(0o600)
compose(ROOT / 'deploy/nats-relief.yml', 'up', '-d', '--no-deps', '--no-build', '--force-recreate', 'nats')
for _ in range(60):
    try:
        restored = jsz()
        if invariants(restored) == invariants(frozen):
            break
    except OSError:
        pass
    time.sleep(1)
else:
    raise RuntimeError('NATS invariants changed')
(ROOT / 'nats-restored.json').write_text(json.dumps(restored, indent=2))
compose(ROOT / 'deploy/crypto-r2.yml', 'up', '-d', '--no-deps', '--no-build', '--force-recreate', 'crypto-storage', crypto=True)
for _ in range(600):
    h = json.loads((STATE / 'health/crypto-storage.json').read_text())
    if h.get('ready') and h.get('build', {}).get('image_id') == release['images']['crypto']['id']:
        break
    time.sleep(1)
else:
    raise RuntimeError('crypto storage did not become ready')
# The old, inactive DB is now a verified duplicate of the migrated state.
# Only its three SQLite files are removed; Market/SK paths are not targets.
source = DATA / 'crypto_state'
assert source.resolve(strict=True) == source and source.parent == DATA
copy = json.loads((ROOT / 'state-copy.json').read_text())['files']
db = sqlite3.connect(f'file:{source}/vibetrading.sqlite3?mode=ro', uri=True)
assert db.execute("SELECT value FROM runtime_metadata WHERE key='collection_generation'").fetchone()[0] == recovery['generation']
providers = {r[0] for r in db.execute('SELECT DISTINCT provider FROM parquet_manifest')}
assert providers <= {'binance-perp', 'bybit-perp', 'bitget-perp'}
assert db.execute('SELECT COUNT(*) FROM order_intents').fetchone()[0] == 0
db.close()
hsh = hashlib.sha256()
with (source / 'vibetrading.sqlite3').open('rb') as f:
    for chunk in iter(lambda: f.read(1024**2), b''):
        hsh.update(chunk)
assert hsh.hexdigest() == copy['vibetrading.sqlite3'][1]
assert h['event_index']['validated_entries'] >= recovery['exact_entries_after']
removed = []
for name in ('vibetrading.sqlite3', 'vibetrading.sqlite3-wal', 'vibetrading.sqlite3-shm'):
    p = source / name
    if p.exists():
        assert p.parent == source and not p.is_symlink()
        removed.append({'path': str(p), 'bytes': p.stat().st_size})
        p.unlink()
run('docker', 'start', 'vibetrading-storage-1', 'vibetrading-maintenance-1', 'vibetrading-collectors-1', 'vibetrading-telegram-1')
compose(ROOT / 'deploy/crypto-r2.yml', 'up', '-d', '--no-deps', '--no-build', '--force-recreate', *CRYPTO[1:], crypto=True)
for name, expected in before['protected_exports'].items():
    st = Path(name).stat()
    assert {'inode': st.st_ino, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns} == expected
assert (DATA / 'state/vibetrading.sqlite3').stat().st_ino == before['market_db_inode']
result = {'completed_ns': time.time_ns(), 'exact_state_copy': True, 'nats_invariants_preserved': True,
    'source_device': DATA.stat().st_dev, 'state_device': STATE.stat().st_dev, 'protected_exports_preserved': True,
    'market_db_inode_preserved': True, 'new_storage_ready': True, 'inactive_duplicate_files_removed': removed,
    'data_originals_deleted': False, 'replay_window_recovery': recovery}
(ROOT / 'cutover.json').write_text(json.dumps(result, indent=2))
print(json.dumps({k: v for k, v in result.items() if k != 'replay_window_recovery'}), flush=True)
