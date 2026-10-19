"""Bounded latest market views; no feed reconstruction, NATS or fsync claims."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import json
import inspect
import math
import os
from pathlib import Path
import stat
import tempfile
import time


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotBudgets:
    max_bytes: int
    max_instruments: int
    book_depth: int
    interval_seconds: float
    max_age_seconds: float

    def __post_init__(self):
        for value in (self.max_bytes, self.max_instruments, self.book_depth):
            if type(value) is not int or value <= 0:
                raise ValueError("Snapshot bounds must be explicit positive integers")
        if self.max_instruments > 512:
            raise ValueError("Snapshot instrument budget exceeds the existing dashboard API limit")
        for value in (self.interval_seconds, self.max_age_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("Snapshot periods must be positive and finite")
        if self.interval_seconds >= self.max_age_seconds:
            raise ValueError("Snapshot period must be shorter than its freshness lease")


def local_snapshot_config(env):
    location = env.get("LOCAL_MARKET_SNAPSHOT_FILE", "")
    raw = env.get("LOCAL_SNAPSHOT_BUDGETS", "")
    if not location or not Path(location).is_absolute() or not raw or len(raw.encode("utf-8")) > 4096:
        raise ValueError("Local dashboard requires an absolute snapshot path and explicit bounded snapshot configuration")
    values = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    if not isinstance(values, dict):
        raise ValueError("Snapshot budgets must be a JSON object")
    return Path(location), SnapshotBudgets(**values)


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise SnapshotError("duplicate_snapshot_key")
        result[key] = value
    return result


def _nonfinite(value):
    raise SnapshotError("nonfinite_snapshot_number")


def _ms(value):
    return value // 1_000_000 if type(value) is int else None


def _stamp(value):
    return int(value.receive_ts_ns or 0)


def _document(engine, budgets, now_ns):
    if engine.hot_path is None:
        raise SnapshotError("authoritative_hot_path_required")
    decision = engine.hot_path.snapshot(now_ns, "DASHBOARD")
    health = engine.stats
    feeds = health.get("feeds", {})
    healthy = bool(feeds) and not health.get("closed", False) and all(item.get("mode") == "LIVE" for item in feeds.values())
    keys = sorted(set(decision.trades) | set(decision.books) | set(decision.index_quotes) | set(decision.bars))
    keys = [key for key in keys if key in decision.trades or key in decision.books
            or key in decision.index_quotes or bool(decision.bars.get(key))]
    if len(keys) > budgets.max_instruments:
        raise SnapshotError("snapshot_instrument_budget_exceeded")
    rows = []
    for key in keys:
        trade, book, index = decision.trades.get(key), decision.books.get(key), decision.index_quotes.get(key)
        recent = engine.hot_path.recent_trades(key, limit=40)
        bars = decision.bars.get(key, ())
        bar = bars[-1] if bars else None
        events = [event for event in (trade, book, index, bar) if event is not None]
        latest = max(events, key=_stamp)
        instrument = latest.instrument
        quote = max((event for event in (index, bar) if event is not None), key=_stamp, default=None)
        quality = decision.quality.get(key, "WARMING")
        age = None if book is None else now_ns - _stamp(book)
        valid = (healthy and quality == "VALID" and book is not None
                 and age is not None and 0 <= age <= budgets.max_age_seconds * 1_000_000_000
                 and bool(book.bids) and bool(book.asks))
        visible = book if valid else None
        price = str(trade.price) if trade else (str(quote.value if index is quote else quote.close) if quote else None)
        if price is None and visible:
            with localcontext() as context:
                context.prec = 128
                price = str((visible.bids[0].price + visible.asks[0].price) / Decimal(2))
        rows.append({"key": key, "venue": instrument.venue.value, "symbol": instrument.std_symbol,
            "asset_class": instrument.asset_class.value, "currency": instrument.currency,
            "section": "crypto" if instrument.asset_class.value in {"crypto_perp", "crypto_spot"} else "stocks",
            "price": price, "last_event_ms": _ms(latest.receive_ts_ns), "session": latest.session,
            "observed_delay_ms": latest.observed_delay_ms, "book_valid": bool(valid),
            "gap_reason": None if valid else ("engine_not_ready" if not healthy else "book_" + quality.lower()),
            "book": None if visible is None else {"receive_ms": _ms(visible.receive_ts_ns),
                "source_ms": _ms(visible.source_ts_ns),
                "bids": [[str(level.price), str(level.quantity)] for level in visible.bids[:budgets.book_depth]],
                "asks": [[str(level.price), str(level.quantity)] for level in visible.asks[:budgets.book_depth]]},
            "trades": [{"id": item.event_id, "price": str(item.price),
                "quantity": str(item.quantity), "side": item.aggressor_side.value,
                "source_ms": _ms(item.source_ts_ns), "receive_ms": _ms(item.receive_ts_ns)} for item in recent],
            "quote_kind": None if quote is None else ("index_quote" if quote is index else "bar_1m")})
    document = {"schema_version": 1, "generated_ns": now_ns,
        "engine": {"incarnation": decision.engine_incarnation, "apply_seq": decision.apply_seq,
            "clock_version": decision.clock_version, "healthy": healthy,
            "feeds": {name: {field: item.get(field) for field in
                ("provider", "mode", "reason", "generation", "applied_sequence")}
                for name, item in feeds.items()}}, "markets": rows}
    return document


def _encoded(document, budgets):
    try:
        raw = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as error:
        raise SnapshotError("invalid_snapshot_document") from error
    if len(raw) > budgets.max_bytes:
        raise SnapshotError("snapshot_byte_budget_exceeded")
    return raw


def capture_market_snapshot(engine, budgets: SnapshotBudgets, *, now_ns=None):
    document = _document(engine, budgets, time.time_ns() if now_ns is None else now_ns)
    _encoded(document, budgets)
    return document


def atomic_write_snapshot(path: Path, raw: bytes):
    """Same-directory atomic replacement only; no physical durability claim."""
    path = Path(path)
    if not path.is_absolute():
        raise SnapshotError("snapshot_path_must_be_absolute")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _decimal(value, *, zero=False):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise SnapshotError("invalid_snapshot_decimal")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise SnapshotError("invalid_snapshot_decimal") from error
    if not number.is_finite() or number < 0 or (number == 0 and not zero):
        raise SnapshotError("invalid_snapshot_decimal")
    return number


def _validate(document, budgets, now_ns):
    if (not isinstance(document, dict) or set(document) != {"schema_version", "generated_ns", "engine", "markets"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or type(document["generated_ns"]) is not int
            or not 0 <= now_ns - document["generated_ns"] <= budgets.max_age_seconds * 1_000_000_000):
        raise SnapshotError("snapshot_stale_or_invalid_version")
    health, rows = document["engine"], document["markets"]
    if (not isinstance(health, dict) or type(health.get("healthy")) is not bool
            or not isinstance(health.get("incarnation"), str) or not health["incarnation"] or len(health["incarnation"]) > 128
            or type(health.get("apply_seq")) is not int or health["apply_seq"] < 0
            or not isinstance(health.get("feeds"), dict)
            or not isinstance(rows, list) or len(rows) > budgets.max_instruments):
        raise SnapshotError("invalid_snapshot_engine_or_instrument_bounds")
    if health["healthy"] and (not health["feeds"] or any(not isinstance(item, dict) or item.get("mode") != "LIVE"
                                                   for item in health["feeds"].values())):
        raise SnapshotError("snapshot_health_contradiction")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or any(not isinstance(row.get(field), str) or not row[field] or len(row[field]) > 512
                                           for field in ("key", "venue", "symbol", "asset_class"))
                or row.get("section") not in {"stocks", "crypto"} or type(row.get("last_event_ms")) is not int
                or type(row.get("book_valid")) is not bool or row["key"] in seen):
            raise SnapshotError("invalid_snapshot_market_row")
        seen.add(row["key"])
        if row.get("price") is not None:
            _decimal(row["price"])
        book = row.get("book")
        if row["book_valid"]:
            if (not health["healthy"] or not isinstance(book, dict) or type(book.get("receive_ms")) is not int
                    or not 0 <= now_ns // 1_000_000 - book["receive_ms"] <= budgets.max_age_seconds * 1000):
                raise SnapshotError("snapshot_stale_or_invalid_book")
            prices = {}
            for side in ("bids", "asks"):
                levels = book.get(side)
                if not isinstance(levels, list) or not 0 < len(levels) <= budgets.book_depth:
                    raise SnapshotError("snapshot_book_depth_exceeded")
                prices[side] = []
                for level in levels:
                    if not isinstance(level, list) or len(level) != 2:
                        raise SnapshotError("invalid_snapshot_book_level")
                    prices[side].append(_decimal(level[0]))
                    _decimal(level[1], zero=True)
                if prices[side] != sorted(prices[side], reverse=side == "bids"):
                    raise SnapshotError("unordered_snapshot_book")
            if prices["bids"][0] >= prices["asks"][0]:
                raise SnapshotError("crossed_snapshot_book")
        elif book is not None:
            raise SnapshotError("invalid_snapshot_book_visibility")
        trades = row.get("trades")
        if not isinstance(trades, list) or len(trades) > 40:
            raise SnapshotError("snapshot_trade_bound_exceeded")
        for trade in trades:
            if (not isinstance(trade, dict) or not isinstance(trade.get("id"), str) or len(trade["id"]) > 512
                    or trade.get("side") not in {"buy", "sell", "unknown"}
                    or type(trade.get("receive_ms")) is not int):
                raise SnapshotError("invalid_snapshot_trade")
            _decimal(trade.get("price"))
            _decimal(trade.get("quantity"), zero=True)
    return document


def read_market_snapshot(path: Path, budgets: SnapshotBudgets, *, now_ns=None):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        if Path(path).is_symlink():
            raise SnapshotError("snapshot_symlink_forbidden")
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise SnapshotError("snapshot_must_be_regular_file")
            raw = handle.read(budgets.max_bytes + 1)
        if len(raw) > budgets.max_bytes:
            raise SnapshotError("snapshot_byte_budget_exceeded")
        document = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        return _validate(document, budgets, time.time_ns() if now_ns is None else now_ns)
    except (OSError, ValueError, TypeError) as error:
        raise SnapshotError("snapshot_unavailable_or_invalid") from error


class LocalSnapshotWriter:
    def __init__(self, engine, path: Path, budgets: SnapshotBudgets, *, on_error=None):
        if not Path(path).is_absolute():
            raise ValueError("Snapshot output path must be absolute")
        if on_error is not None and (not callable(on_error) or inspect.iscoroutinefunction(on_error)):
            raise TypeError("Snapshot error callback must be synchronous")
        self.engine, self.path, self.budgets, self.on_error = engine, Path(path), budgets, on_error
        self._stop = asyncio.Event()
        self._task = None
        self.last_error = None
        self.written = 0

    async def start(self):
        if self._task is not None or self._stop.is_set():
            raise SnapshotError("snapshot_writer_not_startable")
        self._task = asyncio.create_task(self._run(), name="local-market-snapshot-writer")
        return self

    async def _run(self):
        while not self._stop.is_set():
            try:
                document = _document(self.engine, self.budgets, time.time_ns())
                raw = _encoded(document, self.budgets)
                # Exactly one blocking write may be submitted at a time.
                await asyncio.to_thread(atomic_write_snapshot, self.path, raw)
                self.written += 1
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = type(error).__name__
                if self.on_error:
                    try:
                        result = self.on_error(self.last_error)
                        if inspect.isawaitable(result) and inspect.iscoroutine(result):
                            result.close()
                    except Exception:
                        pass
            try:
                await asyncio.wait_for(self._stop.wait(), self.budgets.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def close(self, timeout_seconds=5):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Snapshot close timeout must be positive and finite")
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout_seconds)
            except asyncio.TimeoutError as error:
                # A slow OS write remains the one tracked worker, never a new
                # periodic submission after this close deadline.
                raise SnapshotError("snapshot_write_cleanup_pending") from error
