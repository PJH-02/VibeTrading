"""Arcus native book reconstruction, raw rejection, and bounded resubscription."""
from __future__ import annotations

import asyncio
import hashlib
import json
from decimal import Decimal

import pytest

from core.models import AssetClass, BookUpdateType, EventType, OrderBookEvent, Venue
from feeds.feed_recovery import EngineCursor, RecoveryFrame
from feeds.providers.arcus import ArcusBookResync, ArcusPerpCollector
from market.hot_path import HotPathStore
from market.local_engine import EngineBudgets, EngineError, LocalMarketEngine
from storage.market_spool import MarketSpool, SpoolCursor, SpoolReader, _checkpoint
import feeds.providers.arcus as arcus_module


PRODUCER = "arcus-perp"
INCARNATION = "arcus-test-epoch"


class CaptureBus:
    def __init__(self, *, fail=None):
        self.events = []
        self.fail = fail

    async def publish_market_event(self, event):
        if self.fail is not None and self.fail(event):
            raise OSError("synthetic admission failure")
        self.events.append(event)


def snapshot(seq, bids=None, asks=None, *, symbol="ETHUSDT"):
    native = symbol.removesuffix("USDT") + "-USD"
    return {"type": "subscribed", "channel": "l2OrderbookUpdates", "id": native,
        "contents": {"timestamp": 1_800_000_000_000_000, "lastSequenceId": seq,
            "globalSequenceId": seq + 500_000_000,
            "bids": [["2253.09", "1"]] if bids is None else bids,
            "asks": [["2253.14", "1"]] if asks is None else asks}}


def delta(seq, bids=None, asks=None, *, symbol="ETHUSDT"):
    native = symbol.removesuffix("USDT") + "-USD"
    return {"type": "channel_data", "channel": "l2OrderbookUpdates", "id": native,
        "contents": {"timestamp": 1_800_000_000_001_000, "lastSequenceId": seq,
            "globalSequenceId": seq + 500_000_000, "bids": bids or [], "asks": asks or []}}


def wire(message):
    return json.dumps(message, separators=(",", ":"))


async def parse(collector, bus, message):
    await collector._arcus_message(bus, message, wire(message))


def test_confirmed_cross_is_raw_only_then_gap_and_fresh_snapshot_recovers_hotpath():
    async def scenario():
        collector, bus = ArcusPerpCollector(("ETHUSDT",)), CaptureBus()
        await parse(collector, bus, snapshot(506131406))
        bad = delta(506131407, asks=[["2253.22", "103.9146142"],
                                     ["2252.86", "0.4793693"]])
        bad_raw = wire(bad)
        with pytest.raises(ArcusBookResync, match="crossed"):
            await collector._arcus_message(bus, bad, bad_raw)
        rejected, gap = bus.events[-2:]
        assert rejected.event_type == EventType.ORDER_BOOK
        assert rejected.update_type == BookUpdateType.DELTA
        assert rejected.fast is False and rejected.raw_payload == bad_raw
        assert [(x.price, x.quantity) for x in rejected.asks] == [
            (Decimal("2253.22"), Decimal("103.9146142")),
            (Decimal("2252.86"), Decimal("0.4793693"))]
        assert gap.event_type == EventType.DATA_GAP
        assert "crossed" in gap.reason
        assert "ETHUSDT" not in collector._native_books
        assert collector.last_book.get("ETHUSDT") is None

        # The actual canonical state owner treats the bad native frame as raw
        # evidence, then the gap invalidates it; a new snapshot restores quality.
        hot = HotPathStore()
        snapshot_event, raw_bad = bus.events[0], rejected
        hot.apply_versioned(snapshot_event)
        key = snapshot_event.instrument.key
        prior_book = hot.snapshot().latest_book(key)
        before = hot.apply_seq
        result = hot.apply_versioned(raw_bad)
        assert result.reason == "raw_only" and hot.apply_seq == before + 1
        assert hot.snapshot().quality[key] == "VALID"
        retained = hot.snapshot().latest_book(key)
        assert retained.bids == prior_book.bids and retained.asks == prior_book.asks
        hot.apply_versioned(gap)
        assert hot.snapshot().quality[key] == "INVALID"
        await parse(collector, bus, snapshot(506140000,
            bids=[["2253.00", "2"]], asks=[["2253.05", "3"]]))
        recovered = bus.events[-1]
        assert recovered.fast is not False
        hot.apply_versioned(recovered)
        book = hot.snapshot().latest_book(key)
        assert book is not None and hot.snapshot().quality[key] == "VALID"
        assert recovered.sync_generation > snapshot_event.sync_generation
    asyncio.run(scenario())


def test_engine_replay_of_rejected_raw_delta_is_state_safe_and_queue_free():
    async def scenario():
        collector, bus = ArcusPerpCollector(("ETHUSDT",)), CaptureBus()
        await parse(collector, bus, snapshot(10))
        try:
            await parse(collector, bus, delta(11, asks=[["2252.80", "1"]]))
        except ArcusBookResync:
            pass
        snapshot_event, raw_only = bus.events[0], bus.events[-2]
        engine = LocalMarketEngine(producer_providers={PRODUCER: "arcus-perp"},
            budgets=EngineBudgets(1, 8, 8192, 16, 131072, 5_000_000_000,
                32, 1, 20_000_000_000, .01), on_invalidate=lambda *_: None,
            hot_path=HotPathStore(), trading_filter=lambda _: False)
        try:
            high = EngineCursor(PRODUCER, INCARNATION, 2)
            engine.begin_recovery(EngineCursor(PRODUCER, INCARNATION, 0), high,
                                  verified_checkpoint=True)
            first = engine.apply_replay(RecoveryFrame(EngineCursor(PRODUCER, INCARNATION, 1),
                snapshot_event.model_dump_json().encode()))
            second = engine.apply_replay(RecoveryFrame(high, raw_only.model_dump_json().encode()))
            assert first.cursor.sequence == 1 and second.cursor.sequence == 2
            assert engine.stats["feeds"][PRODUCER]["state_poisoned"] is False
            assert engine.stats["queued_records"] == engine.stats["queued_bytes"] == 0
            assert engine.query_cursor(PRODUCER).cursor == high
        finally:
            await engine.close()
    asyncio.run(scenario())


def test_live_gap_delivery_recovers_suffix_only_but_cold_replay_across_gap_rejects(tmp_path):
    async def scenario():
        collector, bus = ArcusPerpCollector(("ETHUSDT",)), CaptureBus()
        await parse(collector, bus, snapshot(506131406))
        bad = delta(506131407, asks=[["2253.22", "103.9146142"],
                                     ["2252.86", "0.4793693"]])
        with pytest.raises(ArcusBookResync):
            await collector._arcus_message(bus, bad, wire(bad))
        await parse(collector, bus, snapshot(506140000,
            bids=[["2253.00", "2"]], asks=[["2253.05", "3"]]))
        assert [event.event_type for event in bus.events] == [
            EventType.ORDER_BOOK, EventType.ORDER_BOOK, EventType.DATA_GAP, EventType.ORDER_BOOK]
        market_key = bus.events[0].instrument.key

        spool_root = tmp_path / "active-spool"
        limits = dict(max_disk_bytes=1_000_000, max_records=100,
            max_frame_bytes=8192, max_header_bytes=512, segment_max_bytes=65536,
            segment_max_records=32, segment_max_age_seconds=60)
        spool = MarketSpool(spool_root, PRODUCER, INCARNATION, **limits)
        for event in bus.events:
            spool.append(event.model_dump_json().encode())
        durable = spool.flush()
        high = EngineCursor(PRODUCER, INCARNATION, durable.sequence)
        assert durable.sequence == 4
        assert _checkpoint(spool_root, PRODUCER, INCARNATION, limits["max_header_bytes"]) == 4

        budgets = EngineBudgets(2, 8, 8192, 16, 131072, 5_000_000_000,
            32, 1, 20_000_000_000, .01)
        def new_engine():
            return LocalMarketEngine(producer_providers={PRODUCER: "arcus-perp",
                "unrelated": "binance-perp"}, budgets=budgets,
                on_invalidate=lambda *_: None, hot_path=HotPathStore(),
                trading_filter=lambda _: False)

        engine = new_engine()
        cold = new_engine()
        try:
            zero = EngineCursor(PRODUCER, INCARNATION, 0)
            engine.begin_recovery(zero, zero, verified_checkpoint=True)
            assert engine.commit_recovery(zero, hashlib.sha256(b"").hexdigest(), ready=True)
            for sequence, event in enumerate(bus.events[:3], 1):
                engine.on_frame(RecoveryFrame(EngineCursor(PRODUCER, INCARNATION, sequence),
                    event.model_dump_json().encode()))
            committed_gap = engine.query_cursor(PRODUCER)
            assert committed_gap.cursor == EngineCursor(PRODUCER, INCARNATION, 3)
            assert engine.stats["feeds"][PRODUCER]["mode"] == "INVALID"
            assert engine.stats["feeds"][PRODUCER]["state_poisoned"] is False
            assert engine.stats["feeds"]["unrelated"]["applied_sequence"] is None

            # A full cold replay from zero still rejects this historical gap.
            cold.begin_recovery(zero, high, verified_checkpoint=True)
            reader = SpoolReader(spool_root, PRODUCER, INCARNATION, **{
                key: value for key, value in limits.items() if not key.startswith("segment_")})
            prefix = reader.read_batch(max_records=3, max_bytes=131072)
            assert [record.cursor.sequence for record in prefix] == [1, 2, 3]
            cold.apply_replay(RecoveryFrame(EngineCursor(PRODUCER, INCARNATION, 1), prefix[0].payload))
            cold.apply_replay(RecoveryFrame(EngineCursor(PRODUCER, INCARNATION, 2), prefix[1].payload))
            with pytest.raises(EngineError, match="gap_inside_replay_range"):
                cold.apply_replay(RecoveryFrame(EngineCursor(PRODUCER, INCARNATION, 3), prefix[2].payload))

            # A whole-process restart instead begins a separate, fresh source
            # epoch/root containing only its new snapshot, never the old gap.
            fresh_incarnation = "arcus-fresh-epoch"
            fresh_root = tmp_path / "fresh-spool"
            fresh_limits = dict(limits)
            fresh_spool = MarketSpool(fresh_root, PRODUCER, fresh_incarnation, **fresh_limits)
            try:
                fresh_spool.append(bus.events[-1].model_dump_json().encode())
                fresh_durable = fresh_spool.flush()
                fresh_high = EngineCursor(PRODUCER, fresh_incarnation, fresh_durable.sequence)
                fresh_engine = new_engine()
                try:
                    fresh_zero = EngineCursor(PRODUCER, fresh_incarnation, 0)
                    fresh_engine.begin_recovery(fresh_zero, fresh_high, verified_checkpoint=True)
                    fresh_record = SpoolReader(fresh_root, PRODUCER, fresh_incarnation, **{
                        key: value for key, value in fresh_limits.items()
                        if not key.startswith("segment_")}).read_batch(
                            max_records=1, max_bytes=131072)[0]
                    fresh_result = fresh_engine.apply_replay(RecoveryFrame(fresh_high, fresh_record.payload))
                    assert fresh_engine.query_cursor(PRODUCER).digest == fresh_result.digest
                    assert not fresh_engine.commit_recovery(fresh_high, fresh_result.digest, ready=False)
                    assert fresh_engine.commit_recovery(fresh_high, fresh_result.digest, ready=True)
                    assert fresh_engine.hot_path.snapshot().quality[market_key] == "VALID"
                    assert fresh_engine.stats["queued_records"] == fresh_engine.stats["queued_bytes"] == 0
                finally:
                    await fresh_engine.close()
            finally:
                fresh_spool.close()

            # Active-collector delivery recovery starts at committed C=3, so
            # the suffix contains only the fresh snapshot at durable H=4.
            checkpoint = engine.begin_delivery_recovery(high)
            assert checkpoint.cursor == committed_gap.cursor
            suffix_reader = SpoolReader(spool_root, PRODUCER, INCARNATION,
                after=SpoolCursor(PRODUCER, INCARNATION, 3),
                **{key: value for key, value in limits.items() if not key.startswith("segment_")})
            suffix = suffix_reader.read_batch(max_records=1, max_bytes=131072)
            assert len(suffix) == 1 and suffix[0].cursor.sequence == 4
            applied = engine.apply_replay(RecoveryFrame(high, suffix[0].payload))
            exact = engine.query_cursor(PRODUCER)
            assert exact.cursor == high and exact.digest == applied.digest
            assert _checkpoint(spool_root, PRODUCER, INCARNATION, limits["max_header_bytes"]) >= high.sequence
            assert not engine.commit_recovery(high, exact.digest, ready=False)
            assert engine.stats["feeds"][PRODUCER]["mode"] == "WARMING"
            assert engine.commit_recovery(high, exact.digest, ready=True)
            assert engine.stats["feeds"][PRODUCER]["mode"] == "LIVE"
            assert engine.stats["feeds"]["unrelated"]["applied_sequence"] is None
            assert engine.stats["queued_records"] == engine.stats["queued_bytes"] == 0
            assert engine.hot_path.snapshot().quality[market_key] == "VALID"
        finally:
            await engine.close()
            await cold.close()
            spool.close()
    asyncio.run(scenario())


def test_absolute_levels_zero_removal_duplicate_order_and_decimal_precision():
    async def scenario():
        collector, bus = ArcusPerpCollector(("ETHUSDT",)), CaptureBus()
        await parse(collector, bus, snapshot(100,
            bids=[["100.000000000000000001", "2"], ["99", "1"]],
            asks=[["101", "1"]]))
        await parse(collector, bus, delta(104, bids=[
            ["99", "0"],
            ["100.000000000000000001", "0"],
            ["100.000000000000000001", "3.000000000000000007"],
            ["100.000000000000000001", "4.000000000000000009"]]))
        assert collector.last_book["ETHUSDT"] == 104  # Expected first boundary skip.
        side = collector._native_books["ETHUSDT"][0]
        assert side[Decimal("100.000000000000000001")] == Decimal("4.000000000000000009")
        assert Decimal("99") not in side and len(side) == 1
        hot = HotPathStore()
        for item in bus.events:
            result = hot.apply_versioned(item)
            assert result.reason != "native_book_invalid"
        top = hot.snapshot().latest_book(bus.events[0].instrument.key)
        assert top.bids[0].price == Decimal("100.000000000000000001")
        assert top.bids[0].quantity == Decimal("4.000000000000000009")
    asyncio.run(scenario())


def test_native_reconstruction_memory_stays_at_existing_finite_depth():
    async def scenario():
        collector, bus = ArcusPerpCollector(("ETHUSDT",)), CaptureBus()
        bids = [[str(5000-index), "1"] for index in range(1100)]
        await parse(collector, bus, snapshot(100, bids=bids, asks=[["6000", "1"]]))
        native_bids = collector._native_books["ETHUSDT"][0]
        assert len(native_bids) == 1000
        assert min(native_bids) == Decimal("4001")
        assert max(native_bids) == Decimal("5000")
    asyncio.run(scenario())


def test_empty_crossed_and_malformed_snapshot_retain_kind_raw_and_gap():
    async def scenario():
        for case, book in (("crossed", snapshot(1, bids=[["10", "1"]], asks=[["9", "1"]])),
                           ("empty", snapshot(1, bids=[], asks=[["10", "1"]])),
                           ("malformed", snapshot(1, bids=[["NaN", "1"]], asks=[["11", "1"]]))):
            collector, bus = ArcusPerpCollector(("ETHUSDT",)), CaptureBus()
            raw = wire(book)
            with pytest.raises(ArcusBookResync):
                await collector._arcus_message(bus, book, raw)
            rejected, gap = bus.events
            assert rejected.update_type == BookUpdateType.SNAPSHOT
            assert rejected.fast is False and rejected.raw_payload == raw
            assert gap.event_type == EventType.DATA_GAP
            assert not collector._native_books
    asyncio.run(scenario())


def test_midstream_gap_resync_but_stale_duplicate_remains_raw_only():
    async def scenario():
        collector, bus = ArcusPerpCollector(("ETHUSDT",)), CaptureBus()
        await parse(collector, bus, snapshot(10))
        await parse(collector, bus, delta(11, bids=[["2253.10", "2"]]))
        before = len(bus.events)
        stale = delta(11, bids=[["2253.10", "2"]])
        await collector._arcus_message(bus, stale, wire(stale))
        assert len(bus.events) == before + 1
        assert bus.events[-1].fast is False and bus.events[-1].event_type == EventType.ORDER_BOOK
        bad = delta(13, bids=[["2253.11", "2"]])
        with pytest.raises(ArcusBookResync, match="midstream sequence gap"):
            await collector._arcus_message(bus, bad, wire(bad))
        assert bus.events[-2].fast is False and bus.events[-2].update_type == BookUpdateType.DELTA
        assert bus.events[-1].event_type == EventType.DATA_GAP
    asyncio.run(scenario())


class FakeResponse:
    def __init__(self):
        self.status_code = 200
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None
    def raise_for_status(self): return None
    async def json(self):
        return {"markets": [{"marketDisplayName": "ETH-USD", "status": "ONLINE"}]}


class FakeSession:
    def __init__(self, **_): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None
    def get(self, _url): return FakeResponse()


class FakeSocket:
    def __init__(self, messages, owner):
        self.messages, self.owner = list(messages), owner
    async def __aenter__(self):
        self.owner.active += 1
        self.owner.max_active = max(self.owner.max_active, self.owner.active)
        return self
    async def __aexit__(self, *_):
        self.owner.active -= 1
    async def send(self, message): self.owner.sent.append(json.loads(message))
    async def recv(self):
        if self.messages:
            message = self.messages.pop(0)
            if isinstance(message, BaseException):
                raise message
            return message
        await asyncio.sleep(5)
        raise AssertionError("receive timeout should cancel the fake socket")


class FakeConnector:
    def __init__(self, messages, owner): self.socket = FakeSocket(messages, owner)
    async def __aenter__(self): return await self.socket.__aenter__()
    async def __aexit__(self, *args): return await self.socket.__aexit__(*args)


class FakeNetwork:
    def __init__(self, runs):
        self.runs, self.calls = list(runs), 0
        self.active = self.max_active = 0
        self.sent = []
        self.open_timeouts = []
    def connect(self, _url, **kwargs):
        self.open_timeouts.append(kwargs["open_timeout"])
        messages = self.runs[min(self.calls, len(self.runs)-1)]
        self.calls += 1
        return FakeConnector(messages, self)


def test_bounded_retry_resubscribes_with_new_epoch_without_overlapping_sockets(monkeypatch):
    async def scenario():
        first = [wire(snapshot(20)), wire(delta(21, asks=[["2252.80", "1"]]))]
        second = [wire(snapshot(30, bids=[["2253.00", "1"]], asks=[["2253.10", "1"]]))]
        network = FakeNetwork((first, second))
        monkeypatch.setattr(arcus_module.aiohttp, "ClientSession", FakeSession)
        monkeypatch.setattr(arcus_module.websockets, "connect", network.connect)
        bus = CaptureBus()
        collector = ArcusPerpCollector(("ETHUSDT",))
        delays, real_sleep = [], asyncio.sleep
        async def tracked_sleep(delay):
            delays.append(delay)
            await real_sleep(delay)
        monkeypatch.setattr(arcus_module.asyncio, "sleep", tracked_sleep)
        await collector.run(bus, duration_sec=1.15)
        assert network.calls == 2 and network.max_active == 1 and network.active == 0
        assert delays and 0 < delays[0] <= 1.0
        assert collector._retry_count == 1
        snapshots = [event for event in bus.events if isinstance(event, OrderBookEvent)
                     and event.update_type == BookUpdateType.SNAPSHOT and event.fast is not False]
        assert len(snapshots) == 2
        assert snapshots[0].connection_id != snapshots[1].connection_id
        assert snapshots[1].sync_generation > snapshots[0].sync_generation
        assert len([item for item in network.sent if item.get("channel") == "l2OrderbookUpdates"]) == 2
    asyncio.run(scenario())


def test_admission_error_is_fatal_and_not_retried(monkeypatch):
    async def scenario():
        network = FakeNetwork(([wire(snapshot(1))],))
        monkeypatch.setattr(arcus_module.aiohttp, "ClientSession", FakeSession)
        monkeypatch.setattr(arcus_module.websockets, "connect", network.connect)
        bus = CaptureBus(fail=lambda _event: True)
        collector = ArcusPerpCollector(("ETHUSDT",))
        with pytest.raises(RuntimeError, match="admission"):
            await collector.run(bus, duration_sec=2)
        assert network.calls == 1 and network.active == 0
    asyncio.run(scenario())


def test_disconnect_emits_gap_before_new_epoch_snapshot(monkeypatch):
    async def scenario():
        network = FakeNetwork(([wire(snapshot(10)), ConnectionResetError("network close")],
                              [wire(snapshot(20))]))
        monkeypatch.setattr(arcus_module.aiohttp, "ClientSession", FakeSession)
        monkeypatch.setattr(arcus_module.websockets, "connect", network.connect)
        bus = CaptureBus()
        collector = ArcusPerpCollector(("ETHUSDT",))
        delays, real_sleep = [], asyncio.sleep
        async def tracked_sleep(delay):
            delays.append(delay)
            await real_sleep(delay)
        monkeypatch.setattr(arcus_module.asyncio, "sleep", tracked_sleep)
        await collector.run(bus, duration_sec=1.15)
        assert network.calls == 2 and network.max_active == 1
        assert delays and 0 < delays[0] <= 1.0
        first_snapshot = next(e for e in bus.events if isinstance(e, OrderBookEvent))
        gap_index = next(i for i, e in enumerate(bus.events) if e.event_type == EventType.DATA_GAP)
        next_snapshot = next(e for e in bus.events[gap_index+1:]
            if isinstance(e, OrderBookEvent) and e.update_type == BookUpdateType.SNAPSHOT)
        assert first_snapshot.connection_id != next_snapshot.connection_id
        assert first_snapshot.sync_generation < next_snapshot.sync_generation
    asyncio.run(scenario())


def test_cancellation_closes_socket_without_retry(monkeypatch):
    async def scenario():
        network = FakeNetwork(([], []))
        monkeypatch.setattr(arcus_module.aiohttp, "ClientSession", FakeSession)
        monkeypatch.setattr(arcus_module.websockets, "connect", network.connect)
        collector = ArcusPerpCollector(("ETHUSDT",))
        task = asyncio.create_task(collector.run(CaptureBus()))
        while network.calls == 0:
            await asyncio.sleep(.001)
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert network.calls == 1 and network.active == 0
    asyncio.run(scenario())


def test_malformed_native_sequence_is_unknown_not_replaced_with_prior_sequence():
    async def scenario():
        collector, bus = ArcusPerpCollector(("ETHUSDT",)), CaptureBus()
        await parse(collector, bus, snapshot(10))
        malformed = delta(11, asks=[["2253.22", "1"]])
        malformed["contents"]["lastSequenceId"] = "not-a-native-sequence"
        raw = wire(malformed)
        with pytest.raises(ArcusBookResync, match="malformed"):
            await collector._arcus_message(bus, malformed, raw)
        untrusted, gap = bus.events[-2:]
        assert untrusted.fast is False
        assert untrusted.final_update_id is None
        assert untrusted.raw_payload == raw
        assert gap.event_type == EventType.DATA_GAP
    asyncio.run(scenario())
