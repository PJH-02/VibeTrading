from __future__ import annotations

import time
import uuid
import os
import threading
from collections import deque
from dataclasses import dataclass, field
_STAMP_CAPACITY=8192
_STAMP_PID=os.getpid()
_STAMP_LOCK=threading.Lock()
_ISSUED=deque()
_ISSUED_SET=set()


def _reset_stamp_registry():
    global _STAMP_PID,_STAMP_LOCK,_ISSUED,_ISSUED_SET
    _STAMP_PID=os.getpid();_STAMP_LOCK=threading.Lock();_ISSUED=deque();_ISSUED_SET=set()


if hasattr(os,"register_at_fork"):
    os.register_at_fork(after_in_child=_reset_stamp_registry)


def is_local_receive_stamp(stamp: int | None, *, consume: bool=True) -> bool:
    """True only for a recent receive stamp issued by this process."""
    if os.getpid()!=_STAMP_PID:_reset_stamp_registry()
    if type(stamp) is not int or not _STAMP_LOCK.acquire(False):return False
    try:
        found=stamp in _ISSUED_SET
        if found and consume:_ISSUED_SET.discard(stamp)
        return found
    finally:_STAMP_LOCK.release()


MINUTE_NS = 60_000_000_000


def next_utc_minute_ns(now_ns: int | None = None) -> int:
    """First exact UTC minute boundary strictly after readiness."""
    value = time.time_ns() if now_ns is None else int(now_ns)
    return ((value // MINUTE_NS) + 1) * MINUTE_NS


@dataclass
class CollectionContext:
    provider: str
    collection_start_ts_ns: int
    collector_run_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def new_connection_id(self) -> str:
        return f"{self.provider}-{uuid.uuid4().hex}"

    def accepts(self, wallclock_ts_ns: int) -> bool:
        return int(wallclock_ts_ns) >= self.collection_start_ts_ns


def receipt_clock() -> tuple[int, int]:
    """Capture UTC wall clock and monotonic ordering at frame receipt."""
    wall, monotonic = time.time_ns(), time.monotonic_ns()
    if os.getpid()!=_STAMP_PID:_reset_stamp_registry()
    if _STAMP_LOCK.acquire(False):
        try:
            _ISSUED.append(monotonic);_ISSUED_SET.add(monotonic)
            if len(_ISSUED)>_STAMP_CAPACITY:_ISSUED_SET.discard(_ISSUED.popleft())
        finally:_STAMP_LOCK.release()
    return wall, monotonic
