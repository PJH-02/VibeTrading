"""Cooperative process fencing on a shared local volume (not exchange fencing)."""
from __future__ import annotations

from pathlib import Path

from .maintenance_gate import _try_lock, _unlock


class ProcessLease:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if not _try_lock(handle):
            handle.close()
            raise RuntimeError("another process owns " + self.path.name)
        self._handle = handle

    def close(self) -> None:
        if self._handle is not None:
            _unlock(self._handle)
            self._handle.close()
            self._handle = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.close()
