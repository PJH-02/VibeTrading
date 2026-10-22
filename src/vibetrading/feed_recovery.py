"""Bounded checkpoint/high-water handoff for an engine cursor stream.

Cursor sequence is the engine ingress sequence, never an inferred exchange
sequence. Native book validation remains in HotPathStore/provider adapters.
This coordinator has no broker, signal publisher, network or file writer.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
import hashlib
import inspect
import time
from typing import Callable


class FeedMode(str, Enum):
    INVALID = "INVALID"
    REPLAY = "REPLAY"
    WARMING = "WARMING"
    LIVE = "LIVE"


@dataclass(frozen=True, slots=True)
class EngineCursor:
    producer: str
    incarnation: str
    sequence: int

    def __post_init__(self):
        if not self.producer or not self.incarnation or type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("invalid engine cursor")


@dataclass(frozen=True, slots=True)
class RecoveryFrame:
    cursor: EngineCursor
    payload: bytes

    def __post_init__(self):
        if type(self.payload) is not bytes or not self.payload:
            raise ValueError("recovery frame requires owned nonempty bytes")

    @property
    def digest(self):
        return hashlib.sha256(self.payload).hexdigest()


class RecoveryError(RuntimeError):
    pass


class FeedRecovery:
    def __init__(self, apply: Callable[[RecoveryFrame, FeedMode], None], *,
                 max_items: int, max_bytes: int, max_frame_bytes: int,
                 clock: Callable[[], int] = time.monotonic_ns) -> None:
        if (any(type(value) is not int or value <= 0 for value in (max_items, max_bytes, max_frame_bytes))
                or max_frame_bytes > max_bytes):
            raise ValueError("explicit positive recovery budgets required")
        self.apply = apply
        self.max_items, self.max_bytes, self.max_frame_bytes = max_items, max_bytes, max_frame_bytes
        self.clock = clock
        self.mode = FeedMode.INVALID
        self.reason = "not_started"
        self.checkpoint: EngineCursor | None = None
        self.high_water: EngineCursor | None = None
        self.applied: EngineCursor | None = None
        self._buffer: dict[int, RecoveryFrame] = {}
        self._buffer_bytes = 0
        self._digests: OrderedDict[int, str] = OrderedDict()
        self._last_live_ns: int | None = None

    def invalidate(self, reason: str):
        self.mode, self.reason = FeedMode.INVALID, reason
        self._buffer.clear()
        self._buffer_bytes = 0

    def _fail(self, reason: str):
        self.invalidate(reason)
        raise RecoveryError(reason)

    def begin_recovery(self, checkpoint: EngineCursor, high_water: EngineCursor):
        if (checkpoint.producer, checkpoint.incarnation) != (high_water.producer, high_water.incarnation) or checkpoint.sequence > high_water.sequence:
            self._fail("checkpoint_high_water_mismatch")
        self.checkpoint, self.high_water, self.applied = checkpoint, high_water, checkpoint
        self._buffer.clear()
        self._buffer_bytes = 0
        self._digests.clear()
        self._last_live_ns = None
        self.mode = FeedMode.WARMING if checkpoint == high_water else FeedMode.REPLAY
        self.reason = "waiting_for_explicit_readiness"

    def _validate(self, frame: RecoveryFrame):
        if self.mode == FeedMode.INVALID or self.applied is None:
            raise RecoveryError("feed is invalid: " + self.reason)
        if (frame.cursor.producer, frame.cursor.incarnation) != (self.applied.producer, self.applied.incarnation):
            self._fail("producer_incarnation_changed")
        if len(frame.payload) > self.max_frame_bytes:
            self._fail("oversized_recovery_frame")

    def _duplicate(self, frame: RecoveryFrame) -> bool:
        previous = self._digests.get(frame.cursor.sequence)
        if previous is not None:
            if previous != frame.digest:
                self._fail("cursor_content_conflict")
            return True
        if self.applied is not None and frame.cursor.sequence <= self.applied.sequence:
            self._fail("old_cursor_content_unverifiable")
        return False

    def _apply(self, frame: RecoveryFrame, mode: FeedMode):
        if frame.cursor.sequence != self.applied.sequence + 1:
            self._fail("engine_cursor_gap")
        try:
            result = self.apply(frame, mode)
            if result is False:
                raise RecoveryError("state application rejected the recovery frame")
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("recovery apply must be a synchronous state-only callback")
        except BaseException:
            self.invalidate("state_apply_failed")
            raise
        self.applied = frame.cursor
        self._digests[frame.cursor.sequence] = frame.digest
        while len(self._digests) > self.max_items:
            self._digests.popitem(last=False)

    def replay_range(self, frames):
        if self.mode not in (FeedMode.REPLAY, FeedMode.WARMING):
            raise RecoveryError("replay requires an active recovery")
        for frame in frames:
            self._validate(frame)
            if frame.cursor.sequence > self.high_water.sequence:
                self._fail("replay_crossed_high_water")
            if not self._duplicate(frame):
                self._apply(frame, FeedMode.REPLAY)
        if self.applied == self.high_water:
            self.mode = FeedMode.WARMING

    def accept_live(self, frame: RecoveryFrame):
        self._validate(frame)
        if self._duplicate(frame):
            return False
        if self.mode == FeedMode.LIVE:
            self._apply(frame, FeedMode.LIVE)
            self._last_live_ns = self.clock()
            return True
        # Replay owns its interval; retain live overlap to verify its digest.
        existing = self._buffer.get(frame.cursor.sequence)
        if existing:
            if existing.digest != frame.digest:
                self._fail("cursor_content_conflict")
            return False
        if len(self._buffer) >= self.max_items or self._buffer_bytes+len(frame.payload) > self.max_bytes:
            self._fail("live_handoff_buffer_overflow")
        self._buffer[frame.cursor.sequence] = frame
        self._buffer_bytes += len(frame.payload)
        self._last_live_ns = self.clock()
        return True

    def handoff_live(self, *, ready: bool, lease_ns: int):
        if self.mode != FeedMode.WARMING or self.applied != self.high_water:
            self._fail("replay_coverage_incomplete")
        elapsed = None if self._last_live_ns is None else self.clock()-self._last_live_ns
        if ready is not True or type(lease_ns) is not int or lease_ns <= 0 or elapsed is None or not 0 <= elapsed <= lease_ns:
            self._fail("warmup_or_feed_lease_unverified")
        for sequence in sorted(self._buffer):
            frame = self._buffer[sequence]
            if self._duplicate(frame):
                continue
            self._apply(frame, FeedMode.WARMING)
        elapsed = self.clock()-self._last_live_ns
        if not 0 <= elapsed <= lease_ns:
            self._fail("feed_lease_expired_during_handoff")
        self._buffer.clear()
        self._buffer_bytes = 0
        self.mode, self.reason = FeedMode.LIVE, "ready"
        return self.applied

    def validate_ready(self, *, ready: bool, lease_ns: int) -> bool:
        if self.mode != FeedMode.LIVE:
            return False
        elapsed = None if self._last_live_ns is None else self.clock()-self._last_live_ns
        if ready is not True or type(lease_ns) is not int or lease_ns <= 0 or elapsed is None or not 0 <= elapsed <= lease_ns:
            self.invalidate("warmup_or_feed_lease_expired")
            return False
        return True

    @property
    def stats(self):
        return {"mode":self.mode.value, "reason":self.reason, "buffer_items":len(self._buffer),
                "buffer_bytes":self._buffer_bytes, "applied_sequence":self.applied.sequence if self.applied else None}
