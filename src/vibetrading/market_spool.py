"""A bounded local market log independent of NATS.

The synchronous writer belongs to one caller-owned thread. ``append`` never
fsyncs: its receipt means APPENDED only. The caller explicitly group-flushes
before delivering DURABLE input. No retention or sole-copy deletion exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import threading
import time
from typing import Iterator


_MAGIC = b"VTSP\x00\x00\x00\x01"
_PREFIX = struct.Struct(">8sII32s")
_SEGMENT = re.compile(r"([0-9]{20})\.(sealed|active)\Z")
_CHECKPOINT = "checkpoint.json"
_CHECKPOINT_TEMP = "checkpoint.json.tmp"
_PROOF_VERSION = 2
_MAX_PROOF_BYTES = 8192
_MAX_CONSUMERS = 17
_MAX_ABSOLUTE = 2**63 - 1
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FACTORY = object()


@dataclass(frozen=True)
class SpoolCursor:
    producer: str
    incarnation: str
    sequence: int

    @property
    def producer_id(self) -> str:
        return self.producer


def _bounded_absolute(value, name: str) -> int:
    if (isinstance(value, bool) or not isinstance(value, int)
            or value <= 0 or value > _MAX_ABSOLUTE):
        raise ValueError(f"{name} must be a positive integer <= 2**63-1")
    return value


def _digest(value, name: str) -> str:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA256 hex digest")
    return value


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class RetainedPrefix:
    """Immutable factory-issued provenance for a physically retained spool cut.

    The proof is only a trusted-owner record captured while the complete prefix
    still existed. It is not remote/archive approval, GC authority or
    cryptographic protection against a malicious directory owner.
    """

    __slots__ = (
        "producer_id", "incarnation", "generation", "base_sequence",
        "base_payload_sha256", "first_retained_segment",
        "first_retained_segment_sha256", "prefix_chain_sha256",
        "checkpoint_sha256", "durable_sequence", "consumer_cursors",
        "max_disk_bytes", "max_records", "max_frame_bytes", "max_header_bytes",
        "_payload", "_sha256", "_frozen",
    )

    def __init__(self, _factory, *, producer_id, incarnation, generation,
                 base_sequence, base_payload_sha256, first_retained_segment,
                 first_retained_segment_sha256, prefix_chain_sha256,
                 checkpoint_sha256, durable_sequence, consumer_cursors,
                 max_disk_bytes, max_records, max_frame_bytes, max_header_bytes):
        if _factory is not _FACTORY:
            raise TypeError("RetainedPrefix must be issued by capture_retained_prefix or from_bytes")
        _identity(producer_id, "producer_id")
        _identity(incarnation, "incarnation")
        _identity(generation, "generation")
        base = _bounded_absolute(base_sequence, "base_sequence")
        first = _bounded_absolute(first_retained_segment, "first_retained_segment")
        if first <= 1:
            raise ValueError("retained prefix must have a nonzero removed prefix")
        durable = _bounded_absolute(durable_sequence, "durable_sequence")
        if durable < base:
            raise ValueError("captured durable sequence must cover the base")
        _digest(base_payload_sha256, "base_payload_sha256")
        _digest(first_retained_segment_sha256, "first_retained_segment_sha256")
        _digest(prefix_chain_sha256, "prefix_chain_sha256")
        _digest(checkpoint_sha256, "checkpoint_sha256")
        for name, value in (("max_disk_bytes", max_disk_bytes),
                            ("max_records", max_records),
                            ("max_frame_bytes", max_frame_bytes),
                            ("max_header_bytes", max_header_bytes)):
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value <= 0 or value > _MAX_ABSOLUTE):
                raise ValueError(f"{name} must be a positive bounded integer")
        if not isinstance(consumer_cursors, tuple) or not 1 <= len(consumer_cursors) <= _MAX_CONSUMERS:
            raise ValueError(f"consumer_cursors must be a nonempty tuple of at most {_MAX_CONSUMERS}")
        cursors = []
        for cursor in consumer_cursors:
            if not isinstance(cursor, SpoolCursor):
                raise ValueError("consumer_cursors must contain only SpoolCursor values")
            if cursor.producer != producer_id or cursor.incarnation != incarnation:
                raise ValueError("consumer cursor identity does not match the retained producer")
            sequence = _bounded_absolute(cursor.sequence, "consumer sequence")
            if sequence < base:
                raise ValueError("consumer cursor precedes the retained base")
            if sequence > durable:
                raise ValueError("consumer cursor exceeds the captured durable sequence")
            cursors.append(SpoolCursor(cursor.producer, cursor.incarnation, sequence))
        document = {
            "version": _PROOF_VERSION,
            "producer_id": producer_id,
            "incarnation": incarnation,
            "generation": generation,
            "base_sequence": base,
            "base_payload_sha256": base_payload_sha256,
            "first_retained_segment": first,
            "first_retained_segment_sha256": first_retained_segment_sha256,
            "prefix_chain_sha256": prefix_chain_sha256,
            "checkpoint_sha256": checkpoint_sha256,
            "durable_sequence": durable,
            "consumer_cursors": [[c.producer, c.incarnation, c.sequence] for c in cursors],
            "max_disk_bytes": max_disk_bytes,
            "max_records": max_records,
            "max_frame_bytes": max_frame_bytes,
            "max_header_bytes": max_header_bytes,
        }
        payload = _json(document)
        if len(payload) > _MAX_PROOF_BYTES:
            raise ValueError("retained prefix proof exceeds its byte bound")
        object.__setattr__(self, "producer_id", producer_id)
        object.__setattr__(self, "incarnation", incarnation)
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "base_sequence", base)
        object.__setattr__(self, "base_payload_sha256", base_payload_sha256)
        object.__setattr__(self, "first_retained_segment", first)
        object.__setattr__(self, "first_retained_segment_sha256", first_retained_segment_sha256)
        object.__setattr__(self, "prefix_chain_sha256", prefix_chain_sha256)
        object.__setattr__(self, "checkpoint_sha256", checkpoint_sha256)
        object.__setattr__(self, "durable_sequence", durable)
        object.__setattr__(self, "consumer_cursors", tuple(cursors))
        object.__setattr__(self, "max_disk_bytes", max_disk_bytes)
        object.__setattr__(self, "max_records", max_records)
        object.__setattr__(self, "max_frame_bytes", max_frame_bytes)
        object.__setattr__(self, "max_header_bytes", max_header_bytes)
        object.__setattr__(self, "_payload", payload)
        object.__setattr__(self, "_sha256", hashlib.sha256(payload).hexdigest())
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name, value):
        if getattr(self, "_frozen", False):
            raise AttributeError("retained prefix proof is immutable")
        object.__setattr__(self, name, value)

    @property
    def producer(self) -> str:
        return self.producer_id

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def proof_sha256(self) -> str:
        return self._sha256

    @property
    def digest(self) -> str:
        return self._sha256

    @property
    def base_segment_id(self) -> int:
        return self.first_retained_segment - 1

    @property
    def first_retained_segment_id(self) -> int:
        return self.first_retained_segment

    @property
    def first_retained_sha256(self) -> str:
        return self.first_retained_segment_sha256

    @property
    def prefix_sha256(self) -> str:
        return self.prefix_chain_sha256

    @property
    def captured_durable_sequence(self) -> int:
        return self.durable_sequence

    def to_bytes(self) -> bytes:
        return self._payload

    def __eq__(self, other):
        return isinstance(other, RetainedPrefix) and self._payload == other._payload

    def __hash__(self):
        return hash(self._payload)

    def __repr__(self):
        return (f"RetainedPrefix({self.producer_id!r}, {self.incarnation!r}, "
                f"generation={self.generation!r}, base={self.base_sequence}, "
                f"first_segment={self.first_retained_segment}, sha256={self._sha256})")

    @classmethod
    def from_bytes(cls, payload: bytes, *, expected_sha256, expected_generation):
        if not isinstance(payload, bytes) or not payload or len(payload) > _MAX_PROOF_BYTES:
            raise ValueError("retained prefix proof byte bound exceeded")
        if not isinstance(expected_sha256, str) or _HEX.fullmatch(expected_sha256) is None:
            raise ValueError("expected_sha256 must be a lowercase SHA256 hex digest")
        _identity(expected_generation, "expected_generation")
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise ValueError("retained prefix proof hash mismatch")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate retained prefix JSON key")
                result[key] = value
            return result

        def finite(_value):
            raise ValueError("nonfinite retained prefix JSON number")

        try:
            document = json.loads(payload, object_pairs_hook=unique,
                                  parse_constant=finite)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("invalid retained prefix document") from exc
        keys = {
            "version", "producer_id", "incarnation", "generation", "base_sequence",
            "base_payload_sha256", "first_retained_segment",
            "first_retained_segment_sha256", "prefix_chain_sha256",
            "checkpoint_sha256", "durable_sequence", "consumer_cursors",
            "max_disk_bytes", "max_records", "max_frame_bytes", "max_header_bytes",
        }
        if not isinstance(document, dict) or set(document) != keys:
            raise ValueError("retained prefix schema mismatch")
        if type(document["version"]) is not int or document["version"] != _PROOF_VERSION:
            raise ValueError("unsupported retained prefix version")
        generation = document["generation"]
        _identity(generation, "generation")
        if generation != expected_generation:
            raise ValueError("retained prefix generation mismatch")
        raw_cursors = document["consumer_cursors"]
        if not isinstance(raw_cursors, list) or not 1 <= len(raw_cursors) <= _MAX_CONSUMERS:
            raise ValueError("retained prefix consumer cursor schema mismatch")
        cursors = []
        for item in raw_cursors:
            if not isinstance(item, list) or len(item) != 3:
                raise ValueError("retained prefix consumer cursor schema mismatch")
            producer, incarnation, sequence = item
            if not isinstance(producer, str) or not isinstance(incarnation, str):
                raise ValueError("retained prefix consumer identity schema mismatch")
            _bounded_absolute(sequence, "consumer sequence")
            cursors.append(SpoolCursor(producer, incarnation, sequence))
        candidate = cls(
            _FACTORY,
            producer_id=document["producer_id"],
            incarnation=document["incarnation"],
            generation=generation,
            base_sequence=document["base_sequence"],
            base_payload_sha256=document["base_payload_sha256"],
            first_retained_segment=document["first_retained_segment"],
            first_retained_segment_sha256=document["first_retained_segment_sha256"],
            prefix_chain_sha256=document["prefix_chain_sha256"],
            checkpoint_sha256=document["checkpoint_sha256"],
            durable_sequence=document["durable_sequence"],
            consumer_cursors=tuple(cursors),
            max_disk_bytes=document["max_disk_bytes"],
            max_records=document["max_records"],
            max_frame_bytes=document["max_frame_bytes"],
            max_header_bytes=document["max_header_bytes"],
        )
        if candidate.to_bytes() != payload:
            raise ValueError("retained prefix proof is not canonical")
        return candidate


@dataclass(frozen=True)
class AppendReceipt:
    cursor: SpoolCursor
    status: str
    uncertain: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class SpoolRecord:
    cursor: SpoolCursor
    payload: bytes
    segment_id: int
    offset: int
    frame_bytes: int


@dataclass(frozen=True)
class SpoolScan:
    producer_id: str
    incarnation: str
    last_sequence: int
    durable_sequence: int
    record_count: int
    disk_bytes: int
    segment_count: int
    active_segment_id: int | None
    active_records: int
    active_bytes: int
    partial_tail_bytes: int
    partial_tail_offset: int | None
    uncertain_from_sequence: int | None
    uncertain_to_sequence: int | None
    uncertainty_reason: str | None
    directory_fsync_supported: bool
    base_sequence: int = 0
    first_segment_id: int = 1
    last_segment_id: int = 0

    @property
    def uncertain(self) -> bool:
        return self.uncertainty_reason is not None

    @property
    def durable_cursor(self) -> SpoolCursor:
        return SpoolCursor(self.producer_id, self.incarnation, self.durable_sequence)


class SpoolError(RuntimeError):
    def __init__(self, message: str, receipt: AppendReceipt | None = None) -> None:
        self.receipt = receipt
        super().__init__(message)


class SpoolCorruption(SpoolError):
    pass


class SpoolRotateRequired(SpoolError):
    """No bytes admitted. Explicitly seal and retry the same payload."""


def _identity(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
        raise ValueError(f"{name} must contain 1..128 UTF-8 bytes")
    return value


def _positive(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _sync_directory(root: Path) -> bool:
    if os.name == "nt":
        return False
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def _json(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _inventory(root: Path, max_disk_bytes: int, max_records: int) -> tuple[int, int, int | None, bool]:
    """Constant-memory directory inventory; no list of historical records."""
    total = count = largest = 0
    active = None
    checkpoint_temp = False
    if not root.exists():
        return 0, 0, None, False
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                raise SpoolCorruption(f"unexpected spool entry: {entry.name}")
            total += entry.stat(follow_symlinks=False).st_size
            if total > max_disk_bytes:
                raise SpoolError("spool disk byte limit exceeded")
            if entry.name in {"writer.lock", _CHECKPOINT, _CHECKPOINT_TEMP}:
                checkpoint_temp |= entry.name == _CHECKPOINT_TEMP
                continue
            match = _SEGMENT.fullmatch(entry.name)
            if match is None:
                raise SpoolCorruption(f"unknown spool file: {entry.name}")
            number = int(match[1])
            if number <= 0 or number > max_records + 1:
                raise SpoolCorruption("invalid or unbounded segment number")
            count += 1
            largest = max(largest, number)
            if match[2] == "active":
                if active is not None:
                    raise SpoolCorruption("multiple active segments")
                active = number
    if count != largest or (active is not None and active != largest):
        raise SpoolCorruption("missing, duplicate or non-final active segment")
    return total, largest, active, checkpoint_temp


def _inventory_retained(root: Path, max_disk_bytes: int, first_retained_segment: int,
                        max_records: int) -> tuple[int, int, int | None, bool, list[int]]:
    """Bounded O(segment-count) metadata inventory for a trimmed v2 spool.

    Segment IDs are absolute; ``max_records`` bounds both the retained record
    count and the segment metadata accumulated here. The first actual ID must
    exactly match the proof, and every following ID must be contiguous with
    only a final active.
    """
    total = checkpoint_temp = 0
    entries: dict[int, str] = {}
    if not root.exists():
        raise SpoolCorruption("retained spool directory is missing")
    with os.scandir(root) as scan:
        for entry in scan:
            if not entry.is_file(follow_symlinks=False):
                raise SpoolCorruption(f"unexpected spool entry: {entry.name}")
            total += entry.stat(follow_symlinks=False).st_size
            if total > max_disk_bytes:
                raise SpoolError("spool disk byte limit exceeded")
            if entry.name in {"writer.lock", _CHECKPOINT, _CHECKPOINT_TEMP}:
                checkpoint_temp |= entry.name == _CHECKPOINT_TEMP
                continue
            match = _SEGMENT.fullmatch(entry.name)
            if match is None:
                raise SpoolCorruption(f"unknown spool file: {entry.name}")
            number = int(match[1])
            _bounded_absolute(number, "segment id")
            if len(entries) >= max_records + 1:
                raise SpoolCorruption("retained segment count exceeds the record limit")
            if number in entries:
                raise SpoolCorruption("duplicate spool segment number")
            entries[number] = match[2]
    if not entries:
        raise SpoolCorruption("retained spool has no segment files")
    numbers = sorted(entries)
    if numbers[0] != first_retained_segment:
        raise SpoolCorruption("actual first segment differs from the retained proof")
    if numbers[-1] - numbers[0] + 1 != len(numbers):
        raise SpoolCorruption("missing, duplicate or non-contiguous retained segment")
    if len(numbers) > max_records + 1:
        raise SpoolCorruption("retained segment count exceeds the record limit")
    active = None
    for number in numbers:
        kind = entries[number]
        path = root / f"{number:020d}.{kind}"
        if kind == "active":
            if active is not None or number != numbers[-1]:
                raise SpoolCorruption("multiple or non-final active segments")
            active = number
        elif path.stat().st_size == 0:
            raise SpoolCorruption("empty sealed spool segment")
    if entries[first_retained_segment] != "sealed":
        raise SpoolCorruption("first retained segment must be sealed")
    return total, numbers[-1], active, checkpoint_temp, numbers


def _checkpoint(root: Path, producer_id: str, incarnation: str, max_header_bytes: int) -> int:
    path = root / _CHECKPOINT
    if not path.exists():
        return 0
    if path.stat().st_size > max_header_bytes + 512:
        raise SpoolCorruption("checkpoint too large")
    try:
        document = json.loads(path.read_bytes())
        if (set(document) != {"version", "producer_id", "incarnation", "durable_sequence"}
                or type(document["version"]) is not int or document["version"] != 1
                or document["producer_id"] != producer_id or document["incarnation"] != incarnation
                or isinstance(document["durable_sequence"], bool)
                or not isinstance(document["durable_sequence"], int) or document["durable_sequence"] < 0):
            raise ValueError("checkpoint identity/version/cursor")
        return document["durable_sequence"]
    except (ValueError, TypeError, KeyError) as exc:
        raise SpoolCorruption("invalid checkpoint or unknown producer incarnation") from exc


def _checkpoint_with_bytes(root: Path, producer_id: str, incarnation: str,
                           max_header_bytes: int) -> tuple[int, bytes | None]:
    path = root / _CHECKPOINT
    if not path.exists():
        return 0, None
    if path.stat().st_size > max_header_bytes + 512:
        raise SpoolCorruption("checkpoint too large")
    raw = path.read_bytes()
    return _checkpoint(root, producer_id, incarnation, max_header_bytes), raw


def _segment_path(root: Path, number: int, active: int | None) -> Path:
    sealed = root / f"{number:020d}.sealed"
    if sealed.exists():
        return sealed
    return root / f"{number:020d}.active"


def _frames(path: Path, segment_id: int, producer_id: str, incarnation: str,
            max_frame_bytes: int, max_header_bytes: int, *, allow_partial: bool,
            start_offset: int = 0, opened_handle=None) -> Iterator[SpoolRecord | tuple[int, int]]:
    with (path.open("rb") if opened_handle is None else nullcontext(opened_handle)) as handle:
        handle.seek(start_offset)
        while True:
            offset = handle.tell()
            prefix = handle.read(_PREFIX.size)
            if not prefix:
                return
            if len(prefix) != _PREFIX.size:
                if allow_partial:
                    yield offset, len(prefix)
                    return
                raise SpoolCorruption(f"truncated sealed frame at {path.name}:{offset}")
            magic, header_size, payload_size, expected = _PREFIX.unpack(prefix)
            if (magic != _MAGIC or header_size == 0 or header_size > max_header_bytes
                    or payload_size == 0 or _PREFIX.size + header_size + payload_size > max_frame_bytes):
                raise SpoolCorruption(f"invalid frame version/length at {path.name}:{offset}")
            header = handle.read(header_size)
            payload = handle.read(payload_size)
            if len(header) != header_size or len(payload) != payload_size:
                if allow_partial:
                    yield offset, _PREFIX.size + len(header) + len(payload)
                    return
                raise SpoolCorruption(f"truncated sealed frame at {path.name}:{offset}")
            if hashlib.sha256(header + payload).digest() != expected:
                raise SpoolCorruption(f"frame checksum mismatch at {path.name}:{offset}")
            try:
                metadata = json.loads(header)
                sequence = metadata["sequence"]
                if (set(metadata) != {"producer_id", "incarnation", "sequence"}
                        or metadata["producer_id"] != producer_id or metadata["incarnation"] != incarnation
                        or isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0):
                    raise ValueError("frame identity/cursor")
            except (ValueError, TypeError, KeyError) as exc:
                raise SpoolCorruption(f"invalid frame identity/cursor at {path.name}:{offset}") from exc
            yield SpoolRecord(SpoolCursor(producer_id, incarnation, sequence), payload,
                              segment_id, offset, _PREFIX.size + header_size + payload_size)


def scan_spool(root: Path, *, producer_id: str, incarnation: str, max_disk_bytes: int,
               max_records: int, max_frame_bytes: int, max_header_bytes: int,
               retained_prefix: RetainedPrefix | None = None) -> SpoolScan:
    """Validate records sequentially, retaining only one bounded frame in RAM.

    An active final partial tail is reported and preserved. It is never
    truncated or mistaken for a complete frame. Checksum corruption is fatal.
    With an explicit retained proof the inventory begins at its absolute first
    segment; ``max_records`` then bounds retained records, not absolute IDs.
    """
    root = Path(root)
    _identity(producer_id, "producer_id"); _identity(incarnation, "incarnation")
    for name, value in (("max_disk_bytes", max_disk_bytes), ("max_records", max_records),
                        ("max_frame_bytes", max_frame_bytes), ("max_header_bytes", max_header_bytes)):
        _positive(value, name)
    if retained_prefix is not None:
        if not isinstance(retained_prefix, RetainedPrefix):
            raise ValueError("retained_prefix must be an issued RetainedPrefix proof")
        if (retained_prefix.producer_id != producer_id
                or retained_prefix.incarnation != incarnation):
            raise SpoolCorruption("retained proof identity does not match the reader")
        first = retained_prefix.first_retained_segment
        base = retained_prefix.base_sequence
        disk, last, active, checkpoint_temp, numbers = _inventory_retained(
            root, max_disk_bytes, first, max_records)
        if checkpoint_temp:
            raise SpoolCorruption("unfinished checkpoint at retained startup")
        first_path = root / f"{first:020d}.sealed"
        if not first_path.exists() or first_path.stat().st_size == 0:
            raise SpoolCorruption("first retained segment is missing or empty")
        if _hash_file(first_path) != retained_prefix.first_retained_segment_sha256:
            raise SpoolCorruption("first retained segment hash differs from the proof")
        durable, checkpoint_bytes = _checkpoint_with_bytes(
            root, producer_id, incarnation, max_header_bytes)
        if checkpoint_bytes is None:
            raise SpoolCorruption("retained startup requires a durable checkpoint")
        if durable > _MAX_ABSOLUTE:
            raise SpoolCorruption("durable checkpoint exceeds its absolute bound")
        if durable < base:
            raise SpoolCorruption("durable checkpoint does not cover the retained base")
        if durable < retained_prefix.durable_sequence:
            raise SpoolCorruption("durable checkpoint regressed below the retained proof")
        if (durable == retained_prefix.durable_sequence
                and hashlib.sha256(checkpoint_bytes).hexdigest() != retained_prefix.checkpoint_sha256):
            raise SpoolCorruption("retained checkpoint file hash differs from the proof")
        expected = base + 1
        count = active_count = active_size = 0
        first_seen = False
        for number in numbers:
            is_active = number == active
            path = _segment_path(root, number, active)
            if not path.exists():
                raise SpoolCorruption("missing spool segment")
            if not is_active and path.stat().st_size == 0:
                raise SpoolCorruption("empty sealed spool segment")
            if is_active:
                active_size = path.stat().st_size
            for record in _frames(path, number, producer_id, incarnation,
                                  max_frame_bytes, max_header_bytes,
                                  allow_partial=is_active):
                if isinstance(record, tuple):
                    raise SpoolCorruption("partial active tail at retained startup")
                if record.cursor.sequence > _MAX_ABSOLUTE:
                    raise SpoolCorruption("retained sequence exceeds its absolute bound")
                if record.cursor.sequence != expected:
                    raise SpoolCorruption(f"non-contiguous retained sequence at {path.name}:{record.offset}")
                if not first_seen:
                    if record.cursor.sequence != base + 1:
                        raise SpoolCorruption("first retained frame is not base+1")
                    first_seen = True
                expected += 1
                count += 1
                active_count += int(is_active)
                if count > max_records:
                    raise SpoolError("spool retained record limit exceeded")
        if not first_seen:
            raise SpoolCorruption("first retained segment has no records")
        last_sequence = expected - 1
        if durable > last_sequence:
            raise SpoolCorruption("durable checkpoint exceeds verified complete records")
        reasons = []
        if count > durable - base:
            reasons.append("complete_records_beyond_durable_checkpoint")
        uncertain_from = durable + 1 if reasons else None
        return SpoolScan(producer_id, incarnation, last_sequence, durable, count, disk,
                         len(numbers), active, active_count, active_size, 0, None,
                         uncertain_from, last_sequence if reasons else None,
                         ",".join(reasons) or None, os.name != "nt",
                         base, first, last)
    disk, segments, active, checkpoint_temp = _inventory(root, max_disk_bytes, max_records)
    durable = _checkpoint(root, producer_id, incarnation, max_header_bytes)
    count = active_count = active_size = partial = 0
    partial_offset = None
    for number in range(1, segments + 1):
        is_active = number == active
        path = root / f"{number:020d}.{'active' if is_active else 'sealed'}"
        if is_active and not path.exists():
            # A live writer may have renamed its final active file after the
            # inventory. Both names refer to the same immutable prefix.
            path = root / f"{number:020d}.sealed"
        if not path.exists():
            raise SpoolCorruption("missing spool segment")
        if not is_active and path.stat().st_size == 0:
            raise SpoolCorruption("empty sealed spool segment")
        if is_active:
            active_size = path.stat().st_size
        for record in _frames(path, number, producer_id, incarnation, max_frame_bytes, max_header_bytes,
                              allow_partial=is_active):
            if isinstance(record, tuple):
                partial_offset, partial = record
                continue
            if record.cursor.sequence != count + 1:
                raise SpoolCorruption(f"non-contiguous ingress sequence at {path.name}:{record.offset}")
            count += 1
            active_count += int(is_active)
            if count > max_records:
                raise SpoolError("spool record limit exceeded")
    if durable > count:
        raise SpoolCorruption("durable checkpoint exceeds verified complete records")
    reasons = []
    if partial:
        reasons.append("active_partial_tail")
    if count > durable:
        reasons.append("complete_records_beyond_durable_checkpoint")
    if checkpoint_temp:
        reasons.append("unfinished_checkpoint")
    uncertain_from = durable + 1 if reasons else None
    return SpoolScan(producer_id, incarnation, count, durable, count, disk, segments,
                     active, active_count, active_size, partial, partial_offset,
                     uncertain_from, count if count > durable else None,
                     ",".join(reasons) or None, os.name != "nt",
                     0, 1, segments)


def capture_retained_prefix(root: Path, producer_id: str, incarnation: str, *,
                            first_retained_segment: int, generation: str,
                            consumer_cursors, max_disk_bytes: int, max_records: int,
                            max_frame_bytes: int, max_header_bytes: int) -> RetainedPrefix:
    """READ-ONLY capture of provenance while the full original prefix exists.

    No pruning, checkpoint write or spool mutation is performed. All files are
    hashed in bounded chunks and only one frame is held in memory at a time.
    """
    root = Path(root)
    _identity(producer_id, "producer_id"); _identity(incarnation, "incarnation")
    _identity(generation, "generation")
    first = _bounded_absolute(first_retained_segment, "first_retained_segment")
    if first <= 1:
        raise ValueError("retained prefix must remove at least one segment")
    for name, value in (("max_disk_bytes", max_disk_bytes), ("max_records", max_records),
                        ("max_frame_bytes", max_frame_bytes), ("max_header_bytes", max_header_bytes)):
        _positive(value, name)
    if isinstance(max_records, bool) or max_records > _MAX_ABSOLUTE:
        raise ValueError("max_records must be a bounded integer")
    disk, last, active, checkpoint_temp, numbers = _inventory_retained(
        root, max_disk_bytes, 1, max_records)
    if checkpoint_temp:
        raise SpoolCorruption("retained prefix capture requires no unfinished checkpoint")
    if first not in numbers:
        raise SpoolCorruption("first retained segment is missing")
    if last < first:
        raise SpoolCorruption("first retained segment is beyond the spool")
    first_sealed = root / f"{first:020d}.sealed"
    if not first_sealed.exists() or first_sealed.stat().st_size == 0:
        raise SpoolCorruption("first retained segment must be sealed and nonempty")
    durable, checkpoint_bytes = _checkpoint_with_bytes(
        root, producer_id, incarnation, max_header_bytes)
    if checkpoint_bytes is None:
        raise SpoolCorruption("retained prefix capture requires a durable checkpoint")
    expected = 1
    base = None
    base_payload_sha256 = None
    previous_payload_sha256 = None
    first_seen = False
    records = 0
    for number in numbers:
        is_active = number == active
        path = _segment_path(root, number, active)
        if not path.exists():
            raise SpoolCorruption("missing spool segment")
        if not is_active and path.stat().st_size == 0:
            raise SpoolCorruption("empty sealed spool segment")
        for record in _frames(path, number, producer_id, incarnation,
                              max_frame_bytes, max_header_bytes, allow_partial=False):
            if isinstance(record, tuple):
                raise SpoolCorruption("partial active tail during retained capture")
            if record.cursor.sequence != expected:
                raise SpoolCorruption(f"non-contiguous ingress sequence at {path.name}:{record.offset}")
            if number == first and not first_seen:
                if base is None or record.cursor.sequence != base + 1:
                    raise SpoolCorruption("first retained frame is not base+1")
                first_seen = True
            expected += 1
            records += 1
            if records > max_records:
                raise SpoolError("spool record limit exceeded")
            if number < first:
                base = record.cursor.sequence
                base_payload_sha256 = hashlib.sha256(record.payload).hexdigest()
            previous_payload_sha256 = hashlib.sha256(record.payload).hexdigest()
    if not first_seen or base is None or base_payload_sha256 is None:
        raise SpoolCorruption("retained prefix has no removed segment boundary")
    if previous_payload_sha256 is None:
        raise SpoolCorruption("retained prefix has no verified records")
    if durable != records:
        raise SpoolCorruption("retained prefix capture requires a complete durable checkpoint")
    if durable < base:
        raise SpoolCorruption("durable checkpoint does not cover the retained base")
    if records > max_records:
        raise SpoolError("spool record limit exceeded")
    first_sha = _hash_file(root / f"{first:020d}.sealed")
    chain = hashlib.sha256()
    for number in range(1, first + 1):
        segment = root / f"{number:020d}.sealed"
        if not segment.exists():
            raise SpoolCorruption("removed prefix is not entirely sealed")
        chain.update(f"{number:020d}:{_hash_file(segment)}\n".encode("ascii"))
    return RetainedPrefix(
        _FACTORY,
        producer_id=producer_id,
        incarnation=incarnation,
        generation=generation,
        base_sequence=base,
        base_payload_sha256=base_payload_sha256,
        first_retained_segment=first,
        first_retained_segment_sha256=first_sha,
        prefix_chain_sha256=chain.hexdigest(),
        checkpoint_sha256=hashlib.sha256(checkpoint_bytes).hexdigest(),
        durable_sequence=durable,
        consumer_cursors=consumer_cursors,
        max_disk_bytes=max_disk_bytes,
        max_records=max_records,
        max_frame_bytes=max_frame_bytes,
        max_header_bytes=max_header_bytes,
    )


def read_spool_records(root: Path, *, producer_id: str, incarnation: str, max_disk_bytes: int,
                       max_records: int, max_frame_bytes: int, max_header_bytes: int,
                       after: SpoolCursor | None = None, through: SpoolCursor | None = None,
                       durable_only: bool = True,
                       retained_prefix: RetainedPrefix | None = None) -> Iterator[SpoolRecord]:
    # A fresh validated boundary is fixed for this iterator. Concurrent appends
    # are excluded by that upper cursor even if the file grows while reading.
    report = scan_spool(root, producer_id=producer_id, incarnation=incarnation,
                        max_disk_bytes=max_disk_bytes, max_records=max_records,
                        max_frame_bytes=max_frame_bytes, max_header_bytes=max_header_bytes,
                        retained_prefix=retained_prefix)
    for cursor in (after, through):
        if cursor is not None and (cursor.producer_id != producer_id or cursor.incarnation != incarnation
                                   or isinstance(cursor.sequence, bool) or not isinstance(cursor.sequence, int)
                                   or cursor.sequence < 0):
            raise SpoolCorruption("reader cursor has unknown incarnation or invalid sequence")
    if retained_prefix is not None:
        if after is None:
            raise SpoolCorruption("retained prefix requires an explicit after cursor")
        if after.sequence < retained_prefix.base_sequence:
            raise SpoolCorruption("reader start cursor precedes the retained base")
        start = after.sequence
        expected = retained_prefix.base_sequence + 1
        first_number = report.first_segment_id
        last_number = report.last_segment_id
    else:
        start = after.sequence if after is not None else 0
        expected = 1
        first_number = 1
        last_number = report.segment_count
    limit = report.durable_sequence if durable_only else report.last_sequence
    if start > limit or (through is not None and through.sequence > limit):
        raise SpoolCorruption("requested cursor exceeds verified reader boundary")
    end = through.sequence if through is not None else limit
    if end < start:
        raise ValueError("through cursor precedes after cursor")
    for number in range(first_number, last_number + 1):
        # A writer may have sealed the final segment since the scan.
        sealed = Path(root) / f"{number:020d}.sealed"
        path = sealed if sealed.exists() else Path(root) / f"{number:020d}.active"
        for record in _frames(path, number, producer_id, incarnation, max_frame_bytes, max_header_bytes,
                              allow_partial=number == report.active_segment_id):
            if isinstance(record, tuple):
                return
            if retained_prefix is not None and record.cursor.sequence > _MAX_ABSOLUTE:
                raise SpoolCorruption("reader observed a sequence beyond the absolute bound")
            if record.cursor.sequence != expected:
                raise SpoolCorruption("reader observed a sequence gap")
            expected += 1
            if record.cursor.sequence > end:
                return
            if record.cursor.sequence > start:
                yield record
            if record.cursor.sequence == end:
                return


class SpoolReader:
    """Bounded incremental reader with a private read cursor.

    Startup verifies history and locates ``after`` in constant frame memory.
    Later batches read only the current segment/offset and new frames. This
    cursor is delivery progress, not a consumer ACK or deletion authority.
    The caller separately persists its confirmed consumer cursor after ACK.
    """

    def __init__(self, root: Path, producer_id: str, incarnation: str, *, max_disk_bytes: int,
                 max_records: int, max_frame_bytes: int, max_header_bytes: int,
                 after: SpoolCursor | None = None,
                 retained_prefix: RetainedPrefix | None = None) -> None:
        self.root = Path(root)
        self._producer_id = _identity(producer_id, "producer_id")
        self._incarnation = _identity(incarnation, "incarnation")
        for name, value in (("max_disk_bytes", max_disk_bytes), ("max_records", max_records),
                            ("max_frame_bytes", max_frame_bytes), ("max_header_bytes", max_header_bytes)):
            setattr(self, name, _positive(value, name))
        self._retained_prefix = retained_prefix
        report = scan_spool(self.root, producer_id=producer_id, incarnation=incarnation,
                            max_disk_bytes=max_disk_bytes, max_records=max_records,
                            max_frame_bytes=max_frame_bytes, max_header_bytes=max_header_bytes,
                            retained_prefix=retained_prefix)
        if retained_prefix is not None:
            if (after is None or after.producer_id != producer_id
                    or after.incarnation != incarnation
                    or isinstance(after.sequence, bool) or not isinstance(after.sequence, int)
                    or not retained_prefix.base_sequence <= after.sequence <= report.durable_sequence):
                raise SpoolCorruption("retained reader requires an explicit start at/above the base")
            self._sequence = after.sequence
            self._segment_id = report.first_segment_id
            anchor = self._anchor_path(report.first_segment_id).stat()
            self._anchor_identity = (anchor.st_dev, anchor.st_ino)
        else:
            if after is not None and (after.producer_id != producer_id or after.incarnation != incarnation
                    or isinstance(after.sequence, bool) or not isinstance(after.sequence, int)
                    or not 0 <= after.sequence <= report.durable_sequence):
                raise SpoolCorruption("reader start cursor exceeds durable prefix or has unknown incarnation")
            self._sequence = after.sequence if after is not None else 0
            self._segment_id = 1
            self._anchor_identity = None
        self._durable_sequence = report.durable_sequence
        self._offset = 0
        self._identity: tuple[int, int] | None = None
        self._seen_size = 0
        self._sealed_signature: tuple[int, int] | None = None
        self._error: SpoolError | None = None
        if self._sequence and (retained_prefix is None
                               or self._sequence > retained_prefix.base_sequence):
            self._locate_start(report)

    def _anchor_path(self, number: int) -> Path:
        sealed = self.root / f"{number:020d}.sealed"
        if not sealed.exists():
            raise SpoolCorruption("retained first segment anchor is missing")
        return sealed

    def _verify_anchor(self) -> None:
        if self._retained_prefix is None:
            return
        number = self._retained_prefix.first_retained_segment
        path = self._anchor_path(number)
        stat = path.stat()
        if (stat.st_dev, stat.st_ino) != self._anchor_identity:
            raise SpoolCorruption("retained first segment anchor identity changed")
        if _hash_file(path) != self._retained_prefix.first_retained_segment_sha256:
            raise SpoolCorruption("retained first segment anchor hash changed")

    def _path(self, number: int) -> tuple[Path, bool]:
        sealed = self.root / f"{number:020d}.sealed"
        if sealed.exists():
            return sealed, True
        return self.root / f"{number:020d}.active", False

    def _open_segment(self, number: int):
        sealed = self.root / f"{number:020d}.sealed"
        active = self.root / f"{number:020d}.active"
        # A segment transitions from active to sealed exactly once. Three
        # bounded opens cover that rename without reopening historical data.
        for path, is_sealed in ((sealed, True), (active, False), (sealed, True)):
            try:
                return path, is_sealed, path.open("rb")
            except FileNotFoundError:
                continue
        raise SpoolCorruption("incremental reader segment is missing")

    def _locate_start(self, report: SpoolScan) -> None:
        # One startup-only streaming location pass. No per-record index or
        # historical payload list is retained for later polling.
        first = report.first_segment_id if self._retained_prefix is not None else 1
        last = report.last_segment_id if self._retained_prefix is not None else report.segment_count
        for number in range(first, last + 1):
            path, sealed, opened = self._open_segment(number)
            iterator = _frames(path, number, self.producer_id, self.incarnation,
                               self.max_frame_bytes, self.max_header_bytes,
                               allow_partial=not sealed, opened_handle=opened)
            try:
                for record in iterator:
                    if isinstance(record, tuple):
                        break
                    if record.cursor.sequence == self._sequence:
                        stat = os.fstat(opened.fileno())
                        self._segment_id = number
                        self._offset = record.offset + record.frame_bytes
                        self._identity = (stat.st_dev, stat.st_ino)
                        self._seen_size = stat.st_size
                        self._sealed_signature = (stat.st_size, stat.st_mtime_ns) if sealed else None
                        return
            finally:
                iterator.close()
                opened.close()
        raise SpoolCorruption("durable start cursor was not found")

    @property
    def producer_id(self) -> str:
        return self._producer_id

    @property
    def incarnation(self) -> str:
        return self._incarnation

    @property
    def retained_prefix(self) -> RetainedPrefix | None:
        return self._retained_prefix

    @property
    def generation(self) -> str | None:
        return None if self._retained_prefix is None else self._retained_prefix.generation

    @property
    def base_sequence(self) -> int:
        return 0 if self._retained_prefix is None else self._retained_prefix.base_sequence

    @property
    def cursor(self) -> SpoolCursor:
        return SpoolCursor(self.producer_id, self.incarnation, self._sequence)

    @property
    def durable_cursor(self) -> SpoolCursor:
        return SpoolCursor(self.producer_id, self.incarnation, self._durable_sequence)

    def read_batch(self, *, max_records: int, max_bytes: int) -> tuple[SpoolRecord, ...]:
        _positive(max_records, "batch max_records"); _positive(max_bytes, "batch max_bytes")
        if self._error is not None:
            raise SpoolError("incremental reader is fail-closed") from self._error
        # Work on local cursor state. If any frame/integrity operation fails,
        # the entire undelivered batch keeps the caller-visible cursor intact.
        sequence, number, offset = self._sequence, self._segment_id, self._offset
        identity, seen_size, sealed_signature = self._identity, self._seen_size, self._sealed_signature
        records: list[SpoolRecord] = []
        retained_bytes = 0
        try:
            proof = self._retained_prefix
            if proof is None:
                durable = _checkpoint(self.root, self.producer_id, self.incarnation, self.max_header_bytes)
                if durable < self._durable_sequence or durable < sequence:
                    raise SpoolCorruption("durable checkpoint regressed")
                _, segments, _, _ = _inventory(self.root, self.max_disk_bytes, self.max_records)
                # A concurrent writer may be preparing the next checkpoint. Its
                # old published checkpoint remains the only authorization boundary.
                if durable > self.max_records:
                    raise SpoolCorruption("durable checkpoint exceeds record limit")
            else:
                durable, checkpoint_bytes = _checkpoint_with_bytes(
                    self.root, self.producer_id, self.incarnation, self.max_header_bytes)
                if checkpoint_bytes is None:
                    raise SpoolCorruption("retained reader checkpoint is missing")
                if (durable < proof.durable_sequence or durable < proof.base_sequence
                        or durable < self._durable_sequence or durable < sequence):
                    raise SpoolCorruption("retained durable checkpoint regressed")
                if (durable == proof.durable_sequence
                        and hashlib.sha256(checkpoint_bytes).hexdigest() != proof.checkpoint_sha256):
                    raise SpoolCorruption("retained checkpoint file hash changed")
                if durable > _MAX_ABSOLUTE:
                    raise SpoolCorruption("retained durable checkpoint exceeds its bound")
                if durable - proof.base_sequence > self.max_records:
                    raise SpoolCorruption("retained durable sequence exceeds the record limit")
                _, segments, _, _, _ = _inventory_retained(
                    self.root, self.max_disk_bytes, proof.first_retained_segment, self.max_records)
            self._durable_sequence = durable
            while sequence < durable and len(records) < max_records:
                if number > segments:
                    raise SpoolCorruption("durable prefix references a missing segment")
                path, sealed, opened = self._open_segment(number)
                reached_eof = False
                try:
                    stat = os.fstat(opened.fileno())
                    current_identity = (stat.st_dev, stat.st_ino)
                    if identity is not None and current_identity != identity:
                        raise SpoolCorruption("incremental reader segment identity changed")
                    if stat.st_size < seen_size or stat.st_size < offset:
                        raise SpoolCorruption("incremental reader segment shrank")
                    signature = (stat.st_size, stat.st_mtime_ns) if sealed else None
                    if sealed_signature is not None and signature != sealed_signature:
                        raise SpoolCorruption("immutable sealed segment changed")
                    identity, seen_size = current_identity, stat.st_size
                    sealed_signature = signature
                    iterator = _frames(path, number, self.producer_id, self.incarnation,
                                       self.max_frame_bytes, self.max_header_bytes,
                                       allow_partial=False, start_offset=offset, opened_handle=opened)
                    try:
                        while sequence < durable and len(records) < max_records:
                            record = next(iterator, None)
                            if record is None:
                                reached_eof = True
                                break
                            if isinstance(record, tuple) or record.cursor.sequence != sequence + 1:
                                raise SpoolCorruption("non-contiguous incremental durable prefix")
                            if retained_bytes + record.frame_bytes > max_bytes:
                                if not records:
                                    raise SpoolRotateRequired("batch byte budget cannot hold first frame")
                                break
                            records.append(record)
                            retained_bytes += record.frame_bytes
                            sequence = record.cursor.sequence
                            offset = record.offset + record.frame_bytes
                    finally:
                        iterator.close()
                finally:
                    opened.close()
                if reached_eof:
                    # Only a sealed file can have a successor. An active tail
                    # before the durable boundary is not a harmless EOF.
                    if not sealed:
                        renamed = self.root / f"{number:020d}.sealed"
                        if not renamed.exists() or (renamed.stat().st_dev, renamed.stat().st_ino) != identity:
                            raise SpoolCorruption("active file ends before durable boundary")
                    number += 1; offset = 0
                    identity = None; seen_size = 0; sealed_signature = None
                    continue
                break
            if proof is not None and records:
                # Bounded chunked anchor verification precedes publication of
                # every nonempty retained batch. This detects in-place edits and
                # replacement even when the frames themselves still checksum.
                self._verify_anchor()
        except SpoolRotateRequired:
            raise
        except SpoolError as exc:
            self._error = exc
            raise
        except OSError as exc:
            self._error = SpoolCorruption("incremental spool read failed")
            raise self._error from exc
        self._sequence, self._segment_id, self._offset = sequence, number, offset
        self._identity, self._seen_size, self._sealed_signature = identity, seen_size, sealed_signature
        return tuple(records)


class MarketSpool:
    """One producer/incarnation per directory; limits have no implicit defaults."""

    def __init__(self, root: Path, producer_id: str, incarnation: str, *, max_disk_bytes: int,
                 max_records: int, max_frame_bytes: int, max_header_bytes: int,
                 segment_max_bytes: int, segment_max_records: int, segment_max_age_seconds: float) -> None:
        self.root = Path(root)
        self.producer_id = _identity(producer_id, "producer_id")
        self.incarnation = _identity(incarnation, "incarnation")
        for name, value in (("max_disk_bytes", max_disk_bytes), ("max_records", max_records),
                            ("max_frame_bytes", max_frame_bytes), ("max_header_bytes", max_header_bytes),
                            ("segment_max_bytes", segment_max_bytes), ("segment_max_records", segment_max_records)):
            setattr(self, name, _positive(value, name))
        if (not isinstance(segment_max_age_seconds, (int, float)) or isinstance(segment_max_age_seconds, bool)
                or not 0 < segment_max_age_seconds < float("inf")):
            raise ValueError("segment_max_age_seconds must be finite and positive")
        if (max_frame_bytes > 0xFFFFFFFF or max_header_bytes + _PREFIX.size >= max_frame_bytes
                or segment_max_bytes < max_frame_bytes):
            raise ValueError("frame/header/segment byte limits are inconsistent")
        self.segment_max_age_seconds = float(segment_max_age_seconds)
        self._checkpoint_reserve = max_header_bytes + 512
        if max_disk_bytes < max_frame_bytes + 2 * self._checkpoint_reserve + 1:
            raise ValueError("disk limit cannot hold one frame and checkpoint staging")
        self._thread = threading.get_ident()
        self._file = self._lock = None
        self._closed = False
        self._error: SpoolError | None = None
        self.last_receipt: AppendReceipt | None = None
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self._acquire_lock()
            report = self.scan()
            if report.uncertain:
                receipt = AppendReceipt(SpoolCursor(producer_id, incarnation, report.last_sequence),
                                        "UNCERTAIN", True, report.uncertainty_reason)
                raise SpoolError("spool restart requires explicit uncertain-tail recovery", receipt)
            self._sequence = report.last_sequence
            self._durable_sequence = report.durable_sequence
            self._segment_id = report.segment_count
            self._active_records = report.active_records
            self._active_bytes = report.active_bytes
            self._disk_bytes = report.disk_bytes
            self._active_since = time.monotonic()
            if report.active_segment_id is not None:
                self._file = (self.root / f"{self._segment_id:020d}.active").open("ab", buffering=0)
                # Restart cannot establish the previous monotonic age. Require
                # explicit seal instead of extending that segment's age.
                self._active_since = float("-inf")
        except BaseException:
            self._release_lock()
            raise

    def _acquire_lock(self) -> None:
        self._lock = (self.root / "writer.lock").open("a+b")
        self._lock.seek(0, os.SEEK_END)
        if self._lock.tell() == 0:
            self._lock.write(b"0"); self._lock.flush()
        self._lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SpoolError("spool already has a writer") from exc

    def _release_lock(self) -> None:
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def _check(self) -> None:
        if threading.get_ident() != self._thread:
            raise SpoolError("spool writer called from a different thread")
        if self._closed:
            raise SpoolError("spool writer is closed")
        if self._error is not None:
            raise SpoolError("spool writer is fail-closed", self.last_receipt) from self._error

    def _fail(self, reason: str, sequence: int, *, uncertain: bool) -> SpoolError:
        receipt = AppendReceipt(SpoolCursor(self.producer_id, self.incarnation, sequence),
                                "UNCERTAIN" if uncertain else "REJECTED", uncertain, reason)
        self.last_receipt = receipt
        self._error = SpoolError(reason, receipt)
        return self._error

    @property
    def appended_cursor(self) -> SpoolCursor:
        return SpoolCursor(self.producer_id, self.incarnation, self._sequence)

    @property
    def durable_cursor(self) -> SpoolCursor:
        return SpoolCursor(self.producer_id, self.incarnation, self._durable_sequence)

    @property
    def directory_fsync_supported(self) -> bool:
        return os.name != "nt"

    def scan(self) -> SpoolScan:
        return scan_spool(self.root, producer_id=self.producer_id, incarnation=self.incarnation,
                          max_disk_bytes=self.max_disk_bytes, max_records=self.max_records,
                          max_frame_bytes=self.max_frame_bytes, max_header_bytes=self.max_header_bytes)

    def append(self, payload: bytes) -> AppendReceipt:
        self._check()
        sequence = self._sequence + 1
        if not isinstance(payload, bytes) or not payload:
            raise self._fail("payload must be nonempty owned bytes", sequence, uncertain=False)
        header = _json({"producer_id": self.producer_id, "incarnation": self.incarnation, "sequence": sequence})
        size = _PREFIX.size + len(header) + len(payload)
        if len(header) > self.max_header_bytes or size > self.max_frame_bytes:
            raise self._fail("spool frame byte limit exceeded", sequence, uncertain=False)
        if self._sequence >= self.max_records:
            raise self._fail("spool record limit exceeded", sequence, uncertain=False)
        if self._disk_bytes + size + self._checkpoint_reserve > self.max_disk_bytes:
            raise self._fail("spool disk byte limit exceeded", sequence, uncertain=False)
        if self._file is not None and (self._active_records >= self.segment_max_records
                or self._active_bytes + size > self.segment_max_bytes
                or time.monotonic() - self._active_since >= self.segment_max_age_seconds):
            raise SpoolRotateRequired("explicit seal required before append")
        frame = _PREFIX.pack(_MAGIC, len(header), len(payload), hashlib.sha256(header + payload).digest()) + header + payload
        try:
            if self._file is None:
                self._segment_id += 1
                self._file = (self.root / f"{self._segment_id:020d}.active").open("xb", buffering=0)
                self._active_since = time.monotonic()
            view = memoryview(frame)
            while view:
                written = self._file.write(view)
                if not written:
                    raise OSError("spool write made no progress")
                view = view[written:]
            self._sequence = sequence
            self._active_records += 1
            self._active_bytes += size
            self._disk_bytes += size
        except BaseException as exc:
            raise self._fail("spool append I/O uncertain", sequence, uncertain=True) from exc
        self.last_receipt = AppendReceipt(self.appended_cursor, "APPENDED")
        return self.last_receipt

    def _write_checkpoint(self) -> None:
        payload = _json({"version": 1, "producer_id": self.producer_id, "incarnation": self.incarnation,
                         "durable_sequence": self._sequence})
        temporary = self.root / _CHECKPOINT_TEMP
        with temporary.open("xb", buffering=0) as handle:
            view = memoryview(payload)
            while view:
                written = handle.write(view)
                if not written:
                    raise OSError("checkpoint write made no progress")
                view = view[written:]
            os.fsync(handle.fileno())
        os.replace(temporary, self.root / _CHECKPOINT)
        _sync_directory(self.root)

    def flush(self) -> SpoolCursor:
        self._check()
        try:
            if self._file is not None:
                self._file.flush()
                os.fsync(self._file.fileno())
            self._write_checkpoint()
            self._durable_sequence = self._sequence
            self._disk_bytes = _inventory(self.root, self.max_disk_bytes, self.max_records)[0]
        except BaseException as exc:
            raise self._fail("spool flush/checkpoint durability uncertain", self._sequence, uncertain=True) from exc
        return self.durable_cursor

    def seal(self) -> SpoolCursor:
        self._check()
        if self._file is None:
            return self.flush()
        self.flush()
        try:
            self._file.close(); self._file = None
            os.replace(self.root / f"{self._segment_id:020d}.active", self.root / f"{self._segment_id:020d}.sealed")
            _sync_directory(self.root)
            self._active_records = self._active_bytes = 0
        except BaseException as exc:
            raise self._fail("spool seal durability uncertain", self._sequence, uncertain=True) from exc
        return self.durable_cursor

    def iter_records(self, after: SpoolCursor | None = None, through: SpoolCursor | None = None,
                     *, durable_only: bool = True) -> Iterator[SpoolRecord]:
        return read_spool_records(self.root, producer_id=self.producer_id, incarnation=self.incarnation,
                                  max_disk_bytes=self.max_disk_bytes, max_records=self.max_records,
                                  max_frame_bytes=self.max_frame_bytes, max_header_bytes=self.max_header_bytes,
                                  after=after, through=through, durable_only=durable_only)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._check()
            self.seal()
        finally:
            try:
                if self._file is not None:
                    self._file.close()
            finally:
                self._file = None
                self._closed = True
                self._release_lock()

    def gc_disabled(self) -> dict[str, int | str]:
        return {"deleted": 0, "reason": "verified_consumer_and_archive_proof_required"}
