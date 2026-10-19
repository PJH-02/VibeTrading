"""State-only local market application and a bounded ETF event source.

There is no broker, bus, durable writer or network dependency here. A recovery
checkpoint is a trusted attestation that its state was restored by the caller;
this adapter does not invent or persist that state snapshot.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import inspect
import json
import math
import time
from typing import Callable, Mapping

from .feed_recovery import EngineCursor, FeedMode, RecoveryFrame
from .hot_path import HotPathStore, freeze_market_event
from .local_transport import AppliedReceipt
from .models import AssetClass, BaseMarketEvent, EventType, SCHEMA_VERSION, event_from_dict


class EngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class EngineBudgets:
    max_producers: int
    max_instruments: int
    max_frame_bytes: int
    max_records: int
    max_bytes: int
    max_age_ns: int
    max_digests_per_feed: int
    max_state_callbacks: int
    feed_lease_ns: int
    tick_seconds: float

    def __post_init__(self):
        for value in (self.max_producers, self.max_instruments, self.max_frame_bytes,
                      self.max_records, self.max_bytes, self.max_age_ns,
                      self.max_digests_per_feed, self.max_state_callbacks, self.feed_lease_ns):
            if type(value) is not int or value <= 0:
                raise ValueError("Engine budgets must be explicit positive integers")
        if self.max_frame_bytes > self.max_bytes:
            raise ValueError("Engine frame exceeds its byte budget")
        if isinstance(self.tick_seconds, bool) or not math.isfinite(self.tick_seconds) or self.tick_seconds <= 0:
            raise ValueError("Engine watchdog period must be positive and finite")
        if self.tick_seconds * 1_000_000_000 > min(self.max_age_ns, self.feed_lease_ns):
            raise ValueError("Engine watchdog period exceeds the declared age or lease")


@dataclass
class _Feed:
    provider: str
    mode: FeedMode = FeedMode.INVALID
    reason: str = "not_started"
    checkpoint: EngineCursor | None = None
    high_water: EngineCursor | None = None
    applied: EngineCursor | None = None
    generation: int = 0
    last_live_ns: int | None = None
    lease_ns: int = 0
    digests: OrderedDict = field(default_factory=OrderedDict)
    instruments: set[str] = field(default_factory=set)
    initial_proof: tuple[EngineCursor, EngineCursor] | None = None
    initial_consumed: bool = False
    ever_live: bool = False


def _cursor(cursor):
    if (not isinstance(cursor, EngineCursor) or type(cursor.sequence) is not int
            or not 0 <= cursor.sequence <= 2**63 - 1
            or any(not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128
                   for value in (cursor.producer, cursor.incarnation))):
        raise EngineError("invalid_engine_cursor")


def _sync(callback, *args):
    result = callback(*args)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise EngineError("engine_callbacks_must_be_synchronous_state_only")
    return result


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise EngineError("duplicate_json_key")
        result[key] = value
    return result


class LocalMarketEngine:
    def __init__(self, *, producer_providers: Mapping[str, str], budgets: EngineBudgets,
                 on_invalidate: Callable[[str, str], None],
                 hot_path: HotPathStore | None = None, state_callbacks: tuple = (),
                 trading_filter: Callable[[BaseMarketEvent], bool] | None = None,
                 fresh_starts: Mapping[str, str] | None = None, verified_fresh_start: bool = False,
                 clock: Callable[[], int] = time.monotonic_ns):
        if (not isinstance(producer_providers, Mapping) or not producer_providers
                or len(producer_providers) > budgets.max_producers):
            raise ValueError("An explicit bounded producer/provider allowlist is required")
        for producer, provider in producer_providers.items():
            if any(not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128
                   for value in (producer, provider)):
                raise ValueError("Producer and provider identities must be bounded exact strings")
        if (not isinstance(state_callbacks, tuple) or len(state_callbacks) > budgets.max_state_callbacks
                or bool(hot_path is not None) == bool(state_callbacks)):
            raise ValueError("Supply exactly one state authority: hot_path or bounded state_callbacks")
        callbacks = (on_invalidate, *state_callbacks)
        if any(not callable(callback) or inspect.iscoroutinefunction(callback) for callback in callbacks):
            raise TypeError("Engine callbacks must be synchronous")
        if trading_filter is not None and (not callable(trading_filter) or inspect.iscoroutinefunction(trading_filter)):
            raise TypeError("Trading filter must be synchronous")
        self.budgets, self.on_invalidate, self.hot_path = budgets, on_invalidate, hot_path
        self.state_callbacks, self.clock = state_callbacks, clock
        self.trading_filter = trading_filter or (lambda event: event.instrument.asset_class == AssetClass.ETF)
        self._feeds = {producer: _Feed(provider) for producer, provider in producer_providers.items()}
        self._queue = deque()
        self._queue_bytes = 0
        self._ready = asyncio.Event()
        self._source_error = None
        self._consumer_active = False
        self._closed = False
        self._watchdog = None
        self._constructing = True
        self._had_fault = False
        self._applied = self._duplicates = self._replayed = self._delivered = self._discarded = 0
        if fresh_starts is not None:
            if (verified_fresh_start is not True or not isinstance(fresh_starts, Mapping)
                    or not fresh_starts or set(fresh_starts) - set(self._feeds)
                    or (hot_path is not None and hot_path.apply_seq != 0)):
                raise ValueError("Fresh start requires an explicit verified virgin state")
            for producer, incarnation in fresh_starts.items():
                initial = EngineCursor(producer, incarnation, 0)
                self.begin_recovery(initial, initial, verified_checkpoint=True)
                self.handoff(initial, initial, verified=True, ready=True, lease_ns=budgets.feed_lease_ns)
        self._constructing = False

    def _feed(self, cursor):
        _cursor(cursor)
        feed = self._feeds.get(cursor.producer)
        if feed is None:
            raise EngineError("unregistered_producer")
        return feed

    def _flush(self):
        self._discarded += len(self._queue)
        self._queue.clear()
        self._queue_bytes = 0
        self._ready.set()

    def _invalidate(self, producer: str, reason: str, *, fault: bool):
        if fault:
            self._had_fault = True
        selected = [producer] if producer in self._feeds else list(self._feeds)
        self._source_error = EngineError(reason)
        self._flush()
        for name in selected:
            feed = self._feeds[name]
            feed.mode, feed.reason = FeedMode.INVALID, reason
            feed.generation += 1
            try:
                _sync(self.on_invalidate, name, reason)
            except BaseException:
                # The local gate remains closed even if reporting/gating hooks
                # fail. An external execution owner must enforce its own lease.
                feed.reason = reason + ":invalidation_callback_failed"
                self._had_fault = True

    def invalidate(self, producer: str, reason: str):
        self._invalidate(producer, reason, fault=True)

    def on_gap(self, cursor: EngineCursor, reason: str):
        self.invalidate(cursor.producer, "transport_gap:" + reason[:256])

    def begin_recovery(self, checkpoint: EngineCursor, high_water: EngineCursor, *, verified_checkpoint: bool):
        feed = self._feed(checkpoint)
        _cursor(high_water)
        if (verified_checkpoint is not True or checkpoint.producer != high_water.producer
                or checkpoint.incarnation != high_water.incarnation or checkpoint.sequence > high_water.sequence
                or self._closed):
            self.invalidate(checkpoint.producer, "checkpoint_high_water_unverified")
            raise EngineError("checkpoint_high_water_unverified")
        if not self._constructing:
            self._invalidate(checkpoint.producer, "recovery_started",
                fault=feed.ever_live or feed.initial_proof is not None)
        else:
            self._flush()
        feed.generation += 1
        feed.checkpoint, feed.high_water, feed.applied = checkpoint, high_water, checkpoint
        feed.digests.clear()
        feed.last_live_ns = None
        feed.lease_ns = 0
        feed.mode = FeedMode.WARMING if checkpoint == high_water else FeedMode.REPLAY
        feed.reason = "explicit_handoff_required"

    def handoff(self, checkpoint: EngineCursor, high_water: EngineCursor, *, verified: bool,
                ready: bool, lease_ns: int) -> EngineCursor:
        feed = self._feed(checkpoint)
        if (self._closed or verified is not True or ready is not True
                or checkpoint != feed.checkpoint or high_water != feed.high_water
                or feed.applied != high_water or feed.mode != FeedMode.WARMING
                or type(lease_ns) is not int or not 0 < lease_ns <= self.budgets.feed_lease_ns):
            self.invalidate(checkpoint.producer, "handoff_not_verified")
            raise EngineError("handoff_not_verified")
        feed.mode, feed.reason = FeedMode.LIVE, "verified_handoff"
        feed.ever_live = True
        feed.last_live_ns, feed.lease_ns = self.clock(), lease_ns
        if all(item.mode == FeedMode.LIVE for item in self._feeds.values()):
            self._source_error = None
        self._ready.set()
        return high_water

    def _initial_proofs_ready(self):
        if self._had_fault:
            return False
        return all(feed.initial_proof is not None
                   and feed.initial_proof == (feed.checkpoint, feed.high_water)
                   and ((feed.mode == FeedMode.WARMING and not feed.initial_consumed
                         and feed.applied == feed.high_water)
                        or (feed.mode == FeedMode.LIVE and feed.initial_consumed))
                   for feed in self._feeds.values())

    def arm_initial_resume(self, checkpoint: EngineCursor, high_water: EngineCursor):
        """Arm a one-use virgin-process C/H proof, without starting its lease.

        Root calls this only after restoring all state and initializing account
        gates. It is not an automatic recovery route after a live GAP or error.
        """
        feed = self._feed(checkpoint)
        _cursor(high_water)
        if (self._closed or self._had_fault or feed.ever_live or feed.initial_proof is not None
                or feed.initial_consumed or feed.mode != FeedMode.WARMING
                or checkpoint != feed.checkpoint or high_water != feed.high_water or feed.applied != high_water):
            self.invalidate(checkpoint.producer, "initial_resume_proof_invalid")
            raise EngineError("initial_resume_proof_invalid")
        feed.initial_proof = (checkpoint, high_water)
        feed.reason = "initial_resume_armed"
        if self._initial_proofs_ready():
            self._source_error = None
        self._ready.set()

    def initial_resume(self, checkpoint: EngineCursor) -> bool:
        """Consume only an exact armed initial H; retries never renew a lease."""
        try:
            self.check_health()
            feed = self._feed(checkpoint)
            if self._closed or self._had_fault or not self._initial_proofs_ready():
                return False
            if feed.initial_consumed:
                # Idempotent healthy retry, with no new epoch/lease grant.
                return feed.mode == FeedMode.LIVE and checkpoint == feed.applied
            if checkpoint != feed.high_water or checkpoint != feed.applied:
                return False
            feed.initial_consumed = True
            feed.mode, feed.reason = FeedMode.LIVE, "initial_proof_consumed"
            feed.ever_live = True
            feed.last_live_ns, feed.lease_ns = self.clock(), self.budgets.feed_lease_ns
            self._ready.set()
            return True
        except EngineError:
            return False

    def _queue_admissible(self):
        if self._source_error is not None:
            return False
        return all(feed.mode == FeedMode.LIVE for feed in self._feeds.values()) or self._initial_proofs_ready()

    def request_resume(self, checkpoint: EngineCursor) -> bool:
        try:
            self.check_health()
            feed = self._feed(checkpoint)
            return not self._closed and feed.mode == FeedMode.LIVE and checkpoint == feed.applied
        except EngineError:
            return False

    def _decode(self, frame: RecoveryFrame, feed: _Feed) -> BaseMarketEvent:
        if type(frame.payload) is not bytes or not frame.payload or len(frame.payload) > self.budgets.max_frame_bytes:
            raise EngineError("market_frame_budget_exceeded")
        data = json.loads(frame.payload.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(EngineError("nonfinite_json")))
        required = {"event_id", "schema_version", "event_type", "instrument", "provider", "venue",
                    "receive_ts_ns", "wallclock_ts_ns"}
        if (not isinstance(data, dict) or not required.issubset(data)
                or type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION
                or not isinstance(data["event_id"], str) or not data["event_id"]
                or any(type(data[name]) is not int or data[name] <= 0 for name in ("receive_ts_ns", "wallclock_ts_ns"))):
            raise EngineError("explicit_serialized_market_identity_required")
        event = event_from_dict(data)
        if event.provider != feed.provider or event.venue != event.instrument.venue:
            raise EngineError("provider_or_instrument_venue_mismatch")
        key = event.instrument.key
        if key not in feed.instruments:
            if sum(len(item.instruments) for item in self._feeds.values()) >= self.budgets.max_instruments:
                raise EngineError("engine_instrument_budget_exceeded")
            feed.instruments.add(key)
        return freeze_market_event(event)

    def _frame(self, frame, *, replay):
        if type(frame.payload) is not bytes or not frame.payload or len(frame.payload) > self.budgets.max_frame_bytes:
            raise EngineError("market_frame_budget_exceeded")
        feed = self._feed(frame.cursor)
        if self._closed or feed.applied is None:
            raise EngineError("explicit_recovery_required")
        if (frame.cursor.producer, frame.cursor.incarnation) != (feed.applied.producer, feed.applied.incarnation):
            raise EngineError("producer_incarnation_changed")
        old = feed.digests.get(frame.cursor.sequence)
        if old is not None:
            if old != frame.digest:
                raise EngineError("cursor_content_conflict")
            self._duplicates += 1
            return feed, True
        if frame.cursor.sequence <= feed.applied.sequence:
            raise EngineError("old_cursor_content_unverifiable")
        if frame.cursor.sequence != feed.applied.sequence + 1:
            raise EngineError("producer_sequence_gap")
        if replay:
            if feed.mode not in {FeedMode.REPLAY, FeedMode.WARMING} or frame.cursor.sequence > feed.high_water.sequence:
                raise EngineError("replay_outside_verified_range")
        elif feed.mode != FeedMode.LIVE:
            raise EngineError("live_input_before_verified_handoff")
        return feed, False

    def _apply_state(self, event, phase):
        if self.hot_path is not None:
            result = self.hot_path.apply_versioned(event)
            if not result.applied and not result.duplicate and result.reason not in {"raw_only", "data_gap"}:
                raise EngineError("market_state_rejected:" + result.reason)
            return result.applied
        for callback in self.state_callbacks:
            if _sync(callback, event, phase) is False:
                raise EngineError("state_callback_rejected")
        return not (event.event_type == EventType.ORDER_BOOK and getattr(event, "fast", None) is False)

    def _commit_cursor(self, frame, feed, replay):
        feed.applied = frame.cursor
        feed.digests[frame.cursor.sequence] = frame.digest
        while len(feed.digests) > self.budgets.max_digests_per_feed:
            feed.digests.popitem(last=False)
        self._applied += 1
        if replay:
            self._replayed += 1
            if feed.applied == feed.high_water:
                feed.mode = FeedMode.WARMING

    def apply_replay(self, frame: RecoveryFrame) -> AppliedReceipt:
        try:
            feed, duplicate = self._frame(frame, replay=True)
            if not duplicate:
                generation = feed.generation
                event = self._decode(frame, feed)
                if event.event_type == EventType.DATA_GAP:
                    raise EngineError("gap_inside_replay_range")
                self._apply_state(event, FeedMode.REPLAY)
                if feed.generation != generation or feed.mode not in {FeedMode.REPLAY, FeedMode.WARMING}:
                    raise EngineError("state_callback_invalidated_recovery")
                self._commit_cursor(frame, feed, True)
            return AppliedReceipt(frame.cursor, frame.digest)
        except BaseException:
            self.invalidate(frame.cursor.producer, "replay_application_failed")
            raise

    def on_frame(self, frame: RecoveryFrame) -> AppliedReceipt:
        try:
            self.check_health()
            feed, duplicate = self._frame(frame, replay=False)
            if duplicate:
                return AppliedReceipt(frame.cursor, frame.digest)
            generation = feed.generation
            event = self._decode(frame, feed)
            if event.event_type == EventType.DATA_GAP:
                self.invalidate(frame.cursor.producer, "market_data_gap")
                self._apply_state(event, FeedMode.INVALID)
                self._commit_cursor(frame, feed, False)
                return AppliedReceipt(frame.cursor, frame.digest)
            phase = FeedMode.REPLAY if event.ingest_mode == "backfill" else FeedMode.LIVE
            selected = _sync(self.trading_filter, event)
            if type(selected) is not bool:
                raise EngineError("trading_filter_must_return_bool")
            if feed.generation != generation or feed.mode != FeedMode.LIVE:
                raise EngineError("filter_invalidated_feed")
            enqueue = selected and phase == FeedMode.LIVE and self._queue_admissible()
            if enqueue and (len(self._queue) >= self.budgets.max_records or self._queue_bytes + len(frame.payload) > self.budgets.max_bytes):
                raise EngineError("trading_queue_budget_exceeded")
            changed = self._apply_state(event, phase)
            if feed.generation != generation or feed.mode != FeedMode.LIVE:
                raise EngineError("state_callback_invalidated_feed")
            self._commit_cursor(frame, feed, False)
            if phase == FeedMode.LIVE:
                feed.last_live_ns = self.clock()
            if enqueue and changed:
                self._queue.append((frame.cursor, frame.payload, feed.generation, self.clock()))
                self._queue_bytes += len(frame.payload)
                self._ready.set()
            return AppliedReceipt(frame.cursor, frame.digest)
        except BaseException as error:
            producer = frame.cursor.producer
            self.invalidate(producer, str(error)[:256] if isinstance(error, EngineError) else "market_application_failed")
            raise

    def check_health(self):
        if self._closed:
            raise EngineError("engine_closed")
        now = self.clock()
        if self._queue and now - self._queue[0][3] > self.budgets.max_age_ns:
            producer = self._queue[0][0].producer
            self.invalidate(producer, "trading_queue_oldest_age_exceeded")
        for producer, feed in self._feeds.items():
            if feed.mode == FeedMode.LIVE and (feed.last_live_ns is None or not 0 <= now - feed.last_live_ns <= feed.lease_ns):
                self.invalidate(producer, "feed_health_lease_expired")

    async def event_source(self):
        if self._consumer_active:
            raise EngineError("event_source_already_has_consumer")
        self._consumer_active = True
        try:
            while not self._closed:
                self.check_health()
                if self._source_error is not None:
                    raise EngineError("local_market_source_invalid") from self._source_error
                if not all(feed.mode == FeedMode.LIVE for feed in self._feeds.values()):
                    # Early producers may fill a bounded startup tail. Do not
                    # expose it until every declared dependency has resumed.
                    self._ready.clear()
                    await self._ready.wait()
                    continue
                if not self._queue:
                    self._ready.clear()
                    await self._ready.wait()
                    continue
                cursor, payload, generation, stamp = self._queue.popleft()
                self._queue_bytes -= len(payload)
                feed = self._feeds[cursor.producer]
                if feed.mode != FeedMode.LIVE or generation != feed.generation or self.clock() - stamp > self.budgets.max_age_ns:
                    self.invalidate(cursor.producer, "queued_event_generation_or_age_invalid")
                    continue
                self._delivered += 1
                yield json.loads(payload)
        finally:
            self._consumer_active = False

    async def start(self):
        if self._closed or self._watchdog is not None:
            raise EngineError("engine_not_startable")
        self._watchdog = asyncio.create_task(self._watch(), name="local-market-health-watch")
        return self

    async def _watch(self):
        while True:
            await asyncio.sleep(self.budgets.tick_seconds)
            self.check_health()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        for producer in self._feeds:
            self.invalidate(producer, "engine_closed")
        if self._watchdog:
            self._watchdog.cancel()
            await asyncio.gather(self._watchdog, return_exceptions=True)

    @property
    def stats(self):
        return {"closed": self._closed, "applied": self._applied, "duplicates": self._duplicates,
            "replayed": self._replayed, "delivered": self._delivered, "discarded": self._discarded,
            "queued_records": len(self._queue), "queued_bytes": self._queue_bytes,
            "feeds": {producer: {"provider": feed.provider, "mode": feed.mode.value, "reason": feed.reason,
                "generation": feed.generation, "applied_sequence": feed.applied.sequence if feed.applied else None,
                "incarnation": feed.applied.incarnation if feed.applied else None,
                "retained_digests": len(feed.digests), "initial_armed": feed.initial_proof is not None,
                "initial_consumed": feed.initial_consumed} for producer, feed in self._feeds.items()}}
