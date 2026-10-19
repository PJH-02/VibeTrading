"""Move the authoritative crypto state to vda; keep Parquet/NATS on vdb."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.request

ROOT = Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
OLD = Path('/opt/vibetrading/releases/crypto-relief-20261002')
DATA = Path('/srv/vibetrading-data/runtime_data')
STATE = Path('/srv/vibetrading-crypto-state')
CRYPTO = ['crypto-storage', 'crypto-maintenance', 'crypto-collector-binance', 'crypto-collector-bitget', 'crypto-collector-bybit']
ENV = Path('/etc/vibetrading/compose.env')


def run(*args):
    print(json.dumps({'command': list(args)}), flush=True)
    subprocess.run(args, check=True)


def compose(file, *args, crypto=False):
    cmd = ['docker', 'compose', '--project-name', 'vibetrading', '--env-file', str(ENV)]
    if crypto:
        cmd += ['--env-file', str(ROOT / 'deploy/release.env')]
    run(*cmd, '-f', str(file), *args)


def tree(path):
    result = {}
    for p in sorted(path.rglob('*')):
        assert not p.is_symlink(), str(p)
        if not p.is_file():
            continue
        h = hashlib.sha256()
        with p.open('rb') as f:
            for chunk in iter(lambda: f.read(1024**2), b''):
                h.update(chunk)
        st = p.stat()
        result[str(p.relative_to(path))] = [st.st_size, h.hexdigest(), st.st_uid, st.st_gid, st.st_mode]
    return result


def jsz():
    with urllib.request.urlopen('http://127.0.0.1:8222/jsz?streams=true&consumers=true&config=true', timeout=10) as f:
        return json.load(f)


def invariants(v):
    return {s['name']: {'config': s.get('config'), 'created': s.get('created'),
        'state': {k: s['state'][k] for k in ('messages', 'bytes', 'first_seq', 'last_seq')},
        'consumers': {c['name']: {'config': c['config'], 'created': c['created'],
            'ack_floor': {k: c['ack_floor'][k] for k in ('consumer_seq', 'stream_seq')},
            'delivered': {k: c['delivered'][k] for k in ('consumer_seq', 'stream_seq')},
            'num_pending': c['num_pending'], 'num_ack_pending': c['num_ack_pending']}
            for c in s.get('consumer_detail', [])}}
        for a in v['account_details'] for s in a.get('stream_detail', [])}


assert not STATE.exists()
assert shutil.disk_usage('/').free > (DATA / 'crypto_state/vibetrading.sqlite3').stat().st_size + 1280 * 1024**2
assert STATE.parent.stat().st_dev != DATA.stat().st_dev
before = json.loads((OLD / 'before.json').read_text())
maintenance = json.loads(subprocess.check_output(['docker', 'inspect', 'vibetrading-crypto-maintenance-1']))[0]
secret_mounts = {m['Destination']: m['Source'] for m in maintenance['Mounts'] if m['Destination'].startswith('/run/secrets/')}
assert len(secret_mounts) == 2
started = time.time_ns()
run('docker', 'stop', '--timeout', '90', *['vibetrading-' + n + '-1' for n in
    ['crypto-collector-binance', 'crypto-collector-bitget', 'crypto-collector-bybit', 'crypto-maintenance', 'collectors', 'maintenance', 'telegram']])
run('docker', 'stop', '--timeout', '90', 'vibetrading-crypto-storage-1', 'vibetrading-storage-1')
frozen = jsz()
(ROOT / 'nats-frozen.json').write_text(json.dumps(frozen, indent=2))
run('docker', 'stop', '--timeout', '30', 'vibetrading-nats-1')
original = tree(DATA / 'crypto_state')
STATE.mkdir(mode=0o750)
run('cp', '-a', '--', str(DATA / 'crypto_state') + '/.', str(STATE) + '/')
assert tree(STATE) == original, 'crypto state copy hash/ownership mismatch'
db = sqlite3.connect(f'file:{STATE}/vibetrading.sqlite3?mode=ro', uri=True)
generation = db.execute("SELECT value FROM runtime_metadata WHERE key='collection_generation'").fetchone()[0]
assert generation == before['catalogue']['collection_generation']
db.close()
(STATE / 'migration-offline-proof.json').write_text(json.dumps({'all_crypto_writers_stopped': True,
    'generation': generation, 'state_copy_exact': True, 'source_files': original}, indent=2))
os.chown(STATE / 'migration-offline-proof.json', 10001, 10001)
(ROOT / 'state-copy.json').write_text(json.dumps({'files': original, 'source_device': DATA.stat().st_dev,
    'destination_device': STATE.stat().st_dev, 'bytes': sum(v[0] for v in original.values())}, indent=2))
nats_source = Path('/srv/vibetrading-nats')
nats_target = Path('/srv/vibetrading-data/nats')
nats_backup = Path('/srv/vibetrading-data/nats-before-db-split-20261002')
assert nats_source.resolve(strict=True) == nats_source and nats_target.resolve(strict=True) == nats_target
assert not nats_backup.exists()
nats_target.rename(nats_backup)
nats_original = tree(nats_source)
nats_target.mkdir()
run('cp', '-a', '--', str(nats_source) + '/.', str(nats_target) + '/')
assert tree(nats_target) == nats_original, 'NATS return copy mismatch'
(ROOT / 'nats-copy.json').write_text(json.dumps({'files': nats_original, 'bytes': sum(v[0] for v in nats_original.values())}, indent=2))
for parent in (STATE, nats_target):
    for p in parent.rglob('*'):
        if p.is_file():
            with p.open('rb') as f:
                os.fsync(f.fileno())
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
release = json.loads((ROOT / '.deploy-release.json').read_text())
cmd = ['docker', 'run', '--rm', '--name', 'crypto-window-recovery', '--network', 'vibetrading_default',
       '--read-only', '--memory', '512m', '--tmpfs', '/tmp:rw,size=67108864', '--env-file', '/etc/vibetrading/app.env',
       '-v', f'{DATA}:/data', '-v', f'{STATE}:/data/crypto_state', '-v', '/tmp/crypto_relief_recover_window.py:/recover.py:ro']
for dest, source in secret_mounts.items():
    cmd += ['-v', f'{source}:{dest}:ro']
values = {'VIBETRADING_DATA_ROOT': '/data', 'VIBETRADING_STATE_DIR': '/data/crypto_state',
    'PARQUET_DATA_ROOT': '/data/crypto_market_data', 'DELIVERY_LEDGER_FORMAT': 'compact',
    'CRYPTO_EVENT_INDEX_FORMAT': 'immutable-runs-v1', 'CRYPTO_EVENT_RUN_RETENTION_ENABLED': 'true',
    'CRYPTO_ENFORCE_LIVE_EVENT_WINDOW': 'true', 'MARKET_EVENT_MAX_AGE_SECONDS': '21600', 'SQLITE_CACHE_KIB': '131072',
    'ARCHIVE_PREFIX': 'vibetrading/crypto', 'ARCHIVE_ACCESS_KEY_FILE': '/run/secrets/archive_access_key',
    'ARCHIVE_SECRET_KEY_FILE': '/run/secrets/archive_secret_key'}
for key, value in values.items():
    cmd += ['-e', f'{key}={value}']
run(*cmd, '--entrypoint', 'python', release['images']['crypto']['id'], '/recover.py')
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
    raise RuntimeError('NATS state equivalence was not confirmed')
(ROOT / 'nats-restored.json').write_text(json.dumps(restored, indent=2))
compose(ROOT / 'deploy/crypto-r2.yml', 'up', '-d', '--no-deps', '--no-build', '--force-recreate', 'crypto-storage', crypto=True)
for _ in range(600):
    try:
        h = json.loads((STATE / 'health/crypto-storage.json').read_text())
        if h.get('ready') and h.get('build', {}).get('image_id') == release['images']['crypto']['id']:
            break
    except (OSError, ValueError):
        pass
    time.sleep(1)
else:
    raise RuntimeError('new crypto storage never became ready')
run('docker', 'start', 'vibetrading-storage-1', 'vibetrading-maintenance-1', 'vibetrading-collectors-1', 'vibetrading-telegram-1')
compose(ROOT / 'deploy/crypto-r2.yml', 'up', '-d', '--no-deps', '--no-build', '--force-recreate', *CRYPTO[1:], crypto=True)
for name, expected in before['protected_exports'].items():
    st = Path(name).stat()
    assert {'inode': st.st_ino, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns} == expected
assert (DATA / 'state/vibetrading.sqlite3').stat().st_ino == before['market_db_inode']
result = {'started_ns': started, 'completed_ns': time.time_ns(), 'exact_state_copy': True, 'nats_invariants_preserved': True,
    'source_device': DATA.stat().st_dev, 'state_device': STATE.stat().st_dev, 'protected_exports_preserved': True,
    'market_db_inode_preserved': True, 'new_storage_ready': True, 'old_state_copy_retained': str(DATA / 'crypto_state')}
(ROOT / 'cutover.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)
