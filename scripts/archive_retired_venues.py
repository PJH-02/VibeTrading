"""Move retired venue cache to verified cloud storage, retaining catalogue.

Run inside crypto-maintenance with the existing archive secrets and mounts.
Never deletes remote data. Target providers are fixed by the 2026-10-13 request.
"""
import json
import time
from pathlib import Path

from storage.archive import ArchiveMaintenance
from vibetrading.maintenance_gate import maintenance_lock
from vibetrading.settings import get_settings
from storage.storage_backend import sha256_file

settings = get_settings()
assert settings.archive.backend == 'ncloud_storage'
assert settings.state_dir == Path('/data/crypto_state')
worker = ArchiveMaintenance(settings)
audit = settings.data_root / 'scope-migration-20261013'
audit.mkdir(exist_ok=True)
targets = ('bitget-perp', 'bybit-perp')
progress = {'uploaded': 0, 'reclaimed_files': 0, 'reclaimed_bytes': 0}
try:
    while True:
        with maintenance_lock(settings.data_root) as lock:
            if not lock['acquired']:
                time.sleep(2)
                continue
            rows = [dict(r) for r in worker.store._conn.execute(
                "SELECT * FROM parquet_manifest INDEXED BY idx_manifest_local_reclaim WHERE local_present=1 AND provider IN (?,?) LIMIT 32", targets)]
            if not rows:
                break
            for row in rows:
                path = Path(row['path'])
                assert path.resolve().is_relative_to(settings.parquet.data_root.resolve())
                if row['lifecycle_state'] == 'superseded':
                    replacement = worker.store._conn.execute("SELECT * FROM parquet_manifest WHERE file_id=?", (row['superseded_by'],)).fetchone()
                    if replacement is None or replacement['upload_status'] != 'remote_verified':
                        raise RuntimeError('Superseded data has no verified remote replacement')
                elif row['upload_status'] != 'remote_verified':
                    worker._upload(row)
                    progress['uploaded'] += 1
                else:
                    # Existing receipts came from full remote SHA256 reads.
                    # Check object presence/size before reclaiming the cache.
                    head = worker.backend._client.head_object(Bucket=worker.backend.bucket, Key=row['remote_key'])
                    assert int(head['ContentLength']) == row['byte_size']
                    assert head.get('Metadata', {}).get('sha256') == row['file_sha256']
                current = worker.store._conn.execute('SELECT * FROM parquet_manifest WHERE file_id=?', (row['file_id'],)).fetchone()
                assert current['lifecycle_state'] == 'superseded' or current['upload_status'] == 'remote_verified'
                if path.exists():
                    if row['file_sha256']:
                        assert sha256_file(path) == row['file_sha256']
                    size = path.stat().st_size
                    path.unlink()
                    progress['reclaimed_bytes'] += size
                worker.store.mark_local_deleted(row['file_id'])
                progress['reclaimed_files'] += 1
                with (audit / 'retired-archive.jsonl').open('a') as f:
                    f.write(json.dumps({'file_id': row['file_id'], 'provider': row['provider'], 'remote_key': current['remote_key'], 'ts_ns': time.time_ns()}) + '\n')
        (audit / 'retired-archive-progress.json').write_text(json.dumps(progress))
        print(json.dumps(progress), flush=True)
        time.sleep(1)
    # Interrupted writers can leave complete or partial unregistered files.
    # Preserve their exact bytes as recovery objects before removing them.
    roots = [(settings.parquet.data_root, {'bitget-perp', 'bybit-perp'}),
             (settings.data_root / 'market_data', {'hyperliquid', 'binance'})]
    for root, providers in roots:
        for provider in sorted(providers):
            for directory in root.glob(f'event_type=*/trading_date=*/provider={provider}/venue=*'):
                for path in directory.iterdir():
                    if not path.is_file():
                        continue
                    assert path.resolve().is_relative_to(root.resolve())
                    sha = sha256_file(path)
                    size = path.stat().st_size
                    key = f'vibetrading/retired-20261013/cache/{provider}/{sha}/{path.name}'
                    worker.backend.put_file(path, key, sha256=sha)
                    with (audit / 'retired-archive.jsonl').open('a') as f:
                        f.write(json.dumps({'provider': provider, 'path': str(path), 'remote_key': key, 'sha256': sha, 'bytes': size, 'kind': 'recovery_cache', 'ts_ns': time.time_ns()}) + '\n')
                    path.unlink()
                    progress['reclaimed_files'] += 1
                    progress['reclaimed_bytes'] += size
                    (audit / 'retired-archive-progress.json').write_text(json.dumps(progress))
                    print(json.dumps(progress), flush=True)
    progress['complete'] = True
finally:
    (audit / 'retired-archive-progress.json').write_text(json.dumps(progress))
    worker.close()
print(json.dumps(progress), flush=True)
