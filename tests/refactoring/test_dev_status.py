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


def test_bool_stage_count_refused(tmp_path):
    tickets, stage = _simple_canonical()
    stage["counts"]["COMPLETE_LOCAL_IMPLEMENTATION"] = True
    _write_canonical(tmp_path, tickets, stage)
    try:
        dev_status.load_status(tmp_path)
    except ValueError as exc:
        assert "nonnegative int" in str(exc)
    else:
        raise AssertionError("boolean stage count accepted")


def test_null_task_record_refused(tmp_path):
    tickets, stage = _canonical([None], stage_tasks=[])
    _write_canonical(tmp_path, tickets, stage)
    try:
        dev_status.load_status(tmp_path)
    except ValueError as exc:
        assert "must be an object" in str(exc)
    else:
        raise AssertionError("null task record accepted")


def test_null_stage_record_refused(tmp_path):
    tickets, stage = _simple_canonical()
    stage["tasks"][0] = None
    _write_canonical(tmp_path, tickets, stage)
    try:
        dev_status.load_status(tmp_path)
    except ValueError as exc:
        assert "stage_status tasks[] must be an object" in str(exc)
    else:
        raise AssertionError("null stage record accepted")


def test_tooling_blocked_statuses_never_ready(tmp_path):
    tooling = [
        _tooling_ticket("R01", "BLOCKED", []),
        _tooling_ticket("R02", "REWORK_REQUIRED", []),
        _tooling_ticket("R03", "EXTERNAL", []),
    ]
    tickets, stage = _simple_canonical(tooling=tooling)
    _write_canonical(tmp_path, tickets, stage)
    status = dev_status.load_status(tmp_path)
    nodes = {n["task_id"]: n for n in status["nodes"]}
    for task_id in ("R01", "R02"):
        assert nodes[task_id]["state"] == "BLOCKED"
        assert nodes[task_id]["ready"] is False
    assert nodes["R03"]["state"] == "EXTERNAL"
    assert nodes["R03"]["ready"] is False
    assert status["ready"] == ["VN01"]


def test_unknown_tooling_status_refused(tmp_path):
    tickets, stage = _simple_canonical(
        tooling=[_tooling_ticket("R01", "PRODUCTION_BLOCKED", [])],
    )
    _write_canonical(tmp_path, tickets, stage)
    try:
        dev_status.load_status(tmp_path)
    except ValueError as exc:
        assert "unknown tooling status" in str(exc)
    else:
        raise AssertionError("unknown tooling status accepted")


def test_implemented_and_verified_wait_for_acceptance(tmp_path):
    tickets, stage = _simple_canonical(tooling=[
        _tooling_ticket("R01", "IMPLEMENTED", []),
        _tooling_ticket("R02", "VERIFIED", []),
    ])
    _write_canonical(tmp_path, tickets, stage)
    status = dev_status.load_status(tmp_path)
    nodes = {node["task_id"]: node for node in status["nodes"]}
    assert nodes["R01"]["state"] == "IMPLEMENTED"
    assert nodes["R02"]["state"] == "VERIFIED"
    assert not nodes["R01"]["ready"] and not nodes["R02"]["ready"]
    assert status["ready"] == ["VN01"]


def test_paused_when_one_start_flag_false(tmp_path):
    tickets, stage = _simple_canonical(
        execution_control=_execution_control(status=ACTIVE, start=True),
        stage_control=_execution_control(status=ACTIVE, start=False),
    )
    _write_canonical(tmp_path, tickets, stage)
    status = dev_status.load_status(tmp_path)
    assert status["paused"] is True
    assert status["ready"] == []
    assert status["execution_control"]["stage_status_status"] == ACTIVE


def test_real_copied_canonical_cli(tmp_path):
    canonical = REPO_ROOT / TICKETS_DIR
    directory = tmp_path / TICKETS_DIR
    directory.mkdir(parents=True)
    for name in ("tickets_production.json", "stage_status.json"):
        source = canonical / name
        assert source.is_file(), f"missing real canonical file: {source}"
        (directory / name).write_bytes(source.read_bytes())

    tickets = json.loads((directory / "tickets_production.json").read_text(encoding="utf-8"))
    stage = json.loads((directory / "stage_status.json").read_text(encoding="utf-8"))
    tasks = tickets["tasks"]
    tooling = tickets.get("tooling_tasks", [])
    stage_by_id = {item["task_id"]: item["category"] for item in stage["tasks"]}

    status = dev_status.load_status(tmp_path)

    # Shape and counts must agree with the copied canonical metadata; no evolving
    # total or status ID is frozen in this live-source smoke test.
    assert status["node_totals"] == {"VN_TX": len(tasks), "RO": len(tooling)}
    assert len(status["nodes"]) == len(tasks) + len(tooling)
    expected_counts = {}
    for item in stage["tasks"]:
        expected_counts[item["category"]] = expected_counts.get(item["category"], 0) + 1
    assert status["counts"] == expected_counts
    assert sum(status["counts"].values()) == len(tasks)

    nodes = {node["task_id"]: node for node in status["nodes"]}
    assert set(nodes) == {t["task_id"] for t in tasks} | {t["task_id"] for t in tooling}
    for task in tasks:
        node = nodes[task["task_id"]]
        assert node["family"] == "VN/TX"
        assert node["stage"] == stage_by_id[task["task_id"]]
        assert node["stage"] in dev_status.ORIGINAL_STAGES
        assert node["complete"] is (node["stage"] == dev_status.COMPLETE_STAGE)
    for task in tooling:
        node = nodes[task["task_id"]]
        assert node["family"] == "R/O"
        assert node["status"] in dev_status.TOOLING_STATUSES
        assert node["complete"] is (node["status"] == dev_status.TOOLING_COMPLETE_STATUS)

    expected_complete = {
        task["task_id"] for task in tasks
        if stage_by_id[task["task_id"]] == dev_status.COMPLETE_STAGE
    } | {
        task["task_id"] for task in tooling
        if task["status"] == dev_status.TOOLING_COMPLETE_STATUS
    }
    assert {node["task_id"] for node in status["nodes"] if node["complete"]} == expected_complete
    assert status["last_completed_ticket"] == tickets["execution_control"]["last_completed_ticket"]
    assert status["last_completed_tooling_ticket"] == tickets["execution_control"].get(
        "last_completed_tooling_ticket"
    )

    # READY is derived consistently: unique, dependency-free of missing work and
    # dispatchable. VN15 is therefore ready when its prerequisites are complete.
    assert status["ready"] == [node["task_id"] for node in status["nodes"] if node["ready"]]
    assert len(status["ready"]) == len(set(status["ready"]))
    for task_id in status["ready"]:
        node = nodes[task_id]
        assert node["state"] == "READY"
        assert node["missing_dependencies"] == []
        assert all(nodes[dep]["complete"] for dep in node["depends_on"])
    for node in status["nodes"]:
        if (node["complete"] or node["running"] or node["external"]
                or node["missing_dependencies"] or status["paused"]):
            assert not node["ready"]
    assert status["running"] == [node["task_id"] for node in status["nodes"] if node["running"]]

    result = subprocess.run(
        [sys.executable, str(STATUS_SCRIPT), "--root", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    projection = json.loads(result.stdout)
    assert projection["node_totals"] == status["node_totals"]
    assert projection["counts"] == status["counts"]
    assert projection["ready"] == status["ready"]
    assert projection["paused"] == status["paused"]
