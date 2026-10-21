"""Local FULL-commit signer nonces; callers share one protected owner root.

This is signing state, not an execution permit or proof of venue acceptance.
All accounts/subaccounts using a signing wallet must share this store. A
different host/root is not fenced by a cooperative local process lease.
"""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3
import threading

from .process_lease import ProcessLease


class SignerNonceStore:
    durable_nonce_protocol = 1

    def __init__(self, owner_root: Path, *, environment: str, max_signers: int,
                 max_db_bytes: int, max_wal_bytes: int):
        if environment not in {"sandbox", "live"}:
            raise ValueError("Explicit sandbox/live nonce environment required")
        for name, value in (("max_signers", max_signers), ("max_db_bytes", max_db_bytes),
                            ("max_wal_bytes", max_wal_bytes)):
            if type(value) is not int or value <= 0:
                raise ValueError(name + " must be an explicit positive integer")
        if min(max_db_bytes, max_wal_bytes) < 32768:
            raise ValueError("Nonce byte budgets must cover SQLite metadata and one commit")
        self.environment, self.max_signers = environment, max_signers
        self.max_db_bytes, self.max_wal_bytes = max_db_bytes, max_wal_bytes
        self.path = Path(owner_root).resolve() / environment / "execution-nonces.sqlite3"
        self._thread, self._closed = threading.get_ident(), False
        self._lease = ProcessLease(self.path.with_suffix(".lock"))
        self._lease.acquire()
        try:
            self._db = sqlite3.connect(self.path, isolation_level=None, timeout=0)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA wal_autocheckpoint=1")
            self._db.execute("PRAGMA journal_size_limit=" + str(max_wal_bytes))
            page_size=self._db.execute("PRAGMA page_size").fetchone()[0]
            requested_pages=max_db_bytes//page_size
            actual_pages=self._db.execute("PRAGMA max_page_count=" + str(requested_pages)).fetchone()[0]
            if not requested_pages or actual_pages>requested_pages:
                raise ValueError("Existing nonce database exceeds its page budget")
            # One small row/index commit needs finite staging headroom. This
            # is an application guard, not an OS filesystem quota.
            self._wal_headroom=16*(page_size+24)+32
            if max_wal_bytes<self._wal_headroom:
                raise ValueError("Nonce WAL budget lacks commit staging headroom")
            self._db.execute("CREATE TABLE IF NOT EXISTS nonce_metadata(environment TEXT PRIMARY KEY)")
            self._db.execute("CREATE TABLE IF NOT EXISTS signer_nonces(signer TEXT PRIMARY KEY,nonce INTEGER NOT NULL)")
            row = self._db.execute("SELECT environment FROM nonce_metadata").fetchall()
            if row and row != [(environment,)]:
                raise ValueError("Persisted nonce environment differs")
            self._db.execute("INSERT OR IGNORE INTO nonce_metadata VALUES(?)", (environment,))
            self._bounds()
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            self._lease.close()
            raise

    def _check(self):
        if self._closed or threading.get_ident() != self._thread:
            raise RuntimeError("Nonce store requires its open owning thread")

    def _bounds(self):
        for path, limit in ((self.path, self.max_db_bytes),
                            (Path(str(self.path) + "-wal"), self.max_wal_bytes)):
            if path.exists() and path.stat().st_size > limit:
                raise RuntimeError("Nonce store byte budget exhausted")
        if self._db.execute("SELECT COUNT(*) FROM signer_nonces").fetchone()[0] > self.max_signers:
            raise RuntimeError("Nonce signer capacity exhausted")

    def reserve(self, signer_key: str, now_ms: int) -> int:
        """Commit before returning, including on later signing/dispatch failure.

        No rollback or recycling follows a rejected/unknown order. Account and
        subaccount are deliberately absent from this wallet/environment key.
        """
        self._check()
        if not isinstance(signer_key, str) or not re.fullmatch(
                re.escape(self.environment) + r":0x[0-9a-f]{40}", signer_key):
            raise ValueError("Nonce key must be canonical environment:signer-wallet")
        if type(now_ms) is not int or not 0 <= now_ms <= 2**63 - 86_400_001:
            raise ValueError("Explicit bounded epoch milliseconds required")
        self._bounds()
        busy,_,_=self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise RuntimeError("Nonce WAL checkpoint is pinned; new signing is blocked")
        wal=Path(str(self.path)+"-wal")
        if wal.exists() and wal.stat().st_size+self._wal_headroom>self.max_wal_bytes:
            raise RuntimeError("Nonce WAL commit staging capacity exhausted")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            old = self._db.execute("SELECT nonce FROM signer_nonces WHERE signer=?", (signer_key,)).fetchone()
            if old is None and self._db.execute("SELECT COUNT(*) FROM signer_nonces").fetchone()[0] >= self.max_signers:
                raise RuntimeError("Nonce signer capacity exhausted")
            nonce = max(now_ms, old[0] + 1 if old else now_ms)
            if nonce >= now_ms + 86_400_000:
                raise RuntimeError("Nonce clock moved outside the venue acceptance window")
            self._db.execute("INSERT INTO signer_nonces VALUES(?,?) ON CONFLICT(signer) DO UPDATE SET nonce=excluded.nonce",
                             (signer_key, nonce))
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        # A post-commit capacity failure consumes the nonce; it never returns
        # an uncommitted value or rewinds the persisted signer watermark.
        self._bounds()
        return nonce

    def close(self):
        if self._closed:
            return
        self._check()
        try:
            self._db.close()
        finally:
            self._closed = True
            self._lease.close()

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *args):
        self.close()
