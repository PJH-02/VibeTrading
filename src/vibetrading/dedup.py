from __future__ import annotations

from collections import OrderedDict
from time import monotonic
from typing import Generic, TypeVar


T = TypeVar("T")


class BoundedDedup(Generic[T]):
    """Bounded, TTL-aware de-duplication cache for long-running workers."""

    def __init__(self, max_entries: int = 100_000, ttl_seconds: float = 86_400.0) -> None:
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("max_entries and ttl_seconds must be positive")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, float] = OrderedDict()

    def add_if_new(self, key: str) -> bool:
        now = monotonic()
        self._purge(now)
        if key in self._items:
            self._items.move_to_end(key)
            return False
        self._items[key] = now
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)
        return True

    def __contains__(self, key: str) -> bool:
        self._purge(monotonic())
        return key in self._items

    def __len__(self) -> int:
        self._purge(monotonic())
        return len(self._items)

    def _purge(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        while self._items:
            _, timestamp = next(iter(self._items.items()))
            if timestamp >= cutoff:
                break
            self._items.popitem(last=False)

