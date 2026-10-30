"""OPS-01 tests: final 05 projection and bounded manual proof gates."""

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FINAL_SCRIPT = REPO_ROOT / "scripts" / "dev" / "final_task.py"
STATUS_SCRIPT = REPO_ROOT / "scripts" / "dev" / "status.py"
FINAL_REL = Path("docs") / "final" / "05_TASK_STATUS.json"

_spec = importlib.util.spec_from_file_location("dev_final_task", FINAL_SCRIPT)
final = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(final)

MODEL = "opencode/deepseek-v4.1-flash-free"
COMMAND = "python3 -m pytest -q tests/refactoring/test_final_tasks.py"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical(value):
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _diff_hash(changed, before, after):
    return _canonical(
        {rel: [before[rel], after[rel]] for rel in changed}
    )


def _ticket(task_id, status="NOT_STARTED", deps=(), **overrides):
    record = {
        "task_id": task_id,
        "status": status,
        "depends_on": list(deps),
        "definition_file": f"docs/final/tasks/{task_id}.md",
        "definition_heading_id": f"{task_id}-heading",
        "required_for_offline_release": True,
        "base_sha": None,
        "actual_commit_sha": None,
        "acceptance_receipt": None,
        "test_exit": None,
        "blocking_reasons": [],
    }
    record.update(overrides)
    return record


def _dag():
    return [
        _ticket("A"),
        _ticket("B", deps=["A"]),
    ]


def _final_value(
    tickets,
    *,
    stop_requested=False,
    active_task_id=None,
    last_accepted_task_id=None,
):
    return {
        "schema_version": 1,
        "tickets": tickets,
        "stop_requested": stop_requested,
        "active_task_id": active_task_id,
        "last_accepted_task_id": last_accepted_task_id,
    }


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _write_final(root, tickets, **overrides):
    return _write_json(Path(root) / FINAL_REL, _final_value(tickets, **overrides))


def _copy_canonical(root):
    for rel in (
        Path("docs/refactoring/vnext/tickets_production.json"),
        Path("docs/refactoring/vnext/stage_status.json"),
    ):
        destination = Path(root) / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO_ROOT / rel).read_bytes())


def _write_file(root, rel, content):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
    )


def _commit(root):
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.email=ops01@example.invalid",
        "-c",
        "user.name=OPS-01 Test",
        "commit",
        "-q",
        "-m",
        "base",
    )
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _projection(root, tickets, **overrides):
    _write_final(root, tickets, **overrides)
    return final.project_status(root)


def test_two_ticket_dag_only_first_ticket_ready(tmp_path):
    projection = _projection(tmp_path, _dag())
    assert projection["ready"] == ["A"]
    nodes = {node["task_id"]: node for node in projection["tickets"]}
    assert nodes["A"]["ready"] is True
    assert nodes["B"]["ready"] is False
    assert nodes["B"]["missing_dependencies"] == ["A"]

    tickets = [_ticket("A", "ACCEPTED_LOCAL"), _ticket("B", deps=["A"])]
    projection = _projection(tmp_path, tickets)
    assert projection["ready"] == ["B"]


def test_stop_and_active_gates_block_readiness(tmp_path):
    assert _projection(tmp_path, _dag(), stop_requested=True)["ready"] == []
    assert _projection(tmp_path, _dag(), active_task_id="A")["ready"] == []
    assert _projection(tmp_path, _dag(), active_task_id="B")["ready"] == []


def test_optional_external_and_scope_decisions_are_not_ready(tmp_path):
    tickets = [
        _ticket("KR-DATA-01"),
        _ticket("EXT", "EXTERNAL_NOT_AUTHORIZED"),
        _ticket("EXT2", "EXTERNAL_VERIFICATION_PENDING"),
        _ticket("SCOPE", "NOT_APPLICABLE_APPROVED"),
        _ticket("OPS", "OPERATIONAL_ACCEPTED"),
        _ticket("IN_PROGRESS", "IN_PROGRESS"),
        _ticket("BLOCKED", "BLOCKED"),
    ]
    projection = _projection(tmp_path, tickets)
    assert projection["ready"] == []


@pytest.mark.parametrize("case", ["duplicate", "missing_dependency", "cycle", "status"])
def test_invalid_final_graphs_fail_closed(tmp_path, case):
    if case == "duplicate":
        tickets = [_ticket("A"), _ticket("A")]
    elif case == "missing_dependency":
        tickets = [_ticket("A", deps=["MISSING"])]
    elif case == "cycle":
        tickets = [_ticket("A", deps=["B"]), _ticket("B", deps=["A"])]
    else:
        tickets = [_ticket("A", "UNKNOWN_STATE")]
    _write_final(tmp_path, tickets)
    with pytest.raises(ValueError):
        final.project_status(tmp_path)


def test_unknown_active_id_fails_closed(tmp_path):
    _write_final(tmp_path, _dag(), active_task_id="NOT-A-TICKET")
    with pytest.raises(ValueError, match="unknown id"):
        final.project_status(tmp_path)


def test_status_cli_projects_final_when_directory_exists(tmp_path):
    _write_final(tmp_path, _dag())
    result = subprocess.run(
        [sys.executable, str(STATUS_SCRIPT), "--root", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    projection = json.loads(result.stdout)
    assert projection["source"] == str(FINAL_REL)
    assert projection["ready"] == ["A"]

    result = subprocess.run(
        [sys.executable, str(STATUS_SCRIPT), "--root", str(tmp_path), "--markdown"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Final task status" in result.stdout


@pytest.mark.parametrize("broken", ["missing", "malformed"])
def test_status_cli_fails_closed_on_broken_final_state(tmp_path, broken):
    (tmp_path / "docs" / "final").mkdir(parents=True)
    if broken == "malformed":
        (tmp_path / FINAL_REL).write_text("{not json", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(STATUS_SCRIPT), "--root", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "status error" in result.stderr


@pytest.mark.parametrize("broken", ["file", "broken_symlink", "symlink_dir"])
def test_status_cli_never_falls_back_when_final_present_invalid(tmp_path, broken):
    _copy_canonical(tmp_path)
    final_dir = tmp_path / "docs" / "final"
    if broken == "file":
        final_dir.write_text("broken authority", encoding="utf-8")
    elif broken == "broken_symlink":
        final_dir.symlink_to(
            tmp_path / "missing-target", target_is_directory=True
        )
    else:
        external = tmp_path / "external"
        _write_final(external, _dag())
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        final_dir.symlink_to(
            external / "docs" / "final", target_is_directory=True
        )
    result = subprocess.run(
        [sys.executable, str(STATUS_SCRIPT), "--root", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "status error" in result.stderr


def test_project_status_rejects_symlinked_final_ancestor(tmp_path):
    external = tmp_path / "external"
    _write_final(external, _dag())
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "final").symlink_to(
        external / "docs" / "final", target_is_directory=True
    )
    with pytest.raises(ValueError, match="symlink"):
        final.project_status(root)


def test_nonempty_blocking_reason_prevents_ready(tmp_path):
    projection = _projection(
        tmp_path,
        [_ticket("A", blocking_reasons=["unresolved safety gate"])],
    )
    assert projection["ready"] == []


def _check_case(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _write_file(root, "docs/notes.txt", "notes\n")
    _write_file(root, "src/a.py", "before\n")
    _write_final(root, _dag())
    head = _commit(root)

    before = {
        "docs/notes.txt": _sha(root / "docs/notes.txt"),
        "src/a.py": _sha(root / "src/a.py"),
    }
    _write_file(root, "src/a.py", "after\n")
    after = {
        "docs/notes.txt": before["docs/notes.txt"],
        "src/a.py": _sha(root / "src/a.py"),
    }
    changed = ["src/a.py"]
    diff = _diff_hash(changed, before, after)

    packet = {
        "task_id": "A",
        "base_sha": head,
        "model": MODEL,
        "read_paths": ["docs/notes.txt"],
        "edit_paths": ["src/a.py"],
        "forbidden_paths": [".env*", "secrets/**"],
        "source_hashes": dict(before),
        "test_commands": [COMMAND],
    }
    receipt = {
        "task_id": "A",
        "base_sha": head,
        "model": MODEL,
        "before_hashes": dict(before),
        "after_hashes": dict(after),
        "diff_sha256": diff,
        "tests": [{"command": COMMAND, "exit_code": 0}],
        "test_exit": 0,
    }
    return {
        "root": root,
        "head": head,
        "before": before,
        "after": after,
        "packet": packet,
        "receipt": receipt,
    }


def _run_check(case, packet=None, receipt=None, task_id="A"):
    packet_path = case["root"] / "packet.json"
    receipt_path = case["root"] / "receipt.json"
    _write_json(packet_path, packet if packet is not None else case["packet"])
    _write_json(receipt_path, receipt if receipt is not None else case["receipt"])
    return final.check_manual(
        case["root"], task_id, packet_path, receipt_path
    )


def test_check_manual_success_is_pending_review_only(tmp_path):
    case = _check_case(tmp_path)
    result = _run_check(case)
    assert result["status"] == "PENDING_CODEX_REVIEW"
    assert result["mode"] == "MANUAL_PACKET_ONLY"
    assert result["task_id"] == "A"
    assert result["base_sha"] == case["head"]
    assert result["changed_paths"] == ["src/a.py"]
    assert "ACCEPTED" not in json.dumps(result)


def test_check_manual_rejects_null_pinned_read_path(tmp_path):
    case = _check_case(tmp_path)
    packet = copy.deepcopy(case["packet"])
    receipt = copy.deepcopy(case["receipt"])
    packet["read_paths"] = ["docs/absent.txt"]
    for mapping in (
        packet["source_hashes"],
        receipt["before_hashes"],
        receipt["after_hashes"],
    ):
        mapping.pop("docs/notes.txt")
        mapping["docs/absent.txt"] = None
    with pytest.raises(ValueError, match="must not be null for a read path"):
        _run_check(case, packet, receipt)


def test_check_cli_success_and_forgery_are_bounded(tmp_path):
    case = _check_case(tmp_path)
    packet_path = _write_json(case["root"] / "packet.json", case["packet"])
    receipt_path = _write_json(case["root"] / "receipt.json", case["receipt"])

    result = subprocess.run(
        [
            sys.executable,
            str(FINAL_SCRIPT),
            "--root",
            str(case["root"]),
            "check",
            "A",
            "--packet",
            str(packet_path),
            "--receipt",
            str(receipt_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "PENDING_CODEX_REVIEW"

    forged = copy.deepcopy(case["receipt"])
    forged["diff_sha256"] = "f" * 64
    _write_json(receipt_path, forged)
    result = subprocess.run(
        [
            sys.executable,
            str(FINAL_SCRIPT),
            "--root",
            str(case["root"]),
            "check",
            "A",
            "--packet",
            str(packet_path),
            "--receipt",
            str(receipt_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "final_task error" in result.stderr


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda p, r: r.update(diff_sha256="f" * 64), "diff_sha256"),
        (lambda p, r: p.update(base_sha="0" * 40), "base_sha mismatch"),
        (lambda p, r: p.update(model="opencode/not-allowed"), "not allowed"),
        (lambda p, r: r.update(model="freellmapi/glm-4.5-flash"),
         "model mismatch"),
        (lambda p, r: r.update(before_hashes=dict(r["before_hashes"],
                                                  **{"src/a.py": "a" * 64})),
         "before_hashes"),
        (lambda p, r: r["tests"][0].update(exit_code=1), "did not exit zero"),
        (lambda p, r: r.update(tests=[]), "non-empty"),
        (lambda p, r: r.update(test_exit=True), "test_exit"),
        (lambda p, r: p.pop("read_paths"), "read_paths"),
        (lambda p, r: p.update(forbidden_paths=["src/**"]), "forbidden"),
        (lambda p, r: r["after_hashes"].update({"src/extra.py": "b" * 64}),
         "same paths"),
        (lambda p, r: r.update(task_id="B"), "task_id mismatch"),
    ],
)
def test_check_manual_rejects_forged_or_mismatched_evidence(
    tmp_path, mutate, match
):
    case = _check_case(tmp_path)
    packet = copy.deepcopy(case["packet"])
    receipt = copy.deepcopy(case["receipt"])
    mutate(packet, receipt)
    with pytest.raises(ValueError, match=match):
        _run_check(case, packet, receipt)


def test_check_manual_requires_actual_receipt_file(tmp_path):
    case = _check_case(tmp_path)
    packet_path = _write_json(case["root"] / "packet.json", case["packet"])
    missing = case["root"] / "absent-receipt.json"
    with pytest.raises(ValueError, match="existing regular file"):
        final.check_manual(case["root"], "A", packet_path, missing)


def test_check_manual_rejects_symlinked_receipt(tmp_path):
    case = _check_case(tmp_path)
    packet_path = _write_json(case["root"] / "packet.json", case["packet"])
    real = _write_json(case["root"] / "real-receipt.json", case["receipt"])
    link = case["root"] / "receipt.json"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        final.check_manual(case["root"], "A", packet_path, link)


def test_check_manual_rejects_current_hash_drift(tmp_path):
    case = _check_case(tmp_path)
    _write_file(case["root"], "src/a.py", "drifted\n")
    with pytest.raises(ValueError, match="current file hash"):
        _run_check(case)


def test_check_manual_rejects_unexpected_read_edit(tmp_path):
    case = _check_case(tmp_path)
    _write_file(case["root"], "docs/notes.txt", "changed\n")
    receipt = copy.deepcopy(case["receipt"])
    receipt["after_hashes"]["docs/notes.txt"] = _sha(
        case["root"] / "docs/notes.txt"
    )
    receipt["diff_sha256"] = _diff_hash(
        ("src/a.py", "docs/notes.txt"),
        receipt["before_hashes"],
        receipt["after_hashes"],
    )
    with pytest.raises(ValueError, match="scope check failed"):
        _run_check(case, receipt=receipt)


def test_check_manual_rejects_symlinked_pinned_path(tmp_path):
    case = _check_case(tmp_path)
    link = case["root"] / "docs" / "notes-link.txt"
    link.symlink_to(case["root"] / "docs" / "notes.txt")
    packet = copy.deepcopy(case["packet"])
    receipt = copy.deepcopy(case["receipt"])
    packet["read_paths"] = ["docs/notes-link.txt"]
    packet["source_hashes"] = {"docs/notes-link.txt": case["before"]["docs/notes.txt"],
                               "src/a.py": case["before"]["src/a.py"]}
    receipt["before_hashes"] = dict(packet["source_hashes"])
    receipt["after_hashes"] = {"docs/notes-link.txt": case["after"]["docs/notes.txt"],
                               "src/a.py": case["after"]["src/a.py"]}
    with pytest.raises(ValueError, match="symlink"):
        _run_check(case, packet, receipt)


def test_check_manual_requires_ready_task(tmp_path):
    case = _check_case(tmp_path)
    packet = copy.deepcopy(case["packet"])
    receipt = copy.deepcopy(case["receipt"])
    packet["task_id"] = "B"
    receipt["task_id"] = "B"
    with pytest.raises(ValueError, match="not READY"):
        _run_check(case, packet, receipt, task_id="B")


def test_check_manual_rejects_missing_after_tests(tmp_path):
    case = _check_case(tmp_path)
    receipt = copy.deepcopy(case["receipt"])
    del receipt["tests"]
    with pytest.raises(ValueError, match="non-empty"):
        _run_check(case, receipt=receipt)


def test_projection_rejects_symlink_above_repository_root(tmp_path):
    real_root = tmp_path / "real" / "repo"
    _write_final(real_root, _dag())
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        final.project_status(alias / "repo")
