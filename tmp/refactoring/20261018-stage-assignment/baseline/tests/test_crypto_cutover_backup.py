from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import backup_crypto_cutover as backup


@pytest.fixture
def completed_snapshot(tmp_path, monkeypatch):
    source = tmp_path / 'source' / 'vibetrading.sqlite3'
    source.parent.mkdir()
    source.write_bytes(b'original database')
    Path(str(source) + '-wal').write_bytes(b'original WAL')
    target = tmp_path / 'backup' / 'current-crypto-pre-r2.sqlite3'
    target.parent.mkdir()
    target.write_bytes(b'previously checked snapshot')
    monkeypatch.setattr(backup, 'SOURCE', source)
    monkeypatch.setattr(backup, 'TARGET', target)
    result = {
        'consistent_current_db_backup': True,
        'scope': 'same-server local snapshot only',
        'quick_check': 'ok',
        'source_deleted': False,
        'remote_full_sha256_verified': False,
        'snapshot': str(target),
        'snapshot_bytes': target.stat().st_size,
        'source_identity': backup.identity(),
        'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    receipt = target.with_suffix('.local.receipt.json')
    receipt.write_text(json.dumps(result))
    monkeypatch.setattr(sys, 'argv', ['backup_crypto_cutover.py', '--archive-existing'])
    monkeypatch.setattr(backup.sqlite3, 'connect', lambda *a, **k: pytest.fail('must not repeat SQLite backup/check'))
    monkeypatch.setattr(backup.shutil, 'disk_usage', lambda path: SimpleNamespace(free=10 * 1024**3))
    original_read = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == Path('/proc/pressure/io'):
            return 'full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n'
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read_text)
    # The remote receipt directory fsync is Linux-specific; all data checks run.
    monkeypatch.setattr(backup.os, 'O_DIRECTORY', 0, raising=False)
    monkeypatch.setattr(backup.os, 'open', lambda *a, **k: 123)
    monkeypatch.setattr(backup.os, 'fsync', lambda *a: None)
    monkeypatch.setattr(backup.os, 'close', lambda *a: None)
    return target, receipt, result


@pytest.mark.parametrize('field,value', [
    ('consistent_current_db_backup', False),
    ('scope', 'unverified'),
    ('quick_check', 'failed'),
    ('source_deleted', True),
    ('remote_full_sha256_verified', True),
    ('snapshot', '/other/snapshot'),
    ('snapshot_bytes', 0),
    ('sha256', 'not-a-checksum'),
    ('source_identity', {}),
])
def test_reuse_rejects_invalid_receipt_before_archive(completed_snapshot, monkeypatch, field, value):
    _, receipt, result = completed_snapshot
    result[field] = value
    receipt.write_text(json.dumps(result))
    monkeypatch.setattr(backup, 'get_settings', lambda: pytest.fail('no archive setup for invalid receipt'))
    with pytest.raises(RuntimeError, match='receipt mismatch'):
        backup.main()


def test_reuse_rehashes_before_archive(completed_snapshot, monkeypatch):
    target, _, _ = completed_snapshot
    target.write_bytes(b'X' * target.stat().st_size)
    monkeypatch.setattr(backup, 'get_settings', lambda: pytest.fail('no archive setup for corrupt snapshot'))
    with pytest.raises(RuntimeError, match='checksum differs'):
        backup.main()


def test_reuse_uploads_existing_snapshot_and_verifies_full_remote_bytes(completed_snapshot, monkeypatch):
    target, _, original = completed_snapshot
    objects = {}
    settings = SimpleNamespace(archive=SimpleNamespace(backend='ncloud_storage', prefix='vibetrading/crypto'))

    class Client:
        def upload_file(self, filename, bucket, key, **kwargs):
            objects[key] = Path(filename).read_bytes()
            kwargs['Callback'](len(objects[key]))

        def get_object(self, **kwargs):
            return {'Body': io.BytesIO(objects[kwargs['Key']])}

    backend = SimpleNamespace(_client=Client(), bucket='existing-test-bucket', _upload_config=None,
                              put_bytes=lambda payload, key, **kwargs: objects.update({key: payload}))
    monkeypatch.setattr(backup, 'get_settings', lambda: settings)
    monkeypatch.setattr(backup, 'archive_backend', lambda settings: backend)
    before = (target.stat().st_mtime_ns, backup.identity())
    backup.main()
    result = json.loads(target.with_suffix('.receipt.json').read_text())
    assert result['remote_full_sha256_verified'] is True
    assert result['reused_local_snapshot'] is True
    assert result['sha256'] == original['sha256']
    assert result['source_deleted'] is False
    assert (target.stat().st_mtime_ns, backup.identity()) == before
    assert len(objects) == 2  # snapshot plus proof receipt; no source DB/WAL export


def test_reuse_rejects_remote_checksum_mismatch(completed_snapshot, monkeypatch):
    target, _, _ = completed_snapshot
    settings = SimpleNamespace(archive=SimpleNamespace(backend='ncloud_storage', prefix='vibetrading/crypto'))
    client = SimpleNamespace(
        upload_file=lambda filename, bucket, key, **kwargs: kwargs['Callback'](target.stat().st_size),
        get_object=lambda **kwargs: {'Body': io.BytesIO(b'X' * target.stat().st_size)},
    )
    backend = SimpleNamespace(_client=client, bucket='test', _upload_config=None,
                              put_bytes=lambda *a, **k: pytest.fail('no success receipt for corrupt remote bytes'))
    monkeypatch.setattr(backup, 'get_settings', lambda: settings)
    monkeypatch.setattr(backup, 'archive_backend', lambda settings: backend)
    with pytest.raises(RuntimeError, match='remote checksum/length'):
        backup.main()
    assert not target.with_suffix('.receipt.json').exists()


def test_local_only_and_archive_existing_are_exclusive(completed_snapshot, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['backup_crypto_cutover.py', '--local-only', '--archive-existing'])
    with pytest.raises(SystemExit):
        backup.main()
