import asyncio
import time
import pytest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from vibetrading.bus import BufferedMarketEventBus, BusMessage, InMemoryEventBus, NatsJetStreamBus, StreamProbeResult
from vibetrading.market_policy import CollectionPaused, MarketDataPolicyBus, _CollectionPressureLatch
from vibetrading.models import AssetClass, BookUpdateType, InstrumentId, OrderBookEvent, OrderBookLevel, TradeEvent, Venue
from vibetrading.parquet_store import ParquetBatchWriter
from vibetrading.settings import RuntimeSettings
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.storage_worker import StorageWorker


def _settings(tmp_path: Path) -> RuntimeSettings:
    env = tmp_path / ".env"
    env.write_text(f"VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\nPARQUET_FLUSH_SECONDS=3600\n", encoding="utf-8")
    return RuntimeSettings.from_env(env)


def _trade() -> TradeEvent:
    instrument = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.EQUITY, raw_symbol="ABC", std_symbol="ABC")
    return TradeEvent(instrument=instrument, venue=Venue.KIS, provider="kis", source_ts_ns=1, sequence=1, price=Decimal("1"), quantity=Decimal("1"))


def test_redelivery_before_flush_is_buffered_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    event = _trade()
    bus = InMemoryEventBus()
    for _ in range(2):
        bus.messages.append(BusMessage(event.model_dump(mode="json"), "md.trade.kis.equity.ABC", event.event_id,
                                       stream_name="MARKET_DATA", stream_generation="test", stream_sequence=7))

    stats = asyncio.run(StorageWorker(bus, writer).run(0.15))

    assert stats["messages"] == 1
    assert stats["deduplicated"] == 1
    assert store.list_manifest(active_only=True)[0]["rows"] == 1


def test_duplicate_event_delivery_waits_for_canonical_file_commit(tmp_path: Path) -> None:
    class TrackedAck:
        def __init__(self, store: RuntimeStore, sequence: int) -> None:
            self.store = store
            self.sequence = sequence
            self.acked = False

        async def ack(self) -> None:
            assert self.store.delivery_committed("MARKET_DATA", "test", self.sequence)
            self.acked = True

    settings = _settings(tmp_path)
    store = RuntimeStore(settings, tmp_path / "state.sqlite3")
    writer = ParquetBatchWriter(settings, store)
    bus = InMemoryEventBus()
    event = _trade()
    first = TrackedAck(store, 7)
    duplicate = TrackedAck(store, 8)
    for sequence, raw in ((7, first), (8, duplicate)):
        bus.messages.append(BusMessage(
            event.model_dump(mode="json"), "md.trade.kis.equity.ABC", event.event_id,
            stream_name="MARKET_DATA", stream_generation="test", stream_sequence=sequence,
            _raw=raw,
        ))

    stats = asyncio.run(StorageWorker(bus, writer).run(0.15))

    assert first.acked and duplicate.acked, f"stats={stats}; pending={store.delivery_ledger_stats()}"
    assert stats["messages"] == 1
    assert stats["deduplicated"] == 1
    assert stats["acked"] == 2
    assert store.list_manifest(active_only=True)[0]["rows"] == 1


class _Sink:
    def __init__(self) -> None:
        self.events = []

    async def publish_market_event(self, event):
        self.events.append(event)


def _book(receive_ts_ns: int) -> OrderBookEvent:
    instrument = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.FUTURE, raw_symbol="F", std_symbol="F")
    return OrderBookEvent(
        instrument=instrument, venue=Venue.KIS, provider="kis", source_ts_ns=receive_ts_ns,
        receive_ts_ns=receive_ts_ns, sequence=1, update_type=BookUpdateType.SNAPSHOT,
        depth=1, bids=[OrderBookLevel(price=Decimal("100"), quantity=Decimal("1"))],
        asks=[OrderBookLevel(price=Decimal("101"), quantity=Decimal("1"))], is_full_depth=False,
    )


def test_raw_book_frame_is_forwarded_without_sampling_or_truncation() -> None:
    async def scenario() -> None:
        sink = _Sink()
        policy = MarketDataPolicyBus(sink)
        policy._next_pressure_check = float("inf")
        event = _book(1_000_000_000)
        await policy.publish_market_event(event)
        assert sink.events == [event]
        assert sink.events[0].sampled_at_ns is None

    asyncio.run(scenario())


def test_backpressure_pause_survives_a_new_policy_bus() -> None:
    """A WebSocket reconnect must not reopen collection while NATS is full."""
    class Guard:
        def __init__(self, used_ratio: float, free_bytes: int) -> None:
            self._decision = SimpleNamespace(used_ratio=used_ratio, free_bytes=free_bytes,
                                             reserve_bytes=4, resume_margin_bytes=1)
            self.settings = SimpleNamespace(storage_guard=SimpleNamespace(
                fail_closed_used_ratio=0.80, resume_used_ratio=0.75, resume_stable_seconds=60))

        def evaluate(self):
            return self._decision

    async def scenario() -> None:
        _CollectionPressureLatch.paused = False
        _CollectionPressureLatch.resume_eligible_since = None
        try:
            full = MarketDataPolicyBus(_Sink())
            full._guard = Guard(0.1, 20 * 1024**3)
            full._stream_probe = lambda crypto=False: asyncio.sleep(0, result={
                "status": "fresh", "ratio": 0.81, "config_mismatch": False,
                "consecutive_failures": 0})
            try:
                await full._check_pressure()
            except CollectionPaused:
                pass
            else:
                raise AssertionError("full NATS stream did not pause collection")

            reconnect = MarketDataPolicyBus(_Sink())
            reconnect._guard = Guard(0.1, 20 * 1024**3)
            reconnect._stream_probe = lambda crypto=False: asyncio.sleep(0, result={
                "status": "fresh", "ratio": 0.1, "config_mismatch": False,
                "consecutive_failures": 0})
            try:
                await reconnect._check_pressure()
            except CollectionPaused:
                pass
            else:
                raise AssertionError("reconnect cleared backpressure latch")
        finally:
            _CollectionPressureLatch.paused = False
            _CollectionPressureLatch.resume_eligible_since = None

    asyncio.run(scenario())


def test_recent_nats_stream_usage_survives_one_control_plane_timeout(tmp_path: Path) -> None:
    """A brief STREAM.INFO delay is not equivalent to a full JetStream stream."""
    bus = NatsJetStreamBus(_settings(tmp_path))

    async def unavailable(_: str):
        raise TimeoutError("JetStream control plane delayed")

    bus._js = SimpleNamespace(stream_info=unavailable)
    now = time.monotonic()
    bus._stream_probe_cache[bus.market_stream_name] = StreamProbeResult(
        "fresh", bus.market_stream_name, 12, 100, 0.12, "gen", now, now)
    cached = asyncio.run(bus.stream_probe(bus.market_stream_name))
    assert cached.status == "cached"
    assert cached.ratio == 0.12

    bus._stream_probe_cache[bus.market_stream_name] = StreamProbeResult(
        "fresh", bus.market_stream_name, 12, 100, 0.12, "gen", now - 61, now - 61)
    probe = asyncio.run(bus.stream_probe(bus.market_stream_name))
    assert probe.status == "unavailable"
    assert probe.ratio is None
    assert probe.consecutive_failures == 2


def test_single_nats_probe_failure_uses_recent_cache_without_pausing() -> None:
    class Guard:
        def __init__(self):
            self.settings = SimpleNamespace(storage_guard=SimpleNamespace(
                fail_closed_used_ratio=0.80, resume_used_ratio=0.75, resume_stable_seconds=60),
                env={})

        def evaluate(self):
            return SimpleNamespace(used_ratio=0.2, free_bytes=20, reserve_bytes=4, resume_margin_bytes=1)

    async def scenario() -> None:
        _CollectionPressureLatch.paused = False
        _CollectionPressureLatch.primary_reason = None
        _CollectionPressureLatch.blocking_reasons = ()
        policy = MarketDataPolicyBus(_Sink())
        policy._guard = Guard()
        policy._stream_probe = lambda crypto=False: asyncio.sleep(0, result={
            "status": "cached", "ratio": 0.1, "bytes": 100, "max_bytes": 2 * 1024**3,
            "cache_age_seconds": 1, "config_mismatch": False, "consecutive_failures": 1})
        await policy._check_pressure()
        assert not _CollectionPressureLatch.paused

    asyncio.run(scenario())


def test_repeated_nats_control_plane_failures_pause_with_specific_reason() -> None:
    class Guard:
        def __init__(self):
            self.settings = SimpleNamespace(storage_guard=SimpleNamespace(
                fail_closed_used_ratio=0.80, resume_used_ratio=0.75, resume_stable_seconds=60),
                env={})

        def evaluate(self):
            return SimpleNamespace(used_ratio=0.2, free_bytes=20, reserve_bytes=4, resume_margin_bytes=1)

    async def scenario() -> None:
        _CollectionPressureLatch.paused = False
        _CollectionPressureLatch.primary_reason = None
        _CollectionPressureLatch.blocking_reasons = ()
        policy = MarketDataPolicyBus(_Sink())
        policy._guard = Guard()
        policy._stream_probe = lambda crypto=False: asyncio.sleep(0, result={
            "status": "unavailable", "ratio": None, "error": "TimeoutError",
            "consecutive_failures": 3})
        with pytest.raises(CollectionPaused) as caught:
            await policy._check_pressure()
        assert caught.value.primary_reason == "nats_control_plane_unavailable"
        assert caught.value.blocking_reasons == ("nats_control_plane_unavailable",)
        _CollectionPressureLatch.paused = False
        _CollectionPressureLatch.primary_reason = None
        _CollectionPressureLatch.blocking_reasons = ()

    asyncio.run(scenario())


def test_existing_pause_survives_a_single_unavailable_probe() -> None:
    class Guard:
        def __init__(self):
            self.settings = SimpleNamespace(storage_guard=SimpleNamespace(
                fail_closed_used_ratio=0.80, resume_used_ratio=0.75, resume_stable_seconds=60),
                env={})

        def evaluate(self):
            return SimpleNamespace(used_ratio=0.2, free_bytes=20, reserve_bytes=4, resume_margin_bytes=1)

    async def scenario() -> None:
        _CollectionPressureLatch.paused = True
        _CollectionPressureLatch.primary_reason = "nats_stream_capacity"
        _CollectionPressureLatch.blocking_reasons = ("nats_stream_capacity",)
        policy = MarketDataPolicyBus(_Sink())
        policy._guard = Guard()
        policy._stream_probe = lambda crypto=False: asyncio.sleep(0, result={
            "status": "unavailable", "ratio": None, "error": "TimeoutError",
            "consecutive_failures": 1})
        with pytest.raises(CollectionPaused) as caught:
            await policy._check_pressure()
        assert caught.value.primary_reason == "nats_stream_capacity"
        assert caught.value.blocking_reasons == ("nats_probe_recovery_pending",)
        _CollectionPressureLatch.paused = False
        _CollectionPressureLatch.primary_reason = None
        _CollectionPressureLatch.blocking_reasons = ()

    asyncio.run(scenario())


def test_storage_cgroup_pressure_excludes_clean_active_file_cache() -> None:
    used = 790 * 1024 * 1024
    stats = {
        "file": 420 * 1024 * 1024,
        "inactive_file": 140 * 1024 * 1024,
        "file_dirty": 2 * 1024 * 1024,
        "file_writeback": 0,
        "shmem": 0,
    }
    pressure = StorageWorker._cgroup_nonreclaimable_bytes(used, stats)
    assert pressure == 372 * 1024 * 1024


def test_buffered_publisher_preserves_per_symbol_order() -> None:
    class Sink:
        def __init__(self):
            self.sequences = []

        async def publish_market_event(self, event):
            await asyncio.sleep(0)
            self.sequences.append(event.sequence)

    async def scenario() -> None:
        sink = Sink()
        buffered = BufferedMarketEventBus(sink, shards=4, max_items=8, max_bytes=1024 * 1024)
        for sequence in range(20):
            await buffered.publish_market_event(_book(sequence + 1).model_copy(update={"sequence": sequence}))
        await buffered.close()
        assert sink.sequences == list(range(20))

    asyncio.run(scenario())


def test_buffered_publisher_reports_unsent_events_after_failure() -> None:
    class Sink:
        async def publish_market_event(self, event):
            raise TimeoutError("nats timeout")

    async def scenario() -> None:
        buffered=BufferedMarketEventBus(Sink(),shards=1,max_items=8,max_bytes=1024*1024)
        await buffered.publish_market_event(_book(1))
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError,match="buffered NATS publisher failed"):
            await buffered.close()
        assert buffered.stats["failed"]==1
        assert buffered.stats["accepted"]==1
        assert buffered.stats["published"]==0

    asyncio.run(scenario())
