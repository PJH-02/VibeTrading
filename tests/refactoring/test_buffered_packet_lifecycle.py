from __future__ import annotations

import asyncio
import hashlib

import pytest

from scripts.refactoring.encoding_probe import cases
from vibetrading.bus import BufferedMarketEventBus, prepare_market_event


class BlockedSink:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.events = []
        self.fail = False

    async def publish_market_event(self, event):
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("fake transport failed")
        self.events.append(event.sequence)


def assert_accounting(bus, terminal=False):
    stats = bus.stats
    assert stats["accepted"] == stats["published"] + stats["failed"] + stats["pending"]
    assert stats["pending"] == stats["queued_records"] + stats["in_flight"]
    assert all(value >= 0 for value in stats.values())
    assert stats["queued_bytes"] <= bus.max_bytes
    if terminal:
        assert stats["pending"] == stats["queued_bytes"] == 0
        assert not bus._workers


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint", ["items", "bytes"])
async def test_cancelled_admission_owns_no_reservation(constraint):
    sink = BlockedSink()
    event = cases()["crypto_trade"]
    size = prepare_market_event(event).byte_length
    bus = BufferedMarketEventBus(sink, shards=1, max_items=1, max_bytes=size*(1 if constraint == "bytes" else 10))
    await bus.publish_market_event(event)
    await sink.entered.wait()
    if constraint == "items":
        await bus.publish_market_event(event.model_copy(update={"sequence":2}))
    before = bus.stats.copy()
    waiter = asyncio.create_task(bus.publish_market_event(event))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert bus.stats == before
    assert_accounting(bus)
    sink.release.set()
    await bus.close()
    assert_accounting(bus, terminal=True)


@pytest.mark.asyncio
async def test_fifo_cancelled_lock_waiter_does_not_reorder_other_admissions():
    sink = BlockedSink()
    bus = BufferedMarketEventBus(sink, shards=1, max_items=1)
    event = cases()["crypto_trade"]
    await bus.publish_market_event(event.model_copy(update={"sequence":0}))
    await sink.entered.wait()
    tasks = [asyncio.create_task(bus.publish_market_event(event.model_copy(update={"sequence":i}))) for i in range(1,8)]
    await asyncio.sleep(0)
    tasks[3].cancel()
    sink.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[3], asyncio.CancelledError)
    await bus.close()
    assert sink.events == [0,1,2,3,5,6,7]
    assert_accounting(bus, terminal=True)


@pytest.mark.asyncio
async def test_close_wakes_waiters_rejects_new_work_and_is_idempotent():
    sink = BlockedSink()
    bus = BufferedMarketEventBus(sink, shards=1, max_items=1)
    event = cases()["crypto_trade"]
    await bus.publish_market_event(event)
    await sink.entered.wait()
    await bus.publish_market_event(event)
    waiter = asyncio.create_task(bus.publish_market_event(event))
    await asyncio.sleep(0)
    closer = asyncio.create_task(bus.close())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="closed"):
        await asyncio.wait_for(waiter, 1)
    sink.release.set()
    await closer
    await bus.close()
    with pytest.raises(RuntimeError, match="closed"):
        await bus.publish_market_event(event)
    assert bus.stats["published"] == 2
    assert_accounting(bus, terminal=True)


@pytest.mark.asyncio
async def test_timeout_accounts_unconfirmed_worker_and_never_sent_queue():
    sink = BlockedSink()
    bus = BufferedMarketEventBus(sink, shards=1, max_items=1)
    event = cases()["crypto_trade"]
    await bus.publish_market_event(event)
    await sink.entered.wait()
    await bus.publish_market_event(event)
    with pytest.raises(RuntimeError, match="drain timed out"):
        await bus.close(timeout_seconds=.01)
    assert bus.stats["failed"] == 2
    assert sink.events == []
    assert_accounting(bus, terminal=True)
    with pytest.raises(RuntimeError, match="drain timed out"):
        await bus.close()


@pytest.mark.asyncio
async def test_worker_failure_wakes_other_shard_byte_waiter():
    sink = BlockedSink()
    event = cases()["crypto_trade"]
    size = prepare_market_event(event).byte_length
    bus = BufferedMarketEventBus(sink, shards=2, max_items=2, max_bytes=size)
    def shard(instrument):
        return int.from_bytes(hashlib.sha256(instrument.key.encode()).digest()[:4], "big") % 2
    other = next(event.instrument.model_copy(update={"std_symbol":str(i)}) for i in range(100) if shard(event.instrument.model_copy(update={"std_symbol":str(i)})) != shard(event.instrument))
    await bus.publish_market_event(event)
    await sink.entered.wait()
    waiter = asyncio.create_task(bus.publish_market_event(event.model_copy(update={"instrument":other})))
    await asyncio.sleep(0)
    sink.fail = True
    sink.release.set()
    with pytest.raises(RuntimeError, match="fake transport failed"):
        await asyncio.wait_for(waiter, 1)
    with pytest.raises(RuntimeError, match="fake transport failed"):
        await bus.close()
    assert bus.stats["accepted"] == bus.stats["failed"] == 1
    assert_accounting(bus, terminal=True)


@pytest.mark.asyncio
async def test_exact_limit_and_oversize_have_consistent_accounting():
    sink = BlockedSink()
    event = cases()["crypto_trade"]
    size = prepare_market_event(event).byte_length
    bus = BufferedMarketEventBus(sink, shards=1, max_bytes=size)
    await bus.publish_market_event(event)
    assert bus.stats["queued_bytes"] == size
    sink.release.set()
    await bus.close()
    smaller = BufferedMarketEventBus(sink, shards=1, max_bytes=size-1)
    with pytest.raises(RuntimeError, match="exceeds publisher byte limit"):
        await smaller.publish_market_event(event)
    assert smaller.stats["accepted"] == 0
    await smaller.close()
    assert_accounting(smaller, terminal=True)


@pytest.mark.asyncio
async def test_cancelling_close_caller_does_not_abandon_drain():
    sink = BlockedSink()
    bus = BufferedMarketEventBus(sink, shards=1)
    await bus.publish_market_event(cases()["crypto_trade"])
    await sink.entered.wait()
    closer = asyncio.create_task(bus.close())
    await asyncio.sleep(0)
    closer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closer
    sink.release.set()
    await bus.close()
    assert_accounting(bus, terminal=True)
