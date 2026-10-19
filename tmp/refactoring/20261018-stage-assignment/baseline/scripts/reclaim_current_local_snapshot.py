"""Reclaim only the redundant local snapshot after full remote verification.

Original active DB/WAL stay unchanged. Remote object and local recovery pointer
are retained; does not repeat the completed backup/checksum operation.
"""
import json
import os
from pathlib import Path
import shutil


def fsync_dir(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    root = Path('/srv/vibetrading-data')
    snapshot = root / 'r2-cutover-local-backup/current-crypto-pre-r2.sqlite3'
    receipt = snapshot.with_suffix('.receipt.json')
    proof = json.loads(receipt.read_text())
    sha = '33b552ff03bcc3caa72671f1995c7ed7ff699d48e7fc2e8bf69142db3ca4e84e'
    key = f'vibetrading/crypto/recovery/pre-r2-20260930/current-crypto-{sha}.sqlite3'
    if (proof.get('remote_full_sha256_verified') is not True or proof.get('consistent_current_db_backup') is not True
            or proof.get('quick_check') != 'ok' or proof.get('sha256') != sha or proof.get('remote_key') != key
            or proof.get('snapshot_bytes') != 1436606464 or proof.get('source_deleted') is not False):
        raise RuntimeError('verified remote backup receipt required')
    if snapshot.is_symlink() or snapshot.resolve() != snapshot or snapshot.stat().st_size != proof['snapshot_bytes']:
        raise RuntimeError('snapshot path/size changed')
    original = root / 'runtime_data/crypto_state'
    current = {name: [p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino]
               for name in proof['source_identity'] for p in [original / name]}
    if current != proof['source_identity'] or snapshot.samefile(original / 'vibetrading.sqlite3'):
        raise RuntimeError('original source identity changed or snapshot aliases original')
    directory = root / 'runtime_data/recovery-receipts'
    pointer = directory / 'current-crypto-pre-r2-20261001.json'
    with pointer.open('x') as handle:
        json.dump(proof, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_dir(directory)
    free_before = shutil.disk_usage(root).free
    snapshot.unlink()
    fsync_dir(snapshot.parent)
    print(json.dumps({'removed_only_redundant_local_snapshot': str(snapshot), 'original_db_wal_modified': False,
                      'remote_full_sha256_verified': True, 'recovery_pointer': str(pointer),
                      'free_bytes_before': free_before, 'free_bytes_after': shutil.disk_usage(root).free}), flush=True)


if __name__ == '__main__':
    main()
