"""Prepare a source-only bundle and build crypto alone from a pinned local base.

No Market service, source bind mount, credentials, or database is bundled.
The generated compose file must be used alone, with project vibetrading; never
use --remove-orphans because Market and NATS are owned by the existing release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'vibetrading'
SERVICES = ('crypto-storage', 'crypto-maintenance', 'crypto-collector-binance',
            'crypto-collector-bitget', 'crypto-collector-bybit', 'crypto-collector-arcus', 'crypto-collector-grvt')
CANONICAL_ERROR = ('historical crypto r2 bundle cannot package canonical src sources; '
                   'use scripts/build_release_images.py to build current images')
DOCKERFILE = '''ARG CRYPTO_BASE
FROM ${CRYPTO_BASE}
ARG REVISION
ARG CONFIG_HASH
USER root
RUN rm -rf /app/vibetrading /app/config /app/strategies
COPY --chown=10001:10001 vibetrading /app/vibetrading
COPY --chown=10001:10001 config /app/config
COPY --chown=10001:10001 strategies /app/strategies
COPY --chown=10001:10001 main.py /app/main.py
LABEL org.opencontainers.image.revision=${REVISION} org.vibetrading.config-hash=${CONFIG_HASH}
ENV VIBETRADING_BUILD_REVISION=${REVISION} VIBETRADING_BUILD_CONFIG_HASH=${CONFIG_HASH} PYTHONDONTWRITEBYTECODE=1
USER vibetrading
'''


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_package(root=None):
    root = ROOT if root is None else root
    flat, src = root / PACKAGE, root / 'src' / PACKAGE
    if flat.is_symlink() or src.is_symlink() or (root / 'src').is_symlink():
        raise RuntimeError('release package source symlink rejected')
    if flat.is_dir() and src.is_dir():
        raise RuntimeError('ambiguous package source roots: vibetrading and src/vibetrading both exist')
    if src.is_dir():
        return src
    if flat.is_dir():
        return flat
    raise RuntimeError('package source root missing')


def canonical_source(root):
    src = root / 'src'
    if src.is_symlink():
        raise RuntimeError('release package source symlink rejected')
    if not (src / 'runtime').is_dir():
        return None
    if (src / 'runtime').is_symlink():
        raise RuntimeError('release package source symlink rejected')
    flat, legacy = root / PACKAGE, src / PACKAGE
    if flat.exists() or flat.is_symlink() or legacy.exists() or legacy.is_symlink():
        raise RuntimeError('ambiguous canonical and historical source roots; use scripts/build_release_images.py')
    return src


def reject_source_symlinks(base):
    for current, directories, names in os.walk(base, followlinks=False):
        current = Path(current)
        for name in directories + names:
            if (current / name).is_symlink():
                raise RuntimeError('release source symlink rejected')


def canonical_files(root, src):
    paths = []
    for path in (root / 'main.py', root / 'requirements.txt',
                 root / 'scripts/build_crypto_release.py'):
        if path.is_symlink():
            raise RuntimeError('release source symlink rejected')
        if path.is_file():
            paths.append(path)
    reject_source_symlinks(src)
    paths.extend(p for p in src.rglob('*.py')
                 if p.is_file() and '__pycache__' not in p.parts)
    config = root / 'config'
    if config.is_symlink():
        raise RuntimeError('release source symlink rejected')
    if config.is_dir():
        reject_source_symlinks(config)
        paths.extend(p for p in config.rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts
                     and p.suffix in {'.py', '.yaml', '.yml', '.json'})
    if any(p.is_symlink() for p in paths):
        raise RuntimeError('release source symlink rejected')
    return sorted(paths)


def logical_name(path, package, root):
    try:
        relative = path.relative_to(package)
    except ValueError:
        return path.relative_to(root).as_posix()
    return (Path(PACKAGE) / relative).as_posix()


def files(root=None):
    root = ROOT if root is None else root
    src = canonical_source(root)
    if src is not None:
        return canonical_files(root, src)
    paths = [root / 'main.py', root / 'requirements.txt',
             root / 'scripts/build_crypto_release.py']
    for base in (source_package(root), root / 'config', root / 'strategies'):
        if base.is_symlink():
            raise RuntimeError('release source symlink rejected')
        if base.is_dir():
            reject_source_symlinks(base)
        paths.extend(p for p in base.rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix in {'.py', '.yaml', '.yml', '.json'})
    if any(p.is_symlink() for p in paths):
        raise RuntimeError('release source symlink rejected')
    return sorted(paths)


def compose():
    import copy
    import yaml
    profile = yaml.safe_load((ROOT / 'deploy/crypto-profile.yml').read_text(encoding='utf-8'))
    output = {'services': {}, 'secrets': profile['secrets'],
              'networks': {'default': {'external': True, 'name': 'vibetrading_default'}}}
    for name in SERVICES:
        original = profile['services'][name]
        service = copy.deepcopy(profile['services'][original['extends']['service']]
                                if 'extends' in original else original)
        for key, value in original.items():
            if key == 'extends':
                continue
            if key == 'healthcheck':
                service.setdefault(key, {}).update(value)
            else:
                service[key] = copy.deepcopy(value)
        service.pop('extends', None)
        service['image'] = '${VIBETRADING_CRYPTO_IMAGE:?immutable crypto release required}'
        service['env_file'] = ['${VIBETRADING_APP_ENV_FILE:?set in compose.env}',
                               '${VIBETRADING_CRYPTO_RELEASE_ENV_FILE:?set by build}']
        service['volumes'] = ['${VIBETRADING_HOST_DATA_ROOT:?set in compose.env}:/data',
                              '${VIBETRADING_HOST_CRYPTO_STATE_ROOT:?set in compose.env}:/data/crypto_state']
        service['environment'].update({'CRYPTO_EVENT_INDEX_FORMAT': 'immutable-runs-v1',
            'CRYPTO_NATS_MAX_BYTES': '536870912',
            'NATS_FILE_STORE_MAX_BYTES': '2684354560',
            'STORAGE_DATA_DISK_ROOT': '/data/crypto_market_data',
            'STORAGE_NATS_DISK_ROOT': '/data',
            'CRYPTO_ENFORCE_LIVE_EVENT_WINDOW': 'true',
            'CRYPTO_EVENT_RUN_RETENTION_ENABLED': 'true',
            # Bound the local replay catalogue on the separate state disk.
            # Retirement still requires verified remote recovery and ACKs.
            'MARKET_EVENT_MAX_AGE_SECONDS': '43200'})
        if name.startswith('crypto-collector-'):
            service['environment']['CRYPTO_REQUIRE_STORAGE_READY'] = 'true'
            service['environment']['COLLECTOR_PUBLISH_BUFFER_BYTES'] = str(16*1024*1024)
            service['mem_limit'] = '256m'
        if name == 'crypto-maintenance':
            service['environment'].update({'ARCHIVE_UPLOAD_BATCH': '32',
                'CRYPTO_RECOVERY_ARCHIVE_BATCH': '32',
                'ARCHIVE_DELIVERY_HISTORY_PRUNE_LIMIT': '65536',
                'CRYPTO_EVENT_RUN_RETIRE_BATCH': '32'})
            service['environment']['ARCHIVE_DELIVERY_HISTORY_PRUNE_ENABLED'] = 'true'
            # Enables only the receipt-checking path; RuntimeStore still
            # requires per-generation server confirmation and remote coverage.
            service['environment']['ARCHIVE_DELIVERY_PRUNE_ACK_FLOOR_VERIFIED'] = 'true'
        if name == 'crypto-storage':
            service['environment']['CRYPTO_EVENT_GLOBAL_FILTER_BYTES'] = str(128 * 1024 * 1024)
            service['mem_limit'] = '1280m'
            service['memswap_limit'] = '1536m'
        output['services'][name] = service
    return output


def prepare(destination: Path):
    if canonical_source(ROOT) is not None:
        raise RuntimeError(CANONICAL_ERROR)
    if destination.exists():
        raise RuntimeError('bundle destination already exists')
    package = source_package()
    payload = {logical_name(p, package, ROOT): p.read_bytes() for p in files()}
    payload['Dockerfile.r2'] = DOCKERFILE.encode()
    payload['deploy/crypto-r2.yml'] = (json.dumps(compose(), indent=2) + '\n').encode()
    manifest = {name: sha(content) for name, content in sorted(payload.items())}
    payload['bundle-manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary)
        for name, content in payload.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        with tarfile.open(destination, 'w:gz') as archive:
            for name in sorted(payload):
                archive.add(staging / name, arcname=name, recursive=False)
    print(json.dumps({'bundle': str(destination), 'files': len(payload),
                      'sha256': sha(destination.read_bytes()), 'bytes': destination.stat().st_size}))


def run(command):
    return subprocess.check_output(command, cwd=ROOT, text=True).strip()


def build(base: str):
    manifest = json.loads((ROOT / 'bundle-manifest.json').read_text())
    for name, expected in manifest.items():
        if sha((ROOT / name).read_bytes()) != expected:
            raise RuntimeError(f'bundle checksum mismatch: {name}')
    inspected = json.loads(run(['docker', 'image', 'inspect', base]))[0]
    base_id = inspected['Id']
    source_hash = sha(json.dumps(manifest, sort_keys=True).encode())
    config_hash = sha(json.dumps({k: v for k, v in manifest.items()
                                 if k.startswith(('config/', 'deploy/'))}, sort_keys=True).encode())
    revision = 'r2-' + sha(f'{source_hash}:{base_id}'.encode())[:20]
    tag = 'vibetrading-crypto:' + revision
    # A private build tag is checked against its image ID before and after build.
    base_tag = 'vibetrading-crypto-base:' + base_id.split(':')[1][:20]
    run(['docker', 'tag', base_id, base_tag])
    subprocess.run(['docker', 'build', '--network=none', '--pull=false', '-f', 'Dockerfile.r2',
                    '-t', tag, '--build-arg', f'CRYPTO_BASE={base_tag}',
                    '--build-arg', f'REVISION={revision}', '--build-arg', f'CONFIG_HASH={config_hash}', '.'],
                   cwd=ROOT, check=True)
    if json.loads(run(['docker', 'image', 'inspect', base_tag]))[0]['Id'] != base_id:
        raise RuntimeError('base image changed while building')
    image = json.loads(run(['docker', 'image', 'inspect', tag]))[0]
    if image['RootFS']['Layers'][:len(inspected['RootFS']['Layers'])] != inspected['RootFS']['Layers']:
        raise RuntimeError('image base ancestry mismatch')
    image_id = image['Id']
    runtime_files = {k: v for k, v in manifest.items()
                     if k == 'main.py' or k.startswith(('vibetrading/', 'config/', 'strategies/'))}
    check = 'import hashlib,json,pathlib; expected=json.loads(' + repr(json.dumps(runtime_files)) + '); '
    check += 'assert all(hashlib.sha256((pathlib.Path("/app")/p).read_bytes()).hexdigest()==h for p,h in expected.items()); print("runtime_files_verified")'
    run(['docker', 'run', '--rm', '--network=none', '--read-only', '--entrypoint', 'python', image_id, '-c', check])
    release = {'release': '2', 'build_revision': revision, 'config_hash': config_hash,
               'source_hash': source_hash, 'base_image_id': base_id,
               'images': {'crypto': {'tag': tag, 'id': image_id}}, 'runtime_files': runtime_files}
    (ROOT / 'deploy/crypto-release.env').write_text(
        f'VIBETRADING_BUILD_REVISION={revision}\nVIBETRADING_BUILD_CONFIG_HASH={config_hash}\nVIBETRADING_IMAGE_ID={image_id}\n')
    (ROOT / 'deploy/release.env').write_text(f'VIBETRADING_CRYPTO_IMAGE={image_id}\n'
        f'VIBETRADING_CRYPTO_RELEASE_ENV_FILE={ROOT}/deploy/crypto-release.env\n')
    (ROOT / '.deploy-release.json').write_text(json.dumps(release, indent=2) + '\n')
    print(json.dumps({k: v for k, v in release.items() if k != 'runtime_files'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('prepare').add_argument('--output', type=Path, required=True)
    sub.add_parser('build').add_argument('--base-image', required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.output)
    else:
        build(args.base_image)
