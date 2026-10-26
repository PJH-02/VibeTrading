"""Bounded independent storage/live publication lanes inside one event loop.

This separates JetStream ACK latency from live work; both still use NATS.
Admission never waits for a lane. A capacity failure invalidates the feed and
rejects the entire publication rather than dropping a trade or book delta.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import inspect
import hashlib
import time
from typing import Any, Callable

from .bus import MarketEventPacket, MarketPublication, prepare_market_event
from core.models import BaseMarketEvent


def _packet_bounds(packet: MarketEventPacket, accepted_seq: int, cause: str) -> dict[str, Any]:
    snapshot = packet.snapshot
    event_id = snapshot.event_id
    sequence = snapshot.sequence
    return {
        "event_id": event_id[:256],
        "event_id_sha256": hashlib.sha256(event_id.encode("utf-8")).hexdigest(),
        "event_id_truncated": len(event_id) > 256,
        "source_ts_ns": snapshot.source_ts_ns, "receive_ts_ns": snapshot.receive_ts_ns,
        "sequence": sequence[:256] if isinstance(sequence, str) else sequence,
        "accepted_seq": accepted_seq, "cause": cause,
    }


@dataclass
class _Lane:
    name: str
    max_bytes: int
    queue: asyncio.Queue[tuple[MarketPublication, int, int]]
    accepted: int = 0
    published: int = 0
    failed: int = 0
    retained_bytes: int = 0
    in_flight: int = 0
    accepted_at: deque[tuple[float, MarketEventPacket, int]] = field(default_factory=deque)
    never_sent: int = 0
    in_flight_unconfirmed: int = 0
    unconfirmed_first: dict[str, Any] | None = None
    unconfirmed_last: dict[str, Any] | None = None

    def record_unconfirmed(self, packet: MarketEventPacket, sequence: int, cause: str, *, sent: bool) -> None:
        if sent:
            self.in_flight_unconfirmed += 1
        else:
            self.never_sent += 1
        metadata = _packet_bounds(packet, sequence, cause)
        if self.unconfirmed_first is None or sequence < self.unconfirmed_first["accepted_seq"]:
            self.unconfirmed_first = metadata
        if self.unconfirmed_last is None or sequence > self.unconfirmed_last["accepted_seq"]:
            self.unconfirmed_last = metadata

    def stats(self) -> dict[str, Any]:
        age = max(0, int((time.monotonic() - self.accepted_at[0][0]) * 1000)) if self.accepted_at else 0
        result = {
            "accepted": self.accepted, "published": self.published, "failed": self.failed,
            "pending": self.accepted - self.published - self.failed,
            "queued_bytes": self.retained_bytes, "queued_records": self.queue.qsize(),
            "in_flight": self.in_flight, "oldest_age_ms": age,
            "never_sent": self.never_sent, "in_flight_unconfirmed": self.in_flight_unconfirmed,
            "unconfirmed_count": self.never_sent + self.in_flight_unconfirmed,
        }
        for label, metadata in (("unconfirmed_first", self.unconfirmed_first), ("unconfirmed_last", self.unconfirmed_last)):
            if metadata is not None:
                result.update({label + "_" + key: value for key, value in metadata.items()})
        if self.accepted_at:
            for label, item in (("pending_first", self.accepted_at[0]), ("pending_last", self.accepted_at[-1])):
                result.update({label + "_" + key: value for key, value in _packet_bounds(item[1], item[2], "pending").items()})
        return result


class SplitMarketEventBus:
    """Atomic bounded admission followed by one worker per publication lane.

    Byte limits include queued and in-flight durable+live payload bytes for
    each lane holding the packet. They conservatively count aliases twice;
    Python metadata and transport buffers require separate process budgets.
    """

    _market_packet_protocol = 1

    def __init__(
        self, terminal: Any, *, on_durable: Callable[[MarketEventPacket], None],
        on_invalid: Callable[[str], None], max_items: int = 128,
        max_bytes: int = 64 * 1024 * 1024, live_max_items: int = 256,
        live_max_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        max_items, max_bytes, live_max_items, live_max_bytes = (
            int(value) for value in (max_items, max_bytes, live_max_items, live_max_bytes)
        )
        if min(max_items, max_bytes, live_max_items, live_max_bytes) <= 0:
            raise ValueError("fanout queue limits must be positive")
        if inspect.iscoroutinefunction(on_durable) or inspect.iscoroutinefunction(on_invalid):
            raise TypeError("fanout callbacks must be synchronous")
        self.inner = terminal
        self._on_durable, self._on_invalid = on_durable, on_invalid
        self._storage = _Lane("storage", int(max_bytes), asyncio.Queue(maxsize=int(max_items)))
        self._live = _Lane("live", int(live_max_bytes), asyncio.Queue(maxsize=int(live_max_items)))
        self._workers: list[asyncio.Task[None]] = []
        self._error: BaseException | None = None
        self._gap_reason: str | None = None
        self._failure = asyncio.Event()
        self._closing = self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._rejected = self._filtered = self._callback_failures = 0
        self._gap_notifications = 0
        self._rejected_metadata: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    @property
    def stats(self) -> dict[str, Any]:
        result = self._storage.stats()
        result.update({"live_" + key: value for key, value in self._live.stats().items()})
        result.update(gap=int(self._error is not None), gap_notifications=self._gap_notifications,
                      rejected=self._rejected, filtered=self._filtered, callback_failures=self._callback_failures)
        result.update(gap_reason=self._gap_reason, rejected_count_lower_bound=self._rejected,
                      rejected_count_is_lower_bound=True)
        if self._rejected_metadata is not None:
            result.update({"rejected_" + key: value for key, value in self._rejected_metadata.items()})
        return result

    @property
    def gap_reason(self) -> str | None:
        return self._gap_reason

    def _check_open(self) -> None:
        for worker in self._workers:
            if worker.done() and self._error is None and not self._closing:
                self._latch_error(RuntimeError("fanout worker stopped unexpectedly"), "worker_stopped")
        self._raise_error()
        if self._closing or self._closed:
            raise RuntimeError("split market publisher is closed")

    def _ensure_started(self) -> None:
        if not self._workers:
            for lane in (self._storage, self._live):
                worker = asyncio.create_task(self._worker(lane), name=f"market-fanout-{lane.name}")
                worker.add_done_callback(self._worker_done)
                self._workers.append(worker)

    async def start(self) -> None:
        self._check_open()
        self._ensure_started()

    async def publish_market_event(self, event: BaseMarketEvent) -> None:
        await self.publish_market_packet(prepare_market_event(event))

    _packet_public_method = publish_market_event

    async def publish_market_packet(self, packet: MarketEventPacket) -> None:
        self._check_open()
        try:
            publication = self.inner.resolve_market_publication(packet)
        except BaseException as exc:
            self._record_rejected(packet, "publication_resolution_failed")
            self._latch_error(exc, "publication_resolution_failed")
            raise
        if publication is None:
            self._filtered += 1
            return
        # No suspension between checking both lanes and transferring ownership.
        # The actual resolved packet may be a generated timestamp-gap event.
        packet = publication.packet
        size = len(packet.payload) + len(packet.live_payload)
        lanes = (self._storage, self._live) if publication.live else (self._storage,)
        for lane in lanes:
            reason = None
            if lane.queue.full():
                reason = lane.name + "_queue_full"
            elif size > lane.max_bytes or lane.retained_bytes + size > lane.max_bytes:
                reason = lane.name + "_byte_limit"
            if reason:
                self._record_rejected(packet, reason)
                error = RuntimeError(f"market fanout rejected publication: {reason}")
                self._latch_error(error, reason)
                raise error
        self._ensure_started()
        admission_sequence = self._storage.accepted + 1
        try:
            for lane in lanes:
                lane.queue.put_nowait((publication, size, admission_sequence))
                lane.retained_bytes += size
                lane.accepted += 1
                lane.accepted_at.append((time.monotonic(), packet, admission_sequence))
        except BaseException as exc:
            # Capacity cannot change without suspension. An unexpected local
            # queue/allocation failure still closes both workers before they
            # can publish any partially transferred admission.
            self._record_rejected(packet, "admission_failed", order_lower_bound=admission_sequence)
            self._latch_error(exc, "admission_failed")
            raise

    def _record_rejected(self, packet: MarketEventPacket, cause: str, *, order_lower_bound: int | None = None) -> None:
        self._rejected += 1
        metadata = _packet_bounds(packet, self._storage.accepted + 1, cause)
        metadata["order_lower_bound"] = order_lower_bound or metadata.pop("accepted_seq")
        metadata.pop("accepted_seq", None)
        self._rejected_metadata = metadata

    def _invoke_sync(self, callback: Callable[..., None], argument: Any) -> None:
        result = callback(argument)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("fanout callbacks must not return an awaitable")

    def _latch_error(self, error: BaseException, reason: str) -> None:
        if self._error is not None:
            return
        self._error, self._gap_reason = error, reason
        self._failure.set()
        self._gap_notifications += 1
        try:
            self._invoke_sync(self._on_invalid, reason)
        except BaseException:
            # The first publication failure remains authoritative. No tasks or
            # unbounded retries are created to deliver an invalidation signal.
            self._callback_failures += 1

    def _worker_done(self, worker: asyncio.Task[None]) -> None:
        if self._closing or self._error is not None:
            return
        error = RuntimeError("fanout worker cancelled or stopped unexpectedly")
        if not worker.cancelled():
            error = worker.exception() or error
        self._latch_error(error, "worker_stopped")

    async def _worker(self, lane: _Lane) -> None:
        while True:
            publication, size, accepted_sequence = await lane.queue.get()
            lane.in_flight += 1
            published = False
            try:
                if self._error is not None:
                    lane.failed += 1
                    lane.record_unconfirmed(publication.packet, accepted_sequence, self._gap_reason or "feed_invalid", sent=False)
                    lane.accepted_at.popleft()
                    return
                if lane is self._storage:
                    await self.inner.publish_market_storage(publication)
                else:
                    await self.inner.publish_market_live(publication)
                lane.published += 1
                published = True
                # Settle pending metadata together with the counters before a
                # synchronous callback can snapshot the lane. Byte ownership
                # and task accounting still release exactly once in finally.
                lane.accepted_at.popleft()
                if lane is self._storage:
                    self._invoke_sync(self._on_durable, publication.packet)
            except asyncio.CancelledError:
                if not published:
                    lane.failed += 1
                    lane.record_unconfirmed(publication.packet, accepted_sequence, self._gap_reason or lane.name + "_worker_cancelled", sent=True)
                    lane.accepted_at.popleft()
                self._latch_error(RuntimeError("fanout worker cancelled with unconfirmed publication"), lane.name + "_worker_cancelled")
                raise
            except BaseException as exc:
                if not published:
                    lane.failed += 1
                    lane.record_unconfirmed(publication.packet, accepted_sequence, self._gap_reason or lane.name + "_worker_failed", sent=True)
                    lane.accepted_at.popleft()
                else:
                    self._callback_failures += 1
                self._latch_error(exc, lane.name + ("_callback_failed" if published else "_worker_failed"))
            finally:
                lane.retained_bytes -= size
                lane.in_flight -= 1
                lane.queue.task_done()
            if self._error is not None:
                return

    def _raise_error(self) -> None:
        if self._error is not None:
            raise RuntimeError(f"split market publisher invalid: {self._gap_reason}") from self._error

    async def close(self, timeout_seconds: float = 5.0) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._drain_and_close(timeout_seconds))
        await asyncio.shield(self._close_task)

    async def _drain_and_close(self, timeout_seconds: float) -> None:
        joined = asyncio.gather(self._storage.queue.join(), self._live.queue.join())
        failed = asyncio.create_task(self._failure.wait())
        try:
            done, _ = await asyncio.wait((joined, failed), timeout=max(.1, timeout_seconds),
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                self._latch_error(RuntimeError("fanout drain timed out"), "drain_timeout")
            self._raise_error()
        finally:
            joined.cancel()
            failed.cancel()
            for worker in self._workers:
                worker.cancel()
            await asyncio.gather(joined, failed, *self._workers, return_exceptions=True)
            self._workers = []
            for lane in (self._storage, self._live):
                while not lane.queue.empty():
                    publication, size, accepted_sequence = lane.queue.get_nowait()
                    lane.retained_bytes -= size
                    lane.failed += 1
                    lane.record_unconfirmed(publication.packet, accepted_sequence, self._gap_reason or "close_cleanup", sent=False)
                    lane.accepted_at.popleft()
                    lane.queue.task_done()
            self._closed = True
