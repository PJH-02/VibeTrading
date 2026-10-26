"""Reclaim exactly one inactive artifact after its authorized remote backup.

The active DB, WAL, Parquet, and order audit are never opened or deleted.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from vibetrading.settings import get_settings
from storage.storage_backend import archive_backend


NAME = 'vibetrading.sqlite3.precompact-20260928T021539Z'
SIZE = 1763291136
MTIME_NS = 1790561739534870399
SHA = 'ce37e720151176a095c8b473851b1d90209dd6e599fcf8fffedd9d98ab73131c'
KEY = f'vibetrading/crypto/recovery/pre-r2-20260930/{NAME}-{SHA}.bytes'


def main():
    settings = get_settings()
    if settings.archive.backend != 'ncloud_storage' or settings.archive.prefix != 'vibetrading/crypto':
        raise RuntimeError('unexpected archive destination')
    source = Path('/data/crypto_state') / NAME
    if source.is_symlink() or source.resolve() != source:
        raise RuntimeError('inactive artifact path changed')
    before = source.stat()
    if (before.st_size, before.st_mtime_ns) != (SIZE, MTIME_NS):
        raise RuntimeError('inactive artifact content identity changed')
    backend = archive_backend(settings)
    response = backend._client.get_object(Bucket=backend.bucket, Key=KEY+'.receipt.json')
    try:
        raw = response['Body'].read(65537)
    finally:
        response['Body'].close()
    if len(raw)>65536:
        raise RuntimeError('oversized verification receipt')
    receipt = json.loads(raw)
    if (receipt.get('sha256') != SHA or receipt.get('source_bytes') != SIZE
            or receipt.get('source_mtime_ns') != MTIME_NS or receipt.get('remote_key') != KEY
            or receipt.get('remote_full_sha256_verified') is not True):
        raise RuntimeError('remote full-content verification receipt mismatch')
    head = backend._client.head_object(Bucket=backend.bucket, Key=KEY)
    if int(head['ContentLength']) != SIZE or head.get('Metadata', {}).get('sha256') != SHA:
        raise RuntimeError('verified remote object identity changed')
    # Keep a durable local recovery pointer before removing the local copy.
    directory = Path('/data/recovery-receipts')
    directory.mkdir(exist_ok=True)
    pointer = directory / f'{NAME}.json'
    with pointer.open('x', encoding='utf-8') as handle:
        json.dump(dict(receipt, local_reclaim_authorized_by='verified inactive artifact cleanup',
                       reclaim_ts_ns=time.time_ns()), handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    fd=os.open(directory,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    # Abort if any writer changed the inode between the preflight and receipt.
    after=source.stat()
    if (after.st_ino,after.st_size,after.st_mtime_ns)!=(before.st_ino,SIZE,MTIME_NS):
        raise RuntimeError('inactive artifact changed during verification')
    source.unlink()
    fd=os.open(source.parent,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    print(json.dumps({'removed_local_inactive_artifact':str(source),'logical_bytes':SIZE,
                      'remote_key':KEY,'sha256':SHA,'recovery_pointer':str(pointer),
                      'active_db_or_wal_modified':False}),flush=True)


if __name__=='__main__':
    main()
