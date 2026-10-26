import json
import time
from decimal import Decimal
from pathlib import Path

import pytest

from vibetrading.archive import ArchiveMaintenance
from core.models import AssetClass, InstrumentId, TradeEvent, Venue
from vibetrading.parquet_store import ParquetBatchWriter
from vibetrading.settings import RuntimeSettings
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.storage_backend import archive_backend, sha256_file


def relief_settings(tmp_path, monkeypatch, pressure=50, pending=0):
    env = tmp_path / '.env'
    env.write_text(f'VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n'
                   'ARCHIVE_ENABLED=true\nARCHIVE_BACKEND=local\n'
                   'DELIVERY_LEDGER_FORMAT=compact\n'
                   'CRYPTO_EVENT_INDEX_FORMAT=immutable-runs-v1\n'
                   'ARCHIVE_RELIEF_MAX_IO_PRESSURE=90\n'
                   'ARCHIVE_DELIVERY_PRUNE_MAX_IO_PRESSURE=70\n'
                   'ARCHIVE_BACKUP_MAX_IO_PRESSURE=5\n'
                   'ARCHIVE_ORPHAN_MAX_IO_PRESSURE=5\n', encoding='utf-8')
    settings = RuntimeSettings.from_env(env)
    health = settings.state_dir / 'health' / 'crypto-storage.json'
    health.parent.mkdir(parents=True)
    health.write_text(json.dumps({'ts_ns': time.time_ns(), 'pending': pending}))
    read = Path.read_text
    def read_pressure(path, *a, **kw):
        if str(path).replace('\\', '/') == '/proc/pressure/io':
            return f'full avg10={pressure} avg60=50 avg300=50 total=1\n'
        return read(path, *a, **kw)
    monkeypatch.setattr(Path, 'read_text', read_pressure)
    return settings


def test_busy_ingest_still_uploads_verifies_and_reclaims(tmp_path, monkeypatch):
    settings = relief_settings(tmp_path, monkeypatch)
    store = RuntimeStore(settings)
    writer = ParquetBatchWriter(settings, store)
    event = TradeEvent(instrument=InstrumentId(venue=Venue.KIS, asset_class=AssetClass.EQUITY,
                      raw_symbol='ABC', std_symbol='ABC'), venue=Venue.KIS, provider='kis',
                      source_ts_ns=time.time_ns(), sequence=1, price=Decimal('1'), quantity=Decimal('1'))
    for sequence in (1, 2):
        writer.add(event.model_copy(update={'sequence': sequence, 'event_id': f'event-{sequence}'}))
        writer.flush_all()
    maintenance = ArchiveMaintenance(settings, store)
    created = []
    factory = archive_backend
    monkeypatch.setattr('vibetrading.archive.archive_backend', lambda s: created.append(True) or factory(s))
    rows = store.list_manifest()
    # The unverified file must never become a deletion candidate.
    maintenance._upload(rows[0])
    safe = store.deletion_candidates(older_than_ns=0, force=True, limit=10)
    assert [row['file_id'] for row in safe] == [rows[0]['file_id']]
    monkeypatch.setattr(store, 'backup_to', lambda *a, **k: pytest.fail('bulk backup ran under ingest pressure'))
    result = maintenance.run_once(force=True)
    assert result['uploaded'] == 1
    assert result['reclaimed']['files'] == 2
    assert result['sqlite_backup']['reason'] == 'bulk_io_busy'
    assert result['orphan_cleanup']['reason'] == 'bulk_io_busy'
    assert created == [True]
    assert result['cycle_seconds'] >= result['phase_seconds']['upload']
    for row in store.list_manifest():
        assert not Path(row['path']).exists()
        remote = archive_backend(settings)._target(row['remote_key'])
        assert sha256_file(remote) == row['file_sha256']
    store.close()


@pytest.mark.parametrize('pressure,pending,reason', [
    (75, 0, 'storage_io_above_prune_limit'),
    (50, 4000, 'storage_pending_above_prune_limit'),
])
def test_relief_does_not_bypass_ledger_gate(tmp_path, monkeypatch, pressure, pending, reason):
    maintenance = ArchiveMaintenance(relief_settings(tmp_path, monkeypatch, pressure, pending))
    assert maintenance._resource_maintenance_allowed()[0]
    allowed, state = maintenance._heavy_maintenance_allowed()
    assert not allowed and state['reason'] == reason
    assert maintenance._store is None


def test_extreme_pressure_still_yields_without_sqlite(tmp_path, monkeypatch):
    maintenance = ArchiveMaintenance(relief_settings(tmp_path, monkeypatch, pressure=95))
    assert maintenance.run_once(force=True)['reason'] == 'storage_or_io_busy'
    assert maintenance._store is None


def test_bulk_work_yields_before_database_or_tree_scan(tmp_path, monkeypatch):
    maintenance = ArchiveMaintenance(relief_settings(tmp_path, monkeypatch))
    monkeypatch.setattr(Path, 'glob', lambda *a, **k: pytest.fail('unbounded tree scan'))
    assert maintenance._backup_sqlite_if_due(force=True)['reason'] == 'bulk_io_busy'
    assert maintenance._cleanup_orphans(force=True)['reason'] == 'bulk_io_busy'
    assert maintenance._store is None
