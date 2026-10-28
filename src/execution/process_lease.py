"""Cooperative process fencing on a shared local volume (not exchange fencing)."""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import stat

from operations.maintenance_gate import _try_lock, _unlock


# Route vocabularies and the older lease vocabulary name the same physical
# environment.  Normalize once here so old/new owners fence one another.
_ENVIRONMENT_ALIASES = {
    "sandbox": "testnet",
    "live": "real",
    "mock2": "mock",
}
_CANONICAL_ENVIRONMENTS = frozenset({"offline", "paper", "mock", "real", "testnet"})
_CANONICAL_VENUES = frozenset({
    "kis", "kiwoom", "binance", "bitget", "bybit", "grvt", "arcus", "hyperliquid",
})


def normalize_route_environment(value: str) -> str:
    """Map runtime route labels to the strict legacy lease vocabulary."""
    if not isinstance(value, str):
        raise ValueError("Route execution environment is required")
    return _ENVIRONMENT_ALIASES.get(value.casefold(), value.casefold())


def normalize_environment(value: str) -> str:
    """Compatibility alias used by route validation; not by the legacy lease API."""
    return normalize_route_environment(value)


class ProcessLease:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle = None
        self._pid = None
        self._lost = False

    # Explicitly registered extension venues (e.g. a test-only dummy module)
    # may be added without weakening the unknown-venue guard globally.
    _extension_venues: set[str] = set()

    @classmethod
    def register_extension_venue(cls, venue: str) -> str:
        if (not isinstance(venue, str) or not venue.strip() or len(venue) > 64
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in venue)):
            raise ValueError('Extension venue requires a bounded explicit identity')
        cls._extension_venues.add(venue.casefold())
        return venue.casefold()

    @classmethod
    def account(cls, root: Path, venue: str, account: str, environment: str):
        """Fence the whole physical parent account, across products/subaccounts.

        This legacy entrypoint deliberately accepts only its exact canonical
        lowercase vocabulary. Route aliases belong at the native ledger seam.
        Every cooperating process must use the same protected shared root.
        """
        if venue not in _CANONICAL_VENUES and venue not in cls._extension_venues:
            raise ValueError('Noncanonical execution venue')
        if environment not in _CANONICAL_ENVIRONMENTS:
            raise ValueError('Noncanonical execution environment')
        if (not isinstance(account, str) or not account or len(account) > 200
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in account)):
            raise ValueError('Canonical physical parent account is required')
        root = Path(root)
        if not root.is_absolute() or root.resolve() != root:
            raise ValueError('Authority root must be absolute and free of symlinks')
        key = json.dumps([venue, account, environment], separators=(',', ':'), ensure_ascii=False)
        return cls(root / (hashlib.sha256(key.encode()).hexdigest() + '.lock'))

    @classmethod
    def account_for_route(cls, root: Path, venue: str, account: str, environment: str):
        """Native execution seam: normalize route environment, then use strict fencing."""
        return cls.account(root, venue, account, normalize_route_environment(environment))

    def acquire(self) -> None:
        if self._handle is not None:
            self.check()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent.resolve() != self.path.parent.absolute():
            raise ValueError('Lease parent cannot contain symlinks')
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Lease must be a regular file')
            handle = os.fdopen(fd, 'a+b')
        except BaseException:
            os.close(fd)
            raise
        try:
            if not _try_lock(handle):
                raise RuntimeError("another process owns " + self.path.name)
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        self._pid = os.getpid()
        self._lost = False
        try:
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self) -> None:
        if self._handle is None or self._pid != os.getpid():
            raise RuntimeError('Process lease is not owned by this process')
        if self._lost:
            raise RuntimeError('Process lease identity was lost')
        try:
            held = os.fstat(self._handle.fileno())
            current = self.path.stat(follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                raise OSError('lease replaced')
        except OSError as exc:
            self._lost = True
            raise RuntimeError('Process lease identity was lost') from exc

    def close(self) -> None:
        if self._handle is not None:
            if self._pid == os.getpid():
                _unlock(self._handle)
            self._handle.close()
            self._handle = None
            self._pid = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.close()
