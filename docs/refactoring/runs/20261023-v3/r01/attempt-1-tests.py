"""R01 acceptance tests: deterministic read-only status projection."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS_SCRIPT = REPO_ROOT / "scripts" / "dev" / "status.py"

spec = importlib.util.spec_from_file_location("dev_status", STATUS_SCRIPT)
dev_status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev_status)

ACTIVE = "RUNNING_LOCAL_REFACTORING"
STOPPED = "STOPPED_AT_USER_REQUEST"

TICKETS_DIR = "docs/refactoring/vnext"


def _write_canonical(root: Path, tickets: dict, stage: dict) -> None:
    directory = root / TICKETS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tickets_production.json").write_text(json.dumps(tickets), encoding="utf-8")
    (directory / "stage_status.json").write_text(json.dumps(stage), encoding="utf-8")


def _execution_control(status=ACTIVE, start=True, last="VN00", last_tooling="R00"):
    return {
        "status": status,
        "last_completed_ticket": last,
        "last_completed_tooling_ticket": last_tooling,
        "active_ticket": None,
        "start_next_ticket": start,
        "stop_after_ticket": None,
        "server_deploy_authorized": False,
        "live_mutation_authorized": False,
    }


def _ticket(task_id, status, deps, classification):
    return {
        "task_id": task_id,
        "existing_phase": None,
        "status": status,
        "depends_on": deps,
        "baseline": {"head": "0" * 40},
        "stage_classification": classification,
    }


def _tooling_ticket(task_id, status, deps):
    return {"task_id": task_id, "status": status, "depends_on": deps, "scope": "LOCAL_ONLY", "receipt": None}


def _stage_task(task_id, category):
    return {"task_id": task_id, "category": category}


def _canonical(
    tasks,
    tooling=(),
    stage_tasks=None,
    counts=None,
    execution_control=None,
    stage_control=None,
    last="VN00",
    last_tooling="R00",
):
    ec = execution_control or _execution_control(last=last, last_tooling=last_tooling)
    stage_ec = stage_control or _execution_control(last=last, last_tooling=last_tooling)
    if stage_tasks is None:
        stage_tasks = [
            _stage_task(t["task_id"], t["stage_classification"]) for t in tasks
        ]
    if counts is None:
        counts = {}
        for st in stage_tasks:
            counts[st["category"]] = counts.get(st["category"], 0) + 1
    tickets = {
        "schema_version": 1,
        "tasks": tasks,
        "tooling_tasks": list(tooling),
        "execution_control": ec,
    }
    stage = {
        "schema_version": 1,
        "counts": counts,
        "tasks": stage_tasks,
        "execution_control": stage_ec,
    }
    return tickets, stage


def _simple_canonical(tooling=(), **overrides):
    tasks = [
        _ticket("VN00", "COMPLETE_CURRENT_READ_ONLY_BASELINE", [], "COMPLETE_LOCAL_IMPLEMENTATION"),
        _ticket("VN01", "PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES", ["VN00"], "NOT_STARTED"),
        _ticket("VN20", "PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES", ["VN01"], "NOT_STARTED"),
    ]
    tickets, stage = _canonical(tasks, tooling, **overrides)
    stage["tasks"] = stage["tasks"][:2] + [_stage_task("VN20", "NOT_STARTED")]
    return tickets, stage


def test_ready_dependency_satisfaction(tmp_path):
    tickets, stage = _simple_canonical()
    _write_canonical(tmp_path, tickets, stage)
    status = dev_status.load_status(tmp_path)
    node = {n["task_id"]: n for n in status["nodes"]}["VN01"]
    assert node["ready"] is True
    assert node["state"] == "READY"
    assert node["missing_dependencies"] == []
    assert status["ready"] == ["VN01"]
    assert status["paused"] is False


def test_false_predecessor_not_accepted(tmp_path):
    tasks = [
        _ticket("VN00", "PARTIAL_DONE_STATUS", [], "INCOMPLETE"),
        _ticket("VN01", "PLANNED", ["VN00"], "NOT_STARTED"),
    ]
    tickets, stage = _canonical(tasks)
    _write_canonical(tmp_path, tickets, stage)
    status = dev_status.load_status(tmp_path)
    node = {n["task_id"]: n for n in status["nodes"]}["VN01"]
    assert node["ready"] is False
    assert node["state"] == "BLOCKED"
    assert node["missing_dependencies"] == ["VN00"]
    assert status["ready"] == ["VN00"]


def test_duplicate_id_refused(tmp_path):
    tasks = [
        _ticket("VN00", "DONE", [], "COMPLETE_LOCAL_IMPLEMENTATION"),
        _ticket("VN00", "DONE", [], "COMPLETE_LOCAL_IMPLEMENTATION"),
    ]
    tickets, stage = _canonical(tasks)
    stage["tasks"] = [_stage_task("VN00", "COMPLETE_LOCAL_IMPLEMENTATION")]
    _write_canonical(tmp_path, tickets, stage)
    try:
        dev_status.load_status(tmp_path)
    except ValueError as exc:
        assert "duplicate task_id" in str(exc)
    else:
        raise AssertionError("duplicate id accepted")


def test_dangling_dependency_refused(tmp_path):
    tasks = [_ticket("VN01", "PLANNED", ["VNMISSING"], "NOT_STARTED")]
    tickets, stage = _canonical(tasks)
    _write_canonical(tmp_path, tickets, stage)
    try:
        dev_status.load_status(tmp_path)
    except ValueError as exc:
        assert "unknown id" in str(exc)
    else:
        raise AssertionError("dangling dependency accepted")


def test_cycle_refused(tmp_path):
    tasks = [
        _ticket("A", "PLANNED", ["B"], "NOT_STARTED"),
        _ticket("B", "PLANNED", ["A"], "NOT_STARTED"),
    ]
    tickets, stage = _canonical(tasks)
    _write_canonical(tmp_path, tickets, stage)
    try:
        dev_status.load_status(tmp_path)
    except ValueError as exc:
        assert "cycle" in str(exc)
    else:
        raise AssertionError("cycle accepted")


def test_stage_count_mismatch_refused(tmp_path):
    tickets, stage = _simple_canonical()
    stage["counts"]["NOT_STARTED"] = 99
    _write_canonical(tmp_path, tickets, stage)
    try:
        dev_status.load_status(tmp_path)
    except ValueError as exc:
        assert "stage count mismatch" in str(exc)
    else:
        raise AssertionError("count mismatch accepted")


def test_paused_execution_blocks_dispatch(tmp_path):
    tickets, stage = _simple_canonical(
        execution_control=_execution_control(status=STOPPED),
        stage_control=_execution_control(status=STOPPED),
    )
    _write_canonical(tmp_path, tickets, stage)
    status = dev_status.load_status(tmp_path)
    assert status["paused"] is True
    assert status["ready"] == []
    node = {n["task_id"]: n for n in status["nodes"]}["VN01"]
    assert node["ready"] is False
    assert node["state"] == "PAUSED_DISPATCH"


def test_start_next_ticket_false_blocks_dispatch(tmp_path):
    tickets, stage = _simple_canonical(
        execution_control=_execution_control(start=False),
        stage_control=_execution_control(start=False),
    )
    _write_canonical(tmp_path, tickets, stage)
    status = dev_status.load_status(tmp_path)
    assert status["ready"] == []
    assert status["execution_control"]["start_next_ticket"]["tickets"] is False


def test_running_not_ready(tmp_path):
    tickets, stage = _simple_canonical(
        tooling=[_tooling_ticket("O00", "RUNNING", ["VN00"]), _tooling_ticket("R00", "ACCEPTED", [])],
    )
    _write_canonical(tmp_path, tickets, stage)
    status = dev_status.load_status(tmp_path)
    node = {n["task_id"]: n for n in status["nodes"]}["O00"]
    assert node["running"] is True
    assert node["state"] == "RUNNING"
    assert node["ready"] is False
    assert status["running"] == ["O00"]
    assert status["ready"] == ["VN01"]
    tooling = {n["task_id"]: n for n in status["nodes"]}["R00"]
    assert tooling["complete"] is True
    assert tooling["state"] == "COMPLETE"


def test_vn20_external(tmp_path):
    tasks = [
        _ticket("VN00", "COMPLETE_CURRENT_READ_ONLY_BASELINE", [], "COMPLETE_LOCAL_IMPLEMENTATION"),
        _ticket("VN19", "COMPLETE_REVIEW_PACKAGE_PREPARED", [], "COMPLETE_LOCAL_IMPLEMENTATION"),
        _ticket("VN20", "PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES", ["VN19"], "NOT_STARTED"),
    ]
    stage_tasks = [
        _stage_task("VN00", "COMPLETE_LOCAL_IMPLEMENTATION"),
        _stage_task("VN19", "COMPLETE_LOCAL_IMPLEMENTATION"),
        _stage_task("VN20", "NOT_STARTED"),
    ]
    tickets, stage = _canonical(tasks, stage_tasks=stage_tasks)
    _write_canonical(tmp_path, tickets, stage)
    status = dev_status.load_status(tmp_path)
    node = {n["task_id"]: n for n in status["nodes"]}["VN20"]
    assert node["external"] is True
    assert node["ready"] is False
    assert node["state"] == "EXTERNAL"
    assert node["stage"] == "NOT_STARTED"
    assert node["status"] == "PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES"
    assert node["missing_dependencies"] == []
    assert "VN20" in status["external"]


def test_ro_union_without_changing_vn_tx_counts(tmp_path):
    tooling = [
        _tooling_ticket("R00", "ACCEPTED", []),
        _tooling_ticket("R01", "PLANNED", ["R00"]),
        _tooling_ticket("O00", "RUNNING", ["R00"]),
        _tooling_ticket("O01", "PLANNED", ["R01", "O00"]),
    ]
    tickets, stage = _simple_canonical(tooling=tooling)
    _write_canonical(tmp_path, tickets, stage)
    status = dev_status.load_status(tmp_path)
    assert status["node_totals"]["VN_TX"] == 3
    assert status["node_totals"]["RO"] == 4
    for category, value in status["counts"].items():
        assert value == stage["counts"][category]
    assert status["tooling_counts"] == {"ACCEPTED": 1, "PLANNED": 2, "RUNNING": 1}
    o01 = {n["task_id"]: n for n in status["nodes"]}["O01"]
    assert o01["state"] == "BLOCKED"
    assert sorted(o01["missing_dependencies"]) == ["O00", "R01"]
    assert status["ready"] == ["VN01", "R01"]


def test_last_completed_ticket_conflict_refused(tmp_path):
    tickets, stage = _simple_canonical(
        stage_control=_execution_control(last="VN99"),
    )
    _write_canonical(tmp_path, tickets, stage)
    try:
        dev_status.load_status(tmp_path)
    except ValueError as exc:
        assert "conflict" in str(exc)
    else:
        raise AssertionError("conflicting completion records accepted")


def test_real_copied_canonical_cli(tmp_path):
    canonical = REPO_ROOT / TICKETS_DIR
    directory = tmp_path / TICKETS_DIR
    directory.mkdir(parents=True)
    for name in ("tickets_production.json", "stage_status.json"):
        source = canonical / name
        assert source.is_file(), f"missing real canonical file: {source}"
        (directory / name).write_bytes(source.read_bytes())
    status = dev_status.load_status(tmp_path)
    assert status["node_totals"]["VN_TX"] == 51
    assert status["node_totals"]["RO"] == 9
    assert len(status["nodes"]) == 60
    assert status["counts"] == {
        "COMPLETE_LOCAL_IMPLEMENTATION": 14,
        "INCOMPLETE": 23,
        "NOT_STARTED": 14,
    }
    for node in status["nodes"]:
        if node["family"] == "VN/TX":
            assert node["stage"] in {"COMPLETE_LOCAL_IMPLEMENTATION", "INCOMPLETE", "NOT_STARTED"}
    completed = [n for n in status["nodes"] if n["complete"]]
    assert len(completed) == 15
    assert {n["task_id"] for n in completed if n["family"] == "VN/TX"} == {
        "VN00", "VN01", "VN02", "VN03", "VN04A", "VN04B", "VN05", "VN06",
        "VN10", "VN11", "VN12", "VN14A", "VN14B", "VN16",
    }
    assert {n["task_id"] for n in completed if n["family"] == "R/O"} == {"R00"}
    assert status["last_completed_ticket"] == "VN14B"
    assert status["last_completed_tooling_ticket"] == "R00"
    assert {n["task_id"] for n in completed if n["family"] == "R/O"} == {"R00"}
    assert status["ready"] == ["VN07", "TX00", "R01"]
    running = {n["task_id"] for n in status["running"]}
    assert running == {"O00"}
    vn20 = {n["task_id"]: n for n in status["nodes"]}["VN20"]
    assert vn20["state"] == "EXTERNAL" and vn20["ready"] is False

    result = subprocess.run(
        [sys.executable, str(STATUS_SCRIPT), "--root", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    projection = json.loads(result.stdout)
    assert projection["node_totals"] == {"VN_TX": 51, "RO": 9}
