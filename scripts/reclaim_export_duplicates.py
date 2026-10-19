"""Verify retained tar files before reclaiming only their extracted duplicates.

Never touches live market/crypto data, SQLite, or the retained export archives.
The two historical exports are fixed; unexpected members or files fail closed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import time

ROOT = Path('/srv/vibetrading-data/runtime_data/exports')
EXPORTS = {
    'all-history-20260923': ('09a02a3f59f0949f3559b9a93fa8f6ca490722c392eb9580a4a76fb9634540c3', 823153540),
    'vibetrading-market-20260914T130416Z': ('c474817d406d6d9b8529228cf41c26b4da40dbe2f60287f44b41d4b34705bf58', 189041454),
}


def identity(path):
    value = path.stat()
    return [value.st_ino, value.st_size, value.st_mtime_ns]


def inventory(root):
    result = {}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise RuntimeError('export contains a symlink')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = identity(path)
        elif not path.is_dir():
            raise RuntimeError('unexpected export file type')
    return result


def verify(root, name, expected_sha, expected_size, pace):
    directory = root / name
    archive = root / (name + '.tar.gz')
    if (root.resolve() != root or directory.is_symlink() or archive.is_symlink()
            or directory.resolve().parent != root or archive.resolve().parent != root):
        raise RuntimeError('export path escaped fixed root')
    before = inventory(directory)
    archive_before = identity(archive)
    if archive_before[1] != expected_size or not before:
        raise RuntimeError('export identity/size mismatch')
    archive_hash = hashlib.sha256()
    archived = set()
    metadata = None

    class Reader:
        def __init__(self, handle):
            self.handle = handle

        def read(self, size):
            chunk = self.handle.read(size)
            archive_hash.update(chunk)
            pace(len(chunk))
            return chunk

    with archive.open('rb') as raw:
        reader = Reader(raw)
        with tarfile.open(fileobj=reader, mode='r|gz') as tar:
            for member in tar:
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != name:
                    raise RuntimeError('unsafe archived member')
                if member.isdir():
                    continue
                if not member.isfile() or len(path.parts) < 2:
                    raise RuntimeError('nonregular archived member')
                relative = PurePosixPath(*path.parts[1:]).as_posix()
                if relative in archived or relative not in before:
                    raise RuntimeError('duplicate/unexpected archived member')
                archived.add(relative)
                with tar.extractfile(member) as payload:
                    if relative == 'export_metadata.json':
                        if member.size > 65536:
                            raise RuntimeError('oversized export metadata')
                        metadata = json.loads(payload.read())
                        local = json.loads((directory / relative).read_text())
                        # Export adds the archive pointer after building the tar.
                        local.pop('archive', None)
                        metadata.pop('archive', None)
                        if local != metadata:
                            raise RuntimeError('export metadata differs from retained archive')
                    else:
                        digest, size = hashlib.sha256(), 0
                        while chunk := payload.read(256 * 1024):
                            digest.update(chunk)
                            size += len(chunk)
                        local_hash, local_size = hashlib.sha256(), 0
                        with (directory / relative).open('rb') as local:
                            while chunk := local.read(256 * 1024):
                                pace(len(chunk))
                                local_hash.update(chunk)
                                local_size += len(chunk)
                        if size != local_size or digest.digest() != local_hash.digest():
                            raise RuntimeError('extracted export differs from retained archive')
        while reader.read(256 * 1024):
            pass  # Hash all compressed bytes, including any buffered trailer.
    if archive_hash.hexdigest() != expected_sha or archived != set(before) or metadata is None:
        raise RuntimeError('retained archive checksum or complete file set mismatch')
    if identity(archive) != archive_before or inventory(directory) != before:
        raise RuntimeError('export changed during verification')
    return {'directory': str(directory), 'archive': str(archive), 'archive_sha256': expected_sha,
            'archive_identity': archive_before, 'files': before,
            'logical_duplicate_bytes': sum(value[1] for value in before.values()),
            'export_metadata': metadata}


def fsync_dir(path):
    descriptor = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main():
    start = last_report = time.monotonic()
    total = 0

    def pace(count):
        nonlocal total, last_report
        total += count
        delay = start + total / (8 * 1024**2) - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        while True:
            if time.monotonic() - start > 1800:
                raise RuntimeError('bounded verification time exceeded')
            full = next(s for s in Path('/proc/pressure/io').read_text().splitlines() if s.startswith('full '))
            pressure = float(dict(p.split('=') for p in full.split()[1:])['avg10'])
            if pressure <= 5:
                break
            time.sleep(1)
        if time.monotonic() - last_report >= 20:
            print(json.dumps({'verification_read_bytes': total, 'io_full_avg10': pressure}), flush=True)
            last_report = time.monotonic()

    receipt = ROOT.parent / 'recovery-receipts' / 'export-extracted-duplicates-20261001.json'
    if receipt.exists():
        raise RuntimeError('reclaim receipt exists; inspect prior result instead of repeating')
    verified = [verify(ROOT, name, sha, size, pace) for name, (sha, size) in EXPORTS.items()]
    # Revalidate all artifacts before the first removal, after both pass.
    for item in verified:
        if (inventory(Path(item['directory'])) != item['files']
                or identity(Path(item['archive'])) != item['archive_identity']):
            raise RuntimeError('verified export changed before reclaim')
    receipt.parent.mkdir(exist_ok=True)
    with receipt.open('x') as handle:
        json.dump({'verified_before_reclaim': verified, 'retained_tar_archives': True,
                   'live_database_or_parquet_modified': False}, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_dir(receipt.parent)
    free_before = shutil.disk_usage(ROOT).free
    for item in verified:
        target = Path(item['directory'])
        if target.resolve().parent != ROOT or target.name not in EXPORTS:
            raise RuntimeError('reclaim target escaped fixed export root')
        shutil.rmtree(target)
    fsync_dir(ROOT)
    print(json.dumps({'removed_only_verified_extracted_export_duplicates': [x['directory'] for x in verified],
                      'logical_duplicate_bytes': sum(x['logical_duplicate_bytes'] for x in verified),
                      'retained_tar_archives': True, 'free_bytes_before': free_before,
                      'free_bytes_after': shutil.disk_usage(ROOT).free, 'receipt': str(receipt)}), flush=True)


if __name__ == '__main__':
    main()
