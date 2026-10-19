"""Relocate legacy combined exports containing retired data to cloud storage.

The exports are recovery copies, not live collector partitions. Preserve every
byte and verify a full remote SHA256 before reclaiming each local archive.
"""
import json
import time
from pathlib import Path

from vibetrading.settings import get_settings
from vibetrading.storage_backend import archive_backend, sha256_file

settings = get_settings()
backend = archive_backend(settings)
assert settings.archive.backend=='ncloud_storage'
root = settings.data_root/'exports'
allowed = {'kospi200-only-20261001-final.tar.gz','sk-usdt-only-20261001.tar.gz',
    'vibetrading-market-20260914T130416Z.tar.gz','all-history-20260923.tar.gz'}
audit = settings.data_root/'scope-migration-20261013/legacy-exports.jsonl'
for name in sorted(allowed):
    path = root/name
    if not path.exists():
        continue
    assert path.resolve().is_relative_to(root.resolve())
    sha = sha256_file(path)
    size = path.stat().st_size
    key = f'vibetrading/retired-20261013/legacy-exports/{name}'
    backend.put_file(path,key,sha256=sha)
    row = {'path':str(path),'remote_key':key,'sha256':sha,'bytes':size,'verified_ns':time.time_ns()}
    with audit.open('a') as f:
        f.write(json.dumps(row)+'\n')
    path.unlink()
    sidecar = path.with_name(path.name+'.sha256')
    if sidecar.exists():
        side_sha = sha256_file(sidecar)
        backend.put_file(sidecar,key+'.sha256',sha256=side_sha)
        sidecar.unlink()
    print(json.dumps(row),flush=True)
backend.close()
