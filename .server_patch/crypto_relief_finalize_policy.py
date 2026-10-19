"""Finalize the bounded recovery throughput and its immutable provenance."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path('/opt/vibetrading/releases/crypto-relief-20261002')
deploy = root / 'deploy'
release = json.loads((root / '.deploy-release.json').read_text())
compose = json.loads((deploy / 'crypto-r2.yml').read_text())
compose['services']['crypto-maintenance']['environment'].update(CRYPTO_RECOVERY_ARCHIVE_BATCH='8', CRYPTO_EVENT_RUN_RETIRE_BATCH='8')
(deploy / 'crypto-r2.yml').write_text(json.dumps(compose, indent=2) + '\n')
config_hash = hashlib.sha256((deploy / 'crypto-r2.yml').read_bytes()).hexdigest()
revision = 'r2-relief-' + hashlib.sha256((release['source_hash'] + config_hash + release['base_image_id']).encode()).hexdigest()[:16]
dockerfile = (root / 'Dockerfile').read_text().replace(release['config_hash'], config_hash).replace(release['build_revision'], revision)
(root / 'Dockerfile').write_text(dockerfile)
tag = 'vibetrading-crypto:' + revision
subprocess.run(['docker', 'build', '--network=none', '--pull=false', '-t', tag, str(root)], check=True)
image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag]))[0]['Id']
release.update(config_hash=config_hash, build_revision=revision, images={'crypto': {'id': image, 'tag': tag}})
(root / '.deploy-release.json').write_text(json.dumps(release, indent=2) + '\n')
(deploy / 'crypto-release.env').write_text(f'VIBETRADING_BUILD_REVISION={revision}\nVIBETRADING_BUILD_CONFIG_HASH={config_hash}\nVIBETRADING_IMAGE_ID={image}\n')
(deploy / 'release.env').write_text(f'VIBETRADING_CRYPTO_IMAGE={image}\nVIBETRADING_CRYPTO_RELEASE_ENV_FILE={deploy}/crypto-release.env\n')
print(json.dumps({'image_id': image, 'revision': revision, 'config_hash': config_hash}), flush=True)
