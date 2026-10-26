import sqlite3
import threading
import time
from decimal import Decimal
from pathlib import Path

from vibetrading.archive import ArchiveMaintenance
from vibetrading.bus import BusMessage, InMemoryEventBus
from vibetrading.models import AssetClass, InstrumentId, TradeEvent, Venue
from vibetrading.parquet_store import ParquetBatchWriter
from vibetrading.settings import RuntimeSettings
from vibetrading.storage_backend import LocalStorageBackend
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.storage_worker import StorageWorker
from vibetrading.maintenance_gate import maintenance_lock
from scripts.migrate_crypto_delivery_ledger import migrate


def test_jetstream_ack_is_one_way() -> None:
    class Raw:
        def __init__(self) -> None:
            self.acked = False

        async def ack(self) -> None:
            self.acked = True

    raw = Raw()
    message = BusMessage({}, "md.trade.test", "event", _raw=raw)
    __import__("asyncio").run(message.ack())
    assert raw.acked


def _settings(tmp_path: Path, extra: str = "") -> RuntimeSettings:
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n{extra}", encoding="utf-8")
    return RuntimeSettings.from_env(env)


def _event() -> TradeEvent:
    instrument = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.EQUITY, raw_symbol="ABC", std_symbol="ABC")
    return TradeEvent(instrument=instrument, venue=Venue.KIS, provider="kis", source_ts_ns=1, sequence=1, price=Decimal("1.2"), quantity=Decimal("3"))


def test_storage_acks_only_after_manifest_commit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    bus = InMemoryEventBus()
    event = _event()
    bus.messages.append(BusMessage(event.model_dump(mode="json"), "md.trade.kis.equity.ABC", event.event_id,
                                   stream_name="MARKET_DATA", stream_generation="test", stream_sequence=7))

    stats = __import__("asyncio").run(StorageWorker(bus, writer).run(0.1))

    assert stats["acked"] == 1
    assert len(store.list_manifest()) == 1
    assert store.is_storage_committed("MARKET_DATA:7")


def test_multi_partition_flush_uses_one_atomic_ledger_commit(tmp_path: Path) -> None:
    settings=_settings(tmp_path,"DELIVERY_LEDGER_FORMAT=compact\n")
    store=RuntimeStore(settings,tmp_path/"batched.sqlite3");writer=ParquetBatchWriter(settings,store)
    statements=[];store._conn.set_trace_callback(statements.append)
    first=_event().model_copy(update={"sequence":1})
    second=_event().model_copy(update={"provider":"other-provider","sequence":2})
    assert writer.add(first,stream_name="CRYPTO_DATA",stream_generation="g",stream_sequence=1,event_id=first.event_id)
    assert writer.add(second,stream_name="CRYPTO_DATA",stream_generation="g",stream_sequence=2,event_id=second.event_id)

    paths=writer.flush_all()

    assert len(paths)==2 and all(path.exists() for path in paths)
    assert sum(statement=="BEGIN IMMEDIATE" for statement in statements)==1
    assert store.delivery_committed("CRYPTO_DATA","g",1)
    assert store.delivery_committed("CRYPTO_DATA","g",2)
    assert {row["file_id"] for row in store.list_manifest()}=={path.stem.removeprefix("part-") for path in paths}
    assert writer.last_committed_ids=={"CRYPTO_DATA:g:1","CRYPTO_DATA:g:2"}
    store.close()


@__import__("pytest").mark.parametrize("compact",[False,True])
def test_multi_partition_manifest_commit_rolls_back_before_any_delivery_ack(tmp_path: Path,compact: bool) -> None:
    settings=_settings(tmp_path,f"DELIVERY_LEDGER_FORMAT={'compact' if compact else 'legacy'}\n")
    store=RuntimeStore(settings,tmp_path/"batched.sqlite3");writer=ParquetBatchWriter(settings,store)
    statements=[];store._conn.set_trace_callback(statements.append)
    first=_event().model_copy(update={"sequence":1})
    second=_event().model_copy(update={"provider":"other-provider","sequence":1})
    for event in (first,second):
        assert writer.add(event,stream_name="CRYPTO_DATA",stream_generation="g",stream_sequence=1,event_id=event.event_id)

    try:
        writer.flush_all()
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate stream sequence should abort the whole flush transaction")

    assert store.list_manifest()==[]
    assert not store.delivery_committed("CRYPTO_DATA","g",1)
    assert not writer.last_committed_ids
    assert len(list(settings.parquet.data_root.rglob("*.parquet")))==2
    assert sum(statement=="BEGIN IMMEDIATE" for statement in statements)==1
    store.close()


def test_storage_persists_authoritative_consumer_ack_floor(tmp_path: Path) -> None:
    class FloorBus(InMemoryEventBus):
        async def consumer_ack_floor(self, stream: str, durable: str):
            assert (stream, durable) == ("CRYPTO_DATA", "crypto-parquet-storage")
            return "test", 7

    settings = _settings(tmp_path, "DELIVERY_LEDGER_FORMAT=compact\n")
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    bus = FloorBus()
    event = _event()
    bus.messages.append(BusMessage(event.model_dump(mode="json"), "crypto.trade.test", event.event_id,
                                   stream_name="CRYPTO_DATA", stream_generation="test", stream_sequence=7))

    __import__("asyncio").run(StorageWorker(bus, writer, domain="crypto").run(0.1))

    assert store.delivery_ack_floor("CRYPTO_DATA", "test") == 7


def test_local_ack_send_does_not_authorize_delivery_pruning(tmp_path: Path) -> None:
    settings=_settings(tmp_path,"DELIVERY_LEDGER_FORMAT=compact\nPARQUET_FLUSH_SECONDS=0.01\n")
    store=RuntimeStore(settings,tmp_path/"compact.sqlite3")
    writer=ParquetBatchWriter(settings,store);bus=InMemoryEventBus();event=_event()
    bus.messages.append(BusMessage(event.model_dump(mode="json"),"crypto.trade.test",event.event_id,
                                   stream_name="CRYPTO_DATA",stream_generation="generation",stream_sequence=1))
    stats=__import__("asyncio").run(StorageWorker(bus,writer,domain="crypto").run(0.1))
    assert stats["acked"]==1
    assert store.delivery_ack_floor("CRYPTO_DATA","generation")==0
    manifest=store.list_manifest()[0]
    store.mark_remote_verified(manifest["file_id"],"remote/data","remote/manifest",manifest["byte_size"],"hash")
    store._conn.execute("UPDATE parquet_delivery_compact SET persisted_ts_ns=?",(time.time_ns()-25*3600*1_000_000_000,))
    assert store.prune_delivery_history(retain_seconds=24*3600,limit=10)==0
    assert store.delivery_ledger_stats()["rows"]==1


def test_compact_delivery_keeps_unacked_or_unverified_rows(tmp_path: Path) -> None:
    settings=_settings(tmp_path,"DELIVERY_LEDGER_FORMAT=compact\n")
    store=RuntimeStore(settings,tmp_path/"compact.sqlite3");writer=ParquetBatchWriter(settings,store);event=_event()
    writer.add(event,stream_name="CRYPTO_DATA",stream_generation="generation",stream_sequence=1,event_id=event.event_id)
    writer.flush_all();store._conn.execute("UPDATE parquet_delivery_compact SET persisted_ts_ns=0")
    assert store.prune_delivery_history(retain_seconds=1,limit=10)==0
    manifest=store.list_manifest()[0]
    store.mark_remote_verified(manifest["file_id"],"remote/data","remote/manifest",manifest["byte_size"],"hash")
    assert store.prune_delivery_history(retain_seconds=1,limit=10)==0


def test_offline_migration_preserves_source_and_compacts_verified_history(tmp_path: Path) -> None:
    database=tmp_path/"crypto.sqlite3";settings=_settings(tmp_path)
    store=RuntimeStore(settings,database);writer=ParquetBatchWriter(settings,store);event=_event()
    writer.add(event,stream_name="CRYPTO_DATA",stream_generation="generation",stream_sequence=1,event_id=event.event_id)
    writer.flush_all();manifest=store.list_manifest()[0]
    store.mark_remote_verified(manifest["file_id"],"remote/data","remote/manifest",manifest["byte_size"],"hash")
    store._conn.execute("UPDATE parquet_delivery SET persisted_ts_ns=0")
    store.record_archive_backup("backup","remote/state","hash",time.time_ns());store.close()

    result=migrate(database,retain_hours=24,min_free_gb=0)

    assert result["compact_rows"]==1
    assert Path(result["preserved_database"]).exists()
    compact_settings=_settings(tmp_path,"DELIVERY_LEDGER_FORMAT=compact\n")
    compact=RuntimeStore(compact_settings,database)
    assert compact.delivery_ledger_stats()["format"]=="compact"
    assert compact.list_manifest()[0]["file_id"]==manifest["file_id"]


def test_active_stream_migration_never_prunes_above_nats_ack_floor(tmp_path: Path) -> None:
    database=tmp_path/"crypto.sqlite3";settings=_settings(tmp_path)
    store=RuntimeStore(settings,database);writer=ParquetBatchWriter(settings,store);event=_event()
    writer.add(event,stream_name="CRYPTO_DATA",stream_generation="active",stream_sequence=10,event_id=event.event_id)
    writer.flush_all();manifest=store.list_manifest()[0]
    store.mark_remote_verified(manifest["file_id"],"remote/data","remote/manifest",manifest["byte_size"],"hash")
    store._conn.execute("UPDATE parquet_delivery SET persisted_ts_ns=0")
    store.record_archive_backup("backup","remote/state","hash",time.time_ns());store.close()

    result=migrate(database,retain_hours=24,min_free_gb=0,stream_drained=False,current_generation="active",acked_through=9)

    assert result["compact_rows"]==1


def test_storage_flush_limit_is_bounded_below_ack_window(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    worker = StorageWorker(settings=settings)

    assert worker._flush_pending_limit == 2048
    assert worker._flush_pending_limit < settings.nats.max_ack_pending


def test_crypto_rss_flush_requires_meaningful_live_buffer(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "STORAGE_FLUSH_BUFFER_BYTES=33554432\n")
    worker = StorageWorker(settings=settings, domain="crypto")

    assert worker._rss_flush_buffer_floor == 8 * 1024 * 1024


def test_crypto_rss_thresholds_respect_cgroup_headroom(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "PARQUET_RSS_FLUSH_BYTES=419430400\nPARQUET_RSS_PAUSE_BYTES=629145600\n",
    )
    worker = StorageWorker(settings=settings, domain="crypto")

    assert worker._rss_thresholds(768 * 1024 * 1024) == (
        576 * 1024 * 1024,
        int(768 * 1024 * 1024 * 0.90),
    )
    assert worker._rss_thresholds(0) == (419430400, 629145600)


def test_archive_verifies_before_local_delete(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(
        tmp_path,
        "ARCHIVE_ENABLED=true\nARCHIVE_BACKEND=local\nARCHIVE_UPLOAD_AFTER_SECONDS=0\nARCHIVE_LOCAL_RETENTION_SECONDS=0\n",
    )
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    writer.add(_event())
    writer.flush_all()

    maintenance = ArchiveMaintenance(settings, store)
    monkeypatch.setattr(maintenance, "_resource_maintenance_allowed", lambda: (True, {"reason": "quiet"}))
    result = maintenance.run_once(force=True)

    assert result["uploaded"] == 1, store.list_manifest()[0].get("last_upload_error")
    row = store.list_manifest()[0]
    assert row["upload_status"] == "remote_verified"
    assert row["local_present"] == 0


def test_archive_reclaims_verified_files_before_upload_batch(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, "ARCHIVE_ENABLED=true\nARCHIVE_BACKEND=local\nARCHIVE_UPLOAD_AFTER_SECONDS=0\n")
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    writer.add(_event())
    writer.flush_all()
    verified = store.list_manifest()[0]
    store.mark_remote_verified(verified["file_id"], "remote/data", "remote/manifest", verified["byte_size"], "test")
    writer.add(_event().model_copy(update={"sequence": 2}))
    writer.flush_all()
    maintenance = ArchiveMaintenance(settings, store)
    monkeypatch.setattr(maintenance, "_resource_maintenance_allowed", lambda: (True, {"reason": "quiet"}))
    original_upload = maintenance._upload

    def upload_after_reclaim(row):
        assert not Path(verified["path"]).exists()
        original_upload(row)

    maintenance._upload = upload_after_reclaim
    result = maintenance.run_once(force=True)
    assert result["reclaimed"]["files"] >= 1
    assert result["uploaded"] >= 1


def test_archive_superseded_walk_uses_index(tmp_path: Path) -> None:
    store = RuntimeStore(_settings(tmp_path), tmp_path / "state.sqlite3")
    indexes = {row[1] for row in store._conn.execute("PRAGMA index_list(parquet_manifest)")}
    assert "idx_manifest_superseded_by" in indexes
    statements = []
    store._conn.set_trace_callback(statements.append)
    store.deletion_candidates(older_than_ns=0, force=True, limit=1)
    sql = next(statement for statement in statements if "WITH RECURSIVE chain" in statement)
    plan = [row[3] for row in store._conn.execute("EXPLAIN QUERY PLAN " + sql)]
    assert any("idx_manifest_local_reclaim" in step for step in plan)
    assert any("SEARCH replacement USING INDEX idx_manifest_file_id" in step for step in plan)


def test_archive_reclaims_files_in_bounded_transactions(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, "ARCHIVE_ENABLED=true\nARCHIVE_BACKEND=local\n")
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    for _ in range(3):
        writer.add(_event())
        writer.flush_all()
    for row in store.list_manifest():
        store.mark_remote_verified(row["file_id"], "remote/data", "remote/manifest", row["byte_size"], "test")
    statements = []
    store._conn.set_trace_callback(statements.append)
    maintenance = ArchiveMaintenance(settings, store)
    monkeypatch.setattr(maintenance, "_resource_maintenance_allowed", lambda: (True, {"reason": "quiet"}))
    result = maintenance._reclaim_verified(force=True, older_than_ns=0, limit=3)
    assert result["files"] == 3
    assert sum(statement == "BEGIN IMMEDIATE" for statement in statements) == 2


def test_large_sqlite_wal_can_be_truncated(tmp_path: Path) -> None:
    store = RuntimeStore(_settings(tmp_path), tmp_path / "state.sqlite3")
    store.collection_generation()
    result = store.checkpoint_wal_if_large(threshold_bytes=1)
    assert result["attempted"] is True
    assert result["busy"] is False
    assert result["after_bytes"] <= result["before_bytes"]


def test_disk_pressure_does_not_repeat_full_sqlite_backup(tmp_path: Path, monkeypatch) -> None:
    # This tests scheduling, not the host Linux PSI probe. A slow Windows
    # test run must not accidentally turn it into an unavailable-PSI test.
    monkeypatch.setattr(ArchiveMaintenance, '_io_full_avg10', staticmethod(lambda: 0.0))
    from collections import namedtuple
    usage = namedtuple('usage', 'total used free')(100 * 1024**3, 50 * 1024**3, 50 * 1024**3)
    monkeypatch.setattr('shutil.disk_usage', lambda _path: usage)
    settings = _settings(
        tmp_path,
        "ARCHIVE_ENABLED=true\nARCHIVE_BACKEND=local\nARCHIVE_SQLITE_BACKUP_INTERVAL_SECONDS=3600\n",
    )
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    maintenance = ArchiveMaintenance(settings, store)
    first = maintenance._backup_sqlite_if_due(force=True)
    second = maintenance._backup_sqlite_if_due(force=True)

    assert first["ran"] is True
    assert second == {"ran": False, "reason": "not_due"}


def test_sqlite_backup_verifies_remote_object_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ArchiveMaintenance, '_io_full_avg10', staticmethod(lambda: 0.0))
    from collections import namedtuple
    usage = namedtuple('usage', 'total used free')(100 * 1024**3, 50 * 1024**3, 50 * 1024**3)
    monkeypatch.setattr('shutil.disk_usage', lambda _path: usage)
    settings = _settings(tmp_path, "ARCHIVE_ENABLED=true\nARCHIVE_BACKEND=local\n")
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")

    class CountingBackend(LocalStorageBackend):
        calls = 0

        def verify(self, key, *, size_bytes, sha256):
            self.calls += 1
            return super().verify(key, size_bytes=size_bytes, sha256=sha256)

    backend = CountingBackend(tmp_path / "archive")
    monkeypatch.setattr("vibetrading.archive.archive_backend", lambda _settings: backend)
    result = ArchiveMaintenance(settings, store)._backup_sqlite_if_due(force=True)
    assert result["ran"] is True
    assert backend.calls == 1


def test_orphan_is_archived_before_local_removal(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, "ARCHIVE_ENABLED=true\nARCHIVE_BACKEND=local\n")
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    orphan = settings.parquet.data_root / "event_type=trade" / "part-unregistered.parquet.orphan"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"recovery data")
    import os
    import time
    old = time.time() - 7200
    os.utime(orphan, (old, old))

    maintenance = ArchiveMaintenance(settings, store)
    monkeypatch.setattr(maintenance, "_resource_maintenance_allowed", lambda: (True, {"reason": "quiet"}))
    result = maintenance._cleanup_orphans(force=True)

    assert result == {"files": 1, "bytes": len(b"recovery data")}
    assert not orphan.exists()
    assert any(path.read_bytes() == b"recovery data" for path in (settings.data_root / "archive_test").rglob("*") if path.is_file())


def test_existing_catalogue_init_does_not_rewrite_manifest(tmp_path: Path) -> None:
    store = RuntimeStore(_settings(tmp_path), tmp_path / "state.sqlite3")
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    store._init_schema()

    assert not any("UPDATE parquet_manifest SET file_id" in statement for statement in statements)
    assert not any(statement.lstrip().upper().startswith(("CREATE ", "ALTER ")) for statement in statements)


def test_shared_maintenance_lock_serializes_heavy_io(tmp_path: Path) -> None:
    with maintenance_lock(tmp_path) as first:
        assert first["acquired"] is True
        with maintenance_lock(tmp_path) as second:
            assert second == {"acquired": False, "reason": "heavy_maintenance_busy"}


def test_full_backups_share_cooldown_across_market_and_crypto(tmp_path: Path) -> None:
    with maintenance_lock(tmp_path, backup_domain="market", backup_cooldown_seconds=1800) as first:
        assert first["acquired"] is True
    with maintenance_lock(tmp_path, backup_domain="crypto", backup_cooldown_seconds=1800) as second:
        assert second["acquired"] is False
        assert second["reason"] == "shared_backup_cooldown"


def test_store_waits_for_another_process_writer_without_collector_failure(tmp_path: Path) -> None:
    """A transient storage/maintenance write must not abort a collector boot."""
    settings = _settings(tmp_path)
    database = tmp_path / "state.sqlite3"
    RuntimeStore(settings, database).close()
    locker = sqlite3.connect(database, isolation_level=None)
    locker.execute("BEGIN IMMEDIATE")
    errors: list[BaseException] = []

    def open_and_persist() -> None:
        try:
            store = RuntimeStore(settings, database)
            store.write_universe("same-universe", "kiwoom", {"items": ["A"]}, 1)
            store.close()
        except BaseException as exc:  # assertion is made in the parent thread
            errors.append(exc)

    worker = threading.Thread(target=open_and_persist)
    worker.start()
    try:
        # The write is intentionally blocked, rather than immediately raising
        # OperationalError: database is locked as it did at collector boot.
        time.sleep(0.1)
        assert worker.is_alive()
    finally:
        locker.execute("COMMIT")
        locker.close()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert not errors


def test_identical_universe_resolution_does_not_write_again(tmp_path: Path) -> None:
    store = RuntimeStore(_settings(tmp_path), tmp_path / "state.sqlite3")
    store.write_universe("stable-id", "toss", {"items": ["069500"]}, 1)
    changes = store._conn.total_changes
    store.write_universe("stable-id", "toss", {"items": ["069500"]}, 2)

    assert store._conn.total_changes == changes


def test_event_dedup_query_uses_partial_index(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    event = _event()
    writer.add(event, stream_name="MARKET_DATA", stream_generation="g", stream_sequence=1, event_id=event.event_id)
    writer.flush_all()
    plan = " ".join(
        str(row[3]) for row in store._conn.execute(
            "EXPLAIN QUERY PLAN SELECT 1 FROM parquet_delivery WHERE event_id=? AND event_id<>'' LIMIT 1",
            (event.event_id,),
        )
    )
    assert "idx_delivery_event_id" in plan

