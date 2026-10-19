"""Offline integrity faults use fresh local Parquet/catalogue copies only."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from vibetrading import parquet_store, storage_backend
from vibetrading.archive import ArchiveMaintenance
from vibetrading.bus import BusMessage, ConfirmedAckFloor, InMemoryEventBus
from vibetrading.compactor import ParquetCompactor
from vibetrading.models import AssetClass, InstrumentId, TradeEvent, Venue
from vibetrading.parquet_store import ParquetBatchWriter, ParquetHistoryReader, file_sha256
from vibetrading.settings import RuntimeSettings
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.storage_backend import LocalStorageBackend, NcloudStorageBackend
from vibetrading.storage_worker import StorageWorker


class RecordedLocalBackend(LocalStorageBackend):
    def __init__(self, root):
        super().__init__(root)
        self.put_calls = 0
        self.download_calls = 0

    def put_file(self, source, key, *, sha256):
        self.put_calls += 1
        return super().put_file(source, key, sha256=sha256)

    def download_file(self, key, destination):
        self.download_calls += 1
        return super().download_file(key, destination)


@pytest.fixture
def dataset(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\nARCHIVE_BACKEND=local\nDELIVERY_LEDGER_FORMAT=legacy\n",
        encoding="utf-8",
    )
    settings = RuntimeSettings.from_env(env)
    store = RuntimeStore(settings, tmp_path / "catalogue.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    instrument = InstrumentId(
        venue=Venue.KIS, asset_class=AssetClass.EQUITY, raw_symbol="ABC", std_symbol="ABC"
    )
    for sequence in (1, 2):
        writer.add(TradeEvent(
            instrument=instrument, venue=Venue.KIS, provider="kis", source_ts_ns=sequence,
            sequence=sequence, price=Decimal("100.123456789012345678"), quantity=Decimal(sequence),
        ))
        writer.flush_all()
    backend = RecordedLocalBackend(tmp_path / "remote")
    maintenance = ArchiveMaintenance(settings, store)
    maintenance._backend = backend
    rows = store.list_manifest(active_only=True)
    try:
        yield settings, store, maintenance, backend, rows, instrument.key
    finally:
        store.close()


def archived(dataset, monkeypatch):
    settings, store, maintenance, backend, rows, key = dataset
    for row in rows:
        maintenance._upload(row)
        Path(row["path"]).unlink()  # This fixture owns these local test copies.
        store.mark_local_deleted(row["file_id"])
    monkeypatch.setattr(storage_backend, "archive_backend", lambda _settings: backend)
    return settings, store, backend, store.list_manifest(active_only=True), key


def test_archive_changed_source_never_becomes_a_new_verified_receipt(dataset):
    _, store, maintenance, backend, rows, _ = dataset
    source = Path(rows[0]["path"])
    source.write_bytes(Path(rows[1]["path"]).read_bytes())
    altered = source.read_bytes()

    with pytest.raises(RuntimeError, match="local source checksum mismatch"):
        maintenance._upload(rows[0])

    current = store.local_manifest(source)
    assert backend.put_calls == 0
    assert current["file_sha256"] == rows[0]["file_sha256"]
    assert current["upload_status"] == "upload_failed"
    assert current["verified_ts_ns"] is None
    assert current["lease_owner"] is None
    assert source.read_bytes() == altered


@pytest.mark.parametrize("digest", ("bad", "z" * 64, " " * 64))
def test_nonempty_invalid_digest_is_not_accepted_as_legacy(dataset, digest):
    _, store, maintenance, backend, rows, _ = dataset
    row = dict(rows[0], file_sha256=digest)
    with pytest.raises(RuntimeError, match="invalid catalogued file checksum"):
        maintenance._upload(row)
    with pytest.raises(RuntimeError, match="invalid catalogued file checksum"):
        ParquetCompactor(maintenance.settings, store)._compact([row, rows[1]])
    assert backend.put_calls == 0
    assert len(store.list_manifest(active_only=True)) == 2
    assert all(Path(source["path"]).exists() for source in rows)


@pytest.mark.parametrize("legacy", (False, True))
def test_normal_and_missing_legacy_digest_keep_full_verified_upload(dataset, legacy):
    _, store, maintenance, backend, rows, _ = dataset
    row = dict(rows[0])
    if legacy:
        row["file_sha256"] = ""
        store._write("UPDATE parquet_manifest SET file_sha256='' WHERE file_id=?", (row["file_id"],))
    maintenance._upload(row)
    current = store.local_manifest(Path(row["path"]))
    assert current["upload_status"] == "remote_verified"
    assert current["file_sha256"] == file_sha256(Path(row["path"]))
    assert current["remote_manifest_key"]
    assert current["local_present"] == 1
    assert backend.put_calls == 1


def test_compactor_rejects_altered_valid_parquet_without_superseding_sources(dataset):
    settings, store, _, _, rows, _ = dataset
    source = Path(rows[0]["path"])
    source.write_bytes(Path(rows[1]["path"]).read_bytes())
    with pytest.raises(RuntimeError, match="local source checksum mismatch"):
        ParquetCompactor(settings, store)._compact(rows)
    current = store.list_manifest()
    assert len(current) == 2
    assert all(row["lifecycle_state"] == "active" for row in current)
    assert all(Path(row["path"]).exists() for row in rows)


@pytest.mark.parametrize("legacy", (False, True))
def test_valid_compaction_preserves_all_decoded_rows_and_source_files(dataset, legacy):
    settings, store, _, _, rows, _ = dataset
    sources = [dict(row) for row in rows]
    if legacy:
        for row in sources:
            row["file_sha256"] = ""
    expected = [row for source in sources for row in pq.ParquetFile(source["path"]).read().to_pylist()]
    result = ParquetCompactor(settings, store)._compact(sources)
    assert pq.ParquetFile(result["output"]).read().to_pylist() == expected
    assert len(store.list_manifest(active_only=True)) == 1
    assert all(Path(row["path"]).exists() for row in sources)


def test_oversized_remote_file_is_rejected_before_download(dataset, monkeypatch):
    settings, store, backend, rows, _ = archived(dataset, monkeypatch)
    row = rows[0]
    settings = replace(settings, archive=replace(settings.archive, cache_max_bytes=row["byte_size"] - 1))
    reader = ParquetHistoryReader(settings.parquet.data_root, store, settings)
    with pytest.raises(RuntimeError, match="exceeds cache byte budget"):
        reader._available_path(row)
    assert backend.download_calls == 0
    assert store.archive_cache_rows() == []
    assert store.local_manifest(Path(row["path"]))["lease_owner"] is None


def test_available_remote_cache_path_exists_when_returned(dataset, monkeypatch):
    settings, store, backend, rows, _ = archived(dataset, monkeypatch)
    reader = ParquetHistoryReader(settings.parquet.data_root, store, settings)
    path = reader._available_path(rows[0])
    assert path.exists()
    assert file_sha256(path) == rows[0]["file_sha256"]
    assert backend.download_calls == 1


def test_preexisting_valid_but_altered_cache_is_never_used_as_history(dataset, monkeypatch):
    settings, store, backend, rows, key = archived(dataset, monkeypatch)
    reader = ParquetHistoryReader(settings.parquet.data_root, store, settings)
    first = reader._available_path(rows[0])
    alternate = reader._available_path(rows[1])
    first.write_bytes(alternate.read_bytes())
    with pytest.raises(RuntimeError, match="archive cache size mismatch|local source checksum mismatch"):
        reader._available_path(rows[0])
    assert backend.download_calls == 2
    # Reopening history cannot bless a preexisting cache from another receipt.
    with pytest.raises(RuntimeError, match="archive cache size mismatch|local source checksum mismatch"):
        reader.history(instrument_key=key, event_type="trade")


def test_active_reader_pin_prevents_eviction_and_new_download_overcommit(dataset, monkeypatch):
    settings, store, backend, rows, _ = archived(dataset, monkeypatch)
    settings = replace(settings, archive=replace(settings.archive, cache_max_bytes=max(row["byte_size"] for row in rows)))
    reader = ParquetHistoryReader(settings.parquet.data_root, store, settings)
    first = reader._available_path(rows[0])
    with reader._reader_pin(rows[0]):
        with pytest.raises(RuntimeError, match="cache byte budget unavailable"):
            reader._available_path(rows[1])
        assert first.exists()
        assert backend.download_calls == 1
        assert sum(row["byte_size"] for row in store.archive_cache_rows()) <= settings.archive.cache_max_bytes
    assert {row["file_id"] for row in store.archive_cache_rows()} == {rows[0]["file_id"]}


def test_history_open_handle_survives_lease_expiry_and_competing_trim(dataset, monkeypatch):
    settings, store, _, rows, key = archived(dataset, monkeypatch)
    reader = ParquetHistoryReader(settings.parquet.data_root, store, settings)
    trim_settings = replace(settings, archive=replace(settings.archive, cache_max_bytes=0))
    trimmer = ParquetHistoryReader(settings.parquet.data_root, store, trim_settings)
    expected = []
    for row in rows:
        expected.extend(pq.ParquetFile(reader._available_path(row)).read().to_pylist())
    opened = []

    def open_then_trim(handle):
        assert hasattr(handle, "read") and not handle.closed
        opened.append(handle.name)
        store._write("UPDATE parquet_manifest SET lease_until_ts_ns=0")
        try:
            trimmer._trim_cache()
        except RuntimeError as exc:
            # Windows denies unlinking an open handle; that is a valid pin.
            assert "cache byte budget unavailable" in str(exc)
        return pq.ParquetFile(handle)

    monkeypatch.setattr(parquet_store, "_require_pyarrow", lambda: (pa, SimpleNamespace(ParquetFile=open_then_trim)))
    actual = reader.history(instrument_key=key, event_type="trade")
    assert actual == sorted(expected, key=lambda row: row["source_ts_ns"])
    assert len(opened) == len(rows)


def test_download_failure_releases_cache_reservation_and_manifest_pin(dataset, monkeypatch):
    settings, store, backend, rows, _ = archived(dataset, monkeypatch)
    def unavailable(_key, _destination):
        raise OSError("offline fixture download failed")
    monkeypatch.setattr(backend, "download_file", unavailable)
    reader = ParquetHistoryReader(settings.parquet.data_root, store, settings)
    with pytest.raises(OSError, match="offline fixture download failed"):
        reader._available_path(rows[0])
    assert store.archive_cache_rows() == []
    assert store.local_manifest(Path(rows[0]["path"]))["lease_owner"] is None


def partial_download_backend(kind, local_backend, remote_payload, monkeypatch):
    """Fail after writing real bytes, without an SDK, network or credentials."""
    if kind == "local":
        def partial_copy(source, destination):
            destination.write(source.read(31))
            raise OSError("offline partial transfer")
        monkeypatch.setattr(local_backend, "_copy", partial_copy)
        return local_backend

    def partial_s3_download(_bucket, _key, filename):
        Path(filename).write_bytes(remote_payload[:31])
        raise OSError("offline partial transfer")

    backend = object.__new__(NcloudStorageBackend)
    backend.bucket = "offline-fixture"
    backend._client = SimpleNamespace(download_file=partial_s3_download)
    return backend


@pytest.mark.parametrize("kind", ("local", "ncloud"))
def test_partial_backend_download_preserves_existing_destination_and_source(dataset, monkeypatch, kind):
    settings, _, maintenance, backend, rows, _ = dataset
    row = rows[0]
    maintenance._upload(row)
    source = Path(row["path"])
    source_before = source.read_bytes()
    remote = backend._target(maintenance._key(row))
    remote_before = remote.read_bytes()
    failing = partial_download_backend(kind, backend, remote_before, monkeypatch)
    destination = settings.data_root / "existing-reader.parquet"
    destination.write_bytes(b"existing verified reader bytes")
    for _ in range(3):
        with pytest.raises(OSError, match="offline partial transfer"):
            failing.download_file(maintenance._key(row), destination)
        assert destination.read_bytes() == b"existing verified reader bytes"
        assert not destination.with_name(destination.name + ".downloading").exists()
        assert source.read_bytes() == source_before
        assert remote.read_bytes() == remote_before


@pytest.mark.parametrize("kind", ("local", "ncloud", "unmanaged_partial"))
def test_partial_history_download_retries_leave_no_unbudgeted_staging(dataset, monkeypatch, kind):
    settings, store, backend, rows, _ = archived(dataset, monkeypatch)
    row = rows[0]
    remote = backend._target(row["remote_key"])
    remote_before = remote.read_bytes()
    if kind == "unmanaged_partial":
        # The reader also defends against older/custom backend implementations
        # that leave both of its unique attempt paths behind on failure.
        def partial_download(_key, destination):
            destination.write_bytes(remote_before[:17])
            destination.with_name(destination.name + ".downloading").write_bytes(remote_before[:31])
            raise OSError("offline partial transfer")
        failing = SimpleNamespace(download_file=partial_download)
    else:
        failing = partial_download_backend(kind, backend, remote_before, monkeypatch)
    monkeypatch.setattr(storage_backend, "archive_backend", lambda _settings: failing)
    cache_dir = settings.data_root / "archive_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    unrelated = cache_dir / "other-reader.parquet"
    unrelated.write_bytes(b"another reader owns this")
    reader = ParquetHistoryReader(settings.parquet.data_root, store, settings)
    for _ in range(3):
        with pytest.raises(OSError, match="offline partial transfer"):
            reader._available_path(row)
        assert store.archive_cache_rows() == []
        assert store.local_manifest(Path(row["path"]))["lease_owner"] is None
        assert set(cache_dir.iterdir()) == {unrelated}
        assert unrelated.read_bytes() == b"another reader owns this"
        assert remote.read_bytes() == remote_before


def test_expired_download_cannot_replace_or_delete_another_readers_cache(dataset, monkeypatch):
    settings, store, backend, rows, _ = archived(dataset, monkeypatch)
    row = rows[0]
    final = settings.data_root / "archive_cache" / f"{row['file_id']}.parquet"
    original_download = backend.download_file

    def competing_download(key, temporary):
        original_download(key, temporary)
        store._write("UPDATE parquet_manifest SET lease_until_ts_ns=0 WHERE file_id=?", (row["file_id"],))
        assert store.acquire_lease(row["file_id"], "other-reader")
        final.write_bytes(temporary.read_bytes())
        store.touch_archive_cache(row["file_id"], final, final.stat().st_size)

    monkeypatch.setattr(backend, "download_file", competing_download)
    reader = ParquetHistoryReader(settings.parquet.data_root, store, settings)
    try:
        with pytest.raises(RuntimeError, match="lease lost during download"):
            reader._available_path(row)
        assert final.exists()
        assert file_sha256(final) == row["file_sha256"]
        assert {entry["file_id"] for entry in store.archive_cache_rows()} == {row["file_id"]}
        assert store.local_manifest(Path(row["path"]))["lease_owner"] == "other-reader"
        assert not list(final.parent.glob("*.history-*.tmp"))
    finally:
        store.release_lease(row["file_id"], "other-reader")


@pytest.mark.parametrize("stream,with_crypto_receipt", (("MARKET_DATA", False), ("CRYPTO_DATA", False), ("CRYPTO_DATA", True)))
def test_legacy_pruning_is_unsupported_and_redelivery_stays_deduplicated(dataset, stream, with_crypto_receipt):
    settings, store, maintenance, _, _, _ = dataset
    writer = ParquetBatchWriter(settings, store)
    event = TradeEvent(
        instrument=InstrumentId(venue=Venue.KIS, asset_class=AssetClass.EQUITY, raw_symbol="ABC", std_symbol="ABC"),
        venue=Venue.KIS, provider="kis", source_ts_ns=7, sequence=7,
        price=Decimal("17"), quantity=Decimal("1"),
    )
    writer.add(event, stream_name=stream, stream_generation="legacy", stream_sequence=7, event_id=event.event_id)
    paths = writer.flush_all()
    manifest = store.local_manifest(paths[0])
    maintenance._upload(manifest)
    store._write("UPDATE parquet_delivery SET persisted_ts_ns=0")
    if with_crypto_receipt:
        # Even a matching crypto receipt does not invent a supported legacy
        # recovery/pruning contract. Compact/immutable paths remain separate.
        store.record_confirmed_ack_floor(ConfirmedAckFloor(
            "CRYPTO_DATA", "legacy", "crypto-parquet-storage", "consumer", 100, 1,
        ))

    assert store.prune_delivery_history(retain_seconds=1, limit=100) == 0
    assert event.event_id in store.committed_event_ids([event.event_id])
    assert store.delivery_committed(stream, "legacy", 7)
    count = len(store.list_manifest())
    bus = InMemoryEventBus()
    bus.messages.append(BusMessage(
        event.model_dump(mode="json"), "md.trade.fixture" if stream == "MARKET_DATA" else "crypto.trade.fixture", event.event_id,
        stream_name=stream, stream_generation="legacy", stream_sequence=7, delivery_count=2,
    ))
    result = asyncio.run(StorageWorker(bus, writer, settings, domain="crypto" if stream == "CRYPTO_DATA" else "market").run(.1))
    assert result["deduplicated"] == 1
    assert result["messages"] == 0
    assert len(store.list_manifest()) == count
