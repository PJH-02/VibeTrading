from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


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
    return time.time_ns(), time.monotonic_ns()
