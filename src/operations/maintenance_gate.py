"""Cross-container gate for disk-intensive maintenance on the shared volume."""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def _try_lock(handle: Any) -> bool:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            # Locking byte zero does not require reading the file. Reading a
            # byte first races with another process that already holds this
            # lock and raises PermissionError on Windows.
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def claim_backup_slot_locked(data_root: Path, domain: str, cooldown_seconds: int) -> dict[str, Any]:
    """Reserve the shared full-backup cooldown while maintenance lock is held."""
    marker_path = data_root / ".vibetrading-last-full-backup.json"
    now = time.time()
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        last_started = float(marker.get("started_at", 0))
    except (OSError, ValueError, TypeError):
        last_started = 0.0
    if now - last_started < max(0, int(cooldown_seconds)):
        return {"acquired": False, "reason": "shared_backup_cooldown",
                "last_backup_started_at": last_started}
    temporary = marker_path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"domain": domain, "started_at": now}), encoding="utf-8")
    os.replace(temporary, marker_path)
    return {"acquired": True, "reason": "acquired"}


@contextmanager
def maintenance_lock(
    data_root: Path,
    *,
    backup_domain: str | None = None,
    backup_cooldown_seconds: int = 1800,
) -> Iterator[dict[str, Any]]:
    """Acquire the shared-volume lock without blocking storage or other work."""
    data_root.mkdir(parents=True, exist_ok=True)
    lock_path = data_root / ".vibetrading-maintenance.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        if not _try_lock(handle):
            yield {"acquired": False, "reason": "heavy_maintenance_busy"}
            return
        try:
            if backup_domain:
                slot = claim_backup_slot_locked(data_root, backup_domain, backup_cooldown_seconds)
                if not slot["acquired"]:
                    yield slot
                    return
            yield {"acquired": True, "reason": "acquired"}
        finally:
            _unlock(handle)
