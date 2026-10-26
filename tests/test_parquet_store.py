from decimal import Decimal
from pathlib import Path
import os

import pytest

from core.models import AssetClass, InstrumentId, Side, TradeEvent, Venue
from storage.parquet_store import ParquetBatchWriter
from vibetrading.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore


@pytest.mark.skipif(os.name == "nt", reason="Windows does not support the production directory-fsync contract")
def test_directory_fsync_failure_prevents_manifest_commit(tmp_path: Path, monkeypatch) -> None:
    from storage import parquet_store

    env_file = tmp_path / ".env"
    env_file.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env_file)
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    event = TradeEvent(
        instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
                                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
        venue=Venue.BINANCE, provider="binance-perp", source_ts_ns=1,
        sequence=1, price=Decimal("100"), quantity=Decimal("0.1"),
    )
    writer.add(event, stream_name="CRYPTO_DATA", stream_generation="g", stream_sequence=1,
               event_id=event.event_id)

    def fail_directory_sync(_path: Path) -> None:
        raise OSError("directory sync failed")

    monkeypatch.setattr(parquet_store, "_fsync_dir", fail_directory_sync)
    with pytest.raises(OSError, match="directory sync failed"):
        writer.flush_all()

    assert store.list_manifest(active_only=True) == []
    assert writer.last_committed_ids == set()


def test_parquet_writer_commits_manifest(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env_file)
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    event = TradeEvent(
        instrument=InstrumentId(
            venue=Venue.BINANCE,
            asset_class=AssetClass.CRYPTO_SPOT,
            raw_symbol="BTCUSDT",
            std_symbol="BTCUSDT",
        ),
        venue=Venue.BINANCE,
        provider="binance",
        source_ts_ns=1,
        receive_ts_ns=2,
        sequence=1,
        price=Decimal("100"),
        quantity=Decimal("0.1"),
        aggressor_side=Side.BUY,
        trade_id="1",
    )
    writer.add(event)
    paths = writer.flush_all()
    assert len(paths) == 1
    assert paths[0].exists()
    manifest = store.list_manifest()
    assert len(manifest) == 1
    assert manifest[0]["rows"] == 1
    store.close()
