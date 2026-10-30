#!/usr/bin/env python3
"""Read-only projection and manual-packet verification for final tasks.

Stdlib only. ``docs/final/05_TASK_STATUS.json`` is the only mutable current
state. This module projects readiness from that file and validates a
Codex-authored manual packet plus its proof receipt. It never dispatches a
model, executes tests, writes metadata, or records acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

FINAL_DIR = Path("docs") / "final"
FINAL_STATUS = FINAL_DIR / "05_TASK_STATUS.json"
SCHEMA_VERSION = 1

MANUAL_PACKET_ONLY = "MANUAL_PACKET_ONLY"
PENDING_CODEX_REVIEW = "PENDING_CODEX_REVIEW"

NOT_STARTED = "NOT_STARTED"
ACCEPTED_LOCAL = "ACCEPTED_LOCAL"
STATES = {
    NOT_STARTED,
    "IN_PROGRESS",
    "BLOCKED",
    ACCEPTED_LOCAL,
    "EXTERNAL_NOT_AUTHORIZED",
    "EXTERNAL_VERIFICATION_PENDING",
    "OPERATIONAL_ACCEPTED",
    "NOT_APPLICABLE_APPROVED",
}
EXTERNAL_STATES = {
    "EXTERNAL_NOT_AUTHORIZED",
    "EXTERNAL_VERIFICATION_PENDING",
}
SCOPE_DECISION_STATES = {"NOT_APPLICABLE_APPROVED"}
EXCLUDED_TASK_IDS = {"KR-DATA-01"}

ALLOWED_MODELS = {
    "freellmapi/glm-4.5-flash",
    "opencode/deepseek-v4.1-flash-free",
    "freellmapi/gemini-2.5-flash",
}

REQUIRED_TICKET_KEYS = (
    "task_id",
    "status",
    "depends_on",
    "definition_file",
    "definition_heading_id",
    "required_for_offline_release",
    "base_sha",
    "actual_commit_sha",
    "acceptance_receipt",
    "test_exit",
    "blocking_reasons",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40,64}$")


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


def _load_json_file(path, label="json"):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cannot parse {label}: {exc}") from exc


def _input_path(root, value):
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def _load_regular_json(path, label):
    target = Path(path)
    if target.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {target}")
    if not target.is_file():
        raise ValueError(f"{label} must be an existing regular file: {target}")
    return _load_json_file(target, label)


def _load_agent_task():
    try:
        from scripts.dev import agent_task

        return agent_task
    except Exception:
        path = Path(__file__).resolve().with_name("agent_task.py")
        spec = importlib.util.spec_from_file_location(
            "_final_task_agent_task", path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load agent_task helper from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
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


def _validate_ticket(raw, index, seen):
    ticket = _require_dict(raw, f"tickets[{index}]")
    missing = [key for key in REQUIRED_TICKET_KEYS if key not in ticket]
    if missing:
        raise ValueError(
            f"tickets[{index}] is missing required keys: " + ", ".join(missing)
        )
    task_id = _require_nonempty_str(ticket["task_id"], f"tickets[{index}].task_id")
    if task_id in seen:
        raise ValueError(f"duplicate task_id: {task_id}")
    seen.add(task_id)

    status = _require_nonempty_str(ticket["status"], f"{task_id}.status")
    if status not in STATES:
        raise ValueError(f"{task_id}: unknown status {status!r}")

    depends_on = _require_str_list(ticket["depends_on"], f"{task_id}.depends_on")
    _require_nonempty_str(ticket["definition_file"], f"{task_id}.definition_file")
    _require_nonempty_str(
        ticket["definition_heading_id"], f"{task_id}.definition_heading_id"
    )
    if not isinstance(ticket["required_for_offline_release"], bool):
        raise ValueError(f"{task_id}.required_for_offline_release must be boolean")

    for field in ("base_sha", "actual_commit_sha"):
        value = ticket[field]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{task_id}.{field} must be null or a non-empty string")

    receipt = ticket["acceptance_receipt"]
    if receipt is not None and not isinstance(receipt, (str, dict)):
        raise ValueError(
            f"{task_id}.acceptance_receipt must be null, a string or an object"
        )

    test_exit = ticket["test_exit"]
    if test_exit is not None and (
        isinstance(test_exit, bool) or not isinstance(test_exit, int)
    ):
        raise ValueError(f"{task_id}.test_exit must be null or an integer")

    _require_str_list(ticket["blocking_reasons"], f"{task_id}.blocking_reasons")

    return {
        "task_id": task_id,
        "status": status,
        "depends_on": depends_on,
        "definition_file": ticket["definition_file"],
        "definition_heading_id": ticket["definition_heading_id"],
        "required_for_offline_release": ticket["required_for_offline_release"],
        "base_sha": ticket["base_sha"],
        "actual_commit_sha": ticket["actual_commit_sha"],
        "acceptance_receipt": receipt,
        "test_exit": test_exit,
        "blocking_reasons": list(ticket["blocking_reasons"]),
    }


def _no_cycles(records):
    edges = {record["task_id"]: record["depends_on"] for record in records}
    state = {}

    def visit(node):
        if state.get(node) == 1:
            raise ValueError(f"dependency cycle involves {node}")
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in edges[node]:
            visit(dep)
        state[node] = 2

    for node in edges:
        visit(node)


def _require_no_symlink_path(root, rel):
    """Reject a symlinked root or any symlinked ancestor/file of ``rel``."""
    current = _load_agent_task()._validated_root(root)
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"final status traverses a symlink: {rel}")


def load_final_status(root):
    """Load and validate ``docs/final/05_TASK_STATUS.json`` without writing."""
    root = Path(root)
    _require_no_symlink_path(root, FINAL_STATUS)
    source = root / FINAL_STATUS
    if source.is_symlink():
        raise ValueError(f"final status must not be a symlink: {source}")
    if not source.is_file():
        raise ValueError(f"final status is missing: {source}")
    data = _require_dict(_load_json_file(source, "05_TASK_STATUS.json"),
                         "05_TASK_STATUS.json")

    schema = data.get("schema_version")
    if type(schema) is not int or schema != SCHEMA_VERSION:
        raise ValueError("05_TASK_STATUS.json schema_version must be 1")

    if not isinstance(data.get("stop_requested"), bool):
        raise ValueError("05_TASK_STATUS.json stop_requested must be boolean")

    tickets = data.get("tickets")
    if not isinstance(tickets, list):
        raise ValueError("05_TASK_STATUS.json tickets must be a list")

    records = []
    seen = set()
    for index, raw in enumerate(tickets):
        records.append(_validate_ticket(raw, index, seen))

    by_id = {record["task_id"]: record for record in records}
    for record in records:
        for dep in record["depends_on"]:
            if dep not in by_id:
                raise ValueError(
                    f"{record['task_id']} depends on unknown id: {dep}"
                )
    _no_cycles(records)

    for field in ("active_task_id", "last_accepted_task_id"):
        value = data.get(field)
        if value is not None:
            _require_nonempty_str(value, f"05_TASK_STATUS.json {field}")
            if value not in by_id:
                raise ValueError(
                    f"05_TASK_STATUS.json {field} references unknown id: {value}"
                )

    return {
        "schema_version": SCHEMA_VERSION,
        "tickets": records,
        "stop_requested": data["stop_requested"],
        "active_task_id": data.get("active_task_id"),
        "last_accepted_task_id": data.get("last_accepted_task_id"),
    }


def project_status(root):
    """Return a deterministic readiness projection from the final 05 state."""
    data = load_final_status(root)
    tickets = data["tickets"]
    by_id = {ticket["task_id"]: ticket for ticket in tickets}
    counts = {}
    nodes = []
    ready = []

    for ticket in tickets:
        status = ticket["status"]
        counts[status] = counts.get(status, 0) + 1
        missing = [
            dep for dep in ticket["depends_on"]
            if by_id[dep]["status"] != ACCEPTED_LOCAL
        ]
        is_ready = (
            status == NOT_STARTED
            and ticket["task_id"] not in EXCLUDED_TASK_IDS
            and status not in EXTERNAL_STATES
            and status not in SCOPE_DECISION_STATES
            and not ticket["blocking_reasons"]
            and not data["stop_requested"]
            and data["active_task_id"] is None
            and not missing
        )
        if is_ready:
            ready.append(ticket["task_id"])
        nodes.append(
            {
                **ticket,
                "ready": is_ready,
                "missing_dependencies": missing,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "source": str(FINAL_STATUS),
        "counts": dict(sorted(counts.items())),
        "ready": ready,
        "stop_requested": data["stop_requested"],
        "active_task_id": data["active_task_id"],
        "last_accepted_task_id": data["last_accepted_task_id"],
        "tickets": nodes,
    }


def _validate_path_string(path, label, agent_task):
    return agent_task._validate_path_string(path, label)


def _validate_hash_map(value, label, *, allow_null=True):
    mapping = _require_dict(value, label)
    result = {}
    for rel, digest in mapping.items():
        _require_nonempty_str(rel, f"{label} key")
        if digest is None:
            if not allow_null:
                raise ValueError(f"{label}[{rel!r}] must not be null")
        else:
            if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
                raise ValueError(
                    f"{label}[{rel!r}] must be a 64-character lowercase hex digest"
                )
        result[rel] = digest
    return result


def check_manual(root, task_id, packet_path, receipt_path):
    """Validate a manual packet and proof receipt; never records acceptance."""
    root = Path(root)
    agent_task = _load_agent_task()
    root = agent_task._validated_root(root)
    task_id = _require_nonempty_str(task_id, "task_id")

    projection = project_status(root)
    nodes = {node["task_id"]: node for node in projection["tickets"]}
    node = nodes.get(task_id)
    if node is None:
        raise ValueError(f"task is not present in final status: {task_id!r}")
    if not node["ready"]:
        raise ValueError(
            f"task {task_id!r} is not READY "
            f"(state={node['status']!r}, missing={node['missing_dependencies']})"
        )

    head = _git_head(root)
    if head == "UNKNOWN":
        raise ValueError("git HEAD is unavailable; refusing to verify a receipt")

    packet = _require_dict(
        _load_regular_json(_input_path(root, packet_path), "packet"), "packet"
    )
    receipt = _require_dict(
        _load_regular_json(_input_path(root, receipt_path), "receipt"), "receipt"
    )

    packet_task = _require_nonempty_str(packet.get("task_id"), "packet.task_id")
    receipt_task = _require_nonempty_str(receipt.get("task_id"), "receipt.task_id")
    if not (packet_task == receipt_task == task_id):
        raise ValueError(
            "task_id mismatch: "
            f"expected {task_id}, packet={packet_task}, receipt={receipt_task}"
        )

    packet_base = _require_nonempty_str(packet.get("base_sha"), "packet.base_sha")
    receipt_base = _require_nonempty_str(receipt.get("base_sha"), "receipt.base_sha")
    if not (packet_base == receipt_base == head):
        raise ValueError(
            "base_sha mismatch: "
            f"HEAD={head}, packet={packet_base}, receipt={receipt_base}"
        )
    task_base = node.get("base_sha")
    if task_base is not None and task_base != head:
        raise ValueError(
            f"final task base_sha mismatch: expected {head}, got {task_base}"
        )

    packet_model = _require_nonempty_str(packet.get("model"), "packet.model")
    receipt_model = _require_nonempty_str(receipt.get("model"), "receipt.model")
    if packet_model not in ALLOWED_MODELS:
        raise ValueError(f"packet.model is not allowed: {packet_model!r}")
    if packet_model != receipt_model:
        raise ValueError(
            f"model mismatch: packet={packet_model}, receipt={receipt_model}"
        )

    read_paths = _require_str_list(packet.get("read_paths"), "packet.read_paths")
    edit_paths = _require_str_list(
        packet.get("edit_paths"), "packet.edit_paths", require_nonempty=True
    )
    forbidden_paths = _require_str_list(
        packet.get("forbidden_paths"), "packet.forbidden_paths"
    )
    for rel in read_paths:
        _validate_path_string(rel, "packet.read_paths[]", agent_task)
    for rel in edit_paths:
        _validate_path_string(rel, "packet.edit_paths[]", agent_task)
    for rel in read_paths + edit_paths:
        if agent_task._matches_forbidden(rel, forbidden_paths):
            raise ValueError(f"path matches a forbidden pattern: {rel!r}")

    pinned_paths = list(read_paths)
    for rel in edit_paths:
        if rel not in pinned_paths:
            pinned_paths.append(rel)
    if len(pinned_paths) != len(set(pinned_paths)):
        raise ValueError("packet read/edit paths contain duplicates")

    source_hashes = _validate_hash_map(
        packet.get("source_hashes"), "packet.source_hashes"
    )
    before_hashes = _validate_hash_map(
        receipt.get("before_hashes"), "receipt.before_hashes"
    )
    after_hashes = _validate_hash_map(
        receipt.get("after_hashes"), "receipt.after_hashes"
    )

    for rel in read_paths:
        for label, mapping in (
            ("packet.source_hashes", source_hashes),
            ("receipt.before_hashes", before_hashes),
            ("receipt.after_hashes", after_hashes),
        ):
            if rel in mapping and mapping[rel] is None:
                raise ValueError(
                    f"{label}[{rel!r}] must not be null for a read path"
                )

    if set(source_hashes) != set(pinned_paths):
        raise ValueError(
            "packet.source_hashes must pin exactly the packet read/edit paths"
        )
    if before_hashes != source_hashes:
        raise ValueError("receipt.before_hashes does not match packet.source_hashes")
    if set(after_hashes) != set(before_hashes):
        raise ValueError("receipt.after_hashes must cover the same paths")

    try:
        changed = agent_task.check_scope(before_hashes, after_hashes, edit_paths)
    except ValueError as exc:
        raise ValueError(f"scope check failed: {exc}") from exc

    # Canonical form: changed path -> [before_hash, after_hash]. Equivalent
    # deterministic object forms are accepted so long as they encode exactly
    # the same reconstructed pairs.
    candidates = {
        _canonical_json_hash(
            {rel: [before_hashes[rel], after_hashes[rel]] for rel in changed}
        ),
        _canonical_json_hash(
            {
                rel: {
                    "before": before_hashes[rel],
                    "after": after_hashes[rel],
                }
                for rel in changed
            }
        ),
        _canonical_json_hash(
            [
                {
                    "path": rel,
                    "before": before_hashes[rel],
                    "after": after_hashes[rel],
                }
                for rel in changed
            ]
        ),
    }
    diff_sha = _require_nonempty_str(
        receipt.get("diff_sha256"), "receipt.diff_sha256"
    )
    if diff_sha not in candidates:
        raise ValueError(
            "receipt.diff_sha256 does not match the reconstructed before/after hashes"
        )

    for rel, expected in after_hashes.items():
        _validate_path_string(rel, "receipt.after_hashes key", agent_task)
        agent_task._check_no_symlink(root, rel, "receipt.after_hashes")
        target = root / rel
        if expected is None:
            if target.exists() or target.is_symlink():
                raise ValueError(f"path expected to be absent still exists: {rel!r}")
            continue
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"current path is not a regular file: {rel!r}")
        if _sha256_file(target) != expected:
            raise ValueError(f"current file hash does not match receipt: {rel!r}")

    commands = _require_str_list(
        packet.get("test_commands"),
        "packet.test_commands",
        require_nonempty=True,
    )
    tests = receipt.get("tests")
    if not isinstance(tests, list) or not tests:
        raise ValueError("receipt.tests must be a non-empty list")
    if len(tests) != len(commands):
        raise ValueError(
            f"receipt.tests count {len(tests)} does not match command count "
            f"{len(commands)}"
        )
    for index, (command, result) in enumerate(zip(commands, tests)):
        if not isinstance(result, dict):
            raise ValueError(f"receipt.tests[{index}] must be an object")
        if result.get("command") != command:
            raise ValueError(
                f"receipt.tests[{index}].command does not match the exact command"
            )
        exit_code = result.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise ValueError(
                f"receipt.tests[{index}].exit_code must be an integer"
            )
        if exit_code != 0:
            raise ValueError(
                f"receipt.tests[{index}] did not exit zero: {exit_code}"
            )

    test_exit = receipt.get("test_exit")
    if isinstance(test_exit, bool) or not isinstance(test_exit, int):
        raise ValueError("receipt.test_exit must be an integer")
    if test_exit != 0:
        raise ValueError(f"receipt.test_exit did not exit zero: {test_exit}")

    return {
        "status": PENDING_CODEX_REVIEW,
        "mode": MANUAL_PACKET_ONLY,
        "task_id": task_id,
        "base_sha": head,
        "model": packet_model,
        "changed_paths": changed,
    }


def render_markdown(projection, git_head):
    lines = [
        "# Final task status (05)",
        "",
        f"Source: `{projection['source']}` (read-only; 05 is current mutable state).",
        f"Git HEAD: `{git_head}`",
        f"Stop requested: `{projection['stop_requested']}`",
        f"Active task: `{projection['active_task_id']}`",
        f"Last accepted task: `{projection['last_accepted_task_id']}`",
        "",
        "## Counts",
        "",
    ]
    for status, count in projection["counts"].items():
        lines.append(f"- {status}: {count}")
    ready = ", ".join(f"`{item}`" for item in projection["ready"]) or "none"
    lines += ["", f"Ready now (manual packets only): {ready}", "", "## Tickets", ""]
    for node in projection["tickets"]:
        marker = " READY" if node["ready"] else ""
        lines.append(
            f"- `{node['task_id']}`: `{node['status']}`{marker}"
        )
    lines.append("")
    return "\n".join(lines)


def _print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _command_next(args):
    root = Path(args.root)
    projection = project_status(root)
    ready = projection["ready"]
    _print_json(
        {
            "next": ready[0] if ready else None,
            "ready": ready,
            "base_sha": _git_head(root),
            "mode": MANUAL_PACKET_ONLY,
        }
    )
    return 0


def _command_show(args):
    root = Path(args.root)
    projection = project_status(root)
    node = next(
        (item for item in projection["tickets"] if item["task_id"] == args.task_id),
        None,
    )
    if node is None:
        raise ValueError(f"task is not present in final status: {args.task_id!r}")
    _print_json(
        {
            "task": node,
            "base_sha": _git_head(root),
            "mode": MANUAL_PACKET_ONLY,
        }
    )
    return 0


def _command_summarize(args):
    projection = project_status(Path(args.root))
    _print_json(
        {
            "schema_version": projection["schema_version"],
            "source": projection["source"],
            "counts": projection["counts"],
            "ready": projection["ready"],
            "stop_requested": projection["stop_requested"],
            "active_task_id": projection["active_task_id"],
            "last_accepted_task_id": projection["last_accepted_task_id"],
        }
    )
    return 0


def _command_check(args):
    _print_json(
        check_manual(
            Path(args.root), args.task_id, args.packet, args.receipt
        )
    )
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Read-only final task projection and manual packet verification "
            "(stdlib only)."
        )
    )
    parser.add_argument("--root", default=".", help="repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    next_parser = subparsers.add_parser(
        "next", help="emit the next READY task for a manual packet"
    )
    next_parser.set_defaults(handler=_command_next)

    show_parser = subparsers.add_parser(
        "show", help="show one final task plus the base HEAD"
    )
    show_parser.add_argument("task_id")
    show_parser.set_defaults(handler=_command_show)

    summarize_parser = subparsers.add_parser(
        "summarize", help="emit a concise final readiness summary"
    )
    summarize_parser.set_defaults(handler=_command_summarize)

    check_parser = subparsers.add_parser(
        "check", help="verify a manual packet and proof receipt"
    )
    check_parser.add_argument("task_id")
    check_parser.add_argument("--packet", required=True)
    check_parser.add_argument("--receipt", required=True)
    check_parser.set_defaults(handler=_command_check)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ValueError as exc:
        print(f"final_task error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
