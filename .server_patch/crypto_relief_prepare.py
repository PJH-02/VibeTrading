"""Build a minimal immutable patch from the deployed crypto release."""
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import urllib.request

BASE = Path('/opt/vibetrading/releases/crypto-minimal-fix-20261001')
ROOT = Path('/opt/vibetrading/releases/crypto-relief-20261002')
PATCH = Path('/tmp/crypto-relief-patch')
DATA = Path('/srv/vibetrading-data/runtime_data')


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def jsz():
    with urllib.request.urlopen('http://127.0.0.1:8222/jsz?streams=true&consumers=true&config=true', timeout=10) as f:
        return json.load(f)


ROOT.mkdir()
old = json.loads((BASE / '.deploy-release.json').read_text())
base_id = json.loads(run('docker', 'inspect', 'vibetrading-crypto-maintenance-1'))[0]['Image']
assert base_id == old['images']['crypto']['id'], 'deployed image differs from source release'
runtime_files = dict(old['runtime_files'])
for name, expected in runtime_files.items():
    assert digest(BASE / name) == expected, ('source mismatch', name)
for name in ('archive.py', 'runtime.py'):
    target = ROOT / 'vibetrading' / name
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(PATCH / name, target)
    runtime_files['vibetrading/' + name] = digest(target)
deploy = ROOT / 'deploy'
deploy.mkdir()
compose = json.loads((BASE / 'deploy/crypto-r2.yml').read_text())
for service in compose['services'].values():
    service['environment'].update(MARKET_EVENT_MAX_AGE_SECONDS='21600',
                                  NATS_FILE_STORE_MAX_BYTES='1342177280', STORAGE_RESERVE_GB='3')
    service['restart'] = 'unless-stopped'
env = compose['services']['crypto-maintenance']['environment']
env.update(ARCHIVE_IDLE_SECONDS='30', ARCHIVE_UPLOAD_BATCH='32', ARCHIVE_RECLAIM_BATCH='256',
           ARCHIVE_RELIEF_MAX_IO_PRESSURE='90', ARCHIVE_BACKUP_MAX_IO_PRESSURE='5',
           ARCHIVE_ORPHAN_MAX_IO_PRESSURE='5', ARCHIVE_DELIVERY_PRUNE_MAX_IO_PRESSURE='70',
           ARCHIVE_DELIVERY_HISTORY_RETENTION_SECONDS='21600', ARCHIVE_DELIVERY_HISTORY_PRUNE_LIMIT='32768',
           CRYPTO_RECOVERY_ARCHIVE_BATCH='8', CRYPTO_EVENT_RUN_RETIRE_BATCH='8')
(deploy / 'crypto-r2.yml').write_text(json.dumps(compose, indent=2) + '\n')
config_hash = digest(deploy / 'crypto-r2.yml')
source_hash = hashlib.sha256(json.dumps(runtime_files, sort_keys=True).encode()).hexdigest()
revision = 'r2-relief-' + hashlib.sha256((source_hash + config_hash + base_id).encode()).hexdigest()[:16]
base_tag = 'vibetrading-crypto-base:' + base_id.split(':')[1][:20]
run('docker', 'tag', base_id, base_tag)
dockerfile = f'''FROM {base_tag}
USER root
COPY --chown=10001:10001 vibetrading/archive.py /app/vibetrading/archive.py
COPY --chown=10001:10001 vibetrading/runtime.py /app/vibetrading/runtime.py
LABEL org.opencontainers.image.revision={revision} org.vibetrading.config-hash={config_hash}
ENV VIBETRADING_BUILD_REVISION={revision} VIBETRADING_BUILD_CONFIG_HASH={config_hash} PYTHONDONTWRITEBYTECODE=1
USER vibetrading
'''
(ROOT / 'Dockerfile').write_text(dockerfile)
tag = 'vibetrading-crypto:' + revision
subprocess.run(['docker', 'build', '--network=none', '--pull=false', '-t', tag, str(ROOT)], check=True)
image = json.loads(run('docker', 'image', 'inspect', tag))[0]
assert json.loads(run('docker', 'image', 'inspect', base_tag))[0]['Id'] == base_id
image_id = image['Id']
expected = json.dumps(runtime_files)
verify = 'import hashlib,json,pathlib; expected=json.loads(' + repr(expected) + '); '
verify += 'assert all(hashlib.sha256((pathlib.Path("/app")/p).read_bytes()).hexdigest()==h for p,h in expected.items()); print("all_runtime_files_verified")'
print(run('docker', 'run', '--rm', '--read-only', '--network=none', '--entrypoint', 'python', image_id, '-c', verify))
release = dict(old, build_revision=revision, config_hash=config_hash, source_hash=source_hash,
               base_image_id=base_id, images={'crypto': {'tag': tag, 'id': image_id}}, runtime_files=runtime_files)
(ROOT / '.deploy-release.json').write_text(json.dumps(release, indent=2) + '\n')
(deploy / 'crypto-release.env').write_text(f'VIBETRADING_BUILD_REVISION={revision}\nVIBETRADING_BUILD_CONFIG_HASH={config_hash}\nVIBETRADING_IMAGE_ID={image_id}\n')
(deploy / 'release.env').write_text(f'VIBETRADING_CRYPTO_IMAGE={image_id}\nVIBETRADING_CRYPTO_RELEASE_ENV_FILE={deploy}/crypto-release.env\n')
nats = json.loads(run('docker', 'inspect', 'vibetrading-nats-1'))[0]
conf = next(Path(m['Source']) for m in nats['Mounts'] if m['Destination'] == '/etc/nats/nats.conf')
assert 'max_file_store: 1280MiB' in conf.read_text()
shutil.copy2(conf, deploy / 'nats.conf')
nats_compose = {'services': {'nats': {'image': nats['Image'], 'command': nats['Config']['Cmd'],
    'ports': ['127.0.0.1:8222:8222'], 'volumes': ['/srv/vibetrading-nats:/data', f'{deploy}/nats.conf:/etc/nats/nats.conf:ro'],
    'mem_limit': '512m', 'restart': 'unless-stopped', 'healthcheck': {'test': nats['Config']['Healthcheck']['Test'],
    'interval': '10s', 'timeout': '5s', 'retries': 6}, 'logging': {'driver': 'local', 'options': {'max-size': '10m', 'max-file': '3'}}}},
    'networks': {'default': {'external': True, 'name': 'vibetrading_default'}}}
(deploy / 'nats-relief.yml').write_text(json.dumps(nats_compose, indent=2) + '\n')
health = {}
for directory in ('state', 'crypto_state'):
    for p in (DATA / directory / 'health').glob('*.json'):
        if p.stem in ('crypto-storage', 'maintenance', 'collectors-binance-perp', 'collectors-bybit-perp', 'collectors-bitget-perp'):
            health[directory + '/' + p.stem] = json.loads(p.read_text())
db = sqlite3.connect(f'file:{DATA}/crypto_state/vibetrading.sqlite3?mode=ro', uri=True, timeout=3)
db.execute('PRAGMA query_only=ON')
catalogue = dict(db.execute('SELECT key,value FROM runtime_metadata'))
manifest = db.execute('SELECT upload_status,local_present,COUNT(*),SUM(byte_size) FROM parquet_manifest GROUP BY 1,2').fetchall()
db.close()
exports = {str(p): {'inode': p.stat().st_ino, 'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
           for p in (DATA / 'exports').rglob('*') if p.is_file()}
before = {'time': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'health': health, 'jsz': jsz(),
          'data_disk': dict(zip(('total', 'used', 'free'), shutil.disk_usage(DATA))),
          'root_disk': dict(zip(('total', 'used', 'free'), shutil.disk_usage('/'))),
          'io_pressure': Path('/proc/pressure/io').read_text(), 'catalogue': catalogue,
          'manifest': manifest, 'protected_exports': exports,
          'market_db_inode': (DATA / 'state/vibetrading.sqlite3').stat().st_ino}
(ROOT / 'before.json').write_text(json.dumps(before, indent=2))
print(json.dumps({'revision': revision, 'image_id': image_id, 'source_files_changed': ['vibetrading/archive.py', 'vibetrading/runtime.py'],
                  'crypto_policy': env, 'before_disk': before['data_disk'], 'manifest': manifest,
                  'root_free': before['root_disk']['free']}))
