from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

from feeds.collection_timing import MINUTE_NS, CollectionContext, next_utc_minute_ns
from core.models import OpenInterestEvent, Venue
from storage.parquet_store import ParquetBatchWriter
from feeds.providers.binance import BinanceCollector
from feeds.providers.hyperliquid import HyperliquidCollector
from vibetrading.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore
from feeds.universe_config import BinanceSecurity, HyperliquidSecurity
from core.models import AssetClass


def _binance() -> BinanceCollector:
    security = BinanceSecurity("SAMSUNG-USDT", "SAMSUNGUSDT", AssetClass.FUTURE, "stock_derivative", "SAMSUNG")
    return BinanceCollector({security.provider_symbol: security})


def _hyperliquid() -> HyperliquidCollector:
    security = HyperliquidSecurity("SAMSUNG-USDC", "xyz:SMSN", AssetClass.FUTURE, "stock_derivative", "SAMSUNG")
    return HyperliquidCollector({security.provider_symbol: security})


def test_next_minute_is_strict_and_exact() -> None:
    assert next_utc_minute_ns(11 * MINUTE_NS + 31_000_000_000) == 12 * MINUTE_NS
    assert next_utc_minute_ns(12 * MINUTE_NS) == 13 * MINUTE_NS
    assert next_utc_minute_ns(123) % MINUTE_NS == 0


def test_binance_completed_bar_obeys_shared_cutoff() -> None:
    collector = _binance()
    start = 100 * MINUTE_NS
    context = CollectionContext("binance", start, collector_run_id="run")
    row = {"s": "SAMSUNGUSDT", "t": 100 * 60_000, "T": 101 * 60_000 - 1, "o": "1", "h": "2", "l": "1", "c": "2", "v": "3", "n": 4, "x": True}
    event = collector._parse_kline(row, context, start + MINUTE_NS, 1, "connection", 1, "realtime")
    assert event is not None
    assert event.open_ts_ns == start
    assert event.open_ts_ns % MINUTE_NS == 0
    assert event.wallclock_ts_ns == start + MINUTE_NS
    before = dict(row, t=99 * 60_000, T=100 * 60_000 - 1)
    assert collector._parse_kline(before, context, start + MINUTE_NS, 2, "connection", 2, "realtime") is None


def test_hyperliquid_fast_book_preserves_received_levels_and_wallclock() -> None:
    collector = _hyperliquid()
    start = 100 * MINUTE_NS
    context = CollectionContext("hyperliquid", start, collector_run_id="run")
    levels = [[{"px": str(100 - i), "sz": "1.5", "n": i + 1} for i in range(5)], [{"px": str(101 + i), "sz": "2.5", "n": i + 2} for i in range(5)]]
    event = collector._parse_book({"coin": "xyz:SKHX"}, context, start, 1, "connection", 1)
    assert event is None
    event = collector._parse_book({"coin": "xyz:SMSN", "time": 1234, "levels": levels}, context, start, 1, "connection", 1)
    assert event is not None
    assert event.fast is True and event.provider_depth == 5
    assert len(event.bids) == 5 and len(event.asks) == 5
    assert event.bids[0].order_count == 1
    assert event.wallclock_ts_ns == start


def test_open_interest_v2_roundtrip(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    settings = RuntimeSettings.from_env(env)
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    instrument = _binance()._instrument("SAMSUNGUSDT")
    event = OpenInterestEvent(
        instrument=instrument, venue=Venue.BINANCE, provider="binance",
        source_ts_ns=None, source_timestamp_kind="not_provided", wallclock_ts_ns=123,
        open_interest=Decimal("42.5"), open_interest_unit="contract",
    )
    assert event.schema_version == 2 and event.receive_ts_ns == 123
    assert writer.add(event)
    path = writer.flush_all()[0]
    row = pq.ParquetFile(path).read().to_pylist()[0]
    assert row["wallclock_ts_ns"] == 123
    assert row["source_ts_ns"] is None
    assert row["open_interest"] == Decimal("42.500000000000000000")
