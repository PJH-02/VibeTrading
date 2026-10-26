"""State-only local market application and a bounded ETF event source.

There is no broker, bus or network dependency here. An optional caller-owned
spool records checkpoint suffix order without per-event fsync. Market
checkpoints require a quiescent owner and restore only into an unused engine.
They do not contain strategy/account state or prove external readiness.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import hashlib
import inspect
import json
import math
import threading
import time
from typing import Callable, Mapping

from .feed_recovery import EngineCursor, FeedMode, RecoveryFrame
from .hot_path import ApplyResult, DecisionSnapshot, HotPathStore, freeze_market_event
from .local_transport import AppliedReceipt
from .market_checkpoint import CheckpointCodec, CheckpointProducer, _canonical
from .market_apply_order import ApplyOrder, ApplyOrderWriter, order_anchor, order_record
from .market_spool import MarketSpool
from core.models import AssetClass, BaseMarketEvent, EventType, SCHEMA_VERSION, event_from_dict


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
    state_poisoned: bool = False
    state_checkpoint_verified: bool = False


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
                 post_apply_callbacks: tuple = (), on_tick: Callable[[int], None] | None = None,
                 snapshot_clock: Callable[[], int] | None = None,
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
        if (not isinstance(post_apply_callbacks, tuple) or len(post_apply_callbacks) > budgets.max_state_callbacks
                or (post_apply_callbacks and hot_path is None)
                or (post_apply_callbacks and not callable(snapshot_clock))
                or (snapshot_clock is not None and not callable(snapshot_clock))
                or (snapshot_clock is not None and inspect.iscoroutinefunction(snapshot_clock))):
            raise ValueError("Post-apply callbacks require the hot-path authority and an explicit epoch clock")
        callbacks = (on_invalidate, *state_callbacks, *post_apply_callbacks)
        if on_tick is not None:
            callbacks += (on_tick,)
        if any(not callable(callback) or inspect.iscoroutinefunction(callback) for callback in callbacks):
            raise TypeError("Engine callbacks must be synchronous")
        if trading_filter is not None and (not callable(trading_filter) or inspect.iscoroutinefunction(trading_filter)):
            raise TypeError("Trading filter must be synchronous")
        self.budgets, self.on_invalidate, self.hot_path = budgets, on_invalidate, hot_path
        self.state_callbacks, self.clock = state_callbacks, clock
        self.post_apply_callbacks = post_apply_callbacks
        self.on_tick = on_tick
        self.snapshot_clock = snapshot_clock or time.time_ns
        self.trading_filter = trading_filter or (lambda event: event.instrument.asset_class == AssetClass.ETF)
        self._feeds = {producer: _Feed(provider) for producer, provider in producer_providers.items()}
        self._queue = deque()
        self._queue_bytes = 0
        self._ready = asyncio.Event()
        self._source_error = None
        self._consumer_active = False
        self._closed = False
        self._watchdog = None
        self._owner_thread = threading.get_ident()
        self._owner_depth = 0
        self._checkpoint_active = False
        self._checkpoint_restored = False
        self._order_spool = None
        self._replay_order = deque()
        self._committed_feed_cursors = {}
        self._checkpoint_origin_verified = hot_path is not None and self._checkpoint_store_virgin(hot_path)
        self._committed_store_cursor = ((hot_path.engine_incarnation, hot_path.apply_seq)
                                        if hot_path is not None else None)
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

    @contextmanager
    def _owner_operation(self, *, exclusive=False):
        if threading.get_ident() != self._owner_thread:
            raise EngineError("engine_owner_thread_required")
        if self._checkpoint_active or (exclusive and self._owner_depth):
            raise EngineError("engine_owner_operation_in_progress")
        self._owner_depth += 1
        try:
            yield
        finally:
            self._owner_depth -= 1

    def _feed(self, cursor):
        _cursor(cursor)
        feed = self._feeds.get(cursor.producer)
        if feed is None:
            raise EngineError("unregistered_producer")
        return feed

    def feed_instruments(self, producer: str) -> tuple[str, ...]:
        """Return only observed keys for a registered source; empty is explicit."""
        feed = self._feeds.get(producer)
        if feed is None:
            raise EngineError("unregistered_producer")
        return tuple(sorted(feed.instruments))

    def _flush(self, producers=None):
        """Invalidate only the selected source's pending delivery ownership."""
        retained = deque()
        for item in self._queue:
            if producers is None or item[0].producer in producers:
                self._discarded += 1
                self._queue_bytes -= len(item[1])
            else:
                retained.append(item)
        self._queue = retained
        self._ready.set()

    def _invalidate(self, producer: str, reason: str, *, fault: bool):
        with self._owner_operation():
            self._invalidate_feed(producer, reason, fault=fault)

    def _invalidate_feed(self, producer: str, reason: str, *, fault: bool):
        if fault:
            self._had_fault = True
        selected = [producer] if producer in self._feeds else list(self._feeds)
        # Unknown source identity is a shared authority failure. An ordinary
        # registered-feed failure must not erase another feed's queued input.
        if producer not in self._feeds:
            self._source_error = EngineError(reason)
        self._flush(set(selected))
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
        with self._owner_operation(exclusive=True):
            return self._begin_recovery(checkpoint, high_water, verified_checkpoint=verified_checkpoint)

    def _begin_recovery(self, checkpoint, high_water, *, verified_checkpoint):
        if self._replay_order:
            raise EngineError("checkpoint_apply_order_not_consumed")
        feed = self._feed(checkpoint)
        _cursor(high_water)
        committed = self._committed_feed_cursors.get(checkpoint.producer)
        if checkpoint != committed and (committed is not None or checkpoint.sequence != 0):
            raise EngineError("recovery_checkpoint_not_owner_committed")
        if self._checkpoint_restored and (
                checkpoint != feed.applied or not feed.state_checkpoint_verified
                or (checkpoint.sequence and checkpoint.sequence not in feed.digests)
                or any(name != checkpoint.producer and other.mode == FeedMode.REPLAY
                       and other.applied != other.high_water for name, other in self._feeds.items())):
            raise EngineError("checkpoint_retained_cursor_or_global_order_unverified")
        if (verified_checkpoint is not True or checkpoint.producer != high_water.producer
                or checkpoint.incarnation != high_water.incarnation or checkpoint.sequence > high_water.sequence
                or self._closed or feed.state_poisoned):
            self.invalidate(checkpoint.producer, "checkpoint_high_water_unverified")
            raise EngineError("checkpoint_high_water_unverified")
        if not self._constructing:
            self._invalidate(checkpoint.producer, "recovery_started",
                fault=feed.ever_live or feed.initial_proof is not None)
        else:
            self._flush()
        feed.generation += 1
        checkpoint_digest=feed.digests.get(checkpoint.sequence)
        if checkpoint.sequence==0:
            checkpoint_digest=hashlib.sha256(b"").hexdigest()
        feed.checkpoint, feed.high_water, feed.applied = checkpoint, high_water, checkpoint
        if checkpoint.producer not in self._committed_feed_cursors and checkpoint.sequence == 0:
            self._committed_feed_cursors[checkpoint.producer] = checkpoint
        feed.state_checkpoint_verified=True
        feed.digests.clear()
        if checkpoint_digest is not None and checkpoint.sequence:
            feed.digests[checkpoint.sequence]=checkpoint_digest
        feed.last_live_ns = None
        feed.lease_ns = 0
        feed.mode = FeedMode.WARMING if checkpoint == high_water else FeedMode.REPLAY
        feed.reason = "explicit_handoff_required"

    def begin_delivery_recovery(self, high_water: EngineCursor) -> AppliedReceipt:
        """Arm the retained state cursor through a producer's fixed durable H."""
        _cursor(high_water)
        checkpoint=self.query_cursor(high_water.producer)
        if ((checkpoint.cursor.producer,checkpoint.cursor.incarnation)!=(high_water.producer,high_water.incarnation)
                or checkpoint.cursor.sequence>high_water.sequence):
            raise EngineError("delivery_recovery_checkpoint_mismatch")
        self.begin_recovery(checkpoint.cursor,high_water,verified_checkpoint=True)
        return checkpoint

    def handoff(self, checkpoint: EngineCursor, high_water: EngineCursor, *, verified: bool,
                ready: bool, lease_ns: int) -> EngineCursor:
        with self._owner_operation(exclusive=True):
            if self._checkpoint_restored:
                raise EngineError("checkpoint_readiness_validator_required")
            return self._handoff(checkpoint, high_water, verified=verified, ready=ready, lease_ns=lease_ns)

    def _handoff(self, checkpoint, high_water, *, verified, ready, lease_ns):
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
        with self._owner_operation(exclusive=True):
            return self._arm_initial_resume(checkpoint, high_water)

    def _arm_initial_resume(self, checkpoint, high_water):
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
        with self._owner_operation(exclusive=True):
            return self._initial_resume(checkpoint)

    def _initial_resume(self, checkpoint):
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

    def _queue_admissible(self, producer=None):
        if self._source_error is not None:
            return False
        if producer is not None:
            return self._feeds[producer].mode == FeedMode.LIVE
        return all(feed.mode == FeedMode.LIVE for feed in self._feeds.values()) or self._initial_proofs_ready()

    def request_resume(self, checkpoint: EngineCursor) -> bool:
        try:
            self.check_health()
            feed = self._feed(checkpoint)
            return not self._closed and feed.mode == FeedMode.LIVE and checkpoint == feed.applied
        except EngineError:
            return False

    def query_cursor(self, producer: str) -> AppliedReceipt:
        """Return the current state cursor and its content proof for recovery."""
        feed = self._feeds.get(producer)
        if (feed is None or feed.applied is None or self._closed or feed.state_poisoned
                or not feed.state_checkpoint_verified):
            raise EngineError("recovery_cursor_unavailable")
        digest = feed.digests.get(feed.applied.sequence)
        if feed.applied.sequence == 0:
            digest = hashlib.sha256(b"").hexdigest()
        if digest is None:
            raise EngineError("recovery_digest_unavailable")
        return AppliedReceipt(feed.applied, digest)

    def _checkpoint_idle(self):
        if (self.hot_path is None or self._closed or self._constructing or self._queue or self._queue_bytes
                or self._consumer_active or self._source_error is not None):
            raise EngineError("checkpoint_requires_quiescent_market_owner")
        self._require_owned_store_cursor()

    def _require_owned_store_cursor(self):
        if self.hot_path is not None and (
                self.hot_path.engine_incarnation, self.hot_path.apply_seq) != self._committed_store_cursor:
            for feed in self._feeds.values():
                feed.state_poisoned = True
                feed.state_checkpoint_verified = False
            if self._source_error is None:
                self._invalidate_feed("", "checkpoint_store_changed_outside_owner", fault=True)
            raise EngineError("checkpoint_store_changed_outside_owner")

    @staticmethod
    def _checkpoint_store_virgin(hot):
        return not any((hot.apply_seq, hot.clock_version, hot._trades, hot._books, hot._index_quotes,
            hot._versions, hot._book_versions, hot._quality, hot._identity_conflicts, hot._crypto_books,
            hot._crypto_first, hot._fingerprints, hot._seen._items, hot.history._rows, hot.history._limits,
            hot.history._providers, hot.history._quality, hot.history._sizes, hot.history.byte_size))

    def _checkpoint_signature(self):
        return (id(self.hot_path), self.hot_path.engine_incarnation, self.hot_path.apply_seq,
                self.hot_path.clock_version, self._had_fault, tuple(sorted(self._committed_feed_cursors.items())),
                tuple((name, feed.provider, feed.mode, feed.reason, feed.generation,
                    feed.checkpoint, feed.high_water, feed.applied, tuple(feed.digests.items()),
                    tuple(sorted(feed.instruments)), feed.state_poisoned, feed.state_checkpoint_verified,
                    feed.last_live_ns, feed.lease_ns, feed.ever_live, feed.initial_proof, feed.initial_consumed)
                    for name, feed in sorted(self._feeds.items())))

    def _checkpoint_ownership(self, store, instruments):
        if type(instruments) is not dict or set(instruments) != set(self._feeds):
            raise EngineError("checkpoint_instrument_inventory_mismatch")
        total = 0
        owners = {}
        for producer, keys in instruments.items():
            if (type(keys) is not list or len(keys) > self.budgets.max_instruments
                    or any(type(key) is not str or not key or len(key.encode()) > self.budgets.max_frame_bytes for key in keys)
                    or keys != sorted(set(keys))):
                raise EngineError("checkpoint_instrument_inventory_invalid")
            total += len(keys)
            for key in keys:owners.setdefault(key, set()).add(self._feeds[producer].provider)
        if total > self.budgets.max_instruments:
            raise EngineError("checkpoint_instrument_budget_exceeded")
        state_keys = (set(store._versions) | set(store.history._rows) | set(store.history._quality)
                      | {key for _, key in store._fingerprints.values()})
        if not state_keys <= set(owners):
            raise EngineError("checkpoint_state_instrument_unowned")
        for rings in (store._trades, store._books, store._index_quotes, store.history._rows):
            for key, values in rings.items():
                events = values.values() if isinstance(values, dict) else values
                if any(event.provider not in owners.get(key, ()) for event in events):
                    raise EngineError("checkpoint_state_provider_mismatch")

    def capture_checkpoint(self, codec: CheckpointCodec, *, capture_epoch_ns=None,
                           capture_monotonic_ns=None, order_spool=None) -> bytes:
        """Capture market state/C atomically; decision consumers must be quiesced."""
        with self._owner_operation(exclusive=True):
            if not isinstance(codec, CheckpointCodec):
                raise EngineError("checkpoint_codec_required")
            self.check_health()
            self._checkpoint_idle()
            if self._order_spool is not None and not (
                    type(self._order_spool) is ApplyOrderWriter and type(order_spool) is ApplyOrderWriter):
                raise EngineError("checkpoint_apply_order_already_bound")
            if order_spool is not None and (type(order_spool) not in {MarketSpool,ApplyOrderWriter}
                    or order_spool.appended_cursor.sequence or self.state_callbacks or self.post_apply_callbacks):
                raise EngineError("checkpoint_apply_order_requires_empty_spool_and_callback_free_owner")
            if not self._checkpoint_origin_verified:
                raise EngineError("checkpoint_store_origin_unverified")
            vector = []
            for producer, feed in sorted(self._feeds.items()):
                if (feed.state_poisoned or not feed.state_checkpoint_verified
                        or feed.mode not in {FeedMode.LIVE, FeedMode.WARMING}
                        or (feed.mode == FeedMode.WARMING and feed.applied != feed.high_water)):
                    raise EngineError("checkpoint_feed_not_verified")
                receipt = self.query_cursor(producer)
                if self._committed_feed_cursors.get(producer) != receipt.cursor:
                    raise EngineError("checkpoint_cursor_not_owner_committed")
                vector.append(CheckpointProducer(receipt.cursor, receipt.digest, feed.mode.value))
            instruments = {name: sorted(feed.instruments) for name, feed in self._feeds.items()}
            self._checkpoint_ownership(self.hot_path, instruments)
            signature = self._checkpoint_signature()
            self._checkpoint_active = True
            try:
                market = codec.encode(self.hot_path, producers=tuple(vector),
                    capture_epoch_ns=self.snapshot_clock() if capture_epoch_ns is None else capture_epoch_ns,
                    capture_monotonic_ns=capture_monotonic_ns)
                body = {"producer_providers": {name: feed.provider for name, feed in self._feeds.items()},
                        "engine_budgets": asdict(self.budgets), "instruments": instruments,
                        "market": json.loads(market)}
                body_bytes = _canonical(body, codec.max_bytes)
                payload = _canonical({"format": "vibetrading-engine-checkpoint", "version": 1,
                    "sha256": hashlib.sha256(body_bytes).hexdigest(), "body": body}, codec.max_bytes)
                self._checkpoint_idle()
                if signature != self._checkpoint_signature():
                    raise EngineError("checkpoint_owner_changed_during_capture")
                if order_spool is not None:
                    if type(order_spool) is ApplyOrderWriter:
                        order_spool.claim(self)
                    self._append_apply_order(order_spool,order_anchor(payload))
                    self._order_spool = order_spool
                return payload
            finally:
                self._checkpoint_active = False
                self._require_owned_store_cursor()

    def _inspect_checkpoint(self, codec, payload, *, restore_epoch_ns, restore_monotonic_ns):
        if (not isinstance(codec, CheckpointCodec) or self.hot_path is None or type(payload) is not bytes
                or not 0 < len(payload) <= codec.max_bytes or codec.capacity != self.hot_path.checkpoint_capacity()):
            raise EngineError("checkpoint_codec_or_capacity_mismatch")
        try:
            envelope = json.loads(payload, object_pairs_hook=_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(EngineError("nonfinite_checkpoint_json")))
            if (type(envelope) is not dict or set(envelope) != {"format", "version", "sha256", "body"}
                    or envelope["format"] != "vibetrading-engine-checkpoint"
                    or type(envelope["version"]) is not int or envelope["version"] != 1):
                raise EngineError("engine_checkpoint_schema_mismatch")
            body = envelope["body"]
            if type(body) is not dict or set(body) != {"producer_providers", "engine_budgets", "instruments", "market"}:
                raise EngineError("engine_checkpoint_schema_mismatch")
            if (envelope["sha256"] != hashlib.sha256(_canonical(body, codec.max_bytes)).hexdigest()
                    or _canonical(envelope, codec.max_bytes) != payload):
                raise EngineError("engine_checkpoint_checksum_or_canonical_mismatch")
            providers = {name: feed.provider for name, feed in self._feeds.items()}
            if (body["producer_providers"] != providers
                    or _canonical(body["engine_budgets"], codec.max_bytes) != _canonical(asdict(self.budgets), codec.max_bytes)):
                raise EngineError("engine_checkpoint_provider_or_budget_mismatch")
            market = codec.decode(_canonical(body["market"], codec.max_bytes),
                restore_epoch_ns=self.snapshot_clock() if restore_epoch_ns is None else restore_epoch_ns,
                restore_monotonic_ns=restore_monotonic_ns)
            if (set(item.cursor.producer for item in market.producers) != set(self._feeds)
                    or any(item.quality not in {FeedMode.LIVE.value, FeedMode.WARMING.value} for item in market.producers)):
                raise EngineError("checkpoint_producer_inventory_or_quality_mismatch")
            self._checkpoint_ownership(market.store, body["instruments"])
            return market, body["instruments"]
        except EngineError:
            raise
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
            raise EngineError("invalid_engine_checkpoint") from error

    def inspect_checkpoint(self, codec, payload, *, restore_epoch_ns=None, restore_monotonic_ns=None):
        """Validate metadata for independent spool-C verification; install nothing."""
        with self._owner_operation(exclusive=True):
            self._checkpoint_active = True
            try:
                return self._inspect_checkpoint(codec, payload, restore_epoch_ns=restore_epoch_ns,
                    restore_monotonic_ns=restore_monotonic_ns)[0]
            finally:
                self._checkpoint_active = False
                self._require_owned_store_cursor()

    def _virgin_checkpoint_target(self):
        self._checkpoint_idle()
        hot = self.hot_path
        if (self.state_callbacks or self.post_apply_callbacks):
            raise EngineError("checkpoint_callback_state_restore_unsupported")
        if (self._checkpoint_restored or self._had_fault or self._watchdog is not None
                or not self._checkpoint_store_virgin(hot)
                or self._committed_feed_cursors
                or any((self._applied, self._duplicates, self._replayed, self._delivered, self._discarded))
                or any(feed.mode != FeedMode.INVALID or feed.reason != "not_started"
                       or feed.last_live_ns is not None or feed.lease_ns
                       or feed.applied is not None or feed.checkpoint is not None or feed.high_water is not None
                       or feed.digests or feed.instruments or feed.generation or feed.ever_live
                       or feed.state_poisoned or feed.state_checkpoint_verified or feed.initial_proof is not None
                       or feed.initial_consumed for feed in self._feeds.values())):
            raise EngineError("checkpoint_restore_requires_virgin_engine")

    def restore_virgin(self, codec, payload, highwaters, *, verified_cursors,
                       expected_engine_incarnation, restore_epoch_ns=None, restore_monotonic_ns=None,
                       apply_order=None):
        """Install only independently verified C state; no lease/readiness transfers."""
        with self._owner_operation(exclusive=True):
            self._virgin_checkpoint_target()
            self._checkpoint_active = True
            try:
                market, instruments = self._inspect_checkpoint(codec, payload,
                    restore_epoch_ns=restore_epoch_ns, restore_monotonic_ns=restore_monotonic_ns)
                if (type(expected_engine_incarnation) is not str or not expected_engine_incarnation
                        or market.store.engine_incarnation != expected_engine_incarnation):
                    raise EngineError("checkpoint_engine_incarnation_mismatch")
                if (not isinstance(highwaters, Mapping) or not isinstance(verified_cursors, Mapping)
                        or set(highwaters) != set(self._feeds) or set(verified_cursors) != set(self._feeds)):
                    raise EngineError("checkpoint_recovery_inventory_mismatch")
                handoffs = []
                for item in market.producers:
                    checkpoint, high = item.cursor, highwaters[item.cursor.producer]
                    receipt = verified_cursors[item.cursor.producer]
                    _cursor(high)
                    if (not isinstance(receipt, AppliedReceipt) or receipt.cursor != checkpoint or receipt.digest != item.digest
                            or (high.producer, high.incarnation) != (checkpoint.producer, checkpoint.incarnation)
                            or high.sequence < checkpoint.sequence):
                        raise EngineError("checkpoint_spool_cursor_or_digest_mismatch")
                    handoffs.append((checkpoint, high))
                ordered = ()
                if apply_order is not None:
                    if type(apply_order) is not ApplyOrder:
                        raise EngineError("checkpoint_apply_order_proof_required")
                    ordered = apply_order.validate(payload, handoffs)
                elif sum(checkpoint != high for checkpoint, high in handoffs) > 1:
                    raise EngineError("checkpoint_global_delta_order_unproven")
                # All validations complete before the first target mutation.
                self._virgin_checkpoint_target()
                self.hot_path = market.store
                self._committed_store_cursor = (market.store.engine_incarnation, market.store.apply_seq)
                for item, (checkpoint, high) in zip(market.producers, handoffs):
                    feed = self._feeds[checkpoint.producer]
                    feed.checkpoint, feed.high_water, feed.applied = checkpoint, high, checkpoint
                    self._committed_feed_cursors[checkpoint.producer] = checkpoint
                    feed.mode = FeedMode.WARMING if checkpoint == high else FeedMode.REPLAY
                    feed.reason = "checkpoint_external_readiness_required"
                    feed.generation = 1
                    feed.instruments = set(instruments[checkpoint.producer])
                    feed.state_checkpoint_verified = True
                    if checkpoint.sequence:feed.digests[checkpoint.sequence] = item.digest
                self._checkpoint_restored = True
                self._replay_order = deque(ordered)
                self._checkpoint_origin_verified = True
                self._had_fault = True  # A restored process can never use virgin initial-resume proofs.
                return tuple(handoffs)
            finally:
                self._checkpoint_active = False
                self._require_owned_store_cursor()

    def complete_checkpoint_recovery(self, readiness_validator) -> bool:
        """External pure-market readiness proof; callback/account state is not recovered here."""
        if not callable(readiness_validator) or inspect.iscoroutinefunction(readiness_validator):
            raise EngineError("checkpoint_readiness_validator_required")
        with self._owner_operation(exclusive=True):
            self.check_health()
            self._checkpoint_idle()
            if not self._checkpoint_restored or self._replay_order or any(
                    feed.mode not in {FeedMode.WARMING, FeedMode.LIVE}
                    or feed.state_poisoned or not feed.state_checkpoint_verified
                    or (feed.mode == FeedMode.WARMING and feed.applied != feed.high_water)
                    for feed in self._feeds.values()):
                raise EngineError("checkpoint_replay_not_complete")
            warming = tuple(name for name, feed in self._feeds.items() if feed.mode == FeedMode.WARMING)
            self._checkpoint_active = True
            try:
                epoch = self.snapshot_clock()
                if type(epoch) is not int or epoch <= 0:
                    raise EngineError("checkpoint_epoch_clock_invalid")
                snapshot = self.hot_path.snapshot(epoch, phase=FeedMode.REPLAY.value)
                receipts = tuple(self.query_cursor(name) for name in sorted(self._feeds))
                signature = self._checkpoint_signature()
                started = self.clock()
                if type(started) is not int or started < 0:
                    raise EngineError("checkpoint_monotonic_clock_invalid")
                result = _sync(readiness_validator, self, snapshot, receipts)
                after = self.snapshot_clock()
                self._checkpoint_idle()
                if (type(after) is not int or not 0 <= after-epoch <= self.budgets.max_age_ns
                        or signature != self._checkpoint_signature()):
                    raise EngineError("checkpoint_readiness_proof_changed_or_expired")
                refreshed = self.hot_path.snapshot(after, phase=FeedMode.REPLAY.value)
                self._checkpoint_idle()
                if (signature != self._checkpoint_signature() or snapshot.quality != refreshed.quality
                        or snapshot.history_quality != refreshed.history_quality):
                    raise EngineError("checkpoint_readiness_quality_changed")
                stamp = self.clock()
                self._require_owned_store_cursor()
                if signature != self._checkpoint_signature():
                    raise EngineError("checkpoint_readiness_proof_changed_or_expired")
                if type(stamp) is not int or stamp < started:
                    raise EngineError("checkpoint_readiness_proof_changed_or_expired")
                expired = [name for name, feed in self._feeds.items() if feed.mode == FeedMode.LIVE
                           and (feed.last_live_ns is None or not 0 <= stamp-feed.last_live_ns <= feed.lease_ns)]
                if expired:
                    for name in expired:self._invalidate_feed(name, "feed_health_lease_expired", fault=True)
                    raise EngineError("checkpoint_live_dependency_lease_expired")
                if stamp-started > self.budgets.max_age_ns:
                    raise EngineError("checkpoint_readiness_proof_changed_or_expired")
                if result is not True:return False
                for name in warming:
                    feed = self._feeds[name]
                    feed.mode, feed.reason = FeedMode.LIVE, "checkpoint_readiness_validated"
                    feed.ever_live = True
                    feed.last_live_ns, feed.lease_ns = stamp, self.budgets.feed_lease_ns
                self._ready.set()
                return True
            finally:
                self._checkpoint_active = False
                self._require_owned_store_cursor()

    def authorize_state_restore(self, checkpoint: EngineCursor, *, verified: bool) -> None:
        """Same-engine restore is unsupported without restoring the actual HotPath state."""
        del checkpoint, verified
        raise EngineError("same_engine_state_restore_unsupported")

    def commit_recovery(self, high_water: EngineCursor, digest: str, *, ready: bool,
                        lease_ns: int | None = None) -> bool:
        """Commit only the exact replayed H after an external readiness proof."""
        if self._checkpoint_restored:
            raise EngineError("checkpoint_readiness_validator_required")
        feed = self._feed(high_water)
        expected_digest = (hashlib.sha256(b"").hexdigest() if high_water.sequence == 0
                           else feed.digests.get(high_water.sequence))
        if (ready is not True or feed.checkpoint is None or feed.high_water != high_water
                or feed.applied != high_water or expected_digest != digest):
            return False
        self.handoff(feed.checkpoint, high_water, verified=True, ready=True,
                     lease_ns=lease_ns or self.budgets.feed_lease_ns)
        return True

    def initial_resume_receipt(self, checkpoint: EngineCursor) -> AppliedReceipt | bool:
        """Consume the exact one-use initial proof and return its state digest."""
        if not self.initial_resume(checkpoint):
            return False
        return self.query_cursor(checkpoint.producer)

    def resume_receipt(self, checkpoint: EngineCursor) -> AppliedReceipt | bool:
        if self.request_resume(checkpoint):
            return self.query_cursor(checkpoint.producer)
        return self.initial_resume_receipt(checkpoint)

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
        self._require_owned_store_cursor()
        if self.hot_path is not None:
            result = self.hot_path.apply_versioned(event)
            if not result.applied and not result.duplicate and result.reason not in {"raw_only", "data_gap"}:
                raise EngineError("market_state_rejected:" + result.reason)
            if self.post_apply_callbacks:
                phase_name = phase.value if isinstance(phase, FeedMode) else str(phase)
                now_ns=self.snapshot_clock()
                if type(now_ns) is not int or now_ns<=0:
                    raise EngineError("post_apply_epoch_clock_invalid")
                snapshot = self.hot_path.snapshot(now_ns, phase=phase_name)
                expected_cursor = (result.engine_incarnation, result.apply_seq)
                if snapshot.cursor != expected_cursor or (self.hot_path.engine_incarnation, self.hot_path.apply_seq) != expected_cursor:
                    raise EngineError("post_apply_state_cursor_changed")
                for callback in self.post_apply_callbacks:
                    _sync(callback, result, snapshot)
                    if (self.hot_path.engine_incarnation, self.hot_path.apply_seq) != expected_cursor:
                        raise EngineError("post_apply_callback_changed_state_cursor")
            return result.applied
        for callback in self.state_callbacks:
            if _sync(callback, event, phase) is False:
                raise EngineError("state_callback_rejected")
        return not (event.event_type == EventType.ORDER_BOOK and getattr(event, "fast", None) is False)

    def _append_apply_order(self, spool, record):
        try:
            spool.append(_canonical(record,spool.max_frame_bytes))
        except BaseException:
            self._fail_order_journal()
            raise

    def _fail_order_journal(self):
        for feed in self._feeds.values():
            feed.state_poisoned, feed.state_checkpoint_verified = True, False
        self._invalidate_feed("", "checkpoint_apply_order_write_failed", fault=True)

    def fail_order_journal(self, reason=None):
        with self._owner_operation():
            self._fail_order_journal()

    def verify_order_journal(self, writer):
        with self._owner_operation(exclusive=True):
            self._checkpoint_idle()
            if self._order_spool is not writer or any(
                    feed.state_poisoned or not feed.state_checkpoint_verified for feed in self._feeds.values()):
                raise EngineError('checkpoint_apply_order_authority_unverified')

    def _commit_cursor(self, frame, feed, replay):
        if self._order_spool is not None:
            self._append_apply_order(self._order_spool,order_record(frame))
        feed.applied = frame.cursor
        committed = self._committed_feed_cursors.get(frame.cursor.producer)
        if (committed is None or (committed.incarnation == frame.cursor.incarnation
                                 and committed.sequence <= frame.cursor.sequence)):
            self._committed_feed_cursors[frame.cursor.producer] = frame.cursor
        feed.state_checkpoint_verified=True
        feed.digests[frame.cursor.sequence] = frame.digest
        while len(feed.digests) > self.budgets.max_digests_per_feed:
            feed.digests.popitem(last=False)
        self._applied += 1
        if self.hot_path is not None:
            self._committed_store_cursor = (self.hot_path.engine_incarnation, self.hot_path.apply_seq)
        if replay:
            self._replayed += 1
            if feed.applied == feed.high_water:
                feed.mode = FeedMode.WARMING

    def apply_replay(self, frame: RecoveryFrame) -> AppliedReceipt:
        with self._owner_operation(exclusive=True):
            self._require_owned_store_cursor()
            try:
                receipt = self._apply_replay(frame)
            except BaseException:
                try:self._require_owned_store_cursor()
                except EngineError:pass
                raise
            self._require_owned_store_cursor()
            return receipt

    def _apply_replay(self, frame):
        state_started=False
        try:
            feed, duplicate = self._frame(frame, replay=True)
            if not duplicate:
                if self._replay_order and self._replay_order[0] != AppliedReceipt(frame.cursor, frame.digest):
                    raise EngineError("checkpoint_apply_order_cursor_or_digest_mismatch")
                generation = feed.generation
                event = self._decode(frame, feed)
                if event.event_type == EventType.DATA_GAP:
                    raise EngineError("gap_inside_replay_range")
                state_started=True
                self._apply_state(event, FeedMode.REPLAY)
                if feed.generation != generation or feed.mode not in {FeedMode.REPLAY, FeedMode.WARMING}:
                    raise EngineError("state_callback_invalidated_recovery")
                self._commit_cursor(frame, feed, True)
                if self._replay_order:
                    self._replay_order.popleft()
            return AppliedReceipt(frame.cursor, frame.digest)
        except BaseException:
            if state_started:
                feed.state_poisoned=True
                feed.state_checkpoint_verified=False
            self.invalidate(frame.cursor.producer, "replay_application_failed")
            raise

    def on_frame(self, frame: RecoveryFrame) -> AppliedReceipt:
        with self._owner_operation(exclusive=True):
            self._require_owned_store_cursor()
            try:
                receipt = self._on_frame(frame)
            except BaseException:
                try:self._require_owned_store_cursor()
                except EngineError:pass
                raise
            self._require_owned_store_cursor()
            return receipt

    def _on_frame(self, frame):
        state_started=False
        feed=None
        try:
            self.check_health()
            feed, duplicate = self._frame(frame, replay=False)
            if duplicate:
                return AppliedReceipt(frame.cursor, frame.digest)
            generation = feed.generation
            event = self._decode(frame, feed)
            if event.event_type == EventType.DATA_GAP:
                self.invalidate(frame.cursor.producer, "market_data_gap")
                state_started=True
                self._apply_state(event, FeedMode.INVALID)
                self._commit_cursor(frame, feed, False)
                return AppliedReceipt(frame.cursor, frame.digest)
            phase = FeedMode.REPLAY if event.ingest_mode == "backfill" else FeedMode.LIVE
            selected = _sync(self.trading_filter, event)
            if type(selected) is not bool:
                raise EngineError("trading_filter_must_return_bool")
            if feed.generation != generation or feed.mode != FeedMode.LIVE:
                raise EngineError("filter_invalidated_feed")
            enqueue = selected and phase == FeedMode.LIVE and self._queue_admissible(frame.cursor.producer)
            if enqueue and (len(self._queue) >= self.budgets.max_records or self._queue_bytes + len(frame.payload) > self.budgets.max_bytes):
                raise EngineError("trading_queue_budget_exceeded")
            state_started=True
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
            if state_started and feed is not None:
                feed.state_poisoned=True
                feed.state_checkpoint_verified=False
            self.invalidate(producer, str(error)[:256] if isinstance(error, EngineError) else "market_application_failed")
            raise

    def check_health(self):
        with self._owner_operation():
            self._check_health()

    def _check_health(self):
        if self._closed:
            raise EngineError("engine_closed")
        now = self.clock()
        expired = {item[0].producer for item in self._queue
                   if now - item[3] > self.budgets.max_age_ns}
        for producer in expired:
            self.invalidate(producer, "trading_queue_oldest_age_exceeded")
        for producer, feed in self._feeds.items():
            if feed.mode == FeedMode.LIVE and (feed.last_live_ns is None or not 0 <= now - feed.last_live_ns <= feed.lease_ns):
                self.invalidate(producer, "feed_health_lease_expired")

    async def event_source(self, *, required_producers=None):
        """A single delivery consumer with explicit dependency readiness.

        None retains the legacy all-feed barrier. An empty tuple is the
        isolated shared-runtime stream: each strategy checks its own inputs.
        It never grants readiness to a strategy or repairs an invalid feed.
        """
        required = tuple(self._feeds) if required_producers is None else required_producers
        if (not isinstance(required, tuple) or len(set(required)) != len(required)
                or any(name not in self._feeds for name in required)):
            raise EngineError("invalid_source_dependencies")
        if self._consumer_active:
            raise EngineError("event_source_already_has_consumer")
        self._consumer_active = True
        try:
            while not self._closed:
                self.check_health()
                if self._source_error is not None:
                    raise EngineError("local_market_source_invalid") from self._source_error
                if any(self._feeds[name].mode == FeedMode.INVALID for name in required):
                    raise EngineError("local_market_source_invalid")
                if not all(self._feeds[name].mode == FeedMode.LIVE for name in required):
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
        with self._owner_operation(exclusive=True):
            if self._closed or self._watchdog is not None:
                raise EngineError("engine_not_startable")
            self._watchdog = asyncio.create_task(self._watch(), name="local-market-health-watch")
        return self

    async def _watch(self):
        while True:
            await asyncio.sleep(self.budgets.tick_seconds)
            self.check_health()
            if self.on_tick is not None:
                epoch = self.snapshot_clock()
                if type(epoch) is not int or epoch <= 0:
                    raise EngineError("tick_epoch_clock_invalid")
                _sync(self.on_tick, epoch)

    async def close(self):
        with self._owner_operation(exclusive=True):
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
                "initial_consumed": feed.initial_consumed, "state_poisoned": feed.state_poisoned}
                for producer, feed in self._feeds.items()}}
