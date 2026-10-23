"""Package source for recovery; never include credentials or runtime data."""
import hashlib
import io
import json
import tarfile
from pathlib import Path

PACKAGE = 'vibetrading'
DIRECTORIES = ['vibetrading', 'config', 'strategy', 'strategies', 'tests', 'deploy']
DOCKERFILE = b'''FROM vibetrading-capacity-maintenance-base:20261006
USER root
COPY --chown=10001:10001 vibetrading/ /app/vibetrading/
COPY --chown=10001:10001 config/ /app/config/
COPY --chown=10001:10001 strategy/ /app/strategy/
COPY --chown=10001:10001 strategies/ /app/strategies/
COPY --chown=10001:10001 main.py /app/main.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER vibetrading
'''


def source_package(root):
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


def build(root):
    package = source_package(root)
    files = {}
    for directory in DIRECTORIES:
        base = package if directory == PACKAGE else root/directory
        for path in base.rglob('*'):
            if path.is_file() and path.suffix in {'.py','.yaml','.yml','.json','.conf','.sh','.service'} and '__pycache__' not in path.parts:
                relative = path.relative_to(base)
                name = ((Path(PACKAGE) / relative) if directory == PACKAGE
                        else path.relative_to(root))
                files[name.as_posix()] = path.read_bytes()
    files['main.py'] = (root/'main.py').read_bytes()
    files['Dockerfile'] = DOCKERFILE
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    revision = 'telegram-recovery-'+hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
    files['manifest.json'] = json.dumps({'revision': revision, 'files': hashes}, indent=2).encode()
    path = root/'.server_patch/telegram-recovery-20261014.tar.gz'
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, 'w:gz') as archive:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return {'revision': revision, 'files': len(hashes), 'bytes': path.stat().st_size}


def main():
    print(json.dumps(build(Path(__file__).resolve().parents[1])))


if __name__ == '__main__':
    main()
