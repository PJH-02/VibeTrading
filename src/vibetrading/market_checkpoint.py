"""Bounded market-state checkpoint codec, independent of engine recovery.

Capture must run synchronously under the engine owner's cursor barrier. The
producer vector is supplied by that owner; this codec cannot prove the barrier.
Decoded stores are separate objects and carry no LIVE/strategy/execution proof.
Event monotonic receipt fields remain historical provenance; only dedup TTLs
are rebased onto a new monotonic clock.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

from .feed_recovery import EngineCursor
from .hot_path import HotPathStore, PreparedHistory, _event_fingerprint, freeze_market_event
from .models import (AssetClass, Bar1mEvent, BaseMarketEvent, IndexQuoteEvent,
                     InstrumentId, OrderBookEvent, OrderBookLevel, TradeEvent, event_from_dict)


class CheckpointError(ValueError):
    pass


@dataclass(frozen=True)
class CheckpointProducer:
    cursor: EngineCursor
    digest: str
    quality: str


@dataclass(frozen=True)
class MarketCheckpoint:
    store: HotPathStore
    producers: tuple[CheckpointProducer, ...]
    captured_epoch_ns: int
    config_sha256: str


def _require(condition, reason):
    if not condition:
        raise CheckpointError(reason)


def _integer(value, minimum=0):
    _require(type(value) is int and value >= minimum, "invalid checkpoint integer")
    return value


def _text(value):
    _require(type(value) is str and bool(value), "invalid checkpoint identity/text")
    return value


def _digest(value):
    _require(type(value) is str and len(value) == 64 and
             all(char in "0123456789abcdef" for char in value), "invalid checkpoint digest")
    return value


def _object(value, fields):
    _require(type(value) is dict and set(value) == set(fields), "checkpoint schema mismatch")
    return value


def _mapping(value, bound):
    _require(type(value) is dict and len(value) <= bound, "checkpoint map exceeds capacity")
    for key in value:
        _text(key)
    return value


def _list(value, bound):
    _require(type(value) is list and len(value) <= bound, "checkpoint list exceeds capacity")
    return value


def _canonical(value, limit):
    # Inspect without recursively allocating a second object tree. The byte
    # budget also caps nodes, including hostile direct state-seam inputs.
    stack, nodes = [(iter((value,)), 0)], 0
    while stack:
        iterator, depth = stack[-1]
        try:
            item = next(iterator)
        except StopIteration:
            stack.pop()
            continue
        nodes += 1
        _require(nodes <= limit and depth <= 64, "checkpoint nesting/node bound exceeded")
        if isinstance(item, str):
            _require(len(item) <= limit, "checkpoint string exceeds byte bound")
        if isinstance(item, dict):
            stack.append(((child for pair in item.items() for child in pair), depth+1))
        elif isinstance(item, (tuple, list)):
            stack.append((iter(item), depth+1))
    result = bytearray()
    try:
        for chunk in json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, allow_nan=False).iterencode(value):
            encoded = chunk.encode("utf-8")
            _require(len(result)+len(encoded) <= limit, "checkpoint byte bound exceeded")
            result.extend(encoded)
    except (TypeError, ValueError, RecursionError) as error:
        raise CheckpointError("invalid canonical checkpoint JSON") from error
    return bytes(result)


def _capacity(value):
    names = {"max_trades", "max_books", "max_index_quotes", "history_max_rows",
             "history_max_feeds", "history_max_bytes", "dedup_max_entries", "dedup_ttl_seconds"}
    _object(value, names)
    for name in names-{"dedup_ttl_seconds"}:
        _integer(value[name], 1)
    ttl = value["dedup_ttl_seconds"]
    _require(type(ttl) in (int, float) and math.isfinite(ttl) and ttl > 0,
             "invalid checkpoint dedup TTL")
    return dict(value)


def _decimal_text(value, limit):
    _require(value.is_finite() and sys.getsizeof(value) <= limit,
             "checkpoint Decimal exceeds scalar bound")
    text = str(value)
    _require(len(text) <= limit, "checkpoint Decimal exceeds scalar bound")
    return text


def _plain(value, depth=0):
    _require(depth <= 32, "checkpoint event nesting exceeds bound")
    if isinstance(value, (BaseMarketEvent, InstrumentId, OrderBookLevel)):
        return {key: _plain(getattr(value, key), depth+1) for key in type(value).model_fields}
    if isinstance(value, Mapping):
        _require(all(type(key) is str for key in value), "invalid event mapping key")
        return {key: _plain(item, depth+1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth+1) for item in value]
    if isinstance(value, Decimal):
        _require(value.is_finite(), "nonfinite checkpoint Decimal")
        return str(value)
    _require(value is None or isinstance(value, (str, int, float, bool)), "unsupported event value")
    return value


def _metadata(value, *, decode=False, depth=0):
    """Fixed JSON tags preserve Decimal vs string without executable types."""
    _require(depth <= 32, "checkpoint metadata nesting exceeds bound")
    if not decode:
        if isinstance(value, Decimal):
            _require(value.is_finite(), "nonfinite metadata Decimal")
            return ["decimal", str(value)]
        if isinstance(value, Mapping):
            _require(all(type(key) is str for key in value), "invalid metadata key")
            return ["map", {key: _metadata(item, depth=depth+1) for key, item in value.items()}]
        if isinstance(value, (tuple, list)):
            return ["array", [_metadata(item, depth=depth+1) for item in value]]
        _require(value is None or type(value) in (str, int, float, bool), "unsupported metadata value")
        return ["scalar", value]
    _require(type(value) is list and len(value) == 2, "invalid metadata tag")
    tag, data = value
    if tag == "decimal":
        _require(type(data) is str, "invalid metadata Decimal")
        result = Decimal(data)
        _require(result.is_finite() and str(result) == data, "invalid metadata Decimal")
        return result
    if tag == "map":
        _require(type(data) is dict, "invalid metadata mapping")
        return {key: _metadata(item, decode=True, depth=depth+1) for key, item in data.items()}
    if tag == "array":
        _require(type(data) is list, "invalid metadata array")
        return [_metadata(item, decode=True, depth=depth+1) for item in data]
    _require(tag == "scalar" and (data is None or type(data) in (str, int, float, bool)),
             "invalid metadata scalar")
    return data


def _event_data(event, max_bytes):
    # Bound source traversal before copying metadata or escaping a large scalar.
    def model_values(model):
        for name in type(model).model_fields:
            yield name
            yield getattr(model, name)

    pending, used = [(iter((event,)), 0)], 0
    while pending:
        iterator, depth = pending[-1]
        try:
            value = next(iterator)
        except StopIteration:
            pending.pop()
            continue
        _require(depth <= 32, "checkpoint event nesting exceeds bound")
        used += 1
        if isinstance(value, (BaseMarketEvent, InstrumentId, OrderBookLevel)):
            pending.append((model_values(value), depth+1))
        elif isinstance(value, Mapping):
            _require(len(value) <= max_bytes, "checkpoint metadata item bound exceeded")
            pending.append(((child for pair in value.items() for child in pair), depth+1))
        elif isinstance(value, (tuple, list)):
            _require(len(value) <= max_bytes, "checkpoint event item bound exceeded")
            pending.append((iter(value), depth+1))
        else:
            if isinstance(value, Decimal):value = _decimal_text(value, max_bytes)
            if isinstance(value, str):
                _require(len(value) <= max_bytes, "checkpoint event string exceeds byte bound")
                used += len(value)
        _require(used <= max_bytes, "checkpoint event source exceeds byte bound")
    data = _plain(event)
    if isinstance(event, IndexQuoteEvent):
        data["metadata"] = _metadata(event.metadata)
    _canonical(data, max_bytes)
    return data


def _event(data, expected, max_bytes):
    _canonical(data, max_bytes)
    _object(data, expected.model_fields)
    _require(data["schema_version"] == 2 and type(data["schema_version"]) is int,
             "unsupported market event schema")
    _text(data["event_id"])
    _integer(data["receive_ts_ns"])
    _integer(data["wallclock_ts_ns"])
    fields = dict(data)
    if expected is IndexQuoteEvent:
        fields["metadata"] = _metadata(fields["metadata"], decode=True)
    event = freeze_market_event(event_from_dict(fields))
    _require(isinstance(event, expected), "checkpoint event type mismatch")
    _require(_canonical(_event_data(event, max_bytes), max_bytes) == _canonical(data, max_bytes),
             "checkpoint event normalization changed content")
    return event


def _export_store(store, capture_monotonic_ns, max_event_bytes, max_bytes):
    _integer(capture_monotonic_ns)
    _integer(max_event_bytes, 1)
    _integer(max_bytes, 1)
    capacity = _capacity(store.checkpoint_capacity())
    feeds = capacity["history_max_feeds"]
    containers = (store._trades, store._books, store._index_quotes, store._versions,
                  store._book_versions, store._quality, store._crypto_books, store.history._rows,
                  store.history._limits, store.history._providers, store.history._quality,
                  store._identity_conflicts, store._crypto_first)
    _require(all(len(values) <= feeds for values in containers), "source feed capacity exceeded")
    _require(len(store._fingerprints) <= capacity["dedup_max_entries"] and
             len(store._seen._items) <= capacity["dedup_max_entries"], "source dedup capacity exceeded")
    nodes = sum(len(values) for values in containers)+len(store._fingerprints)*3+len(store._seen._items)*2
    for values, bound in ((store._trades, capacity["max_trades"]), (store._books, capacity["max_books"]),
                          (store._index_quotes, capacity["max_index_quotes"]),
                          (store.history._rows, capacity["history_max_rows"])):
        _require(all(len(rows) <= bound for rows in values.values()), "source ring capacity exceeded")
        nodes += sum(len(rows) for rows in values.values())
    for native in store._crypto_books.values():
        _require(len(native[0]) <= 1000 and len(native[1]) <= 1000, "source native depth exceeded")
        nodes += (len(native[0])+len(native[1]))*3
    _require(nodes <= max_bytes, "source checkpoint node bound exceeded")
    remaining = max_bytes

    def consume(value):
        nonlocal remaining
        _require(remaining > 0, "checkpoint aggregate byte bound exceeded")
        remaining -= len(_canonical(value, remaining))
        return value

    def event_data(event):
        _require(remaining > 0, "checkpoint aggregate byte bound exceeded")
        return consume(_event_data(event, min(max_event_bytes, remaining)))

    def native_data(native):
        result = {"generation": native[2], "final_update_id": native[3]}
        for name, side in (("bids", native[0]), ("asks", native[1])):
            result[name] = []
            for price, quantity in sorted(side.items(), reverse=name=="bids"):
                result[name].append(consume([_decimal_text(price, remaining), _decimal_text(quantity, remaining)]))
        return result

    ttl = Decimal.from_float(float(store._seen.ttl_seconds))
    now = Decimal(capture_monotonic_ns)/1_000_000_000
    state = {
        "capacity": capacity,
        "engine": {"incarnation": store.engine_incarnation, "apply_seq": store.apply_seq,
                   "clock_version": store.clock_version},
        "trades": {key: [event_data(event) for event in events]
                   for key, events in store._trades.items()},
        "books": {key: [event_data(event) for event in events]
                  for key, events in store._books.items()},
        "index_quotes": {key: [event_data(event) for event in events]
                         for key, events in store._index_quotes.items()},
        "versions": dict(store._versions), "book_versions": dict(store._book_versions),
        "quality": dict(store._quality), "identity_conflicts": sorted(store._identity_conflicts),
        "crypto_first": sorted(store._crypto_first),
        "crypto_books": {key: native_data(book) for key, book in store._crypto_books.items()},
        "history": {"rows": {key: [event_data(event) for event in rows.values()]
                              for key, rows in store.history._rows.items()},
                    "limits": dict(store.history._limits), "providers": dict(store.history._providers),
                    "quality": dict(store.history._quality)},
        "fingerprints": [consume([identity, digest, key]) for identity, (digest, key) in store._fingerprints.items()],
        "dedup": [consume([identity, int((Decimal.from_float(stamp)+ttl-now)*1_000_000_000)])
                  for identity, stamp in store._seen._items.items()],
    }
    _canonical(state, max_bytes)
    return state


def _restore_store(cls, state, expected_capacity, elapsed_ns, restore_monotonic_ns, max_event_bytes, max_bytes):
    _integer(elapsed_ns)
    _integer(restore_monotonic_ns)
    _integer(max_event_bytes, 1)
    _integer(max_bytes, 1)
    _canonical(state, max_bytes)
    fields = {"capacity", "engine", "trades", "books", "index_quotes", "versions", "book_versions",
              "quality", "identity_conflicts", "crypto_first", "crypto_books", "history", "fingerprints", "dedup"}
    _object(state, fields)
    capacity = _capacity(state["capacity"])
    _require(capacity == _capacity(expected_capacity), "checkpoint capacity/config mismatch")
    store = cls(capacity["max_trades"], capacity["max_books"], capacity["max_index_quotes"])
    store.history = PreparedHistory(capacity["history_max_rows"], capacity["history_max_feeds"],
                                    capacity["history_max_bytes"])
    store._seen.max_entries = capacity["dedup_max_entries"]
    store._seen.ttl_seconds = capacity["dedup_ttl_seconds"]
    engine = _object(state["engine"], {"incarnation", "apply_seq", "clock_version"})
    store.engine_incarnation = _text(engine["incarnation"])
    store.apply_seq = _integer(engine["apply_seq"])
    store.clock_version = _integer(engine["clock_version"])
    feeds = capacity["history_max_feeds"]
    for name in ("versions", "book_versions", "quality"):
        values = _mapping(state[name], feeds)
        if name == "quality":
            for value in values.values():_text(value)
        else:
            for value in values.values():
                _integer(value, 1)
                _require(value <= store.apply_seq+store.clock_version, "invalid checkpoint version")
        setattr(store, "_"+name, dict(values))
    _require(set(store._quality) == set(store._versions), "market quality/version keys mismatch")
    _require(set(store._book_versions) <= set(store._versions) and
             all(value <= store._versions[key] for key, value in store._book_versions.items()),
             "book version mismatch")
    for name, model, bound in (("trades", TradeEvent, capacity["max_trades"]),
                               ("books", OrderBookEvent, capacity["max_books"]),
                               ("index_quotes", IndexQuoteEvent, capacity["max_index_quotes"])):
        for key, rows in _mapping(state[name], feeds).items():
            _require(key in store._versions, "ring lacks market version")
            target = getattr(store, "_"+name)[key]
            for row in _list(rows, bound):
                event = _event(row, model, max_event_bytes)
                _require(event.instrument.key == key, "ring instrument mismatch")
                target.append(event)
    history = _object(state["history"], {"rows", "limits", "providers", "quality"})
    rows = _mapping(history["rows"], feeds)
    limits = _mapping(history["limits"], feeds)
    _require(set(rows) == set(limits), "history registration mismatch")
    providers = _mapping(history["providers"], feeds)
    _require(set(providers) <= set(rows), "history provider mismatch")
    for key, provider in providers.items():_text(provider)
    for key, values in rows.items():
        bound = _integer(limits[key], 1)
        _require(bound <= capacity["history_max_rows"], "history window exceeds capacity")
        store.history.register(key, bound)
        for row in _list(values, bound):
            event = _event(row, Bar1mEvent, max_event_bytes)
            _require(event.instrument.key == key and providers.get(key) == event.provider and
                     event.closed and event.open_ts_ns < event.close_ts_ns,
                     "invalid checkpoint history row")
            identity = (event.open_ts_ns, event.close_ts_ns)
            _require(identity not in store.history._rows[key], "duplicate checkpoint bar")
            size = len(event.model_dump_json().encode("utf-8"))
            store.history._rows[key][identity] = event
            store.history._sizes[(key, *identity)] = size
            store.history.byte_size += size
            _require(store.history.byte_size <= capacity["history_max_bytes"], "history bytes exceed capacity")
    store.history._providers = dict(providers)
    store.history._quality = dict(_mapping(history["quality"], feeds))
    for value in store.history._quality.values():_text(value)
    for name in ("identity_conflicts", "crypto_first"):
        values = _list(state[name], feeds)
        for value in values:_text(value)
        _require(values == sorted(set(values)), "noncanonical checkpoint set")
        setattr(store, "_"+name, set(values))
    _require(store._identity_conflicts <= set(store._versions), "unknown conflicted instrument")
    for key in store._identity_conflicts:
        _require(store._quality[key] == "EVENT_ID_CONFLICT" and
                 store.history._quality.get(key) == "EVENT_ID_CONFLICT" and key not in store._books,
                 "checkpoint conflict latch mismatch")
    for key, native in _mapping(state["crypto_books"], feeds).items():
        _object(native, {"bids", "asks", "generation", "final_update_id"})
        sides = []
        for name in ("bids", "asks"):
            levels = _list(native[name], 1000)
            _require(bool(levels), "empty native checkpoint book")
            side = {}
            for level in levels:
                _require(type(level) is list and len(level) == 2 and
                         all(type(value) is str for value in level), "invalid native level")
                price, quantity = (Decimal(value) for value in level)
                _require(price.is_finite() and quantity.is_finite() and quantity != 0 and
                         str(price) == level[0] and str(quantity) == level[1] and price not in side,
                         "invalid native Decimal/duplicate level")
                side[price] = quantity
            _require(list(side) == sorted(side, reverse=name=="bids"), "native levels are not sorted")
            sides.append(side)
        bids, asks = sides
        generation, final = _integer(native["generation"]), _integer(native["final_update_id"])
        _require(max(bids) < min(asks) and key not in store._identity_conflicts and
                 store._quality.get(key) in {"VALID", "STALE"} and bool(store._books.get(key)),
                 "native book quality/projection mismatch")
        projection = store._books[key][-1]
        _require(projection.instrument.asset_class == AssetClass.CRYPTO_PERP and
                 int(projection.sync_generation or 0) == generation and int(projection.final_update_id or 0) == final,
                 "native book cursor mismatch")
        for name, side in (("bids", bids), ("asks", asks)):
            _require([(level.price, level.quantity) for level in getattr(projection, name)] ==
                     sorted(side.items(), reverse=name=="bids")[:50], "native book projection mismatch")
        store._crypto_books[key] = (bids, asks, generation, final)
    _require(store._crypto_first <= set(store._crypto_books), "native first-delta state mismatch")
    for key, values in store._books.items():
        if values and values[-1].instrument.asset_class == AssetClass.CRYPTO_PERP:
            _require(key in store._crypto_books, "crypto projection lacks native checkpoint book")
    fingerprints = _list(state["fingerprints"], capacity["dedup_max_entries"])
    for entry in fingerprints:
        _require(type(entry) is list and len(entry) == 3, "invalid checkpoint fingerprint")
        identity, digest, key = _text(entry[0]), _digest(entry[1]), _text(entry[2])
        _require(identity not in store._fingerprints, "duplicate checkpoint fingerprint")
        store._fingerprints[identity] = (digest, key)
    ttl_ns = int(Decimal.from_float(float(store._seen.ttl_seconds))*1_000_000_000)
    for entry in _list(state["dedup"], capacity["dedup_max_entries"]):
        _require(type(entry) is list and len(entry) == 2, "invalid checkpoint dedup entry")
        identity, remaining = _text(entry[0]), _integer(entry[1], 1)
        _require(identity in store._fingerprints and identity not in store._seen._items and
                 remaining <= ttl_ns and remaining > elapsed_ns, "expired or inconsistent checkpoint dedup")
        stamp = (Decimal(restore_monotonic_ns)-Decimal(ttl_ns-remaining+elapsed_ns))/1_000_000_000
        store._seen._items[identity] = math.nextafter(float(stamp), -math.inf)
    _require(list(store._seen._items) == [identity for identity in store._fingerprints
                                       if identity in store._seen._items], "checkpoint dedup order mismatch")
    originals = [event for rings in (store._trades, store._books, store._index_quotes)
                 for rows in rings.values() for event in rows
                 if not (isinstance(event, OrderBookEvent) and event.instrument.asset_class == AssetClass.CRYPTO_PERP)]
    originals.extend(event for rows in store.history._rows.values() for event in rows.values())
    for event in originals:
        if event.event_id in store._fingerprints:
            _require(store._fingerprints[event.event_id] == (_event_fingerprint(event), event.instrument.key),
                     "checkpoint retained-event fingerprint mismatch")
    return store


class CheckpointCodec:
    def __init__(self, *, max_bytes: int, max_event_bytes: int, max_producers: int,
                 config_sha256: str, capacity: dict):
        self.max_bytes = _integer(max_bytes, 1)
        self.max_event_bytes = _integer(max_event_bytes, 1)
        self.max_producers = _integer(max_producers, 1)
        _require(max_event_bytes <= max_bytes, "checkpoint event bound exceeds document bound")
        self.config_sha256 = _digest(config_sha256)
        self.capacity = _capacity(capacity)

    def _producers(self, rows):
        result, seen = [], set()
        for row in _list(rows, self.max_producers):
            _object(row, {"producer", "incarnation", "sequence", "digest", "quality"})
            producer, incarnation = _text(row["producer"]), _text(row["incarnation"])
            _require(producer not in seen, "duplicate producer checkpoint")
            seen.add(producer)
            cursor = EngineCursor(producer, incarnation, _integer(row["sequence"]))
            digest, quality = _digest(row["digest"]), _text(row["quality"])
            if cursor.sequence == 0:
                _require(digest == hashlib.sha256(b"").hexdigest(), "empty producer digest mismatch")
            result.append(CheckpointProducer(cursor, digest, quality))
        _require(bool(result), "checkpoint producer vector is empty")
        _require([item.cursor.producer for item in result] == sorted(seen), "producer vector is not canonical")
        return tuple(result)

    def _body(self, payload):
        _require(type(payload) is bytes and 0 < len(payload) <= self.max_bytes, "checkpoint byte bound exceeded")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                _require(key not in result, "duplicate checkpoint JSON key")
                result[key] = value
            return result

        def invalid_constant(value):
            raise CheckpointError("nonfinite checkpoint JSON")

        try:
            envelope = json.loads(payload, object_pairs_hook=unique, parse_constant=invalid_constant)
            _object(envelope, {"format", "version", "sha256", "body"})
            _require(envelope["format"] == "vibetrading-market-checkpoint" and
                     type(envelope["version"]) is int and envelope["version"] == 1,
                     "unsupported checkpoint format/version")
            body = _object(envelope["body"], {"config_sha256", "captured_epoch_ns", "producers", "state"})
            _require(_digest(envelope["sha256"]) == hashlib.sha256(_canonical(body, self.max_bytes)).hexdigest(),
                     "checkpoint checksum mismatch")
            _require(body["config_sha256"] == self.config_sha256, "checkpoint config mismatch")
            _integer(body["captured_epoch_ns"], 1)
            _require(_canonical(envelope, self.max_bytes) == payload, "checkpoint JSON is not canonical")
            return body
        except CheckpointError:
            raise
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
            raise CheckpointError("invalid checkpoint document") from error

    def encode(self, store: HotPathStore, *, producers: tuple[CheckpointProducer, ...],
               capture_epoch_ns: int | None = None, capture_monotonic_ns: int | None = None) -> bytes:
        epoch = time.time_ns() if capture_epoch_ns is None else _integer(capture_epoch_ns, 1)
        monotonic = time.monotonic_ns() if capture_monotonic_ns is None else _integer(capture_monotonic_ns)
        _require(store.checkpoint_capacity() == self.capacity, "checkpoint capacity/config mismatch")
        _require(type(producers) in (tuple, list) and len(producers) <= self.max_producers,
                 "checkpoint producer vector exceeds capacity")
        _require(all(isinstance(item, CheckpointProducer) for item in producers), "invalid checkpoint producer")
        _require(all(isinstance(item.cursor, EngineCursor) for item in producers), "invalid checkpoint producer cursor")
        vector = [{"producer": item.cursor.producer, "incarnation": item.cursor.incarnation,
                   "sequence": item.cursor.sequence, "digest": item.digest, "quality": item.quality}
                  for item in producers]
        vector.sort(key=lambda item: item["producer"])
        self._producers(vector)
        state = store.export_checkpoint_state(capture_monotonic_ns=monotonic,
            max_event_bytes=self.max_event_bytes, max_bytes=self.max_bytes)
        body = {"config_sha256": self.config_sha256, "captured_epoch_ns": epoch,
                "producers": vector, "state": state}
        body_bytes = _canonical(body, self.max_bytes)
        # Validate the complete state before any persistence; no source mutation.
        HotPathStore.restore_checkpoint_state(state, expected_capacity=self.capacity, elapsed_ns=0,
            restore_monotonic_ns=monotonic, max_event_bytes=self.max_event_bytes, max_bytes=self.max_bytes)
        return _canonical({"format": "vibetrading-market-checkpoint", "version": 1,
                           "sha256": hashlib.sha256(body_bytes).hexdigest(), "body": body}, self.max_bytes)

    def decode(self, payload: bytes, *, restore_epoch_ns: int | None = None,
               restore_monotonic_ns: int | None = None) -> MarketCheckpoint:
        body = self._body(payload)
        epoch = time.time_ns() if restore_epoch_ns is None else _integer(restore_epoch_ns, 1)
        monotonic = time.monotonic_ns() if restore_monotonic_ns is None else _integer(restore_monotonic_ns)
        _require(epoch >= body["captured_epoch_ns"], "checkpoint wall clock regressed")
        try:
            producers = self._producers(body["producers"])
            store = HotPathStore.restore_checkpoint_state(body["state"], expected_capacity=self.capacity,
                elapsed_ns=epoch-body["captured_epoch_ns"], restore_monotonic_ns=monotonic,
                max_event_bytes=self.max_event_bytes, max_bytes=self.max_bytes)
        except CheckpointError:
            raise
        except (ValueError, TypeError, KeyError, OverflowError, ArithmeticError) as error:
            raise CheckpointError("invalid checkpoint state") from error
        # Wall receipt time remains unchanged; stale books are never refreshed.
        store.expire(epoch)
        return MarketCheckpoint(store, producers, body["captured_epoch_ns"], self.config_sha256)

    def write(self, path: str | Path, payload: bytes) -> None:
        """Durable same-directory replace; unsupported directory fsync fails closed."""
        body = self._body(payload)
        # Validate state at capture time, independently of persistence duration.
        self.decode(payload, restore_epoch_ns=body["captured_epoch_ns"], restore_monotonic_ns=time.monotonic_ns())
        _write_checkpoint_file(path, payload, self.max_bytes)

    def _read_bytes(self, path):
        return _read_checkpoint_file(path, self.max_bytes)

    def read(self, path: str | Path, **restore_clock) -> MarketCheckpoint:
        return self.decode(self._read_bytes(path), **restore_clock)


def _read_checkpoint_file(path, max_bytes):
    with open(path, "rb") as stream:
        payload = stream.read(max_bytes+1)
    _require(0 < len(payload) <= max_bytes, "checkpoint byte bound exceeded")
    return payload


def _write_checkpoint_file(path, payload, max_bytes):
    """Persist already validated immutable codec/engine bytes, with one owner."""
    _require(type(payload) is bytes and 0 < len(payload) <= max_bytes, "checkpoint byte bound exceeded")
    path = Path(path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    temporary = None
    try:
        os.fsync(directory)  # Confirm directory fsync support before replacement.
        descriptor, temporary = tempfile.mkstemp(prefix=path.name+".", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        os.fsync(directory)
        _require(_read_checkpoint_file(path, max_bytes) == payload, "checkpoint readback mismatch")
    finally:
        if temporary is not None:
            os.unlink(temporary)
        os.close(directory)
