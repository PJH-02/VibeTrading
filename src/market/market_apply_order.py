"""Bounded proof of a single owner's market apply order; no guessed ordering."""
from dataclasses import dataclass
import asyncio
import hashlib
import json

from feeds.feed_recovery import EngineCursor
from vibetrading.local_transport import AppliedReceipt
from storage.market_checkpoint import _canonical
from storage.market_spool import SpoolCursor, SpoolReader, scan_spool
from vibetrading.local_observation import LocalObservationBus


def order_anchor(payload):
    return ['vibetrading-apply-order', 1, hashlib.sha256(payload).hexdigest()]


def order_record(frame):
    c = frame.cursor
    return [c.producer, c.incarnation, c.sequence, frame.digest]


class ApplyOrderWriter(LocalObservationBus):
    """Raw order arrays using the existing bounded single-thread spool lifecycle."""

    async def _connect(self):
        self._loop = asyncio.get_running_loop()
        self._last_admission = None
        await super()._connect()
        if self._baseline_sequence:
            raise RuntimeError('apply order writer requires an empty journal')

    @property
    def appended_cursor(self):
        return EngineCursor(self.producer,self.incarnation,self._baseline_sequence+self._accepted)

    @property
    def max_frame_bytes(self):
        return self.budgets.max_frame_bytes

    def append(self, payload):
        if (asyncio.get_running_loop() is not getattr(self,'_loop',None)
                or getattr(self,'_claimed_engine',None) is None):
            raise RuntimeError('apply order writer requires its owner loop')
        self._last_admission = self._admit_payload(payload)
        return self._last_admission

    def claim(self, engine):
        self._check()
        if (not self._ready or self.appended_cursor.sequence or getattr(self,'_claimed_engine',None) is not None
                or asyncio.get_running_loop() is not self._loop):
            raise RuntimeError('apply order writer is not an unused owner-loop journal')
        self._claimed_engine = engine
        self.on_invalid = engine.fail_order_journal

    async def flush(self, *, timeout_seconds=5):
        self._check()
        admission = self._last_admission
        if admission is None:
            return self.durable_cursor
        receipt = await self.wait_durable(admission,timeout_seconds=timeout_seconds)
        self._check()
        return receipt.cursor


@dataclass(frozen=True, init=False)
class ApplyOrder:
    checkpoint_sha256: str
    journal_high: SpoolCursor
    receipts: tuple[AppliedReceipt, ...]

    def __init__(self, *args, **kwargs):
        raise TypeError('apply order must be read from a durable journal')

    @classmethod
    def read(cls, reader: SpoolReader, *, max_bytes: int):
        """Freeze a durable endpoint; spool checks framing, identity and ordinals."""
        if type(reader) is not SpoolReader or reader.cursor.sequence != 0 or type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError('apply order requires a fresh reader and finite byte budget')
        report = scan_spool(reader.root, producer_id=reader.producer_id, incarnation=reader.incarnation,
            **{k: getattr(reader, k) for k in ('max_disk_bytes', 'max_records', 'max_frame_bytes', 'max_header_bytes')})
        if report.uncertain:
            raise ValueError('apply order tail is uncertain')
        high = reader.durable_cursor
        if high.sequence < 1:
            raise ValueError('apply order has no durable anchor')
        used, anchor, receipts = 0, None, []
        while reader.cursor.sequence < high.sequence:
            rows = reader.read_batch(max_records=1, max_bytes=reader.max_frame_bytes)
            if not rows:
                raise ValueError('apply order durable prefix incomplete')
            raw = rows[0].payload
            used += len(raw)
            if used > max_bytes:
                raise ValueError('apply order byte budget exceeded')
            data = json.loads(raw)
            if _canonical(data, max_bytes) != raw:
                raise ValueError('apply order noncanonical record')
            if anchor is None:
                if (type(data) is not list or len(data) != 3 or data[:2] != ['vibetrading-apply-order', 1]
                        or type(data[1]) is not int):
                    raise ValueError('apply order invalid anchor')
                anchor = data[2]
            else:
                if (type(data) is not list or len(data) != 4
                        or any(type(s) is not str or not 0 < len(s.encode()) <= 128 for s in data[:2])
                        or type(data[2]) is not int or data[2] <= 0):
                    raise ValueError('apply order invalid cursor')
                receipts.append(AppliedReceipt(EngineCursor(*data[:3]), data[3]))
            digest = data[-1]
            if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('apply order invalid digest')
        proof = object.__new__(cls)
        for name, value in (('checkpoint_sha256', anchor), ('journal_high', high), ('receipts', tuple(receipts))):
            object.__setattr__(proof, name, value)
        return proof

    def validate(self, payload, handoffs):
        if (self.checkpoint_sha256 != hashlib.sha256(payload).hexdigest()
                or self.journal_high.sequence != len(self.receipts) + 1):
            raise ValueError('apply order checkpoint or endpoint mismatch')
        cursors = {c.producer: c for c, _ in handoffs}
        highs = {h.producer: h for _, h in handoffs}
        for receipt in self.receipts:
            c = receipt.cursor
            previous = cursors.get(c.producer)
            if (previous is None or c.incarnation != previous.incarnation
                    or c.sequence != previous.sequence + 1 or c.sequence > highs[c.producer].sequence):
                raise ValueError('apply order missing, extra or nonconsecutive cursor')
            cursors[c.producer] = c
        if cursors != highs:
            raise ValueError('apply order does not cover frozen highwaters')
        return self.receipts
