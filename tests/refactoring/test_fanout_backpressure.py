from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import hashlib
import time

import pytest

from runtime.bus import MarketPublication, prepare_market_event
from market.market_fanout import SplitMarketEventBus
from core.models import AssetClass, BookUpdateType, DataGapEvent, InstrumentId, OrderBookEvent, OrderBookLevel, TradeEvent, Venue


def trade(sequence=1):
    return TradeEvent(
        instrument=InstrumentId(venue=Venue.GRVT, asset_class=AssetClass.CRYPTO_PERP,
                                raw_symbol="BTC_USDT_Perp", std_symbol="BTCUSDT"),
        venue=Venue.GRVT, provider="grvt-perp", source_ts_ns=time.time_ns(),
        sequence=sequence, price=Decimal("101.123456789012345678"), quantity=Decimal("0.001"),
    )


class Terminal:
    def __init__(self):
        self.storage_release, self.live_release = asyncio.Event(), asyncio.Event()
        self.storage_release.set()
        self.live_release.set()
        self.storage_started, self.live_started = asyncio.Event(), asyncio.Event()
        self.stored, self.live = [], []
        self.storage_error = self.live_error = None
        self.eligible = True
        self.filtered = False
        self.replacement = None
        self.resolutions = 0

    def resolve_market_publication(self, packet):
        self.resolutions += 1
        if self.filtered:
            return None
        return MarketPublication(self.replacement or packet, self.eligible, True)

    async def publish_market_storage(self, publication):
        self.storage_started.set()
        await self.storage_release.wait()
        if self.storage_error:
            raise self.storage_error
        self.stored.append(publication.packet)

    async def publish_market_live(self, publication):
        self.live_started.set()
        await self.live_release.wait()
        if self.live_error:
            raise self.live_error
        self.live.append(publication.packet)


def build(terminal, **limits):
    durable, invalid = [], []
    bus = SplitMarketEventBus(terminal, on_durable=durable.append, on_invalid=invalid.append, **limits)
    return bus, durable, invalid


def closed_accounting(bus):
    for prefix in ("", "live_"):
        stats = bus.stats
        assert stats[prefix + "pending"] == 0
        assert stats[prefix + "queued_bytes"] == 0
        assert stats[prefix + "in_flight"] == 0
        assert stats[prefix + "accepted"] == stats[prefix + "published"] + stats[prefix + "failed"]


@pytest.mark.asyncio
async def test_jetstream_stall_does_not_hold_live_or_report_false_durable_success():
    terminal = Terminal()
    terminal.storage_release.clear()
    bus, durable, invalid = build(terminal)
    packet = prepare_market_event(trade())
    await bus.publish_market_packet(packet)
    await asyncio.wait_for(terminal.live_started.wait(), 1)
    await asyncio.sleep(0)
    assert terminal.live == [packet]
    assert terminal.stored == durable == []
    assert bus.stats["pending"] == 1
    assert bus.stats["live_pending"] == 0
    assert bus.stats["queued_bytes"] == len(packet.payload) + len(packet.live_payload)
    terminal.storage_release.set()
    await bus.close()
    assert durable == [packet]
    assert invalid == []
    assert terminal.resolutions == 1
    closed_accounting(bus)


@pytest.mark.asyncio
async def test_both_lanes_preserve_fifo_and_owned_raw_live_bytes():
    terminal = Terminal()
    bus, durable, invalid = build(terminal)
    reference = trade()
    book = OrderBookEvent(
        instrument=reference.instrument, venue=Venue.GRVT, provider="grvt-perp",
        source_ts_ns=time.time_ns(), sequence=1, update_type=BookUpdateType.SNAPSHOT, depth=1,
        bids=[OrderBookLevel(price=Decimal("100"), quantity=Decimal("1"))],
        asks=[OrderBookLevel(price=Decimal("102"), quantity=Decimal("1"))], raw_payload='{"원문":"book"}',
    )
    first = prepare_market_event(book)
    await bus.publish_market_packet(first)
    book.bids.append(OrderBookLevel(price=Decimal("99"), quantity=Decimal("7")))
    packets = [first]
    for sequence in range(2, 10):
        packet = prepare_market_event(trade(sequence))
        packets.append(packet)
        await bus.publish_market_packet(packet)
    await bus.close()
    assert terminal.stored == terminal.live == durable == packets
    assert len(json.loads(first.payload)["bids"]) == 1
    assert json.loads(first.payload)["raw_payload"] == '{"원문":"book"}'
    assert "raw_payload" not in json.loads(first.live_payload)
    assert invalid == []
    assert terminal.resolutions == len(packets)
    closed_accounting(bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ("storage", "live"))
async def test_full_lane_rejects_both_admissions_and_latches_once(lane):
    terminal = Terminal()
    limits = {"max_items": 1} if lane == "storage" else {"live_max_items": 1}
    bus, _, invalid = build(terminal, **limits)
    await bus.publish_market_event(trade(1))
    rejected = trade(2)
    # Admission has no suspension: neither worker can dequeue before this call.
    with pytest.raises(RuntimeError, match=lane + "_queue_full"):
        await bus.publish_market_event(rejected)
    assert bus.stats["accepted"] == bus.stats["live_accepted"] == 1
    assert bus.stats["rejected"] == 1
    assert bus.stats["gap"] == 1
    assert bus.stats["rejected_event_id"] == rejected.event_id
    assert bus.stats["rejected_order_lower_bound"] == 2
    assert bus.stats["rejected_count_lower_bound"] == 1
    with pytest.raises(RuntimeError, match="invalid"):
        await bus.publish_market_event(trade(3))
    with pytest.raises(RuntimeError, match="invalid"):
        await bus.close()
    assert invalid == [lane + "_queue_full"]
    assert bus.stats["gap_notifications"] == 1
    closed_accounting(bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ("storage", "live"))
async def test_oversize_checks_both_retained_payloads_before_any_admission(lane):
    terminal = Terminal()
    packet = prepare_market_event(trade())
    size = len(packet.payload) + len(packet.live_payload)
    limits = {"max_bytes": size - 1} if lane == "storage" else {"live_max_bytes": size - 1}
    bus, durable, invalid = build(terminal, **limits)
    with pytest.raises(RuntimeError, match=lane + "_byte_limit"):
        await bus.publish_market_packet(packet)
    assert bus.stats["accepted"] == bus.stats["live_accepted"] == 0
    assert bus._workers == []
    assert durable == []
    assert invalid == [lane + "_byte_limit"]
    with pytest.raises(RuntimeError):
        await bus.close()
    closed_accounting(bus)


@pytest.mark.asyncio
async def test_filter_and_storage_only_publications_do_not_fill_live_queue():
    terminal = Terminal()
    bus, durable, invalid = build(terminal)
    terminal.filtered = True
    await bus.publish_market_event(trade(1))
    assert bus.stats["filtered"] == 1
    assert bus._workers == []
    terminal.filtered = False
    terminal.eligible = False
    await bus.publish_market_event(trade(2))
    await bus.close()
    assert len(durable) == 1
    assert terminal.live == []
    assert bus.stats["live_accepted"] == 0
    assert invalid == []
    closed_accounting(bus)


@pytest.mark.asyncio
async def test_durable_callback_receives_actual_resolved_timestamp_gap_packet():
    terminal = Terminal()
    original = trade()
    gap = DataGapEvent(
        instrument=original.instrument, venue=original.venue, provider=original.provider,
        source_ts_ns=time.time_ns(), reason="invalid timestamp", resync_status="blocked_invalid_timestamp",
    )
    terminal.replacement = prepare_market_event(gap)
    terminal.eligible = False
    bus, durable, _ = build(terminal)
    await bus.publish_market_event(original)
    await bus.close()
    assert durable == [terminal.replacement]
    assert durable[0].snapshot.event_id == gap.event_id
    assert durable[0].snapshot.event_id != original.event_id


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ("storage", "live"))
async def test_worker_failure_blocks_new_admission_and_reconciles_all_bytes(lane):
    terminal = Terminal()
    setattr(terminal, lane + "_error", OSError("offline lane failure"))
    bus, _, invalid = build(terminal)
    for sequence in range(3):
        await bus.publish_market_event(trade(sequence))
    await asyncio.wait_for(bus._failure.wait(), 1)
    with pytest.raises(RuntimeError, match="invalid"):
        await bus.publish_market_event(trade(4))
    with pytest.raises(RuntimeError, match="invalid"):
        await bus.close()
    assert invalid == [lane + "_worker_failed"]
    assert len(bus._workers) == 0
    closed_accounting(bus)


@pytest.mark.asyncio
async def test_in_flight_worker_cancel_marks_unconfirmed_publication_failed():
    terminal = Terminal()
    terminal.storage_release.clear()
    bus, durable, invalid = build(terminal)
    await bus.publish_market_event(trade())
    await asyncio.wait_for(terminal.storage_started.wait(), 1)
    bus._workers[0].cancel()
    await asyncio.wait_for(bus._failure.wait(), 1)
    with pytest.raises(RuntimeError):
        await bus.close()
    assert durable == []
    assert invalid == ["storage_worker_cancelled"]
    assert bus.stats["failed"] == 1
    closed_accounting(bus)


@pytest.mark.asyncio
async def test_cancelled_close_caller_does_not_abandon_timeout_cleanup():
    terminal = Terminal()
    terminal.storage_release.clear()
    bus, _, invalid = build(terminal)
    packets = [prepare_market_event(trade(sequence)) for sequence in range(3)]
    for packet in packets:
        await bus.publish_market_packet(packet)
    await asyncio.wait_for(terminal.storage_started.wait(), 1)
    caller = asyncio.create_task(bus.close(timeout_seconds=.1))
    await asyncio.sleep(0)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    with pytest.raises(RuntimeError, match="drain_timeout"):
        await bus.close()
    assert invalid == ["drain_timeout"]
    closed_accounting(bus)
    stats = bus.stats
    assert stats["in_flight_unconfirmed"] == 1
    assert stats["never_sent"] == 2
    assert stats["unconfirmed_count"] == 3
    assert stats["unconfirmed_first_event_id"] == packets[0].snapshot.event_id
    assert stats["unconfirmed_last_event_id"] == packets[-1].snapshot.event_id
    assert stats["unconfirmed_first_source_ts_ns"] == packets[0].snapshot.source_ts_ns
    assert stats["unconfirmed_last_receive_ts_ns"] == packets[-1].snapshot.receive_ts_ns
    assert stats["unconfirmed_first_accepted_seq"] == 1
    assert stats["unconfirmed_last_accepted_seq"] == 3
    assert stats["unconfirmed_first_cause"] == "drain_timeout"
    assert stats["unconfirmed_last_cause"] == "drain_timeout"


@pytest.mark.asyncio
async def test_first_invalidation_can_snapshot_pending_and_rejected_bounds():
    terminal = Terminal()
    observed = []
    bus = SplitMarketEventBus(terminal, on_durable=lambda packet: None,
                             on_invalid=lambda reason: observed.append(bus.stats), max_items=1)
    first, second = trade(1), trade(2)
    await bus.publish_market_event(first)
    with pytest.raises(RuntimeError):
        await bus.publish_market_event(second)
    assert len(observed) == 1
    assert observed[0]["pending_first_event_id"] == first.event_id
    assert observed[0]["pending_last_accepted_seq"] == 1
    assert observed[0]["rejected_event_id"] == second.event_id
    assert observed[0]["rejected_order_lower_bound"] == 2
    with pytest.raises(RuntimeError):
        await bus.close()
    closed_accounting(bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ("storage", "live"))
async def test_first_worker_failure_snapshot_excludes_failed_item_from_pending_bounds(lane):
    terminal = Terminal()
    setattr(terminal, lane + "_error", OSError("offline lane failed"))
    # Keep the other lane in flight so its pending metadata is still visible.
    getattr(terminal, ("live" if lane == "storage" else "storage") + "_release").clear()
    observed = []
    bus = SplitMarketEventBus(terminal, on_durable=lambda packet: None,
                             on_invalid=lambda reason: observed.append(bus.stats))
    packets = [prepare_market_event(trade(sequence)) for sequence in range(3)]
    for packet in packets:
        await bus.publish_market_packet(packet)
    await asyncio.wait_for(bus._failure.wait(), 1)
    assert len(observed) == 1
    snapshot = observed[0]
    prefix = "" if lane == "storage" else "live_"
    assert snapshot[prefix + "failed"] == 1
    assert snapshot[prefix + "pending"] == 2
    assert snapshot[prefix + "pending_first_event_id"] == packets[1].snapshot.event_id
    assert snapshot[prefix + "pending_last_event_id"] == packets[2].snapshot.event_id
    assert snapshot[prefix + "unconfirmed_first_event_id"] == packets[0].snapshot.event_id
    for lane_prefix in ("", "live_"):
        assert snapshot[lane_prefix + "accepted"] == sum(
            snapshot[lane_prefix + name] for name in ("published", "failed", "pending"))
    with pytest.raises(RuntimeError):
        await bus.close()
    closed_accounting(bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_failure", (False, True))
async def test_single_settled_item_has_no_pending_bounds_at_first_invalidation(callback_failure):
    terminal = Terminal()
    if not callback_failure:
        terminal.storage_error = OSError("offline storage failed")
    observed = []
    def durable_failed(_packet):
        raise RuntimeError("offline callback failed")
    bus = SplitMarketEventBus(terminal,
                             on_durable=durable_failed if callback_failure else lambda packet: None,
                             on_invalid=lambda reason: observed.append(bus.stats))
    await bus.publish_market_event(trade())
    await asyncio.wait_for(bus._failure.wait(), 1)
    snapshot = observed[0]
    assert snapshot["accepted"] == 1
    assert snapshot["pending"] == 0
    assert not any(key.startswith("pending_first_") or key.startswith("pending_last_") for key in snapshot)
    assert snapshot["published"] == int(callback_failure)
    assert snapshot["failed"] == int(not callback_failure)
    assert snapshot["unconfirmed_count"] == int(not callback_failure)
    with pytest.raises(RuntimeError):
        await bus.close()
    closed_accounting(bus)


@pytest.mark.asyncio
async def test_gap_scalar_metadata_bounds_untrusted_long_ids_after_cleanup():
    terminal = Terminal()
    bus, _, _ = build(terminal, max_bytes=1)
    event = trade().model_copy(update={"event_id": "x" * 5000, "sequence": "s" * 5000})
    with pytest.raises(RuntimeError):
        await bus.publish_market_event(event)
    with pytest.raises(RuntimeError):
        await bus.close()
    stats = bus.stats
    assert len(stats["rejected_event_id"]) == 256
    assert len(stats["rejected_sequence"]) == 256
    assert stats["rejected_event_id_truncated"] is True
    assert stats["rejected_event_id_sha256"] == hashlib.sha256(event.event_id.encode()).hexdigest()
    closed_accounting(bus)


@pytest.mark.asyncio
async def test_callback_failure_cannot_undo_confirmed_storage_or_retry_notifications():
    terminal = Terminal()
    notifications = []
    def durable_failed(_packet):
        raise RuntimeError("offline readiness callback failed")
    def invalid_failed(reason):
        notifications.append(reason)
        raise RuntimeError("offline invalidation callback failed")
    bus = SplitMarketEventBus(terminal, on_durable=durable_failed, on_invalid=invalid_failed)
    await bus.publish_market_event(trade())
    await asyncio.wait_for(bus._failure.wait(), 1)
    with pytest.raises(RuntimeError):
        await bus.close()
    assert bus.stats["published"] == 1
    assert bus.stats["failed"] == 0
    assert bus.stats["callback_failures"] == 2
    assert notifications == ["storage_callback_failed"]
    closed_accounting(bus)
