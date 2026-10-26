import json
import sqlite3
import time
from pathlib import Path

import pytest

from storage.archive import ArchiveMaintenance
from runtime.settings import RuntimeSettings
from storage.sqlite_store import MaintenanceDeferred, RuntimeStore


def settings_for(path: Path, domain: str = 'market') -> RuntimeSettings:
    env = path / '.env'
    env.write_text(f'VIBETRADING_DATA_ROOT={path.as_posix()}\nARCHIVE_ENABLED=true\n'
                   'ARCHIVE_BACKEND=local\n'
                   f'DELIVERY_LEDGER_FORMAT={"compact" if domain == "crypto" else "legacy"}\n', encoding='utf-8')
    return RuntimeSettings.from_env(env)


@pytest.mark.parametrize('domain', ['market', 'crypto'])
@pytest.mark.parametrize('force', [False, True])
def test_busy_cycle_never_opens_sqlite(tmp_path, monkeypatch, domain, force):
    settings = settings_for(tmp_path, domain)
    component = 'crypto-storage' if domain == 'crypto' else 'storage'
    health = settings.state_dir / 'health' / f'{component}.json'
    health.parent.mkdir(parents=True)
    health.write_text(json.dumps({'ts_ns': time.time_ns(), 'pending': 0}))

    def no_database(*args, **kwargs):
        raise AssertionError('busy maintenance opened SQLite')

    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if str(path).replace('\\', '/') == '/proc/pressure/io':
            return 'full avg10=90.00 avg60=80.00 avg300=70.00 total=1\n'
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr("storage.archive.RuntimeStore", no_database)
    result = ArchiveMaintenance(settings).run_once(force=force)
    assert result['ran'] is False
    assert result['reason'] == 'storage_or_io_busy'
    assert result['heavy_maintenance']['io_full_avg10'] == 90


def test_missing_health_defers_without_opening_sqlite(tmp_path):
    maintenance = ArchiveMaintenance(settings_for(tmp_path))
    assert maintenance.run_once()['reason'] == 'storage_health_unavailable'
    assert maintenance._store is None


def test_pressure_rises_during_cycle_stops_remaining_work(tmp_path, monkeypatch):
    store = RuntimeStore(settings_for(tmp_path))
    maintenance = ArchiveMaintenance(store.settings, store)
    busy = False
    monkeypatch.setattr(maintenance, '_resource_maintenance_allowed',
                        lambda: (not busy, {'reason': 'storage_or_io_busy' if busy else 'quiet'}))
    def reclaim(**kwargs):
        nonlocal busy
        busy = True
        return {'files': 1, 'bytes': 123}
    monkeypatch.setattr(maintenance, '_reclaim_verified', reclaim)
    def forbidden(*args, **kwargs):
        raise AssertionError('work continued after pressure rose')
    for name in ['checkpoint_wal_if_large', 'archive_candidates', 'delivery_ledger_stats', 'last_archive_backup_ts_ns']:
        monkeypatch.setattr(store, name, forbidden)
    result = maintenance.run_once(force=True)
    assert result['deferred_stage'] == 'wal_checkpoint'
    assert result['reclaimed']['files'] == 1
    assert result['deferred']
    store.close()


def test_quiet_cycle_does_not_scan_ledger_or_repeat_empty_reclaim(tmp_path, monkeypatch):
    store = RuntimeStore(settings_for(tmp_path))
    store.record_archive_backup('backup', 'remote', 'sha', time.time_ns())
    maintenance = ArchiveMaintenance(store.settings, store)
    monkeypatch.setattr(maintenance, '_resource_maintenance_allowed', lambda: (True, {'reason': 'quiet'}))
    statements = []
    store._conn.set_trace_callback(statements.append)
    result = maintenance.run_once(force=True)
    assert not result.get('deferred')
    assert result['ledger']['measured'] is False
    assert not any('COUNT(*)' in query.upper() for query in statements)
    assert sum('WITH RECURSIVE chain' in query for query in statements) == 1
    store.close()


def test_archive_allowed_with_normal_storage_backlog_but_ledger_prune_yields(tmp_path, monkeypatch):
    settings=settings_for(tmp_path,'crypto')
    health=settings.state_dir/'health'/'crypto-storage.json'
    health.parent.mkdir(parents=True)
    health.write_text(json.dumps({'ts_ns':time.time_ns(),'pending':4000}))
    original_read=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda path,*a,**k: 'full avg10=1.00 avg60=1.00 avg300=1.00 total=1\n'
                        if str(path).replace('\\','/')=='/proc/pressure/io' else original_read(path,*a,**k))
    maintenance=ArchiveMaintenance(settings)
    assert maintenance._resource_maintenance_allowed()[0] is True
    allowed,state=maintenance._heavy_maintenance_allowed()
    assert allowed is False
    assert state['pending']==4000
    assert state['reason']=='storage_pending_above_prune_limit'


def test_backup_disallowed_does_not_open_database(tmp_path):
    maintenance = ArchiveMaintenance(settings_for(tmp_path))
    assert maintenance._backup_sqlite_if_due(force=True, allowed=False)['ran'] is False


def test_delivery_prune_is_blocked_until_ack_floor_provenance_is_trusted(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\nARCHIVE_ENABLED=true\n"
                   "ARCHIVE_BACKEND=local\nARCHIVE_DELIVERY_HISTORY_PRUNE_ENABLED=true\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    maintenance = ArchiveMaintenance(settings, store)
    monkeypatch.setattr(maintenance, "_resource_maintenance_allowed", lambda: (True, {"reason": "quiet"}))
    monkeypatch.setattr(maintenance, "_heavy_maintenance_allowed", lambda: (True, {"reason": "quiet"}))
    monkeypatch.setattr(store, "prune_delivery_history", lambda **_: pytest.fail("unsafe prune was called"))
    monkeypatch.setattr(maintenance, "_reclaim_verified", lambda **_: {"files": 0, "bytes": 0})
    monkeypatch.setattr(store, "checkpoint_wal_if_large", lambda *_: {"attempted": False})
    monkeypatch.setattr(store, "archive_candidates", lambda **_: [])
    monkeypatch.setattr(maintenance, "_backup_sqlite_if_due", lambda **_: {"ran": False})
    monkeypatch.setattr(maintenance, "_cleanup_orphans", lambda **_: {"removed": 0})

    result = maintenance.run_once(force=False)

    assert result["pruned_deliveries"] == 0
    assert result["delivery_prune_reason"] == "ack_floor_provenance_unverified"


@pytest.mark.parametrize('cause', ['pressure', 'time'])
def test_sqlite_scan_can_yield_and_connection_still_commits(tmp_path, monkeypatch, cause):
    store = RuntimeStore(settings_for(tmp_path))
    ticks = iter([0.0, 0.3, 0.6, 0.9])
    monkeypatch.setattr("storage.sqlite_store.time.monotonic", lambda: next(ticks, 2.0))
    checks = 0
    def check():
        nonlocal checks
        checks += 1
        if cause == 'pressure' and checks > 1:
            raise MaintenanceDeferred('storage_or_io_busy')

    reason = 'storage_or_io_busy' if cause == 'pressure' else 'maintenance_read_time_budget'
    with pytest.raises(MaintenanceDeferred, match=reason):
        with store.maintenance_read_guard(check, max_seconds=0.2 if cause == 'time' else 3):
            store._conn.execute('WITH RECURSIVE seq(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM seq WHERE n<100000) SELECT SUM(n) FROM seq').fetchone()
    # The handler is removed. Subsequent catalogue writes and reads are intact.
    store.record_archive_backup('after', 'remote', 'sha', 123)
    assert store.last_archive_backup_ts_ns() == 123
    assert not store._conn.in_transaction
    store.close()


def test_sqlite_read_guard_preserves_unrelated_errors(tmp_path):
    store = RuntimeStore(settings_for(tmp_path))
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        with store.maintenance_read_guard(lambda: None):
            store._conn.execute('SELECT * FROM missing_table')
    store.close()


def test_run_index_crypto_upload_is_not_blocked_by_live_truncate(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text(f'VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\nARCHIVE_ENABLED=true\n'
                   'ARCHIVE_BACKEND=local\nDELIVERY_LEDGER_FORMAT=compact\n'
                   'CRYPTO_EVENT_INDEX_FORMAT=immutable-runs-v1\n', encoding='utf-8')
    store = RuntimeStore(RuntimeSettings.from_env(env))
    store.record_archive_backup('recent', 'remote', 'sha', time.time_ns())
    maintenance = ArchiveMaintenance(store.settings, store)
    monkeypatch.setattr(maintenance, '_resource_maintenance_allowed', lambda: (True, {'reason': 'quiet'}))
    monkeypatch.setattr(store, 'checkpoint_wal_if_large', lambda *_: pytest.fail('live TRUNCATE attempted'))
    queried = []
    monkeypatch.setattr(store, 'archive_candidates', lambda **_: queried.append(True) or [])
    result = maintenance.run_once(force=False)
    assert queried == [True]
    assert result['wal_checkpoint']['reason'] == 'crypto_autocheckpoint_enabled'
    assert store._conn.execute('PRAGMA wal_autocheckpoint').fetchone()[0] == 1000
    store.close()
