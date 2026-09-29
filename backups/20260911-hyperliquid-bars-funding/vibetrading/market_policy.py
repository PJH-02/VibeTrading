from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .models import AssetClass, BookUpdateType, EventType, OrderBookEvent, OrderBookLevel, utc_now_ns
from .storage_guard import StorageGuard
from .universe_config import configured_hyperliquid_securities


NANOSECONDS_PER_SECOND = 1_000_000_000


class CollectionPaused(RuntimeError):
    """Raised when storage or JetStream backpressure must stop collectors."""


def configured_hyperliquid_symbols() -> set[str]:
    """Return only explicitly approved stock/index-linked Hyperliquid symbols."""
    result: set[str] = set()
    result.update(configured_hyperliquid_securities())
    return result


@dataclass
class _BookState:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    valid: bool = False
    last_sequence: int | None = None


class MarketDataPolicyBus:
    """Apply the storage policy before events enter JetStream.

    Trade events are forwarded unchanged.  Order books are reconstructed when a
    provider emits deltas, then emitted at most once per instrument per second.
    The original exchange timestamp remains in source_ts_ns; sampled_at_ns is
    the end of the one-second interval represented by the saved snapshot.
    """

    def __init__(self, inner: Any, *, sample_seconds: float = 1.0) -> None:
        self.inner = inner
        self.sample_ns = max(1, int(sample_seconds * NANOSECONDS_PER_SECOND))
        self._books: dict[str, _BookState] = {}
        self._pending: dict[str, tuple[int, OrderBookEvent]] = {}
        self._allowed_hyperliquid = configured_hyperliquid_symbols()
        self._guard = StorageGuard()
        self._collection_paused = False
        self._next_pressure_check = 0.0
        self._resume_eligible_since: float | None = None
        self._book_lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def publish_market_event(self, event: Any) -> None:
        await self._check_pressure()
        if not self._allowed(event):
            return
        if event.event_type == EventType.ORDER_BOOK:
            async with self._book_lock:
                snapshot = self._normalise_book(event)
                if snapshot is not None:
                    await self._buffer_book(snapshot)
            return
        await self.inner.publish_market_event(event)

    def _allowed(self, event: Any) -> bool:
        if event.event_type not in {EventType.TRADE, EventType.ORDER_BOOK, EventType.DATA_GAP}:
            return False
        asset_class = event.instrument.asset_class
        if asset_class in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERP}:
            return False
        if event.instrument.venue.value == "hyperliquid":
            return event.instrument.raw_symbol.upper() in self._allowed_hyperliquid
        return True

    def _normalise_book(self, event: OrderBookEvent) -> OrderBookEvent | None:
        key = event.instrument.key
        state = self._books.setdefault(key, _BookState())
        sequence = self._int_sequence(event.sequence)
        if event.update_type == BookUpdateType.SNAPSHOT:
            state.bids = {level.price: level.quantity for level in event.bids if level.quantity > 0}
            state.asks = {level.price: level.quantity for level in event.asks if level.quantity > 0}
            state.valid = bool(event.bids or event.asks)
        else:
            if not state.valid:
                return None
            if sequence is not None and state.last_sequence is not None and sequence > state.last_sequence + 1:
                state.valid = False
                return None
            self._apply_levels(state.bids, event.bids)
            self._apply_levels(state.asks, event.asks)
        state.last_sequence = sequence if sequence is not None else state.last_sequence
        if not state.valid:
            return None
        bids = [OrderBookLevel(price=price, quantity=quantity) for price, quantity in sorted(state.bids.items(), reverse=True)[:10]]
        asks = [OrderBookLevel(price=price, quantity=quantity) for price, quantity in sorted(state.asks.items())[:10]]
        return event.model_copy(
            update={
                "update_type": BookUpdateType.SNAPSHOT,
                "bids": bids,
                "asks": asks,
                "depth": max(len(bids), len(asks)),
                "is_full_depth": event.is_full_depth,
            }
        )

    @staticmethod
    def _apply_levels(target: dict[Decimal, Decimal], levels: list[OrderBookLevel]) -> None:
        for level in levels:
            if level.quantity <= 0:
                target.pop(level.price, None)
            else:
                target[level.price] = level.quantity

    @staticmethod
    def _int_sequence(value: int | str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def _buffer_book(self, event: OrderBookEvent) -> None:
        bucket = event.receive_ts_ns // self.sample_ns
        key = event.instrument.key
        prior = self._pending.get(key)
        if prior is None or bucket > prior[0]:
            if prior is not None:
                # The old bucket is complete because a newer observation arrived.
                await self._publish_sample(prior[0], prior[1])
            self._pending[key] = (bucket, event)
        elif bucket == prior[0]:
            self._pending[key] = (bucket, event)

    async def flush_due(self) -> int:
        async with self._book_lock:
            current_bucket = utc_now_ns() // self.sample_ns
            due = [key for key, (bucket, _) in self._pending.items() if bucket < current_bucket]
            for key in due:
                bucket, event = self._pending[key]
                await self._publish_sample(bucket, event)
                self._pending.pop(key, None)
            return len(due)

    async def flush_all(self) -> int:
        async with self._book_lock:
            pending = list(self._pending.items())
            for key, (bucket, event) in pending:
                await self._publish_sample(bucket, event)
                self._pending.pop(key, None)
            return len(pending)

    async def run_sampler(self) -> None:
        """Independent timer: an idle last book is still emitted on time."""
        while True:
            await asyncio.sleep(0.2)
            await self.flush_due()

    async def _publish_sample(self, bucket: int, event: OrderBookEvent) -> None:
        sampled = event.model_copy(update={"sampled_at_ns": (bucket + 1) * self.sample_ns})
        await self.inner.publish_market_event(sampled)

    async def _check_pressure(self) -> None:
        now = time.monotonic()
        if now < self._next_pressure_check:
            if self._collection_paused:
                raise CollectionPaused("collection paused by storage or JetStream backpressure")
            return
        self._next_pressure_check = now + 10.0
        decision = self._guard.evaluate()
        stream_ratio = await self._market_stream_ratio()
        should_pause = (
            decision.used_ratio >= 0.80
            or decision.free_bytes <= 4 * 1024 * 1024 * 1024
            or stream_ratio >= 0.80
        )
        can_resume = (
            decision.used_ratio < 0.50
            and decision.free_bytes >= 6 * 1024 * 1024 * 1024
            and stream_ratio < 0.50
        )
        if should_pause:
            self._collection_paused = True
            self._resume_eligible_since = None
        elif self._collection_paused and can_resume:
            if self._resume_eligible_since is None:
                self._resume_eligible_since = now
            elif now - self._resume_eligible_since >= self._guard.settings.storage_guard.resume_stable_seconds:
                self._collection_paused = False
                self._resume_eligible_since = None
        else:
            self._resume_eligible_since = None
        if self._collection_paused:
            raise CollectionPaused("collection paused by storage or JetStream backpressure")

    async def _market_stream_ratio(self) -> float:
        probe = getattr(self.inner, "market_stream_usage_ratio", None)
        if probe is None:
            return 0.0
        try:
            return float(await probe())
        except Exception:
            # A collector must not keep filling local disk when the durable bus
            # cannot report its health.
            return 1.0
