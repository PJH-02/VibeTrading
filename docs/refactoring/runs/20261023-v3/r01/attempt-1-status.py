#!/usr/bin/env python3
"""Deterministic read-only status projection for the v3 refactoring DAG.

Stdlib only. Reads the two canonical JSON files under docs/refactoring/vnext/
(the original VN/TX stage plan plus R/O tooling tickets) and emits a bounded
projection. It never writes canonical JSON and never infers completion from a
substring of a status string.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

VNEXT_DIR = Path("docs") / "refactoring" / "vnext"
TICKETS_CANONICAL = VNEXT_DIR / "tickets_production.json"
STAGE_CANONICAL = VNEXT_DIR / "stage_status.json"

COMPLETE_STAGE = "COMPLETE_LOCAL_IMPLEMENTATION"
ORIGINAL_STAGES = {COMPLETE_STAGE, "INCOMPLETE", "NOT_STARTED"}
TOOLING_COMPLETE_STATUS = "ACCEPTED"
RUNNING_STATUS = "RUNNING"
ACTIVE_EXECUTION_STATUSES = {"RUNNING_LOCAL_REFACTORING"}
EXTERNAL_TASK_IDS = {"VN20"}

STATE_COMPLETE = "COMPLETE"
STATE_RUNNING = "RUNNING"
STATE_EXTERNAL = "EXTERNAL"
STATE_BLOCKED = "BLOCKED"
STATE_READY = "READY"
STATE_PAUSED = "PAUSED_DISPATCH"


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"canonical file missing: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _require_str(value, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_str_list(value, label: str) -> list:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"{label} must be a list of strings")
    return sorted(value)


def _execution_control(data: dict, label: str) -> dict:
    ec = data.get("execution_control")
    if not isinstance(ec, dict):
        raise ValueError(f"{label}: execution_control object missing")
    status = _require_str(ec.get("status"), f"{label}: execution_control.status")
    if not isinstance(ec.get("start_next_ticket"), bool):
        raise ValueError(f"{label}: execution_control.start_next_ticket must be boolean")
    last = _require_str(ec.get("last_completed_ticket"), f"{label}: execution_control.last_completed_ticket")
    return {
        "status": status,
        "start_next_ticket": ec["start_next_ticket"],
        "last_completed_ticket": last,
        "last_completed_tooling_ticket": ec.get("last_completed_tooling_ticket"),
    }


def _collect_tasks(tickets: dict) -> tuple:
    """Validate IDs, dependencies and cycles; return (original, tooling) records."""
    tasks = tickets.get("tasks")
    tooling = tickets.get("tooling_tasks", [])
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tickets_production.json: tasks must be a non-empty list")
    if not isinstance(tooling, list):
        raise ValueError("tickets_production.json: tooling_tasks must be a list")
    records, tooling_records, seen = [], [], set()
    for raw in tasks:
        records.append(_record(raw, "VN/TX", "tasks", seen))
    for raw in tooling:
        tooling_records.append(_record(raw, "R/O", "tooling_tasks", seen))
    known = seen
    all_records = records + tooling_records
    for rec in all_records:
        for dep in rec["depends_on"]:
            if dep not in known:
                raise ValueError(f"{rec['task_id']} depends on unknown id: {dep}")
    _no_cycles(all_records)
    return records, tooling_records


def _record(raw, family: str, label: str, seen: set) -> dict:
    tid = _require_str(raw.get("task_id"), f"{label}[].task_id")
    if tid in seen:
        raise ValueError(f"duplicate task_id: {tid}")
    seen.add(tid)
    return {
        "task_id": tid,
        "family": family,
        "status": _require_str(raw.get("status"), f"{tid}.status"),
        "depends_on": _require_str_list(raw.get("depends_on"), f"{tid}.depends_on"),
        "stage_classification": raw.get("stage_classification"),
    }


def _no_cycles(records: list) -> None:
    edges = {r["task_id"]: r["depends_on"] for r in records}
    state: dict = {}

    def visit(node: str) -> None:
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


def _stage_authority(stage: dict, records: list) -> tuple:
    """Validate stage_status.json; return (category_by_id, declared_counts)."""
    declared = stage.get("counts")
    tasks = stage.get("tasks")
    if not isinstance(declared, dict) or not declared:
        raise ValueError("stage_status.json: counts object missing")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("stage_status.json: tasks must be a non-empty list")
    category_by_id, tally, seen = {}, {}, set()
    for raw in tasks:
        tid = _require_str(raw.get("task_id"), "stage_status tasks[].task_id")
        if tid in seen:
            raise ValueError(f"duplicate stage task_id: {tid}")
        seen.add(tid)
        category = _require_str(raw.get("category"), f"{tid}.category")
        if category not in ORIGINAL_STAGES:
            raise ValueError(f"{tid}: unknown stage category {category}")
        category_by_id[tid] = category
        tally[category] = tally.get(category, 0) + 1
    original_ids = {r["task_id"] for r in records}
    if seen != original_ids:
        raise ValueError(
            "stage_status.json: stage tasks do not match original VN/TX records "
            f"(missing={sorted(original_ids - seen)}, extra={sorted(seen - original_ids)})"
        )
    for category in sorted(set(declared) | set(tally)):
        declared_value = declared.get(category, 0)
        if not isinstance(declared_value, int) or declared_value != tally.get(category, 0):
            raise ValueError(
                f"stage count mismatch for {category}: declared={declared_value} "
                f"actual={tally.get(category, 0)}"
            )
    return category_by_id, dict(declared)


def load_status(root: Path) -> dict:
    """Build the deterministic read-only status projection (no writes)."""
    root = Path(root)
    tickets_path = root / TICKETS_CANONICAL
    stage_path = root / STAGE_CANONICAL
    tickets = _load_json(tickets_path)
    stage_data = _load_json(stage_path)
    tickets_ec = _execution_control(tickets, str(TICKETS_CANONICAL))
    stage_ec = _execution_control(stage_data, str(STAGE_CANONICAL))
    if tickets_ec["last_completed_ticket"] != stage_ec["last_completed_ticket"]:
        raise ValueError(
            "last_completed_ticket conflict: "
            f"tickets={tickets_ec['last_completed_ticket']} "
            f"stage_status={stage_ec['last_completed_ticket']}"
        )
    records, tooling_records = _collect_tasks(tickets)
    category_by_id, stage_counts = _stage_authority(stage_data, records)

    for rec in records:
        if rec["stage_classification"] is not None and rec["stage_classification"] != category_by_id[rec["task_id"]]:
            raise ValueError(
                f"{rec['task_id']}: stage_classification {rec['stage_classification']!r} "
                f"conflicts with stage category {category_by_id[rec['task_id']]!r}"
            )

    all_records = records + tooling_records
    ticket_ids = set(category_by_id)
    complete_by_id: dict = {}
    for rec in all_records:
        if rec["family"] == "VN/TX":
            complete_by_id[rec["task_id"]] = category_by_id[rec["task_id"]] == COMPLETE_STAGE
        else:
            complete_by_id[rec["task_id"]] = rec["status"] == TOOLING_COMPLETE_STATUS

    paused = any(ec["status"] not in ACTIVE_EXECUTION_STATUSES for ec in (tickets_ec, stage_ec))
    gates_open = not paused and tickets_ec["start_next_ticket"] and stage_ec["start_next_ticket"]

    nodes = []
    for rec in all_records:
        tid = rec["task_id"]
        stage = category_by_id.get(tid)
        running = rec["status"] == RUNNING_STATUS
        external = tid in EXTERNAL_TASK_IDS
        missing = [dep for dep in rec["depends_on"] if not complete_by_id[dep]]
        ready = not complete_by_id[tid] and not running and not external and not missing and gates_open
        if complete_by_id[tid]:
            state = STATE_COMPLETE
        elif running:
            state = STATE_RUNNING
        elif external:
            state = STATE_EXTERNAL
        elif missing:
            state = STATE_BLOCKED
        elif ready:
            state = STATE_READY
        else:
            state = STATE_PAUSED
        nodes.append(
            {
                "task_id": tid,
                "family": rec["family"],
                "status": rec["status"],
                "stage": stage,
                "depends_on": rec["depends_on"],
                "complete": complete_by_id[tid],
                "running": running,
                "external": external,
                "missing_dependencies": missing,
                "state": state,
                "ready": ready,
            }
        )
    tooling_tally: dict = {}
    for node in nodes:
        if node["family"] == "R/O":
            tooling_tally[node["status"]] = tooling_tally.get(node["status"], 0) + 1
    return {
        "schema_version": 1,
        "sources": {"tickets": str(TICKETS_CANONICAL), "stage_status": str(STAGE_CANONICAL)},
        "execution_control": {
            "tickets_status": tickets_ec["status"],
            "stage_status_status": stage_ec["status"],
            "start_next_ticket": {
                "tickets": tickets_ec["start_next_ticket"],
                "stage_status": stage_ec["start_next_ticket"],
            },
            "paused": paused,
        },
        "counts": stage_counts,
        "tooling_counts": tooling_tally,
        "node_totals": {"VN_TX": len(records), "RO": len(tooling_records)},
        "ready": [n["task_id"] for n in nodes if n["ready"]],
        "running": [n["task_id"] for n in nodes if n["running"]],
        "external": [n["task_id"] for n in nodes if n["external"]],
        "nodes": nodes,
        "paused": paused,
        "last_completed_ticket": tickets_ec["last_completed_ticket"],
        "last_completed_tooling_ticket": tickets_ec["last_completed_tooling_ticket"],
    }


def render_markdown(status: dict, git_head: str) -> str:
    lines = [
        "# Local status projection (v3)",
        "",
        f"Canonical sources: `{status['sources']['tickets']}`, `{status['sources']['stage_status']}`; "
        "`context.json` is a bounded derived summary only.",
        f"Git HEAD: `{git_head}`",
        "",
        "## Execution control",
        "",
        f"- tickets: `{status['execution_control']['tickets_status']}`; start_next_ticket="
        f"`{status['execution_control']['start_next_ticket']['tickets']}`",
        f"- stage_status: `{status['execution_control']['stage_status_status']}`; start_next_ticket="
        f"`{status['execution_control']['start_next_ticket']['stage_status']}`",
        f"- dispatch paused: `{status['paused']}`",
        "",
        "## Stage counts (VN/TX)",
        "",
    ]
    for category in sorted(status["counts"]):
        lines.append(f"- {category}: {status['counts'][category]}")
    tooling = ", ".join(f"{name} {value}" for name, value in sorted(status["tooling_counts"].items())) or "none"
    lines += [
        f"- tooling (R/O): {tooling}",
        f"- originals plus tooling: {status['node_totals']['VN_TX']} VN/TX nodes, "
        f"{status['node_totals']['RO']} R/O nodes",
        "",
        "## Progress",
        "",
        f"- last completed ticket: `{status['last_completed_ticket']}`; last completed tooling "
        f"ticket: `{status['last_completed_tooling_ticket']}`",
        f"- running (no duplicate dispatch): {', '.join(f'`{x}`' for x in status['running']) or 'none'}",
        f"- ready now (dispatch unlocked): {', '.join(f'`{x}`' for x in status['ready']) or 'none'}",
        f"- external (never auto-run): {', '.join(f'`{x}`' for x in status['external']) or 'none'}",
        "",
        "Local/operational distinction: `COMPLETE_LOCAL_IMPLEMENTATION` marks local implementation",
        "acceptance only; production, deploy and long-duration resource gates remain open. The",
        "canonical record keeps the original VN/TX status strings, e.g. VN07 stays `INCOMPLETE` and",
        "VN20 actual cutover/deletion is EXTERNAL and is never executed automatically.",
        "",
    ]
    return "\n".join(lines)


def _git_head(root: Path) -> str:
    """Record the current Git HEAD; when unavailable, UNKNOWN is explicit."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    if completed.returncode != 0:
        return "UNKNOWN"
    return completed.stdout.strip() or "UNKNOWN"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only v3 status projection (stdlib only).")
    parser.add_argument("--root", default=".", help="repository root to read canonical JSON from")
    parser.add_argument("--json", action="store_true", help="emit JSON projection (default)")
    parser.add_argument("--markdown", action="store_true", help="render concise markdown status")
    args = parser.parse_args(argv)
    try:
        status = load_status(Path(args.root))
    except ValueError as exc:
        print(f"status error: {exc}", file=sys.stderr)
        return 2
    git_head = _git_head(Path(args.root))
    if args.markdown:
        print(render_markdown(status, git_head), end="")
    else:
        status["git_head"] = git_head
        print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
