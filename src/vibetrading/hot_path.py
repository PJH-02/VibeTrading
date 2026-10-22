from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping
import uuid
import hashlib
import json

import time
from decimal import Decimal
from .models import AssetClass, Bar1mEvent, BaseMarketEvent, BookUpdateType, EventType, IndexQuoteEvent, InstrumentId, OrderBookEvent, OrderBookLevel, TradeEvent, Venue, event_from_dict
from .dedup import BoundedDedup


class HistoryNotPrepared(RuntimeError):
    pass


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool, Decimal)):
        return value
    raise TypeError(f"Unsupported mutable decision metadata: {type(value).__name__}")


class _DecisionBook(OrderBookEvent):
    # Internal read model only; external event/schema classes remain unchanged.
    bids: tuple[OrderBookLevel, ...] = ()
    asks: tuple[OrderBookLevel, ...] = ()


class _DecisionIndex(IndexQuoteEvent):
    metadata: Any = None


def freeze_market_event(event: BaseMarketEvent) -> BaseMarketEvent:
    if isinstance(event, OrderBookEvent):
        fields = {name: getattr(event, name) for name in OrderBookEvent.model_fields}
        fields.update(bids=tuple(event.bids), asks=tuple(event.asks))
        return _DecisionBook.model_validate(fields)
    if isinstance(event, IndexQuoteEvent):
        fields = {name: getattr(event, name) for name in IndexQuoteEvent.model_fields}
        fields["metadata"] = _freeze(event.metadata)
        return _DecisionIndex.model_validate(fields)
    # The other supported market models contain frozen InstrumentId + scalars.
    return event


def _event_fingerprint(event: BaseMarketEvent) -> str:
    def canonical(value: Any) -> Any:
        if isinstance(value, BaseMarketEvent) or isinstance(value, (InstrumentId, OrderBookLevel)):
            return {name: canonical(getattr(value, name)) for name in type(value).model_fields}
        if isinstance(value, Mapping):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [canonical(item) for item in value]
        if isinstance(value, Decimal):
            return str(value)
        return value
    payload = json.dumps(canonical(event), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ApplyResult:
    event: BaseMarketEvent
    applied: bool
    duplicate: bool
    reason: str
    engine_incarnation: str
    apply_seq: int


class PreparedHistory:
    """Bounded, causally visible closed bars. No reader or I/O in this object."""

    def __init__(self, max_rows: int = 2048, max_feeds: int = 256,
                 max_bytes: int = 16 * 1024 * 1024) -> None:
        if min(max_rows, max_feeds, max_bytes) < 1:
            raise ValueError("History bounds must be positive")
        self.max_rows, self.max_feeds, self.max_bytes = max_rows, max_feeds, max_bytes
        self._rows: dict[str, dict[tuple[int, int], Bar1mEvent]] = {}
        self._limits: dict[str, int] = {}
        self._providers: dict[str, str] = {}
        self._quality: dict[str, str] = {}
        self._sizes: dict[tuple[str, int, int], int] = {}
        self.byte_size = 0

    def register(self, instrument_key: str, limit: int | None = None) -> None:
        bound = self.max_rows if limit is None else limit
        if isinstance(bound, bool) or not isinstance(bound, int) or not 1 <= bound <= self.max_rows:
            raise ValueError("History row request exceeds its declared bound")
        if instrument_key not in self._rows:
            if len(self._rows) >= self.max_feeds:
                raise ValueError("History feed limit exceeded")
            self._rows[instrument_key] = {}
        self._limits[instrument_key] = max(bound, self._limits.get(instrument_key, 0))

    def invalidate(self, instrument_key: str, reason: str) -> None:
        self._quality[instrument_key] = reason

    @staticmethod
    def _content(bar: Bar1mEvent) -> tuple[Any, ...]:
        return (bar.provider, bar.open, bar.high, bar.low, bar.close, bar.volume,
                bar.trade_count, bar.closed)

    def add(self, bar: Bar1mEvent) -> bool:
        key = bar.instrument.key
        if key not in self._rows:
            self.register(key)
        if bar.open_ts_ns >= bar.close_ts_ns or not bar.closed:
            self.invalidate(key, "INVALID_BAR")
            return False
        if bar.receive_ts_ns is None:
            self.invalidate(key, "AVAILABILITY_UNVERIFIED")
            return False
        if key in self._providers and self._providers[key] != bar.provider:
            self.invalidate(key, "PROVIDER_CONFLICT")
            return False
        identity = (bar.open_ts_ns, bar.close_ts_ns)
        existing = self._rows[key].get(identity)
        if existing is not None:
            if self._content(existing) != self._content(bar):
                self.invalidate(key, "REVISION_UNVERIFIED")
            return False
        size = len(bar.model_dump_json().encode("utf-8"))
        if self.byte_size + size > self.max_bytes:
            self.invalidate(key, "HISTORY_OVERFLOW")
            return False
        self._providers[key] = bar.provider
        self._rows[key][identity] = bar
        self._sizes[(key, *identity)] = size
        self.byte_size += size
        while len(self._rows[key]) > self._limits[key]:
            oldest = min(self._rows[key], key=lambda item: (item[1], item[0]))
            self._rows[key].pop(oldest)
            self.byte_size -= self._sizes.pop((key, *oldest))
        return True

    def load(self, instrument_key: str, rows: Iterable[dict | Bar1mEvent]) -> None:
        self.register(instrument_key)
        for row in rows:
            if isinstance(row, Bar1mEvent):
                bar = row
            else:
                instrument = row.get("instrument") or {
                    name: row["instrument_" + name] for name in InstrumentId.model_fields
                    if "instrument_" + name in row
                }
                values = {name: row[name] for name in Bar1mEvent.model_fields if name in row}
                values["instrument"] = instrument
                # Common model defaulting cannot supply missing historical availability.
                if values.get("receive_ts_ns") is None:
                    self.invalidate(instrument_key, "AVAILABILITY_UNVERIFIED")
                    continue
                bar = Bar1mEvent.model_validate(values)
            if bar.instrument.key != instrument_key:
                raise ValueError("History instrument identity does not match request")
            self.add(bar)

    def snapshot(self, as_of_ns: int) -> tuple[Mapping[str, tuple[Bar1mEvent, ...]], Mapping[str, str]]:
        bars = {
            key: tuple(sorted((bar for bar in rows.values()
                               if int(bar.receive_ts_ns or 0) <= as_of_ns and bar.close_ts_ns <= as_of_ns),
                              key=lambda bar: (bar.close_ts_ns, bar.open_ts_ns)))
            for key, rows in self._rows.items()
        }
        quality = {key: self._quality.get(key, "VALID" if values else "HISTORY_INCOMPLETE")
                   for key, values in bars.items()}
        return MappingProxyType(bars), MappingProxyType(quality)


@dataclass(frozen=True)
class DecisionSnapshot:
    engine_incarnation: str
    apply_seq: int
    now_ns: int
    phase: str
    clock_version: int
    versions: Mapping[str, int]
    book_versions: Mapping[str, int]
    quality: Mapping[str, str]
    trades: Mapping[str, TradeEvent]
    books: Mapping[str, OrderBookEvent]
    index_quotes: Mapping[str, IndexQuoteEvent]
    bars: Mapping[str, tuple[Bar1mEvent, ...]]
    history_quality: Mapping[str, str]

    @property
    def cursor(self) -> tuple[str, int]:
        return self.engine_incarnation, self.apply_seq

    def latest_trade(self, instrument_key: str) -> TradeEvent | None:
        return self.trades.get(instrument_key)

    def latest_book(self, instrument_key: str) -> OrderBookEvent | None:
        return self.books.get(instrument_key) if self.quality.get(instrument_key) == "VALID" else None

    def latest_index_quote(self, instrument_key: str) -> IndexQuoteEvent | None:
        return self.index_quotes.get(instrument_key)

    def closed_bars(self, instrument_key: str, limit: int = 1000) -> tuple[Bar1mEvent, ...]:
        if limit < 0:
            raise ValueError("History limit must be nonnegative")
        return self.bars.get(instrument_key, ())[-limit:] if limit else ()

    def history(self, *, instrument_key: str, event_type: str, limit: int = 1000) -> list[dict]:
        if event_type != EventType.BAR_1M.value or instrument_key not in self.bars:
            raise HistoryNotPrepared(f"Unprepared history: {instrument_key}/{event_type}")
        rows = []
        for bar in self.closed_bars(instrument_key, limit):
            row = bar.model_dump(mode="json")
            instrument = row.pop("instrument")
            row.update({"instrument_" + name: value for name, value in instrument.items()})
            rows.append(row)
        return rows


class HotPathStore:
    def __init__(
        self,
        max_trades: int = 2048,
        max_books: int = 512,
        max_index_quotes: int = 2048,
    ) -> None:
        self.max_trades, self.max_books, self.max_index_quotes = max_trades, max_books, max_index_quotes
        self._trades: dict[str, deque[TradeEvent]] = defaultdict(lambda: deque(maxlen=max_trades))
        self._books: dict[str, deque[OrderBookEvent]] = defaultdict(lambda: deque(maxlen=max_books))
        self._index_quotes: dict[str, deque[IndexQuoteEvent]] = defaultdict(lambda: deque(maxlen=max_index_quotes))
        self._seen = BoundedDedup(max_entries=max(max_trades * 8, 16_384))
        self._fingerprints: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._identity_conflicts: set[str] = set()
        self._crypto_books: dict[str, tuple[dict[Decimal, Decimal], dict[Decimal, Decimal], int, int]] = {}
        self._crypto_first: set[str] = set()
        self.engine_incarnation = uuid.uuid4().hex
        self.apply_seq = 0
        self.clock_version = 0
        self._versions: dict[str, int] = {}
        self._book_versions: dict[str, int] = {}
        self._quality: dict[str, str] = {}
        self.history = PreparedHistory()

    def checkpoint_capacity(self) -> dict:
        return {"max_trades": self.max_trades, "max_books": self.max_books,
                "max_index_quotes": self.max_index_quotes,
                "history_max_rows": self.history.max_rows, "history_max_feeds": self.history.max_feeds,
                "history_max_bytes": self.history.max_bytes,
                "dedup_max_entries": self._seen.max_entries, "dedup_ttl_seconds": self._seen.ttl_seconds}

    def export_checkpoint_state(self, *, capture_monotonic_ns: int, max_event_bytes: int, max_bytes: int) -> dict:
        """Synchronous owner-only capture; the caller must establish its cursor barrier."""
        from .market_checkpoint import _export_store
        return _export_store(self, capture_monotonic_ns, max_event_bytes, max_bytes)

    @classmethod
    def restore_checkpoint_state(cls, state: dict, *, expected_capacity: dict,
                                 elapsed_ns: int, restore_monotonic_ns: int,
                                 max_event_bytes: int, max_bytes: int) -> "HotPathStore":
        """Build a separate store. This does not recover an engine or enable LIVE."""
        from .market_checkpoint import _restore_store
        return _restore_store(cls, state, expected_capacity, elapsed_ns,
                              restore_monotonic_ns, max_event_bytes, max_bytes)

    def apply(self, event: BaseMarketEvent | dict) -> bool:
        return self.apply_versioned(event).applied

    def apply_versioned(self, event: BaseMarketEvent | dict) -> ApplyResult:
        if isinstance(event, dict):
            event = event_from_dict(event)
        if event.instrument.key not in self._versions and len(self._versions) >= self.history.max_feeds:
            raise ValueError("Market-state feed bound exceeded")
        # Validate/freeze before changing the dedup ledger, so a rejected event
        # can be corrected/retried without being mistaken for an applied input.
        event = freeze_market_event(event)
        fingerprint = _event_fingerprint(event)
        previous = self._fingerprints.get(event.event_id)
        if previous is not None and previous[0] != fingerprint:
            self.apply_seq += 1
            for key in {previous[1], event.instrument.key}:
                self._identity_conflicts.add(key)
                self._quality[key] = "EVENT_ID_CONFLICT"
                self._crypto_books.pop(key, None)
                self._crypto_first.discard(key)
                self._books.pop(key, None)
                self.history.invalidate(key, "EVENT_ID_CONFLICT")
                self._versions[key] = self._versions.get(key, 0) + 1
                self._book_versions[key] = self._book_versions.get(key, 0) + 1
            return ApplyResult(event, False, False, "event_id_conflict",
                               self.engine_incarnation, self.apply_seq)
        if not self._seen.add_if_new(event.event_id):
            self._fingerprints.move_to_end(event.event_id)
            return ApplyResult(event, False, True, "duplicate",
                               self.engine_incarnation, self.apply_seq)
        self._fingerprints[event.event_id] = (fingerprint, event.instrument.key)
        self._fingerprints.move_to_end(event.event_id)
        while len(self._fingerprints) > self._seen.max_entries:
            self._fingerprints.popitem(last=False)
        self.apply_seq += 1
        key = event.instrument.key
        if key in self._identity_conflicts:
            return ApplyResult(event, False, False, "identity_conflict_latched",
                               self.engine_incarnation, self.apply_seq)
        applied, changed, reason = True, True, "applied"
        if event.event_type == EventType.TRADE:
            self._trades[key].append(event)  # type: ignore[arg-type]
        elif event.event_type == EventType.DATA_GAP:
            self._crypto_books.pop(key, None)
            self._crypto_first.discard(key)
            self._books.pop(key, None)
            self._quality[key] = "INVALID"
            self.history.invalidate(key, "HISTORY_INCOMPLETE")
            self._book_versions[key] = self._book_versions.get(key, 0) + 1
            applied, reason = False, "data_gap"
        elif event.event_type == EventType.ORDER_BOOK:
            book = event  # type: ignore[assignment]
            if book.instrument.asset_class == AssetClass.CRYPTO_PERP:
                if book.fast is False:
                    applied, changed, reason = False, False, "raw_only"
                else:
                    reconstructed = self._apply_crypto_book(book)
                    if reconstructed is not None:
                        self._books[key].append(freeze_market_event(reconstructed))
                        self._quality[key] = "VALID"
                    else:
                        # Never expose the last good projection after native invalidation.
                        self._books.pop(key, None)
                        self._quality[key] = "INVALID"
                        applied, reason = False, "native_book_invalid"
            else:
                self._books[key].append(book)
                self._quality[key] = "VALID"
            if changed:
                self._book_versions[key] = self._book_versions.get(key, 0) + 1
        elif event.event_type == EventType.INDEX_QUOTE:
            self._index_quotes[key].append(event)  # type: ignore[arg-type]
        elif event.event_type == EventType.BAR_1M:
            self.history.add(event)  # type: ignore[arg-type]
        if changed:
            self._versions[key] = self._versions.get(key, 0) + 1
            self._quality.setdefault(key, "VALID")
        return ApplyResult(event, applied, False, reason, self.engine_incarnation, self.apply_seq)

    def load_history(self, instrument_key: str, rows: Iterable[dict | Bar1mEvent]) -> None:
        self.history.load(instrument_key, rows)

    def closed_bars(self, instrument_key: str, limit: int = 1000) -> tuple[Bar1mEvent, ...]:
        return self.snapshot().closed_bars(instrument_key, limit)

    def expire(self, now_ns: int | None = None) -> None:
        now_ns = time.time_ns() if now_ns is None else now_ns
        for key, values in self._books.items():
            if (values and values[-1].instrument.asset_class == AssetClass.CRYPTO_PERP
                    and now_ns - int(values[-1].receive_ts_ns or 0) > 1_000_000_000
                    and self._quality.get(key) == "VALID"):
                self._quality[key] = "STALE"
                self._versions[key] = self._versions.get(key, 0) + 1
                self.clock_version += 1

    def snapshot(self, now_ns: int | None = None, phase: str = "SHADOW") -> DecisionSnapshot:
        now_ns = time.time_ns() if now_ns is None else now_ns
        self.expire(now_ns)
        bars, history_quality = self.history.snapshot(now_ns)
        quality = dict(self._quality)
        for key, status in history_quality.items():
            if status not in {"VALID", "HISTORY_INCOMPLETE"}:
                quality[key] = status
        return DecisionSnapshot(
            self.engine_incarnation, self.apply_seq, now_ns, phase, self.clock_version,
            MappingProxyType(dict(self._versions)), MappingProxyType(dict(self._book_versions)),
            MappingProxyType(quality),
            MappingProxyType({key: values[-1] for key, values in self._trades.items() if values}),
            MappingProxyType({key: values[-1] for key, values in self._books.items() if values}),
            MappingProxyType({key: values[-1] for key, values in self._index_quotes.items() if values}),
            bars, history_quality,
        )

    def _apply_crypto_book(self, event: OrderBookEvent) -> OrderBookEvent | None:
        key = event.instrument.key
        current = self._crypto_books.get(key)
        if event.update_type == BookUpdateType.SNAPSHOT:
            bids: dict[Decimal, Decimal] = {}
            asks: dict[Decimal, Decimal] = {}
            self._crypto_first.add(key)
        elif current is not None and current[2] == event.sync_generation and event.final_update_id is not None and event.final_update_id > current[3]:
            previous=event.previous_update_id
            if previous is not None and previous != current[3]:
                if key not in self._crypto_first or not previous <= current[3] <= event.final_update_id:
                    self._crypto_books.pop(key,None)
                    self._crypto_first.discard(key)
                    return None
            if event.instrument.venue == Venue.BYBIT and event.final_update_id != current[3]+1:
                self._crypto_books.pop(key,None)
                self._crypto_first.discard(key)
                return None
            bids, asks = current[0], current[1]
        else:
            self._crypto_books.pop(key, None)
            self._crypto_first.discard(key)
            return None
        for target, levels in ((bids, event.bids), (asks, event.asks)):
            for level in levels:
                if level.quantity == 0:
                    target.pop(level.price, None)
                else:
                    target[level.price] = level.quantity
        if len(bids) > 1000:
            for price in sorted(bids)[:-1000]: bids.pop(price,None)
        if len(asks) > 1000:
            for price in sorted(asks)[1000:]: asks.pop(price,None)
        if not bids or not asks:
            self._crypto_books.pop(key, None)
            self._crypto_first.discard(key)
            return None
        self._crypto_books[key] = (bids, asks, int(event.sync_generation or 0), int(event.final_update_id or 0))
        top_bids = sorted(bids, reverse=True)[:50]
        top_asks = sorted(asks)[:50]
        if top_bids[0] >= top_asks[0]:
            self._crypto_books.pop(key, None)
            self._crypto_first.discard(key)
            return None
        if event.update_type == BookUpdateType.DELTA:
            self._crypto_first.discard(key)
        return event.model_copy(update={"update_type": BookUpdateType.SNAPSHOT,
            "bids": tuple(OrderBookLevel(price=p, quantity=bids[p]) for p in top_bids),
            "asks": tuple(OrderBookLevel(price=p, quantity=asks[p]) for p in top_asks),
            "depth": max(len(top_bids), len(top_asks)), "is_full_depth": False})

    def latest_trade(self, instrument_key: str) -> TradeEvent | None:
        values = self._trades.get(instrument_key)
        return values[-1] if values else None

    def recent_trades(self, instrument_key: str, limit: int = 40) -> tuple[TradeEvent, ...]:
        """Read the existing ring newest first, without creating another cache."""
        if type(limit) is not int or not 0<=limit<=40:
            raise ValueError("recent trade view limit must be between zero and forty")
        from itertools import islice
        return tuple(islice(reversed(self._trades.get(instrument_key,())),limit))

    def latest_book(self, instrument_key: str) -> OrderBookEvent | None:
        values = self._books.get(instrument_key)
        event = values[-1] if values else None
        if event is not None and event.instrument.asset_class == AssetClass.CRYPTO_PERP:
            if time.time_ns() - int(event.receive_ts_ns or 0) > 1_000_000_000:
                return None
        return event

    def latest_index_quote(self, instrument_key: str) -> IndexQuoteEvent | None:
        values = self._index_quotes.get(instrument_key)
        return values[-1] if values else None

    def instruments(self) -> list[str]:
        return sorted(set(self._trades) | set(self._books) | set(self._index_quotes))


@dataclass(frozen=True)
class StrategyDataView:
    hot_path: HotPathStore | DecisionSnapshot
    history_reader: object | None = None
    ready: bool = True
    phase: str = "SHADOW"

    def history(self, instrument_key: str, event_type: str, limit: int = 1000) -> list[dict]:
        if not isinstance(self.hot_path, DecisionSnapshot):
            raise HistoryNotPrepared("Use a prepared decision snapshot; callback disk reads are disabled")
        return self.hot_path.history(instrument_key=instrument_key, event_type=event_type, limit=limit)

    @property
    def cursor(self) -> tuple[str, int]:
        if isinstance(self.hot_path, DecisionSnapshot):
            return self.hot_path.cursor
        return self.hot_path.engine_incarnation, self.hot_path.apply_seq

    def closed_bars(self, instrument_key: str, limit: int = 1000) -> tuple[Bar1mEvent, ...]:
        return self.hot_path.closed_bars(instrument_key, limit)

    def latest_trade(self, instrument_key: str) -> TradeEvent | None:
        return self.hot_path.latest_trade(instrument_key)

    def latest_book(self, instrument_key: str) -> OrderBookEvent | None:
        return self.hot_path.latest_book(instrument_key)

    def latest_index_quote(self, instrument_key: str) -> IndexQuoteEvent | None:
        return self.hot_path.latest_index_quote(instrument_key)

    def subscribe_keys(self, subscriptions: Iterable[object]) -> list[str]:
        out: list[str] = []
        for item in subscriptions:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and item.get("instrument_key"):
                out.append(str(item["instrument_key"]))
        return out
