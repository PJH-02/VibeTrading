"""Read-only documentation hygiene manifest verifier.

The verifier checks KEEP/INDEX_UPDATE/ARCHIVE_GIT retention decisions against
the current worktree, the recorded Git base and an optional private backup
root.  It never writes to disk and reports only a path and a reason per error.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
ACTIONS = ("KEEP", "ARCHIVE_GIT", "INDEX_UPDATE")
AUTHORITY_PATHS = (
    "docs/final/01_FINAL_PLAN.md",
    "docs/final/02_DEVELOPER_TICKETS.md",
    "docs/final/03_ARCHITECTURE_REQUIREMENTS.md",
    "docs/final/04_COMPLETED_MANIFEST.json",
    "docs/final/05_TASK_STATUS.json",
)

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE = re.compile(r"^[A-Za-z]:")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _has_hex(value, pattern: re.Pattern) -> bool:
    return isinstance(value, str) and pattern.match(value) is not None


def _is_safe_relpath(value) -> bool:
    """Accept only canonical, non-escaping repository-relative POSIX paths."""
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or value.startswith("\\") or "\\" in value:
        return False
    if _DRIVE.match(value) or "\x00" in value:
        return False
    if any(ord(char) < 32 for char in value):
        return False
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return PurePosixPath(value).as_posix() == value


def _first_symlink(base: Path, relative: str) -> Path | None:
    current = base
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            return current
    return None


def _read_regular(base: Path, relative: str) -> tuple[bytes | None, str | None]:
    """Read a file under *base* without following any symlink component."""
    if _first_symlink(base, relative) is not None:
        return None, "symlink rejected"
    path = base.joinpath(*relative.split("/"))
    try:
        if not path.exists():
            return None, "file missing"
        if not path.is_file():
            return None, "not a regular file"
        return path.read_bytes(), None
    except OSError:
        return None, "file unreadable"


def _run_git(root: Path, args: list[str]):
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _base_reachable(root: Path, base_sha: str) -> bool:
    proc = _run_git(root, ["merge-base", "--is-ancestor", base_sha, "HEAD"])
    return proc is not None and proc.returncode == 0


def _archive_snapshot(root: Path, base_sha: str, paths: list[str]):
    """Read the index and original blobs in two Git processes for any size batch."""
    tracked = _run_git(root, ["ls-files", "-z"])
    if tracked is None or tracked.returncode:
        return None, {}
    names = set(tracked.stdout.decode("utf-8", errors="surrogateescape").split("\0"))
    if not paths:
        return names, {}
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "cat-file", "--batch"],
            input="".join(f"{base_sha}:{path}\n" for path in paths).encode(),
            capture_output=True, check=False, timeout=30,
        )
        if proc.returncode:
            return names, {}
        stream = io.BytesIO(proc.stdout)
        blobs = {}
        for path in paths:
            header = stream.readline().split()
            if len(header) == 2 and header[1] == b"missing":
                continue
            if len(header) != 3 or header[1] != b"blob":
                return names, {}
            size = int(header[2])
            if size < 0:
                return names, {}
            payload = stream.read(size)
            if len(payload) != size or stream.read(1) != b"\n":
                return names, {}
            blobs[path] = payload
        return names, blobs
    except (OSError, subprocess.SubprocessError, ValueError):
        return names, {}


def _new_counts() -> dict:
    return {
        "entries": 0,
        "keep": 0,
        "archive_git": 0,
        "index_update": 0,
        "protected_receipts": 0,
        "authority_paths": 0,
        "passed": 0,
        "failed": 0,
        "errors": 0,
    }


def _finish(errors: list[dict], counts: dict) -> dict:
    counts["errors"] = len(errors)
    return {
        "ok": not errors,
        "valid": not errors,
        "counts": counts,
        "errors": errors,
    }


def _verify_backup(
    backup_root: Path | None,
    backup_available: bool,
    backup_path: str,
    expected_sha: str,
    expected_size: int,
    label: str,
    add,
) -> None:
    if backup_root is None:
        add(label, "backup root required")
        return
    if not backup_available:
        add(label, "backup root unavailable")
        return
    data, error = _read_regular(backup_root, backup_path)
    if error is not None:
        add(label, f"backup {error}")
        return
    assert data is not None
    if len(data) != expected_size:
        add(label, "backup size mismatch")
    if hashlib.sha256(data).hexdigest() != expected_sha:
        add(label, "backup sha256 mismatch")


def verify(root: Path, manifest: dict, backup_root: Path | None = None) -> dict:
    """Verify a documentation retention manifest without writing anything."""
    errors: list[dict] = []
    counts = _new_counts()

    def add(path, reason: str) -> None:
        errors.append({"path": path if isinstance(path, str) else "", "reason": reason})

    if not isinstance(root, Path):
        add("", "root must be a Path")
        return _finish(errors, counts)
    if any(part.is_symlink() for part in (root, *root.parents)):
        add("", "root symlink rejected")
        return _finish(errors, counts)
    if not root.is_dir():
        add("", "root is not an existing directory")
        return _finish(errors, counts)

    backup_available = False
    if backup_root is not None:
        if not isinstance(backup_root, Path):
            add("", "backup root must be a Path")
        elif any(part.is_symlink() for part in (backup_root, *backup_root.parents)):
            add("", "backup root symlink rejected")
        elif not backup_root.is_dir():
            add("", "backup root is not an existing directory")
        else:
            backup_available = True

    if not isinstance(manifest, dict):
        add("", "manifest must be a JSON object")
        return _finish(errors, counts)

    if not (_is_int(manifest.get("schema_version")) and manifest.get("schema_version") == SCHEMA_VERSION):
        add("", "schema_version must be 1")

    base_sha = manifest.get("base_sha")
    base_valid = _has_hex(base_sha, _HEX40)
    if not base_valid:
        add("", "base_sha must be lowercase 40 hex")
    base_reachable = False
    if base_valid:
        base_reachable = _base_reachable(root, base_sha)
        if not base_reachable:
            add("", "base commit unreachable")

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        add("", "entries must be a list")
        entries = []

    receipts = manifest.get("protected_receipts")
    if not isinstance(receipts, list):
        add("", "protected_receipts must be a list")
        receipts = []
    counts["protected_receipts"] = len(receipts)

    authorities = manifest.get("authority_paths")
    if not isinstance(authorities, list):
        add("", "authority_paths must be a list")
    else:
        counts["authority_paths"] = len(authorities)
        authorities_are_strings = all(isinstance(item, str) for item in authorities)
        if (
            len(authorities) != len(AUTHORITY_PATHS)
            or not authorities_are_strings
            or set(authorities) != set(AUTHORITY_PATHS)
        ):
            add("", "authority_paths must be exactly the five final docs")

    archive_paths = list(dict.fromkeys(
        entry["path"] for entry in entries
        if isinstance(entry, dict) and entry.get("action") == "ARCHIVE_GIT"
        and _is_safe_relpath(entry.get("path"))
    ))
    tracked_paths, base_blobs = _archive_snapshot(root, base_sha, archive_paths) if base_reachable else (None, {})
    seen_paths: set[str] = set()
    for raw_entry in entries:
        counts["entries"] += 1
        errors_before_entry = len(errors)

        if not isinstance(raw_entry, dict):
            add("", "entry must be an object")
            counts["failed"] += 1
            continue

        path = raw_entry.get("path")
        label = path if isinstance(path, str) else ""
        path_valid = _is_safe_relpath(path)
        if not path_valid:
            add(label, "unsafe or invalid relative path")
        elif path in seen_paths:
            add(path, "duplicate manifest path")
            path_valid = False
        else:
            seen_paths.add(path)

        action = raw_entry.get("action")
        action_valid = action in ACTIONS
        if not action_valid:
            add(label, "action must be KEEP, ARCHIVE_GIT or INDEX_UPDATE")
        elif action == "KEEP":
            counts["keep"] += 1
        elif action == "ARCHIVE_GIT":
            counts["archive_git"] += 1
        else:
            counts["index_update"] += 1

        sha256 = raw_entry.get("sha256")
        sha_valid = _has_hex(sha256, _HEX64)
        if not sha_valid:
            add(label, "sha256 must be lowercase 64 hex")

        size = raw_entry.get("bytes")
        size_valid = _is_int(size) and size >= 0
        if not size_valid:
            add(label, "bytes must be a non-negative integer")

        reason = raw_entry.get("reason")
        if not (isinstance(reason, str) and reason.strip()):
            add(label, "reason must be a non-empty string")

        callers = raw_entry.get("callers")
        if not (
            isinstance(callers, list)
            and callers
            and all(isinstance(caller, str) and caller.strip() for caller in callers)
        ):
            add(label, "callers must be a non-empty list of non-empty strings")

        expiry = raw_entry.get("expiry")
        if not (isinstance(expiry, str) and expiry.strip()):
            add(label, "expiry must be a non-empty string")

        backup_path = raw_entry.get("backup_path")
        backup_path_valid = True
        if action == "ARCHIVE_GIT":
            if backup_path is None:
                add(label, "backup_path required for ARCHIVE_GIT")
                backup_path_valid = False
            elif not _is_safe_relpath(backup_path):
                add(label, "backup_path must be a safe relative POSIX path")
                backup_path_valid = False
        elif backup_path is not None and not _is_safe_relpath(backup_path):
            add(label, "backup_path must be a safe relative POSIX path")
            backup_path_valid = False

        current_sha256 = raw_entry.get("current_sha256")
        current_valid = (
            _has_hex(current_sha256, _HEX64) if current_sha256 is not None else None
        )
        if action == "INDEX_UPDATE":
            if current_valid is not True:
                add(label, "current_sha256 required for INDEX_UPDATE")
        elif current_sha256 is not None and not current_valid:
            add(label, "current_sha256 must be lowercase 64 hex")

        essentials_valid = (
            path_valid
            and action_valid
            and sha_valid
            and size_valid
            and backup_path_valid
        )
        if not essentials_valid:
            counts["failed"] += 1
            continue

        if action == "KEEP":
            data, error = _read_regular(root, path)
            if error is not None:
                add(path, f"KEEP {error}")
            else:
                assert data is not None
                if len(data) != size:
                    add(path, "size mismatch")
                if hashlib.sha256(data).hexdigest() != sha256:
                    add(path, "sha256 mismatch")
        elif action == "INDEX_UPDATE":
            data, error = _read_regular(root, path)
            if error is not None:
                add(path, f"INDEX_UPDATE {error}")
            else:
                assert data is not None
                if current_valid is True and hashlib.sha256(data).hexdigest() != current_sha256:
                    add(path, "current sha256 mismatch")
            if backup_path is not None:
                _verify_backup(
                    backup_root,
                    backup_available,
                    backup_path,
                    sha256,
                    size,
                    path,
                    add,
                )
        else:
            tracked = path in tracked_paths if tracked_paths is not None else None
            if tracked is None:
                add(path, "git unavailable")
            elif tracked:
                add(path, "archive path still tracked")
            if not base_reachable:
                add(path, "base commit unreachable for blob check")
            else:
                blob = base_blobs.get(path)
                if blob is None:
                    add(path, "base blob missing")
                else:
                    if len(blob) != size:
                        add(path, "base blob size mismatch")
                    if hashlib.sha256(blob).hexdigest() != sha256:
                        add(path, "base blob sha256 mismatch")
            _verify_backup(
                backup_root,
                backup_available,
                backup_path,
                sha256,
                size,
                path,
                add,
            )

        if len(errors) == errors_before_entry:
            counts["passed"] += 1
        else:
            counts["failed"] += 1

    seen_receipt_paths: set[str] = set()
    for receipt in receipts:
        if not isinstance(receipt, dict):
            add("", "protected receipt must be an object")
            continue
        task_id = receipt.get("task_id")
        path = receipt.get("path")
        label = path if isinstance(path, str) else ""
        sha256 = receipt.get("sha256")

        valid = True
        if not (isinstance(task_id, str) and task_id.strip()):
            add(label, "receipt task_id must be a non-empty string")
            valid = False
        if not _is_safe_relpath(path):
            add(label, "receipt path must be a safe relative POSIX path")
            valid = False
        elif path in seen_receipt_paths:
            add(path, "duplicate protected receipt path")
            valid = False
        else:
            seen_receipt_paths.add(path)
        if not _has_hex(sha256, _HEX64):
            add(label, "receipt sha256 must be lowercase 64 hex")
            valid = False
        if not valid:
            continue

        data, error = _read_regular(root, path)
        if error is not None:
            add(path, f"protected receipt {error}")
        else:
            assert data is not None
            if hashlib.sha256(data).hexdigest() != sha256:
                add(path, "protected receipt sha256 mismatch")

    return _finish(errors, counts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path)
    args = parser.parse_args(argv)

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        errors = [{"path": str(args.manifest), "reason": "manifest unreadable or malformed JSON"}]
        summary = _finish(errors, _new_counts())
    else:
        try:
            summary = verify(args.root, manifest, args.backup_root)
        except Exception:
            errors = [{"path": str(args.root), "reason": "verifier failed safely"}]
            summary = _finish(errors, _new_counts())

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
