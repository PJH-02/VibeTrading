"""Quiesce writers, verify a cross-disk NATS copy, then deploy crypto only."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

ROOT = Path('/opt/vibetrading/releases/crypto-relief-20261002')
DEPLOY = ROOT / 'deploy'
DATA = Path('/srv/vibetrading-data/runtime_data')
SOURCE = Path('/srv/vibetrading-data/nats')
TARGET = Path('/srv/vibetrading-nats')
ENV = Path('/etc/vibetrading/compose.env')
CRYPTO = ['crypto-storage', 'crypto-maintenance', 'crypto-collector-binance', 'crypto-collector-bitget', 'crypto-collector-bybit']


def run(*args):
    print(json.dumps({'command': list(args)}), flush=True)
    subprocess.run(args, check=True)


def compose(file, *args, crypto=False):
    cmd = ['docker', 'compose', '--project-name', 'vibetrading', '--env-file', str(ENV)]
    if crypto:
        cmd += ['--env-file', str(DEPLOY / 'release.env')]
    run(*cmd, '-f', str(file), *args)


def jsz():
    with urllib.request.urlopen('http://127.0.0.1:8222/jsz?streams=true&consumers=true&config=true', timeout=10) as f:
        return json.load(f)


def invariant(value):
    streams = {}
    for account in value.get('account_details', []):
        for stream in account.get('stream_detail', []):
            state = stream['state']
            streams[stream['name']] = {'state': {k: state[k] for k in ('messages', 'bytes', 'first_seq', 'last_seq')},
                'created': stream.get('created'), 'config': stream.get('config'),
                # NATS does not persist last_active; it disappears at restart.
                # Compare durable sequences, configuration and pending counts.
                'consumers': {c['name']: {'created': c['created'], 'config': c['config'],
                    'ack_floor': {k: c['ack_floor'][k] for k in ('consumer_seq', 'stream_seq')},
                    'delivered': {k: c['delivered'][k] for k in ('consumer_seq', 'stream_seq')},
                    'num_ack_pending': c['num_ack_pending'], 'num_pending': c['num_pending']}
                    for c in stream.get('consumer_detail', [])}}
    assert streams, 'empty NATS inventory'
    return streams


def tree(path):
    entries = {}
    for p in sorted(path.rglob('*')):
        assert not p.is_symlink(), ('unexpected symlink', str(p))
        if not p.is_file():
            continue
        h = hashlib.sha256()
        with p.open('rb') as f:
            for chunk in iter(lambda: f.read(1024**2), b''):
                h.update(chunk)
        st = p.stat()
        entries[str(p.relative_to(path))] = [st.st_size, h.hexdigest(), st.st_uid, st.st_gid, st.st_mode]
    return entries


def wait_nats():
    for _ in range(60):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8222/healthz', timeout=2) as f:
                if f.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError('NATS did not become ready')


def wait_storage(started):
    for _ in range(600):
        try:
            health = json.loads((DATA / 'crypto_state/health/crypto-storage.json').read_text())
            if int(health.get('ts_ns', 0)) > started and health.get('ready'):
                return health
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('crypto storage not ready')


assert SOURCE.resolve(strict=True) == SOURCE and SOURCE.is_dir()
if TARGET.exists():
    # The first attempt rolled back solely because last_active is transient.
    # Retain that verified copy too; never overwrite an unknown directory.
    retry_backup = Path('/srv/vibetrading-nats-attempt-1')
    assert (ROOT / 'nats-copy.json').exists() and not retry_backup.exists()
    assert TARGET.resolve(strict=True) == TARGET and TARGET.stat().st_dev == Path('/srv').stat().st_dev
    mounted = json.loads(subprocess.check_output(['docker', 'inspect', 'vibetrading-nats-1']))[0]
    assert next(m['Source'] for m in mounted['Mounts'] if m['Destination'] == '/data') == str(SOURCE)
    TARGET.rename(retry_backup)
assert not TARGET.exists(), 'target must be new and empty'
assert SOURCE.stat().st_dev != Path('/srv').stat().st_dev, 'NATS destination is not a second disk'
assert shutil.disk_usage('/').free > 1342177280 + 1024**3, 'OS reserve insufficient'
before = json.loads((ROOT / 'before.json').read_text())
for name, expected in before['protected_exports'].items():
    st = Path(name).stat()
    assert {'inode': st.st_ino, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns} == expected
old_env = ENV.read_bytes()
(ROOT / 'compose.env.before').write_bytes(old_env)
(ROOT / 'compose.env.before').chmod(0o600)
inspect = json.loads(subprocess.check_output(['docker', 'inspect', 'vibetrading-nats-1']))[0]
assert next(m['Source'] for m in inspect['Mounts'] if m['Destination'] == '/data') == str(SOURCE)
started = time.time_ns()
quiesced = False
migrated = False
try:
    run('docker', 'stop', '--time', '90', *['vibetrading-' + n + '-1' for n in
        ['crypto-collector-binance', 'crypto-collector-bitget', 'crypto-collector-bybit', 'collectors', 'crypto-maintenance', 'maintenance', 'telegram']])
    quiesced = True
    run('docker', 'stop', '--time', '90', 'vibetrading-crypto-storage-1', 'vibetrading-storage-1')
    frozen = jsz()
    (ROOT / 'nats-frozen.json').write_text(json.dumps(frozen, indent=2))
    run('docker', 'stop', '--time', '30', 'vibetrading-nats-1')
    original = tree(SOURCE)
    TARGET.mkdir(mode=0o750)
    run('cp', '-a', '--', str(SOURCE) + '/.', str(TARGET) + '/')
    assert tree(TARGET) == original, 'NATS copy hash/ownership mismatch'
    for p in TARGET.rglob('*'):
        if p.is_file():
            with p.open('rb') as f:
                os.fsync(f.fileno())
    for p in [TARGET, *[p for p in TARGET.rglob('*') if p.is_dir()]]:
        fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    (ROOT / 'nats-copy.json').write_text(json.dumps({'files': len(original), 'bytes': sum(v[0] for v in original.values()),
        'entries': original, 'source_device': SOURCE.stat().st_dev, 'target_device': TARGET.stat().st_dev}, indent=2))
    lines = old_env.decode().splitlines()
    assert sum(line.startswith('VIBETRADING_HOST_NATS_ROOT=') for line in lines) == 1
    ENV.write_text('\n'.join('VIBETRADING_HOST_NATS_ROOT=/srv/vibetrading-nats' if line.startswith('VIBETRADING_HOST_NATS_ROOT=') else line for line in lines) + '\n')
    ENV.chmod(0o600)
    compose(DEPLOY / 'nats-relief.yml', 'up', '-d', '--no-deps', '--no-build', '--force-recreate', 'nats')
    wait_nats()
    restored = jsz()
    (ROOT / 'nats-restored.json').write_text(json.dumps(restored, indent=2))
    assert invariant(frozen) == invariant(restored), 'NATS stream or consumer invariant changed'
    migrated = True
    run('python3', str(ROOT / 'crypto_relief_shrink_window.py'))
    compose(DEPLOY / 'crypto-r2.yml', 'up', '-d', '--no-deps', '--no-build', '--force-recreate', 'crypto-storage', crypto=True)
    storage = wait_storage(time.time_ns() - 5 * 10**9)
    run('docker', 'start', 'vibetrading-storage-1', 'vibetrading-maintenance-1', 'vibetrading-collectors-1', 'vibetrading-telegram-1')
    compose(DEPLOY / 'crypto-r2.yml', 'up', '-d', '--no-deps', '--no-build', '--force-recreate', *CRYPTO[1:], crypto=True)
    exports_ok = True
    for name, expected in before['protected_exports'].items():
        st = Path(name).stat()
        assert {'inode': st.st_ino, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns} == expected
    assert (DATA / 'state/vibetrading.sqlite3').stat().st_ino == before['market_db_inode']
    result = {'started_ns': started, 'completed_ns': time.time_ns(), 'nats_exact_copy': True,
              'stream_consumer_invariants_preserved': True, 'copied_files': len(original),
              'copied_bytes': sum(v[0] for v in original.values()), 'protected_exports_preserved': exports_ok,
              'market_db_inode_preserved': True, 'new_storage_ready': storage.get('ready'),
              'rollback_store_retained': str(SOURCE)}
    (ROOT / 'cutover.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
except Exception:
    # Before stream equivalence is proven, always restore the original NATS
    # mount. The stopped original store is kept intact throughout the cutover.
    if quiesced and not migrated:
        ENV.write_bytes(old_env)
        ENV.chmod(0o600)
        compose(Path('/root/vibetrading/docker-compose.yml'), 'up', '-d', '--no-deps', '--no-build', '--force-recreate', 'nats')
        wait_nats()
        run('docker', 'start', *['vibetrading-' + n + '-1' for n in CRYPTO + ['storage', 'maintenance', 'collectors', 'telegram']])
    raise
