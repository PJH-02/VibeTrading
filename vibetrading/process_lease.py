"""Cooperative process fencing on a shared local volume (not exchange fencing)."""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import stat

from .maintenance_gate import _try_lock, _unlock


class ProcessLease:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle = None
        self._pid = None
        self._lost = False

    @classmethod
    def account(cls, root: Path, venue: str, account: str, environment: str):
        """Fence the whole physical parent account, across products/subaccounts.

        Callers supply canonical physical identities, never credential/route aliases.
        Every cooperating process must use the same protected shared root.
        """
        if venue not in {'kis', 'binance', 'bitget', 'bybit', 'grvt', 'arcus', 'hyperliquid'}:
            raise ValueError('Noncanonical execution venue')
        if environment not in {'offline', 'paper', 'mock', 'real', 'testnet'}:
            raise ValueError('Noncanonical execution environment')
        if (not isinstance(account, str) or not account or len(account) > 200
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in account)):
            raise ValueError('Canonical physical parent account is required')
        root = Path(root)
        if not root.is_absolute() or root.resolve() != root:
            raise ValueError('Authority root must be absolute and free of symlinks')
        key = json.dumps([venue, account, environment], separators=(',', ':'), ensure_ascii=False)
        return cls(root / (hashlib.sha256(key.encode()).hexdigest() + '.lock'))

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
