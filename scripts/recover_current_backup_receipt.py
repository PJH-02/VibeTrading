"""Recover the already verified remote receipt after a local permission error.

No backup, upload, full-object checksum replay, checkpoint, or source deletion.
"""
import hashlib
import json
import os
from pathlib import Path

from vibetrading.settings import get_settings
from storage.storage_backend import archive_backend

SHA = '33b552ff03bcc3caa72671f1995c7ed7ff699d48e7fc2e8bf69142db3ca4e84e'
SIZE = 1436606464
KEY = f'vibetrading/crypto/recovery/pre-r2-20260930/current-crypto-{SHA}.sqlite3'
EXPECTED_SOURCE = {
    'vibetrading.sqlite3': [1436606464, 1790675030665676603, 1048590],
    'vibetrading.sqlite3-wal': [1239555592, 1790667614686035422, 1048635],
}


def main():
    path = Path('/backup/current-crypto-pre-r2.receipt.json')
    if path.exists():
        raise RuntimeError('receipt exists; inspect completed recovery rather than repeat')
    settings = get_settings()
    if settings.archive.backend != 'ncloud_storage' or settings.archive.prefix != 'vibetrading/crypto':
        raise RuntimeError('existing crypto archive required')
    backend = archive_backend(settings)
    remote = backend._client.get_object(Bucket=backend.bucket, Key=KEY + '.receipt.json')
    try:
        payload = remote['Body'].read(65537)
    finally:
        remote['Body'].close()
    if len(payload) > 65536:
        raise RuntimeError('oversized remote receipt')
    receipt = json.loads(payload)
    if (receipt.get('sha256') != SHA or receipt.get('snapshot_bytes') != SIZE
            or receipt.get('remote_key') != KEY or receipt.get('remote_full_sha256_verified') is not True
            or receipt.get('consistent_current_db_backup') is not True or receipt.get('quick_check') != 'ok'
            or receipt.get('source_identity') != EXPECTED_SOURCE or receipt.get('source_deleted') is not False
            or receipt.get('reused_local_snapshot') is not True):
        raise RuntimeError('remote verification receipt mismatch')
    head = backend._client.head_object(Bucket=backend.bucket, Key=KEY)
    if head['ContentLength'] != SIZE or head.get('Metadata', {}).get('sha256') != SHA:
        raise RuntimeError('verified remote object identity mismatch')
    before = {name: [p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino]
              for name in EXPECTED_SOURCE for p in [Path('/source') / name]}
    if before != EXPECTED_SOURCE:
        raise RuntimeError('original source changed since full verification')
    with path.open('xb') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    descriptor = os.open('/backup', os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print(json.dumps(dict(receipt, receipt_recovered_after_local_permission_error=True,
                          remote_receipt_sha256=hashlib.sha256(payload).hexdigest())), flush=True)


if __name__ == '__main__':
    main()
