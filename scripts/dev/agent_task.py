#!/usr/bin/env python3
"""Bounded task packets and deterministic review gates for the v3 dispatcher.

Stdlib only. This module is a small core around the read-only status projection:
it validates Codex-authored task specs, pins source/canonical hashes, checks
scope/tests, classifies worker failures and writes private pending-review
receipts. It never dispatches a model, mutates canonical metadata, executes
tests, or records acceptance.
"""

from __future__ import annotations

import argparse
import errno
import fnmatch
import hashlib
import importlib.util
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
REVIEW_SEVERITIES = {"low", "normal", "critical"}
DECISION_PENDING = "PENDING_CODEX_REVIEW"
PROVENANCE_UNTRUSTED = "UNTRUSTED_INPUT"
QUALIFIED_O00 = "QUALIFIED_O00"
VERIFIED_TESTS = "VERIFIED_TESTS"

REQUIRED_SPEC_KEYS = (
    "objective",
    "read_paths",
    "edit_paths",
    "forbidden_paths",
    "contract_ids",
    "implementation_steps",
    "acceptance",
    "test_commands",
    "stop_conditions",
    "review_severity",
    "profile",
    "model",
)

FAILURE_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
FAILURE_AUTH = "AUTH_FAILURE"
FAILURE_MODEL_TOOL = "MODEL_TOOL_FAILURE"
FAILURE_TIMEOUT = "EXECUTION_TIMEOUT"
FAILURE_TEST = "TEST_FAILURE"
FAILURE_CONTRACT = "CONTRACT_VIOLATION"
FAILURE_SCOPE = "SCOPE_VIOLATION"
FAILURE_EXTERNAL = "EXTERNAL_ONLY"
FAILURE_SAFETY = "SAFETY_BLOCKER"

_GLOB_CHARS = set("*?[]{}!")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40,64}$")

_BLOCKED_COMPONENTS = {
    ".git",
    ".ssh",
    ".aws",
    "runtime_data",
    "secrets",
    ".netrc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
_BLOCKED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")


def _load_status_module():
    try:
        from scripts.dev import status as status_module

        return status_module
    except Exception:
        path = Path(__file__).resolve().with_name("status.py")
        spec = importlib.util.spec_from_file_location("_agent_task_dev_status", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load status helper from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


status_mod = _load_status_module()


def _require_dict(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_nonempty_str(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_str_list(value, label, require_nonempty=False):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of strings")
    if require_nonempty and not value:
        raise ValueError(f"{label} must be a non-empty list of strings")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} must be a list of non-empty strings")
    return list(value)


def _require_hex64(value, label):
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{label} must be a 64-character lowercase hex digest")
    return value


def _git_head(root):
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    if completed.returncode != 0:
        return "UNKNOWN"
    head = completed.stdout.strip()
    if not head or not _GIT_SHA.fullmatch(head):
        return "UNKNOWN"
    return head


def _load_json_file(path, label="json"):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cannot parse {label}: {exc}") from exc


def _load_canonical(root):
    tickets = _require_dict(
        _load_json_file(root / status_mod.TICKETS_CANONICAL, "tickets_production.json"),
        "tickets_production.json",
    )
    stage = _require_dict(
        _load_json_file(root / status_mod.STAGE_CANONICAL, "stage_status.json"),
        "stage_status.json",
    )
    return tickets, stage


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _canonical_json_hash(value):
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not deterministic JSON: {exc}") from exc
    return hashlib.sha256(payload).hexdigest()


def _blocked_component(part):
    lowered = part.lower()
    if lowered in _BLOCKED_COMPONENTS:
        return True
    if lowered.startswith(".env"):
        return True
    if lowered.startswith("credential"):
        return True
    if lowered.endswith(_BLOCKED_SUFFIXES):
        return True
    return False


def _validate_path_string(path, label="path"):
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if path != path.strip():
        raise ValueError(f"{label} must not contain surrounding whitespace")
    if "\x00" in path:
        raise ValueError(f"{label} contains NUL")
    if "\\" in path:
        raise ValueError(f"{label} must use POSIX separators")
    if path.startswith("/") or path.startswith("//"):
        raise ValueError(f"{label} must be relative: {path!r}")
    if re.match(r"^[A-Za-z]:", path):
        raise ValueError(f"{label} must not be a Windows path: {path!r}")
    if any(ch in _GLOB_CHARS for ch in path):
        raise ValueError(f"{label} must not contain glob characters: {path!r}")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{label} must be normalized and traversal-free: {path!r}")
    if posixpath.normpath(path) != path:
        raise ValueError(f"{label} must be normalized: {path!r}")
    for part in parts:
        if _blocked_component(part):
            raise ValueError(f"{label} references a blocked path component: {path!r}")
    return path


def _check_no_symlink(root, rel, label):
    current = root
    for part in rel.split("/"):
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} traverses a symlink: {rel!r}")


def _existing_regular_file(root, rel, label):
    _check_no_symlink(root, rel, label)
    target = root / rel
    if not target.is_file():
        raise ValueError(f"{label} must be an existing regular file: {rel!r}")
    return target


def _validated_root(root):
    """Return an absolute root after rejecting symlinked root/ancestor components."""
    if isinstance(root, str) and not root.strip():
        raise ValueError("root must be a non-empty path")
    path = Path(root)
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"root traverses a symlink: {current}")
    if not absolute.exists():
        raise ValueError(f"root does not exist: {absolute}")
    if not absolute.is_dir():
        raise ValueError(f"root is not a directory: {absolute}")
    return absolute


def _require_canonical_files(root):
    for rel in (status_mod.TICKETS_CANONICAL, status_mod.STAGE_CANONICAL):
        _existing_regular_file(root, str(rel), "canonical metadata")


def _check_missing_ancestors(root, rel, label):
    current = root
    for part in rel.split("/")[:-1]:
        current = current / part
        if current.exists():
            if not current.is_dir():
                raise ValueError(f"{label} has a non-directory ancestor: {rel!r}")


def _is_glob_pattern(pattern):
    return any(ch in _GLOB_CHARS for ch in pattern)


def _matches_forbidden(rel, patterns):
    for raw in patterns:
        pattern = raw.strip()
        if not _is_glob_pattern(pattern):
            continue
        if fnmatch.fnmatchcase(rel, pattern):
            return True
        if "/" not in pattern and fnmatch.fnmatchcase(posixpath.basename(rel), pattern):
            return True
        if pattern.startswith("**/"):
            stripped = pattern[3:]
            if fnmatch.fnmatchcase(rel, stripped):
                return True
            if fnmatch.fnmatchcase(posixpath.basename(rel), stripped):
                return True
    return False


def _validate_forbidden_patterns(caller, canonical):
    caller_list = _require_str_list(caller, "forbidden_paths")
    canonical_list = _require_str_list(canonical, "canonical forbidden_paths")
    missing = [pattern for pattern in canonical_list if pattern not in caller_list]
    if missing:
        raise ValueError(
            "forbidden_paths must retain canonical restrictions; missing="
            + json.dumps(missing, ensure_ascii=False)
        )
    return caller_list


def _canonical_edit_scope(raw_record, family):
    raw = raw_record.get("edit_paths")
    if family == "R/O":
        if not isinstance(raw, list) or not raw:
            raise ValueError("R/O task has no canonical concrete edit_paths registration")
    elif raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("canonical edit_paths must be a list")

    entries = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            raw_path = item
            action = None
        elif isinstance(item, dict):
            raw_path = item.get("path")
            action = item.get("action")
        else:
            raise ValueError("canonical edit_paths entries must be objects or strings")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("canonical edit_paths.path must be a non-empty string")
        if action is not None and not isinstance(action, str):
            raise ValueError("canonical edit_paths.action must be a string or null")
        action = action.upper() if isinstance(action, str) else None

        is_dir_scope = raw_path.endswith("/")
        concrete = raw_path[:-1] if is_dir_scope else raw_path
        _validate_path_string(concrete, "canonical edit_paths.path")
        if is_dir_scope and family == "R/O":
            raise ValueError("R/O canonical edit_paths must be concrete files")
        if concrete in seen:
            raise ValueError(f"duplicate canonical edit path: {concrete}")
        seen.add(concrete)
        entries.append({"path": concrete, "action": action, "directory": is_dir_scope})
    return entries


def _resolve_canonical_edit(rel, entries):
    for entry in entries:
        if entry["directory"]:
            if rel.startswith(entry["path"] + "/"):
                return entry
        elif rel == entry["path"]:
            return entry
    raise ValueError(f"edit path is outside canonical scope: {rel!r}")


def _validate_spec(spec):
    spec = _require_dict(spec, "spec")
    missing = [key for key in REQUIRED_SPEC_KEYS if key not in spec]
    if missing:
        raise ValueError("spec is missing required keys: " + ", ".join(missing))

    objective = _require_nonempty_str(spec["objective"], "objective")
    read_paths = _require_str_list(spec["read_paths"], "read_paths")
    edit_paths = _require_str_list(spec["edit_paths"], "edit_paths")
    forbidden_paths = _require_str_list(spec["forbidden_paths"], "forbidden_paths")
    contract_ids = _require_str_list(spec["contract_ids"], "contract_ids")
    implementation_steps = _require_str_list(
        spec["implementation_steps"], "implementation_steps", require_nonempty=True
    )
    acceptance = _require_str_list(spec["acceptance"], "acceptance", require_nonempty=True)
    test_commands = _require_str_list(
        spec["test_commands"], "test_commands", require_nonempty=True
    )
    stop_conditions = _require_str_list(
        spec["stop_conditions"], "stop_conditions", require_nonempty=True
    )
    severity = _require_nonempty_str(spec["review_severity"], "review_severity")
    if severity not in REVIEW_SEVERITIES:
        raise ValueError(
            f"review_severity must be one of {sorted(REVIEW_SEVERITIES)}, got {severity!r}"
        )
    profile = _require_nonempty_str(spec["profile"], "profile")
    model = _require_nonempty_str(spec["model"], "model")

    for path in read_paths:
        _validate_path_string(path, "read_paths[]")
    for path in edit_paths:
        _validate_path_string(path, "edit_paths[]")

    return {
        "objective": objective,
        "read_paths": read_paths,
        "edit_paths": edit_paths,
        "forbidden_paths": forbidden_paths,
        "contract_ids": contract_ids,
        "implementation_steps": implementation_steps,
        "acceptance": acceptance,
        "test_commands": test_commands,
        "stop_conditions": stop_conditions,
        "review_severity": severity,
        "profile": profile,
        "model": model,
    }


def _find_record(tickets, task_id):
    for raw in tickets.get("tasks", []):
        if isinstance(raw, dict) and raw.get("task_id") == task_id:
            return raw, "VN/TX"
    for raw in tickets.get("tooling_tasks", []):
        if isinstance(raw, dict) and raw.get("task_id") == task_id:
            return raw, "R/O"
    raise ValueError(f"task is not present in canonical metadata: {task_id!r}")


def _model_route(tickets, profile, model):
    profiles = tickets.get("model_routing_profiles")
    if not isinstance(profiles, dict):
        raise ValueError("canonical model_routing_profiles missing")
    profile_models = profiles.get(profile)
    if not isinstance(profile_models, list) or model not in profile_models:
        raise ValueError(f"model {model!r} is not registered in profile {profile!r}")

    readiness = tickets.get("model_readiness")
    if not isinstance(readiness, list):
        raise ValueError("canonical model_readiness missing")
    matches = [
        entry
        for entry in readiness
        if isinstance(entry, dict) and entry.get("model_id") == model
    ]
    if len(matches) != 1:
        raise ValueError(f"model {model!r} must have exactly one model_readiness entry")
    entry = matches[0]
    if entry.get("status") != QUALIFIED_O00:
        raise ValueError(
            f"model {model!r} is not {QUALIFIED_O00} (got {entry.get('status')!r})"
        )
    route = _require_nonempty_str(
        entry.get("provider_route"), f"model_readiness[{model}].provider_route"
    )
    if "/" not in model:
        raise ValueError(f"model id must include a provider prefix: {model!r}")
    provider = model.split("/", 1)[0]
    if route != provider:
        raise ValueError(
            f"model {model!r} provider_route {route!r} does not match "
            f"model prefix {provider!r}"
        )
    return provider, route


def _check_ready(status, task_id):
    node = next((item for item in status.get("nodes", []) if item.get("task_id") == task_id), None)
    if node is None:
        raise ValueError(f"task is not present in status projection: {task_id!r}")
    if not node.get("ready") or node.get("state") != status_mod.STATE_READY:
        raise ValueError(
            f"task {task_id!r} is not READY (state={node.get('state')!r})"
        )
    return node


def make_packet(root, task_id, spec):
    """Build a validated TaskPacket without writing anything."""
    root = _validated_root(root)
    task_id = _require_nonempty_str(task_id, "task_id")
    spec = _validate_spec(spec)
    _require_canonical_files(root)

    initial_canonical_hashes = {str(rel): _sha256_file(root / rel)
                                for rel in (status_mod.TICKETS_CANONICAL, status_mod.STAGE_CANONICAL)}
    status = status_mod.load_status(root)
    _check_ready(status, task_id)

    head = _git_head(root)
    if head == "UNKNOWN":
        raise ValueError("git HEAD is unavailable; refusing to build a packet")

    tickets, _stage = _load_canonical(root)
    record, family = _find_record(tickets, task_id)

    prerequisites = _require_str_list(record.get("depends_on"), f"{task_id}.depends_on")
    canonical_forbidden = _validate_forbidden_patterns(
        spec["forbidden_paths"], record.get("forbidden_paths") or []
    )

    canonical_contracts = []
    seen_contracts = set()
    for field in ("contracts", "contract_ids"):
        if field not in record:
            continue
        validated = _require_str_list(record[field], f"{task_id}.{field}")
        for cid in validated:
            if cid not in seen_contracts:
                seen_contracts.add(cid)
                canonical_contracts.append(cid)
    missing_contracts = [cid for cid in canonical_contracts if cid not in spec["contract_ids"]]
    if missing_contracts:
        raise ValueError(
            "contract_ids must retain canonical contracts; missing="
            + json.dumps(missing_contracts, ensure_ascii=False)
        )

    provider, route = _model_route(tickets, spec["profile"], spec["model"])

    source_hashes = {}
    for rel in spec["read_paths"]:
        if _matches_forbidden(rel, canonical_forbidden):
            raise ValueError(f"read path matches a forbidden pattern: {rel!r}")
        target = _existing_regular_file(root, rel, "read_paths[]")
        source_hashes[rel] = _sha256_file(target)

    edit_scope = _canonical_edit_scope(record, family)
    for rel in spec["edit_paths"]:
        if _matches_forbidden(rel, canonical_forbidden):
            raise ValueError(f"edit path matches a forbidden pattern: {rel!r}")
        _validate_path_string(rel, "edit_paths[]")
        entry = _resolve_canonical_edit(rel, edit_scope)
        _check_no_symlink(root, rel, "edit_paths[]")
        target = root / rel
        if target.exists():
            if not target.is_file():
                raise ValueError(f"edit path must be a regular file: {rel!r}")
            source_hashes[rel] = _sha256_file(target)
        else:
            if entry["action"] != "ADD":
                raise ValueError(
                    f"missing edit path requires canonical ADD action: {rel!r}"
                )
            _check_missing_ancestors(root, rel, "edit_paths[]")
            source_hashes[rel] = None

    canonical_hashes = {
        str(status_mod.TICKETS_CANONICAL): _sha256_file(
            root / status_mod.TICKETS_CANONICAL
        ),
        str(status_mod.STAGE_CANONICAL): _sha256_file(
            root / status_mod.STAGE_CANONICAL
        ),
    }

    if canonical_hashes != initial_canonical_hashes:
        raise ValueError("canonical metadata changed while building packet")

    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "family": family,
        "base_sha": head,
        "prerequisites": prerequisites,
        "objective": spec["objective"],
        "read_paths": spec["read_paths"],
        "edit_paths": spec["edit_paths"],
        "forbidden_paths": spec["forbidden_paths"],
        "contract_ids": spec["contract_ids"],
        "implementation_steps": spec["implementation_steps"],
        "acceptance": spec["acceptance"],
        "test_commands": spec["test_commands"],
        "stop_conditions": spec["stop_conditions"],
        "review_severity": spec["review_severity"],
        "profile": spec["profile"],
        "model": spec["model"],
        "provider": provider,
        "route": route,
        "canonical_hashes": canonical_hashes,
        "source_hashes": source_hashes,
    }


def check_scope(snapshot, after, allowed):
    """Return sorted changed paths, or raise if any change is unsafe/out of scope."""
    snapshot = _require_dict(snapshot, "snapshot")
    after = _require_dict(after, "after")
    allowed = _require_str_list(allowed, "allowed")

    allowed_set = set()
    for rel in allowed:
        _validate_path_string(rel, "allowed[]")
        allowed_set.add(rel)

    values = {}
    for label, mapping in (("snapshot", snapshot), ("after", after)):
        for rel, digest in mapping.items():
            _validate_path_string(rel, f"{label} key")
            if digest is not None and not isinstance(digest, str):
                raise ValueError(f"{label}[{rel!r}] must be a string or null")
            values.setdefault(rel, None)

    changed = sorted(
        rel for rel in values if snapshot.get(rel) != after.get(rel)
    )
    out_of_scope = [rel for rel in changed if rel not in allowed_set]
    if out_of_scope:
        raise ValueError(
            "out-of-scope changes: " + json.dumps(out_of_scope, ensure_ascii=False)
        )
    return changed


def verify_source(root, packet):
    """Revalidate HEAD, canonical hashes and every pinned source hash."""
    root = _validated_root(root)
    _require_canonical_files(root)
    packet = _require_dict(packet, "packet")
    base_sha = _require_nonempty_str(packet.get("base_sha"), "packet.base_sha")

    head = _git_head(root)
    if head == "UNKNOWN":
        raise ValueError("git HEAD is unavailable during source verification")
    if head != base_sha:
        raise ValueError(f"git HEAD changed: expected {base_sha}, got {head}")

    canonical_hashes = _require_dict(
        packet.get("canonical_hashes"), "packet.canonical_hashes"
    )
    required_canonical = {
        str(status_mod.TICKETS_CANONICAL),
        str(status_mod.STAGE_CANONICAL),
    }
    if not required_canonical.issubset(canonical_hashes):
        raise ValueError("packet.canonical_hashes is missing canonical files")
    for rel, expected in canonical_hashes.items():
        _validate_path_string(rel, "canonical_hashes key")
        expected = _require_hex64(expected, f"canonical_hashes[{rel!r}]")
        target = _existing_regular_file(root, rel, "canonical_hashes")
        if _sha256_file(target) != expected:
            raise ValueError(f"canonical metadata changed: {rel}")

    read_paths = _require_str_list(packet.get("read_paths"), "packet.read_paths")
    edit_paths = _require_str_list(packet.get("edit_paths"), "packet.edit_paths")
    expected_paths = []
    seen_expected = set()
    for label, rels in (
        ("packet.read_paths[]", read_paths),
        ("packet.edit_paths[]", edit_paths),
    ):
        seen_in_list = set()
        for rel in rels:
            _validate_path_string(rel, label)
            if rel in seen_in_list:
                raise ValueError(f"duplicate pinned path: {rel!r}")
            seen_in_list.add(rel)
            if rel not in seen_expected:
                seen_expected.add(rel)
                expected_paths.append(rel)

    source_hashes = _require_dict(
        packet.get("source_hashes"), "packet.source_hashes"
    )
    for rel in source_hashes:
        _validate_path_string(rel, "source_hashes key")
    missing = [rel for rel in expected_paths if rel not in source_hashes]
    extra = sorted(rel for rel in source_hashes if rel not in seen_expected)
    if missing:
        raise ValueError(
            "packet.source_hashes is missing pinned paths: "
            + json.dumps(missing, ensure_ascii=False)
        )
    if extra:
        raise ValueError(
            "packet.source_hashes has extra pinned paths: "
            + json.dumps(extra, ensure_ascii=False)
        )

    for rel in expected_paths:
        expected = source_hashes[rel]
        _check_no_symlink(root, rel, "source_hashes")
        target = root / rel
        if expected is None:
            if target.exists():
                raise ValueError(f"pinned new path now exists: {rel}")
            continue
        expected = _require_hex64(expected, f"source_hashes[{rel!r}]")
        if not target.is_file():
            raise ValueError(f"pinned source file is missing: {rel}")
        if _sha256_file(target) != expected:
            raise ValueError(f"source file changed: {rel}")
    return True


def check_tests(commands, results):
    """Validate exact ordered test evidence; returns VERIFIED_TESTS only."""
    commands = _require_str_list(commands, "commands", require_nonempty=True)
    if not isinstance(results, list):
        raise ValueError("results must be a list")
    if len(results) != len(commands):
        raise ValueError(
            f"results count {len(results)} does not match commands count {len(commands)}"
        )
    for index, (command, result) in enumerate(zip(commands, results)):
        if not isinstance(result, dict):
            raise ValueError(f"results[{index}] must be an object")
        if result.get("command") != command:
            raise ValueError(f"results[{index}] command does not match ordered command")
        exit_code = result.get("exit_code")
        if type(exit_code) is not int:
            raise ValueError(f"results[{index}].exit_code must be an integer")
        if exit_code != 0:
            raise ValueError(f"results[{index}] did not exit zero: {exit_code}")
        _require_hex64(result.get("stdout_sha256"), f"results[{index}].stdout_sha256")
    return {
        "status": VERIFIED_TESTS,
        "commands": list(commands),
        "result_count": len(results),
    }


def classify_failure(exit_code, text):
    """Classify a worker failure without retrying or switching models."""
    if isinstance(exit_code, bool) or (
        exit_code is not None and not isinstance(exit_code, int)
    ):
        raise ValueError("exit_code must be an integer or null")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    lowered = text.lower()

    rate_patterns = (
        "rate limit",
        "rate-limit",
        "rate_limit",
        "ratelimit",
        "too many requests",
        "quota",
        "resource_exhausted",
        "capacity exceeded",
        "overloaded",
        "429",
    )
    if exit_code == 429 or any(p in lowered for p in rate_patterns):
        return FAILURE_RATE_LIMIT

    auth_patterns = (
        "unauthorized",
        "unauthenticated",
        "authentication",
        "invalid api key",
        "api key",
        "api_key",
        "401",
        "403",
        "permission denied",
        "not authorized",
        "credential",
    )
    if exit_code in (401, 403) or any(p in lowered for p in auth_patterns):
        return FAILURE_AUTH

    timeout_patterns = (
        "timeout",
        "timed out",
        "deadline exceeded",
        "etimedout",
        "took too long",
    )
    if exit_code == 124 or any(p in lowered for p in timeout_patterns):
        return FAILURE_TIMEOUT

    if "contract" in lowered:
        return FAILURE_CONTRACT

    scope_patterns = (
        "scope",
        "out-of-scope",
        "out of scope",
        "forbidden path",
        "path traversal",
        "untracked",
        "deletion",
        "symlink",
    )
    if any(p in lowered for p in scope_patterns):
        return FAILURE_SCOPE

    safety_patterns = (
        "safety",
        "unsafe",
        "secret",
        "dangerous",
        "destructive",
        "sensitive",
        "blocked by policy",
    )
    if any(p in lowered for p in safety_patterns):
        return FAILURE_SAFETY

    external_patterns = (
        "external",
        "production",
        "deploy",
        "live mutation",
        "host attestation",
        "network",
    )
    if any(p in lowered for p in external_patterns):
        return FAILURE_EXTERNAL

    test_patterns = (
        "test failure",
        "tests failed",
        "test failed",
        "failed tests",
        "pytest",
        "assertionerror",
        "assert ",
        "regression",
    )
    if any(p in lowered for p in test_patterns):
        return FAILURE_TEST

    return FAILURE_MODEL_TOOL


def _bounded_items(items, label):
    shown = list(items)
    if len(shown) <= 50:
        return shown
    return shown[:50] + [f"... +{len(shown) - 50} more {label}"]


def handoff(packet):
    """Return a bounded handoff summary; Codex review and independent tests remain required."""
    packet = _require_dict(packet, "packet")
    task_id = _require_nonempty_str(packet.get("task_id"), "packet.task_id")
    objective = _require_nonempty_str(packet.get("objective"), "packet.objective")
    read_paths = _require_str_list(packet.get("read_paths"), "packet.read_paths")
    edit_paths = _require_str_list(packet.get("edit_paths"), "packet.edit_paths")
    test_commands = _require_str_list(
        packet.get("test_commands"), "packet.test_commands", require_nonempty=True
    )
    contracts = _require_str_list(packet.get("contract_ids"), "packet.contract_ids")
    base_sha = _require_nonempty_str(packet.get("base_sha"), "packet.base_sha")

    objective_text = objective if len(objective) <= 500 else objective[:497] + "..."
    lines = [
        f"Task: {task_id}",
        f"Base SHA: {base_sha}",
        f"Objective: {objective_text}",
        "Read scope: " + (", ".join(_bounded_items(read_paths, "paths")) or "(none)"),
        "Edit scope: " + (", ".join(_bounded_items(edit_paths, "paths")) or "(none)"),
        "Contracts: " + (", ".join(_bounded_items(contracts, "contracts")) or "(none)"),
        "Exact test commands:",
    ]
    for index, command in enumerate(test_commands, start=1):
        lines.append(f"  {index}. {command}")
    lines.append(
        "Codex review and independent host test verification are required; "
        "this worker handoff is not acceptance evidence."
    )
    return "\n".join(lines)


def _raise_state_dir_component_error(exc, is_last, parent_fd, name):
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        is_symlink = exc.errno == errno.ELOOP
        if not is_symlink:
            try:
                entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                is_symlink = stat.S_ISLNK(entry.st_mode)
            except OSError:
                pass
        if is_symlink:
            raise ValueError("state_dir traverses a symlink") from exc
        if is_last:
            raise ValueError("state_dir is not a directory") from exc
        raise ValueError("state_dir has a non-directory ancestor") from exc
    raise ValueError(f"cannot open state directory: {exc}") from exc


def _open_state_dir_component(parent_fd, name, is_last):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    except OSError as exc:
        _raise_state_dir_component_error(exc, is_last, parent_fd, name)
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ValueError(f"cannot create state directory: {exc}") from exc
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        _raise_state_dir_component_error(exc, is_last, parent_fd, name)


def _open_private_state_leaf(state_dir):
    absolute = Path(state_dir).absolute()
    if str(absolute) == absolute.anchor:
        raise ValueError("state_dir must name a private leaf directory")
    try:
        dir_fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise ValueError(f"cannot open state directory root: {exc}") from exc
    try:
        parts = absolute.parts[1:]
        for index, part in enumerate(parts):
            is_last = index == len(parts) - 1
            child_fd = _open_state_dir_component(dir_fd, part, is_last)
            os.close(dir_fd)
            dir_fd = child_fd
        if os.fstat(dir_fd).st_uid != os.getuid():
            raise ValueError("state_dir leaf is not owned by the current user")
        try:
            os.fchmod(dir_fd, 0o700)
        except OSError as exc:
            raise ValueError(f"cannot secure state directory: {exc}") from exc
        return dir_fd, absolute
    except BaseException:
        os.close(dir_fd)
        raise


def write_record(state_dir, packet, results, *, decision):
    """Append one private PENDING_CODEX_REVIEW receipt; never records acceptance."""
    if decision != DECISION_PENDING:
        raise ValueError(f"decision must be exactly {DECISION_PENDING!r}")
    packet = _require_dict(packet, "packet")
    if not isinstance(results, list):
        raise ValueError("results must be a list")

    packet_hash = _canonical_json_hash(packet)
    results_hash = _canonical_json_hash(results)

    leaf_fd, absolute = _open_private_state_leaf(state_dir)
    try:
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "AGENT_TASK_RECEIPT",
            "decision": DECISION_PENDING,
            "provenance": PROVENANCE_UNTRUSTED,
            "host_execution_attested": False,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "task_id": packet.get("task_id"),
            "base_sha": packet.get("base_sha"),
            "model": packet.get("model"),
            "packet_sha256": packet_hash,
            "results_sha256": results_hash,
        }
        payload = (
            json.dumps(receipt, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        )

        for _attempt in range(100):
            name = (
                "receipt-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                + "-"
                + uuid.uuid4().hex
                + ".json"
            )
            try:
                fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=leaf_fd,
                )
            except FileExistsError:
                continue
            except OSError as exc:
                raise ValueError(f"cannot create receipt: {exc}") from exc
            try:
                os.fchmod(fd, 0o600)
                data = memoryview(payload.encode("utf-8"))
                while data:
                    written = os.write(fd, data)
                    data = data[written:]
                os.fsync(fd)
            except OSError as exc:
                raise ValueError(f"cannot write receipt: {exc}") from exc
            finally:
                os.close(fd)
            try:
                os.fsync(leaf_fd)
            except OSError:
                pass
            receipt["path"] = str(absolute / name)
            return receipt
        raise ValueError("cannot allocate a unique receipt path")
    finally:
        os.close(leaf_fd)


def _print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _command_packet(args):
    spec = _load_json_file(args.spec, "spec")
    _print_json(make_packet(Path(args.root), args.task_id, spec))
    return 0


def _command_list_ready(args):
    status = status_mod.load_status(Path(args.root))
    status["git_head"] = _git_head(Path(args.root))
    _print_json(status)
    return 0


def _command_check_scope(args):
    snapshot = _load_json_file(args.snapshot, "snapshot")
    after = _load_json_file(args.after, "after")
    allowed = _load_json_file(args.allowed, "allowed")
    print(json.dumps(check_scope(snapshot, after, allowed), ensure_ascii=False))
    return 0


def _command_check_tests(args):
    commands = _load_json_file(args.commands, "commands")
    results = _load_json_file(args.results, "results")
    _print_json(check_tests(commands, results))
    return 0


def _command_record(args):
    packet = _load_json_file(args.packet, "packet")
    results = _load_json_file(args.results, "results")
    receipt = write_record(
        Path(args.state_dir), packet, results, decision=args.decision
    )
    _print_json(receipt)
    return 0


def _command_handoff(args):
    packet = _load_json_file(args.packet, "packet")
    print(handoff(packet))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Bounded task packets and deterministic review gates (stdlib only)."
    )
    parser.add_argument("--root", default=".", help="repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-ready", help="emit the existing status projection")

    packet = subparsers.add_parser("packet", help="build a validated TaskPacket")
    packet.add_argument("task_id")
    packet.add_argument("--spec", required=True, help="path to Codex-authored JSON spec")
    packet.set_defaults(handler=_command_packet)

    scope = subparsers.add_parser("check-scope", help="check snapshot changes")
    scope.add_argument("--snapshot", required=True)
    scope.add_argument("--after", required=True)
    scope.add_argument("--allowed", required=True)
    scope.set_defaults(handler=_command_check_scope)

    tests = subparsers.add_parser("check-tests", help="check exact ordered test evidence")
    tests.add_argument("--commands", required=True)
    tests.add_argument("--results", required=True)
    tests.set_defaults(handler=_command_check_tests)

    record = subparsers.add_parser("record", help="write a private pending-review receipt")
    record.add_argument("--state-dir", required=True)
    record.add_argument("--packet", required=True)
    record.add_argument("--results", required=True)
    record.add_argument("--decision", default=DECISION_PENDING)
    record.set_defaults(handler=_command_record)

    handoff_parser = subparsers.add_parser("handoff", help="emit a bounded handoff summary")
    handoff_parser.add_argument("--packet", required=True)
    handoff_parser.set_defaults(handler=_command_handoff)

    subparsers.choices["list-ready"].set_defaults(handler=_command_list_ready)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ValueError as exc:
        print(f"agent_task error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
