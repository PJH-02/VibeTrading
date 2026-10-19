"""Build the final split-DB release, using only changed, verified source files."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

root = Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
base = Path('/opt/vibetrading/releases/crypto-relief-20261002')
patch = Path('/tmp/crypto-relief-patch')
root.mkdir()
(root / 'deploy').mkdir()
old = json.loads((base / '.deploy-release.json').read_text())
files = dict(old['runtime_files'])
changed = ['archive.py', 'runtime.py', 'storage_backend.py', 'storage_guard.py', 'market_policy.py']
for name in changed:
    path = root / 'vibetrading' / name
    path.parent.mkdir(exist_ok=True)
    shutil.copy2(patch / name, path)
    files['vibetrading/' + name] = hashlib.sha256(path.read_bytes()).hexdigest()
compose = json.loads((base / 'deploy/crypto-r2.yml').read_text())
for service in compose['services'].values():
    service['volumes'].append('/srv/vibetrading-crypto-state:/data/crypto_state')
    service['environment'].update(STORAGE_RESERVE_GB='4', STORAGE_STATE_DISK_ROOT='/data/crypto_state',
        STORAGE_STATE_RESERVE_BYTES='1073741824', STORAGE_STATE_RESUME_MARGIN_BYTES='268435456',
        MARKET_EVENT_MAX_AGE_SECONDS='43200')
storage = compose['services']['crypto-storage']
storage['environment']['SQLITE_CACHE_KIB'] = '131072'
storage['mem_limit'] = '1024m'
maintenance = compose['services']['crypto-maintenance']
maintenance['command'] = ['ionice', '-c2', '-n7', 'nice', '-n', '10', 'python', 'main.py', 'run', '--role', 'crypto-maintenance']
maintenance['environment'].update(SQLITE_CACHE_KIB='131072', CRYPTO_NATS_MAX_BYTES='134217728',
    ARCHIVE_UPLOAD_BATCH='8', ARCHIVE_BACKLOG_IDLE_SECONDS='5', ARCHIVE_DELIVERY_HISTORY_PRUNE_LIMIT='8192')
(root / 'deploy/crypto-r2.yml').write_text(json.dumps(compose, indent=2) + '\n')
config_hash = hashlib.sha256((root / 'deploy/crypto-r2.yml').read_bytes()).hexdigest()
source_hash = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
revision = 'r2-split-db-' + hashlib.sha256((config_hash + source_hash).encode()).hexdigest()[:16]
base_id = old['images']['crypto']['id']
base_tag = 'vibetrading-crypto-base:' + base_id.split(':')[1][:20]
subprocess.run(['docker', 'tag', base_id, base_tag], check=True)
lines = [f'FROM {base_tag}', 'USER root']
lines += [f'COPY --chown=10001:10001 vibetrading/{name} /app/vibetrading/{name}' for name in changed]
lines += [f'LABEL org.opencontainers.image.revision={revision} org.vibetrading.config-hash={config_hash}',
          f'ENV VIBETRADING_BUILD_REVISION={revision} VIBETRADING_BUILD_CONFIG_HASH={config_hash} PYTHONDONTWRITEBYTECODE=1', 'USER vibetrading']
(root / 'Dockerfile').write_text('\n'.join(lines) + '\n')
tag = 'vibetrading-crypto:' + revision
subprocess.run(['docker', 'build', '--pull=false', '--network=none', '-t', tag, str(root)], check=True)
image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag]))[0]['Id']
verify = 'import hashlib,json,pathlib; expected=json.loads(' + repr(json.dumps(files)) + '); '
verify += 'assert all(hashlib.sha256((pathlib.Path("/app")/p).read_bytes()).hexdigest()==h for p,h in expected.items());print("all_runtime_files_verified")'
subprocess.run(['docker', 'run', '--rm', '--network=none', '--read-only', '--entrypoint', 'python', image, '-c', verify], check=True)
release = dict(old, build_revision=revision, config_hash=config_hash, source_hash=source_hash,
               base_image_id=base_id, images={'crypto': {'tag': tag, 'id': image}}, runtime_files=files)
(root / '.deploy-release.json').write_text(json.dumps(release, indent=2))
(root / 'deploy/crypto-release.env').write_text(f'VIBETRADING_BUILD_REVISION={revision}\nVIBETRADING_BUILD_CONFIG_HASH={config_hash}\nVIBETRADING_IMAGE_ID={image}\n')
(root / 'deploy/release.env').write_text(f'VIBETRADING_CRYPTO_IMAGE={image}\nVIBETRADING_CRYPTO_RELEASE_ENV_FILE={root}/deploy/crypto-release.env\n')
nats = json.loads((base / 'deploy/nats-relief.yml').read_text())
nats['services']['nats']['volumes'] = ['/srv/vibetrading-data/nats:/data', f'{root}/deploy/nats.conf:/etc/nats/nats.conf:ro']
shutil.copy2(base / 'deploy/nats.conf', root / 'deploy/nats.conf')
(root / 'deploy/nats-relief.yml').write_text(json.dumps(nats, indent=2))
print(json.dumps({'root': str(root), 'image': image, 'revision': revision}), flush=True)
