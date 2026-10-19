"""Validate explicit deployment mount contracts before creating data paths."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re


def linux_mounts(text: str) -> dict[str, dict[str, str]]:
    result = {}
    for line in text.splitlines():
        left, separator, right = line.partition(" - ")
        fields, details = left.split(), right.split()
        if not separator or len(fields) < 6 or len(details) < 2:
            continue
        unescape = lambda value: re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)
        result[unescape(fields[4])] = {"device":fields[2], "filesystem":details[0],
                                       "source":unescape(details[1]), "options":fields[5]}
    return result


def validate_runtime_paths(settings, *, mountinfo: str | None = None, extra_data_paths=()) -> list[dict]:
    contract_file = settings.env.get("VIBETRADING_STORAGE_MOUNTS_FILE", "")
    enforce = settings.env.get("VIBETRADING_ENFORCE_MOUNTS", "false").lower() == "true"
    if not contract_file:
        if enforce:
            raise RuntimeError("storage mount enforcement requires a reviewed contract file")
        return []
    document = json.loads(Path(contract_file).read_text(encoding="utf-8"))
    entries = document.get("mounts")
    if document.get("schema_version") != 1 or not isinstance(entries, list) or not entries:
        raise RuntimeError("invalid storage mount contract")
    if mountinfo is None and os.name != "nt":
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    mounts = linux_mounts(mountinfo) if mountinfo is not None else None
    verified = []
    for entry in entries:
        path = Path(entry["path"])
        if not path.is_absolute() or not path.exists() or not path.is_dir():
            raise RuntimeError("required storage mount is missing: " + str(path))
        resolved = path.resolve(strict=True)
        if resolved == Path(resolved.anchor) or resolved != path.absolute():
            raise RuntimeError("unsafe storage mount path: " + str(path))
        observed = mounts.get(str(resolved)) if mounts is not None else None
        if (mounts is not None and observed is None) or (mounts is None and not os.path.ismount(resolved)):
            raise RuntimeError("required path is not a mount point: " + str(path))
        for field in ("device", "filesystem", "source"):
            if entry.get(field) and (observed is None or entry[field] != observed[field]):
                raise RuntimeError("storage mount identity mismatch: " + str(path))
        if observed is not None and "ro" in observed["options"].split(",") and entry.get("writable", True):
            raise RuntimeError("required storage mount is read-only: " + str(path))
        volume_id = entry.get("volume_id")
        if volume_id:
            marker = resolved / ".vibetrading-volume-id"
            if not marker.is_file() or marker.is_symlink() or marker.read_text(encoding="utf-8").strip() != volume_id:
                raise RuntimeError("storage volume marker mismatch: " + str(path))
        if enforce and not (entry.get("device") or volume_id):
            raise RuntimeError("enforced mount requires explicit device or volume identity")
        verified.append({"path":str(resolved), "identity_verified":bool(entry.get("device") or volume_id)})
    roots = [Path(item["path"]) for item in verified]
    bindings = document.get("path_bindings", {})
    if enforce and set(bindings) != {"data_root", "state_dir", "parquet_root"}:
        raise RuntimeError("enforced mount contract requires all data path bindings")
    # Logs may intentionally live in bounded /tmp; datasets and ledgers may not.
    paths=(("data_root",settings.data_root), ("state_dir",settings.state_dir), ("parquet_root",settings.parquet.data_root))
    for name, path in (*paths, *(("data_root",path) for path in extra_data_paths)):
        resolved_path = Path(path).resolve()
        allowed = roots
        if name in bindings:
            bound = Path(bindings[name]).resolve()
            if bound not in roots:
                raise RuntimeError("data path binding references an unverified mount")
            allowed = [bound]
        if not any(resolved_path.is_relative_to(root) for root in allowed):
            raise RuntimeError("data path lies outside reviewed storage mounts: " + str(path))
        if mounts is not None:
            # A reviewed parent does not establish the identity of a nested
            # mount. Resolve the filesystem that will actually receive writes.
            governing = [(Path(point), details) for point, details in mounts.items()
                         if resolved_path.is_relative_to(Path(point))]
            actual, details = max(governing, key=lambda item: len(item[0].parts))
            if actual not in allowed:
                raise RuntimeError("data path uses an unreviewed nested mount: " + str(path))
            if "ro" in details["options"].split(","):
                raise RuntimeError("data path uses a read-only mount: " + str(path))
    return verified
