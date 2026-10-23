"""O01A tests: bounded task packets and deterministic review gates."""

import hashlib
import importlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "dev" / "agent_task.py"

_spec = importlib.util.spec_from_file_location("dev_agent_task", SCRIPT)
dev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dev)

ACTIVE = "RUNNING_LOCAL_REFACTORING"
STOPPED = "STOPPED_AT_USER_REQUEST"
TICKETS_DIR = Path("docs") / "refactoring" / "vnext"

CANON_FORBIDDEN = [
    ".env*",
    "**/secrets/**",
    "runtime_data/**",
    ".git/**",
    "docs/refactoring/runs/20261018-stage-assignment/**",
    "docs/evidence/**",
    "server writes",
    "real orders",
    "external messages",
    "production data deletion",
    "KR missing-minute repair",
    "any product/config file outside edit_paths",
]


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _execution_control(status=ACTIVE, start=True, last="VN00", last_tooling="O00"):
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


def _ticket(
    task_id,
    status,
    deps,
    classification,
    *,
    edit_paths=None,
    read_paths=None,
    forbidden=None,
    contracts=None,
):
    record = {
        "task_id": task_id,
        "status": status,
        "depends_on": deps,
        "baseline": {"head": "0" * 40},
        "stage_classification": classification,
    }
    if edit_paths is not None:
        record["edit_paths"] = edit_paths
    if read_paths is not None:
        record["read_paths"] = read_paths
    if forbidden is not None:
        record["forbidden_paths"] = forbidden
    if contracts is not None:
        record["contracts"] = contracts
    return record


def _tooling(task_id, status, deps, *, edit_paths=None, contracts=None, forbidden=None):
    record = {
        "task_id": task_id,
        "status": status,
        "depends_on": deps,
        "scope": "LOCAL_ONLY",
        "receipt": None,
    }
    if edit_paths is not None:
        record["edit_paths"] = edit_paths
    if contracts is not None:
        record["contracts"] = contracts
    if forbidden is not None:
        record["forbidden_paths"] = forbidden
    return record


def _default_tasks(vn00_category="COMPLETE_LOCAL_IMPLEMENTATION"):
    return [
        _ticket(
            "VN00",
            "COMPLETE_CURRENT_READ_ONLY_BASELINE",
            [],
            vn00_category,
        ),
        _ticket(
            "VN01",
            "PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES",
            ["VN00"],
            "NOT_STARTED",
            edit_paths=[
                {"path": "src/target.py", "action": "MODIFY"},
                {"path": "src/new.py", "action": "ADD"},
                {"path": "src/gone.py", "action": "MODIFY"},
            ],
            read_paths=["src/target.py"],
            forbidden=CANON_FORBIDDEN,
            contracts=["C01", "C02"],
        ),
    ]


def _default_tooling(extra=None):
    records = [
        _tooling("R00", "ACCEPTED", []),
        _tooling("R01", "ACCEPTED", ["R00"]),
        _tooling("O00", "ACCEPTED", ["R00"]),
        _tooling(
            "O99",
            "PLANNED",
            ["R01", "O00"],
            edit_paths=[{"path": "tools/ro.py", "action": "MODIFY"}],
            contracts=["C10"],
            forbidden=CANON_FORBIDDEN,
        ),
    ]
    if extra:
        records.extend(extra)
    return records


def _default_profiles():
    return {
        "state_machine": [
            "opencode/model-a",
            "opencode/model-b",
            "opencode/model-c",
            "opencode/catalog-only",
        ]
    }


def _default_readiness():
    return [
        {
            "model_id": "opencode/model-a",
            "status": "QUALIFIED_O00",
            "provider_route": "opencode",
        },
        {
            "model_id": "opencode/model-b",
            "status": "QUALIFIED_O00",
            "provider_route": "opencode",
        },
        {
            "model_id": "opencode/model-c",
            "status": "REJECTED_O00",
            "provider_route": "opencode",
        },
    ]


def _canonical(
    tasks,
    tooling,
    *,
    profiles=None,
    readiness=None,
    tickets_ec=None,
    stage_ec=None,
    last="VN00",
    last_tooling="O00",
):
    stage_tasks = [
        {"task_id": task["task_id"], "category": task["stage_classification"]}
        for task in tasks
    ]
    counts = {}
    for item in stage_tasks:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    tickets_ec = tickets_ec or _execution_control(
        last=last, last_tooling=last_tooling
    )
    stage_ec = stage_ec or _execution_control(last=last, last_tooling=last_tooling)
    tickets = {
        "schema_version": 1,
        "tasks": tasks,
        "tooling_tasks": tooling,
        "execution_control": tickets_ec,
        "model_routing_profiles": profiles or _default_profiles(),
        "model_readiness": readiness if readiness is not None else _default_readiness(),
    }
    stage = {
        "schema_version": 1,
        "counts": counts,
        "tasks": stage_tasks,
        "execution_control": stage_ec,
    }
    return tickets, stage


def _write_canonical(root: Path, tickets: dict, stage: dict) -> None:
    directory = root / TICKETS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tickets_production.json").write_text(
        json.dumps(tickets), encoding="utf-8"
    )
    (directory / "stage_status.json").write_text(
        json.dumps(stage), encoding="utf-8"
    )


def _write_file(root: Path, rel: str, content: str = "sample\n") -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


PATH_MAP_REL = "docs/refactoring/vnext/path_map.json"


def _path_map(**overrides):
    value = {
        "schema_version": 1,
        "root_moves": [
            {
                "from": "vibetrading/",
                "to": "src/vibetrading/",
                "applies_to": "One-to-one namespace root move only",
            }
        ],
        "files": [
            {
                "from": "vibetrading/target.py",
                "to": "src/vibetrading/target.py",
                "move_only_sha256": "a" * 64,
                "move_only_ast_sha256": "b" * 64,
            }
        ],
        "ambiguous_targets": [],
    }
    value.update(overrides)
    return value


def _write_path_map(root: Path, value=None) -> Path:
    return _write_file(root, PATH_MAP_REL, json.dumps(value or _path_map()))


def _git(root: Path, *args):
    return subprocess.run(
        ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
    )


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _commit(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "user.name=O01A Test",
        "commit",
        "-q",
        "-m",
        "base",
    )
    return _head(root)


def _setup(
    tmp_path,
    *,
    tasks=None,
    tooling=None,
    profiles=None,
    readiness=None,
    tickets_ec=None,
    stage_ec=None,
    files=None,
    commit=True,
):
    tasks = tasks if tasks is not None else _default_tasks()
    tooling = tooling if tooling is not None else _default_tooling()
    files = dict(files or {})
    files.setdefault("src/target.py", "print('target')\n")
    files.setdefault("tools/ro.py", "print('ro')\n")
    files.setdefault("src/other.py", "print('other')\n")
    files.setdefault("docs/notes.md", "notes\n")
    for rel, content in files.items():
        _write_file(tmp_path, rel, content)
    tickets, stage = _canonical(
        tasks,
        tooling,
        profiles=profiles,
        readiness=readiness,
        tickets_ec=tickets_ec,
        stage_ec=stage_ec,
    )
    _write_canonical(tmp_path, tickets, stage)
    if commit:
        _commit(tmp_path)
    return tmp_path


def _default_spec(**overrides):
    spec = {
        "objective": "Implement the bounded unit",
        "read_paths": ["src/target.py"],
        "edit_paths": ["src/target.py"],
        "forbidden_paths": list(CANON_FORBIDDEN),
        "contract_ids": ["C01", "C02"],
        "implementation_steps": ["make the change"],
        "acceptance": ["focused tests pass"],
        "test_commands": [
            "python3 -m pytest -q tests/refactoring/test_agent_task.py"
        ],
        "stop_conditions": ["scope ambiguity"],
        "review_severity": "critical",
        "profile": "state_machine",
        "model": "opencode/model-a",
    }
    spec.update(overrides)
    return spec


def _ro_spec(**overrides):
    spec = _default_spec(
        read_paths=["tools/ro.py"],
        edit_paths=["tools/ro.py"],
        contract_ids=["C10"],
    )
    spec.update(overrides)
    return spec


def _mapped_tasks():
    tasks = _default_tasks()
    tasks[1]["read_paths"] = ["vibetrading/target.py", "vibetrading/pkg/mod.py"]
    tasks[1]["edit_paths"] = [
        {"path": "vibetrading/target.py", "action": "MODIFY"},
        {"path": "vibetrading/new.py", "action": "ADD"},
        {"path": "vibetrading/pkg/", "action": "MODIFY"},
    ]
    return tasks


def _mapped_files(extra=None):
    files = {
        "src/vibetrading/target.py": "print('target')\n",
        "src/vibetrading/pkg/mod.py": "print('mod')\n",
    }
    files.update(extra or {})
    return files


def _mapped_setup(tmp_path, *, tasks=None, files=None, map_value=None):
    root = _setup(
        tmp_path,
        tasks=tasks or _mapped_tasks(),
        files=_mapped_files(files),
    )
    _write_path_map(root, map_value)
    return root


def _mapped_spec(**overrides):
    spec = _default_spec(
        read_paths=["vibetrading/target.py", "vibetrading/pkg/mod.py"],
        edit_paths=[
            "vibetrading/target.py",
            "vibetrading/new.py",
            "vibetrading/pkg/mod.py",
        ],
    )
    spec.update(overrides)
    return spec


def _mapped_current_spec(**overrides):
    spec = _default_spec(
        read_paths=["src/vibetrading/target.py", "src/vibetrading/pkg/mod.py"],
        edit_paths=[
            "src/vibetrading/target.py",
            "src/vibetrading/new.py",
            "src/vibetrading/pkg/mod.py",
        ],
    )
    spec.update(overrides)
    return spec


def _results(commands):
    return [
        {
            "command": command,
            "exit_code": 0,
            "stdout_sha256": hashlib.sha256(command.encode()).hexdigest(),
        }
        for command in commands
    ]


def _cli(root: Path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_make_packet_ready_with_exact_hashes(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(
        root, "VN01", _default_spec(edit_paths=["src/target.py", "src/new.py"])
    )

    assert packet["task_id"] == "VN01"
    assert packet["family"] == "VN/TX"
    assert packet["base_sha"] == _head(root)
    assert packet["prerequisites"] == ["VN00"]
    assert packet["provider"] == "opencode"
    assert packet["route"] == "opencode"
    assert packet["model"] == "opencode/model-a"
    assert packet["source_hashes"]["src/target.py"] == _sha(root / "src/target.py")
    assert packet["source_hashes"]["src/new.py"] is None
    assert packet["canonical_hashes"]["docs/refactoring/vnext/tickets_production.json"] == _sha(
        root / "docs/refactoring/vnext/tickets_production.json"
    )
    assert packet["canonical_hashes"]["docs/refactoring/vnext/stage_status.json"] == _sha(
        root / "docs/refactoring/vnext/stage_status.json"
    )


def test_path_map_translates_exact_prefix_and_directory_scope(tmp_path):
    root = _mapped_setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _mapped_spec())

    assert packet["read_paths"] == [
        "src/vibetrading/target.py",
        "src/vibetrading/pkg/mod.py",
    ]
    assert packet["edit_paths"] == [
        "src/vibetrading/target.py",
        "src/vibetrading/new.py",
        "src/vibetrading/pkg/mod.py",
    ]
    assert (
        packet["source_hashes"]["src/vibetrading/target.py"]
        == _sha(root / "src/vibetrading/target.py")
    )
    assert packet["source_hashes"]["src/vibetrading/new.py"] is None
    assert (
        packet["source_hashes"]["src/vibetrading/pkg/mod.py"]
        == _sha(root / "src/vibetrading/pkg/mod.py")
    )
    assert PATH_MAP_REL in packet["canonical_hashes"]
    assert packet["canonical_hashes"][PATH_MAP_REL] == _sha(root / PATH_MAP_REL)
    assert dev.verify_source(root, packet) is True


def test_path_map_old_and_current_specs_converge(tmp_path):
    root = _mapped_setup(tmp_path)
    old = dev.make_packet(root, "VN01", _mapped_spec())
    current = dev.make_packet(root, "VN01", _mapped_current_spec())

    assert old["read_paths"] == current["read_paths"]
    assert old["edit_paths"] == current["edit_paths"]
    assert old["source_hashes"] == current["source_hashes"]
    assert old["contract_ids"] == current["contract_ids"]


def test_path_map_does_not_expand_canonical_scope(tmp_path):
    root = _mapped_setup(
        tmp_path,
        files={"src/vibetrading/other.py": "print('other')\n"},
    )
    with pytest.raises(ValueError, match="outside canonical scope"):
        dev.make_packet(
            root,
            "VN01",
            _mapped_spec(edit_paths=["vibetrading/other.py"]),
        )


def test_path_map_translates_canonical_forbidden_under_old_prefix(tmp_path):
    forbidden = list(CANON_FORBIDDEN) + ["vibetrading/private/**"]
    tasks = _mapped_tasks()
    tasks[1]["forbidden_paths"] = forbidden
    root = _mapped_setup(tmp_path, tasks=tasks)

    with pytest.raises(ValueError, match="forbidden pattern"):
        dev.make_packet(
            root,
            "VN01",
            _mapped_spec(
                read_paths=["vibetrading/private/secret.py"],
                forbidden_paths=forbidden,
            ),
        )

    packet = dev.make_packet(
        root, "VN01", _mapped_spec(forbidden_paths=forbidden)
    )
    assert "vibetrading/private/**" in packet["forbidden_paths"]
    assert "src/vibetrading/private/**" in packet["forbidden_paths"]

    translated = [
        "src/vibetrading/private/**" if item == "vibetrading/private/**" else item
        for item in forbidden
    ]
    translated_packet = dev.make_packet(
        root, "VN01", _mapped_spec(forbidden_paths=translated)
    )
    assert "vibetrading/private/**" in translated_packet["forbidden_paths"]
    assert "src/vibetrading/private/**" in translated_packet["forbidden_paths"]


@pytest.mark.parametrize(
    "map_value,match",
    [
        (
            _path_map(ambiguous_targets=["vibetrading/target.py"]),
            "ambiguous_targets",
        ),
        (
            _path_map(
                files=[
                    {"from": "vibetrading/x.py", "to": "src/a.py"},
                    {"from": "vibetrading/x.py", "to": "src/b.py"},
                ]
            ),
            "conflicting files entries",
        ),
        (
            _path_map(
                files=[
                    {"from": "vibetrading/target.py", "to": "src/other/target.py"}
                ]
            ),
            "conflicting target candidates",
        ),
        (
            _path_map(
                root_moves=[
                    {"from": "vibetrading/", "to": "src/vibetrading/"},
                    {"from": "vibetrading/pkg/", "to": "lib/pkg/"},
                ]
            ),
            "conflicting overlapping root_moves",
        ),
        (_path_map(root_moves="vibetrading/"), "root_moves must be a list"),
        (
            _path_map(files=[{"from": "vibetrading/x.py"}]),
            "files\\[0\\]\\.to",
        ),
        (
            _path_map(files=[{"from": "../escape.py", "to": "src/x.py"}]),
            "traversal",
        ),
        (
            _path_map(
                root_moves=[{"from": "/absolute/", "to": "src/absolute/"}]
            ),
            "relative",
        ),
        (
            _path_map(files=[{"from": "vibetrading/x.py", "to": ".env"}]),
            "blocked",
        ),
        (_path_map(schema_version=2), "schema_version"),
    ],
)
def test_invalid_path_map_fails_closed(tmp_path, map_value, match):
    root = _setup(tmp_path)
    _write_path_map(root, map_value)
    with pytest.raises(ValueError, match=match):
        dev.make_packet(root, "VN01", _default_spec())


def test_path_map_symlink_is_rejected_before_reading(tmp_path):
    root = _setup(tmp_path)
    outside = tmp_path / "outside-map.json"
    outside.write_text(json.dumps(_path_map()), encoding="utf-8")
    target = root / PATH_MAP_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        dev.make_packet(root, "VN01", _default_spec())


def test_verify_source_rejects_path_map_change_and_removal(tmp_path):
    root = _mapped_setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _mapped_spec())
    map_path = root / PATH_MAP_REL
    original = map_path.read_text(encoding="utf-8")

    map_path.write_text(original + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="path_map"):
        dev.verify_source(root, packet)

    map_path.write_text(original, encoding="utf-8")
    assert dev.verify_source(root, packet) is True

    map_path.unlink()
    with pytest.raises(ValueError, match="path_map"):
        dev.verify_source(root, packet)


def test_verify_source_rejects_path_map_added_after_pin(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    assert PATH_MAP_REL not in packet["canonical_hashes"]

    _write_path_map(root, _path_map())
    with pytest.raises(ValueError, match="path_map"):
        dev.verify_source(root, packet)


def test_path_map_digest_comes_from_decoded_generation(tmp_path, monkeypatch):
    root = _mapped_setup(tmp_path)
    map_path = root / PATH_MAP_REL
    replacement = map_path.read_text(encoding="utf-8").replace(
        "src/vibetrading/", "other/vibetrading/"
    )
    real_validate = dev._validate_path_map

    def validate_then_replace(value):
        validated = real_validate(value)
        map_path.write_text(replacement, encoding="utf-8")
        return validated

    monkeypatch.setattr(dev, "_validate_path_map", validate_then_replace)
    mapping, digest = dev._load_path_map(root)

    assert mapping["root_moves"] == [("vibetrading/", "src/vibetrading/")]
    assert digest != _sha(map_path)


@pytest.mark.parametrize("action", ["add", "change", "remove"])
def test_make_packet_fences_path_map_generation(tmp_path, monkeypatch, action):
    root = _setup(tmp_path)
    map_path = root / PATH_MAP_REL
    if action != "add":
        _write_path_map(root, _path_map())
    original_load = dev.status_mod.load_status

    def mutate_then_return(current_root):
        result = original_load(current_root)
        if action == "add":
            _write_path_map(current_root, _path_map())
        elif action == "change":
            map_path.write_text(
                json.dumps(_path_map(root_moves=[])), encoding="utf-8"
            )
        else:
            map_path.unlink()
        return result

    monkeypatch.setattr(dev.status_mod, "load_status", mutate_then_return)
    with pytest.raises(ValueError, match="canonical metadata changed"):
        dev.make_packet(root, "VN01", _default_spec())


def test_no_path_map_preserves_legacy_old_paths(tmp_path):
    tasks = _default_tasks()
    tasks[1]["read_paths"] = ["vibetrading/target.py"]
    tasks[1]["edit_paths"] = [{"path": "vibetrading/target.py", "action": "MODIFY"}]
    root = _setup(
        tmp_path,
        tasks=tasks,
        files={"vibetrading/target.py": "print('legacy')\n"},
    )

    packet = dev.make_packet(
        root,
        "VN01",
        _default_spec(
            read_paths=["vibetrading/target.py"],
            edit_paths=["vibetrading/target.py"],
        ),
    )
    assert packet["read_paths"] == ["vibetrading/target.py"]
    assert packet["edit_paths"] == ["vibetrading/target.py"]
    assert PATH_MAP_REL not in packet["canonical_hashes"]


def test_ro_ready_packet_uses_registered_scope(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "O99", _ro_spec())
    assert packet["family"] == "R/O"
    assert packet["prerequisites"] == ["R01", "O00"]
    assert packet["edit_paths"] == ["tools/ro.py"]


@pytest.mark.parametrize(
    "case",
    [
        "paused",
        "false_predecessor",
        "running",
        "blocked",
        "external",
        "pending_implemented",
        "pending_verified",
    ],
)
def test_make_packet_rejects_non_ready_tasks(tmp_path, case):
    if case == "paused":
        stopped = _execution_control(status=STOPPED)
        root = _setup(tmp_path, tickets_ec=stopped, stage_ec=stopped)
        target, spec = "O99", _ro_spec()
    elif case == "false_predecessor":
        root = _setup(tmp_path, tasks=_default_tasks("INCOMPLETE"))
        target, spec = "VN01", _default_spec()
    else:
        status = {
            "running": "RUNNING",
            "blocked": "BLOCKED",
            "external": "EXTERNAL",
            "pending_implemented": "IMPLEMENTED",
            "pending_verified": "VERIFIED",
        }[case]
        tooling = _default_tooling(
            [
                _tooling(
                    "O98",
                    status,
                    ["R01", "O00"],
                    edit_paths=[{"path": "tools/ro.py", "action": "MODIFY"}],
                    contracts=["C10"],
                    forbidden=CANON_FORBIDDEN,
                )
            ]
        )
        root = _setup(tmp_path, tooling=tooling)
        target, spec = "O98", _ro_spec()

    with pytest.raises(ValueError, match="not READY"):
        dev.make_packet(root, target, spec)


@pytest.mark.parametrize(
    "profile,model,match",
    [
        ("state_machine", "opencode/not-listed", "not registered"),
        ("different-profile", "opencode/model-a", "not registered"),
        ("state_machine", "opencode/model-c", "not QUALIFIED_O00"),
        ("state_machine", "opencode/catalog-only", "exactly one"),
    ],
)
def test_make_packet_rejects_unqualified_or_wrong_model(tmp_path, profile, model, match):
    root = _setup(tmp_path)
    with pytest.raises(ValueError, match=match):
        dev.make_packet(
            root, "VN01", _default_spec(profile=profile, model=model)
        )


def test_make_packet_rejects_mismatched_canonical_provider_route(tmp_path):
    profiles = _default_profiles()
    profiles["state_machine"] = list(profiles["state_machine"]) + [
        "opencode/model-mismatch"
    ]
    readiness = _default_readiness() + [
        {
            "model_id": "opencode/model-mismatch",
            "status": "QUALIFIED_O00",
            "provider_route": "freellmapi",
        }
    ]
    root = _setup(tmp_path, profiles=profiles, readiness=readiness)

    with pytest.raises(ValueError, match="does not match"):
        dev.make_packet(
            root,
            "VN01",
            _default_spec(model="opencode/model-mismatch"),
        )


def test_make_packet_preserves_original_contracts_and_forbidden(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(
        root,
        "VN01",
        _default_spec(contract_ids=["C99", "C02", "C01"]),
    )
    assert set(["C01", "C02"]).issubset(set(packet["contract_ids"]))
    assert packet["forbidden_paths"] == CANON_FORBIDDEN


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"contract_ids": ["C01"]}, "retain canonical contracts"),
        ({"forbidden_paths": CANON_FORBIDDEN[:-1]}, "retain canonical restrictions"),
    ],
)
def test_make_packet_rejects_lost_contracts_or_forbidden(tmp_path, overrides, match):
    root = _setup(tmp_path)
    with pytest.raises(ValueError, match=match):
        dev.make_packet(root, "VN01", _default_spec(**overrides))


def test_make_packet_reads_tx_contract_ids_without_contracts(tmp_path):
    tasks = _default_tasks()
    tasks.append(
        _ticket(
            "TX00",
            "PLANNED_LOCAL_AUTHORIZED_SUBJECT_TO_PREREQUISITES",
            [],
            "NOT_STARTED",
            edit_paths=[{"path": "src/target.py", "action": "MODIFY"}],
            read_paths=["src/target.py"],
            forbidden=CANON_FORBIDDEN,
        )
    )
    tasks[-1]["contract_ids"] = ["C10"]
    assert "contracts" not in tasks[-1]
    root = _setup(tmp_path, tasks=tasks)

    with pytest.raises(ValueError, match="retain canonical contracts"):
        dev.make_packet(root, "TX00", _default_spec())

    packet = dev.make_packet(
        root, "TX00", _default_spec(contract_ids=["C01", "C02", "C10"])
    )
    assert "C10" in packet["contract_ids"]


def test_make_packet_preserves_union_of_both_contract_fields(tmp_path):
    tasks = _default_tasks()
    tasks[1]["contract_ids"] = ["C10"]
    root = _setup(tmp_path, tasks=tasks)

    with pytest.raises(ValueError, match="retain canonical contracts"):
        dev.make_packet(root, "VN01", _default_spec())

    packet = dev.make_packet(
        root, "VN01", _default_spec(contract_ids=["C99", "C02", "C10", "C01"])
    )
    assert {"C01", "C02", "C10"}.issubset(set(packet["contract_ids"]))


def test_make_packet_rejects_non_list_contract_fields(tmp_path):
    tasks = _default_tasks()
    tasks[1]["contract_ids"] = "C10"
    root = _setup(tmp_path, tasks=tasks)
    with pytest.raises(ValueError, match="contract_ids must be a list"):
        dev.make_packet(root, "VN01", _default_spec())


def test_make_packet_rejects_missing_ro_scope(tmp_path):
    tooling = _default_tooling()
    for record in tooling:
        if record["task_id"] == "O99":
            record.pop("edit_paths", None)
    root = _setup(tmp_path, tooling=tooling)
    with pytest.raises(ValueError, match="no canonical concrete edit_paths"):
        dev.make_packet(root, "O99", _ro_spec())


def test_make_packet_rejects_out_of_scope_and_missing_modify(tmp_path):
    root = _setup(tmp_path)
    with pytest.raises(ValueError, match="outside canonical scope"):
        dev.make_packet(
            root, "VN01", _default_spec(edit_paths=["src/other.py"])
        )
    with pytest.raises(ValueError, match="requires canonical ADD"):
        dev.make_packet(
            root, "VN01", _default_spec(edit_paths=["src/gone.py"])
        )


@pytest.mark.parametrize(
    "rel",
    [
        "/etc/passwd",
        r"C:\temp\escape.py",
        "../escape.py",
        "src/*.py",
        "",
        ".",
        "src/./target.py",
        "src//target.py",
        ".env.local",
        "secrets/key.txt",
        "runtime_data/state.json",
        ".ssh/id_rsa",
        "certs/server.pem",
        "credentials.json",
    ],
)
def test_make_packet_rejects_unsafe_paths(tmp_path, rel):
    root = _setup(tmp_path)
    with pytest.raises(ValueError):
        dev.make_packet(root, "VN01", _default_spec(read_paths=[rel]))


def test_make_packet_rejects_symlink_components(tmp_path):
    root = _setup(tmp_path)
    (root / "linkdir").symlink_to("src")
    (root / "src" / "link.py").symlink_to("target.py")
    for rel in ("linkdir/target.py", "src/link.py"):
        with pytest.raises(ValueError, match="symlink"):
            dev.make_packet(root, "VN01", _default_spec(read_paths=[rel]))


@pytest.mark.parametrize("nested", [False, True])
def test_make_packet_rejects_symlink_root_or_ancestor_before_reads(
    tmp_path, monkeypatch, nested
):
    repo = _setup(tmp_path / "repo")
    link = tmp_path / "root-link"
    link.symlink_to(repo, target_is_directory=True)
    target = link
    if nested:
        (repo / "nested").mkdir()
        target = link / "nested"

    def forbidden(*args, **kwargs):
        raise AssertionError("canonical or git read before root denial")

    monkeypatch.setattr(dev.status_mod, "load_status", forbidden)
    monkeypatch.setattr(dev, "_git_head", forbidden)
    monkeypatch.setattr(dev, "_load_json_file", forbidden)

    with pytest.raises(ValueError, match="symlink"):
        dev.make_packet(target, "VN01", _default_spec())


def test_make_packet_rejects_empty_or_missing_root(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        dev.make_packet("", "VN01", _default_spec())
    with pytest.raises(ValueError, match="does not exist"):
        dev.make_packet(tmp_path / "missing", "VN01", _default_spec())


def test_verify_source_rejects_symlink_root_before_reads(tmp_path, monkeypatch):
    repo = _setup(tmp_path / "repo")
    packet = dev.make_packet(repo, "VN01", _default_spec())
    link = tmp_path / "root-link"
    link.symlink_to(repo, target_is_directory=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("canonical or git read before root denial")

    monkeypatch.setattr(dev, "_git_head", forbidden)
    monkeypatch.setattr(dev, "_load_json_file", forbidden)

    with pytest.raises(ValueError, match="symlink"):
        dev.verify_source(link, packet)


@pytest.mark.parametrize(
    "canonical_name",
    ["tickets_production.json", "stage_status.json"],
)
@pytest.mark.parametrize("operation", ["make_packet", "verify_source"])
def test_canonical_symlink_denied_before_reads(
    tmp_path, monkeypatch, canonical_name, operation
):
    root = _setup(tmp_path)
    packet = (
        dev.make_packet(root, "VN01", _default_spec())
        if operation == "verify_source"
        else None
    )
    canonical = root / TICKETS_DIR / canonical_name
    outside = tmp_path / ("copy-" + canonical_name)
    outside.write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8")
    canonical.unlink()
    canonical.symlink_to(outside)

    def forbidden(*args, **kwargs):
        raise AssertionError("canonical data read before symlink denial")

    monkeypatch.setattr(dev.status_mod, "load_status", forbidden)
    monkeypatch.setattr(dev, "_git_head", forbidden)
    monkeypatch.setattr(dev, "_load_json_file", forbidden)

    with pytest.raises(ValueError, match="symlink"):
        if operation == "make_packet":
            dev.make_packet(root, "VN01", _default_spec())
        else:
            dev.verify_source(root, packet)


def test_make_packet_rejects_concrete_forbidden_path(tmp_path):
    root = _setup(
        tmp_path, files={"docs/evidence/leak.md": "evidence\n"}
    )
    with pytest.raises(ValueError, match="forbidden pattern"):
        dev.make_packet(
            root,
            "VN01",
            _default_spec(read_paths=["docs/evidence/leak.md"]),
        )


def test_check_scope_returns_sorted_allowed_changes():
    snapshot = {"src/a.py": "old-a", "src/b.py": None}
    after = {"src/a.py": "new-a", "src/b.py": "new-b", "src/d.py": "new-d"}
    changed = dev.check_scope(
        snapshot, after, ["src/d.py", "src/a.py", "src/b.py"]
    )
    assert changed == ["src/a.py", "src/b.py", "src/d.py"]


@pytest.mark.parametrize(
    "snapshot,after,allowed,match",
    [
        (
            {"src/a.py": "old"},
            {"src/a.py": "new", "src/extra.py": "new"},
            ["src/a.py"],
            "out-of-scope",
        ),
        (
            {"src/a.py": "old"},
            {"src/a.py": None},
            ["src/other.py"],
            "out-of-scope",
        ),
        (
            {"src/a.py": "old"},
            {"../escape.py": "new"},
            ["../escape.py"],
            "traversal",
        ),
        (
            {"src/a.py": "old"},
            {"src/a.py": "new"},
            ["src/*.py"],
            "glob",
        ),
    ],
)
def test_check_scope_rejects_out_of_scope_and_unsafe(snapshot, after, allowed, match):
    with pytest.raises(ValueError, match=match):
        dev.check_scope(snapshot, after, allowed)


def test_verify_source_detects_concurrent_edit_with_same_head(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    head = _head(root)
    _write_file(root, "src/target.py", "print('changed without commit')\n")
    assert _head(root) == head
    with pytest.raises(ValueError, match="source file changed"):
        dev.verify_source(root, packet)


def test_verify_source_detects_modified_canonical_hash(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    canonical = root / TICKETS_DIR / "tickets_production.json"
    canonical.write_text(canonical.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="canonical metadata changed"):
        dev.verify_source(root, packet)


def test_verify_source_success(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    assert dev.verify_source(root, packet) is True


def test_verify_source_requires_exact_pin_coverage(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(
        root, "VN01", _default_spec(edit_paths=["src/target.py", "src/new.py"])
    )

    empty = dict(packet)
    empty["source_hashes"] = {}
    with pytest.raises(ValueError, match="missing pinned paths"):
        dev.verify_source(root, empty)

    dropped = dict(packet)
    dropped["source_hashes"] = dict(packet["source_hashes"])
    dropped["source_hashes"].pop("src/new.py")
    with pytest.raises(ValueError, match="missing pinned paths"):
        dev.verify_source(root, dropped)

    extra = dict(packet)
    extra["source_hashes"] = dict(packet["source_hashes"])
    extra["source_hashes"]["src/other.py"] = _sha(root / "src/other.py")
    with pytest.raises(ValueError, match="extra pinned paths"):
        dev.verify_source(root, extra)


@pytest.mark.parametrize(
    "source_hashes,match",
    [
        ({"../escape.py": "a" * 64}, "traversal"),
        ({"src/target.py": "A" * 64}, "64-character"),
    ],
)
def test_verify_source_rejects_unsafe_or_invalid_pins(tmp_path, source_hashes, match):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    packet["source_hashes"] = source_hashes
    with pytest.raises(ValueError, match=match):
        dev.verify_source(root, packet)


def test_verify_source_rejects_duplicate_pinned_paths(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    packet["read_paths"] = list(packet["read_paths"]) + ["src/target.py"]
    with pytest.raises(ValueError, match="duplicate pinned path"):
        dev.verify_source(root, packet)


def test_check_tests_verified_exact_ordered():
    commands = ["python3 -m pytest -q a.py", "python3 -m pytest -q b.py"]
    verified = dev.check_tests(commands, _results(commands))
    assert verified["status"] == "VERIFIED_TESTS"
    assert verified["commands"] == commands


@pytest.mark.parametrize(
    "commands,results,match",
    [
        ([], [], "non-empty"),
        (["python3 -m pytest -q a.py"], [], "count"),
        (
            ["python3 -m pytest -q a.py"],
            [
                {
                    "command": "python3 -m pytest -q a.py",
                    "exit_code": 0,
                    "stdout_sha256": "a" * 64,
                },
                {
                    "command": "python3 -m pytest -q b.py",
                    "exit_code": 0,
                    "stdout_sha256": "b" * 64,
                },
            ],
            "count",
        ),
        (
            ["python3 -m pytest -q a.py"],
            [
                {
                    "command": "python3 -m pytest -q b.py",
                    "exit_code": 0,
                    "stdout_sha256": "a" * 64,
                }
            ],
            "does not match",
        ),
        (
            ["python3 -m pytest -q a.py"],
            [
                {
                    "command": "python3 -m pytest -q a.py",
                    "exit_code": True,
                    "stdout_sha256": "a" * 64,
                }
            ],
            "must be an integer",
        ),
        (
            ["python3 -m pytest -q a.py"],
            [
                {
                    "command": "python3 -m pytest -q a.py",
                    "exit_code": 1,
                    "stdout_sha256": "a" * 64,
                }
            ],
            "did not exit zero",
        ),
        (
            ["python3 -m pytest -q a.py"],
            [
                {
                    "command": "python3 -m pytest -q a.py",
                    "exit_code": 0,
                    "stdout_sha256": "A" * 64,
                }
            ],
            "64-character",
        ),
    ],
)
def test_check_tests_rejects_invalid_evidence(commands, results, match):
    with pytest.raises(ValueError, match=match):
        dev.check_tests(commands, results)


@pytest.mark.parametrize(
    "exit_code,text,expected",
    [
        (429, "", "PROVIDER_RATE_LIMIT"),
        (None, "rate limit exceeded", "PROVIDER_RATE_LIMIT"),
        (401, "", "AUTH_FAILURE"),
        (None, "invalid api key", "AUTH_FAILURE"),
        (124, "", "EXECUTION_TIMEOUT"),
        (None, "request timed out", "EXECUTION_TIMEOUT"),
        (1, "contract mismatch", "CONTRACT_VIOLATION"),
        (1, "out-of-scope edit", "SCOPE_VIOLATION"),
        (1, "unsafe secret access", "SAFETY_BLOCKER"),
        (1, "external deployment required", "EXTERNAL_ONLY"),
        (1, "pytest test failed", "TEST_FAILURE"),
        (1, "worker crashed in an odd way", "MODEL_TOOL_FAILURE"),
    ],
)
def test_classify_failure(exit_code, text, expected):
    assert dev.classify_failure(exit_code, text) == expected


def test_handoff_contains_bounded_scopes_and_exact_commands(tmp_path):
    root = _setup(tmp_path)
    spec = _default_spec(test_commands=["python3 -m pytest -q first.py"])
    packet = dev.make_packet(root, "VN01", spec)
    text = dev.handoff(packet)
    assert "Implement the bounded unit" in text
    assert "src/target.py" in text
    assert "python3 -m pytest -q first.py" in text
    assert "Codex review" in text


def test_write_record_private_unique_and_nonaccepting(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    commands = packet["test_commands"]
    results = _results(commands)
    canonical_before = (
        root / TICKETS_DIR / "tickets_production.json"
    ).read_bytes()
    state_dir = tmp_path / "private-state"

    first = dev.write_record(
        state_dir, packet, results, decision="PENDING_CODEX_REVIEW"
    )
    second = dev.write_record(
        state_dir, packet, results, decision="PENDING_CODEX_REVIEW"
    )

    assert first["path"] != second["path"]
    assert Path(first["path"]).is_file() and Path(second["path"]).is_file()
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    for receipt_path in (Path(first["path"]), Path(second["path"])):
        assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["decision"] == "PENDING_CODEX_REVIEW"
        assert receipt["provenance"] == "UNTRUSTED_INPUT"
        assert receipt["host_execution_attested"] is False
        assert receipt["packet_sha256"] == dev._canonical_json_hash(packet)
        assert receipt["results_sha256"] == dev._canonical_json_hash(results)
        assert "ACCEPTED" not in receipt["decision"]

    assert (
        root / TICKETS_DIR / "tickets_production.json"
    ).read_bytes() == canonical_before
    with pytest.raises(ValueError, match="decision"):
        dev.write_record(state_dir, packet, results, decision="ACCEPTED")


def test_write_record_rejects_symlink_state_dir_before_touching_target(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    results = _results(packet["test_commands"])
    victim = tmp_path / "victim-state"
    victim.mkdir()
    victim_file = victim / "keep.txt"
    victim_file.write_text("keep\n", encoding="utf-8")
    os.chmod(victim, 0o755)
    os.chmod(victim_file, 0o644)
    before_dir_mode = stat.S_IMODE(victim.stat().st_mode)
    before_content = victim_file.read_bytes()

    link = tmp_path / "state-link"
    link.symlink_to(victim, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        dev.write_record(
            link, packet, results, decision="PENDING_CODEX_REVIEW"
        )

    assert stat.S_IMODE(victim.stat().st_mode) == before_dir_mode
    assert victim_file.read_bytes() == before_content
    assert {item.name for item in victim.iterdir()} == {"keep.txt"}


def test_write_record_rejects_symlink_ancestor_before_mkdir(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    results = _results(packet["test_commands"])
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link-parent"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        dev.write_record(
            link / "state", packet, results, decision="PENDING_CODEX_REVIEW"
        )

    assert not (outside / "state").exists()


def test_write_record_rejects_non_directory_target_or_ancestor(tmp_path):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    results = _results(packet["test_commands"])
    target_file = tmp_path / "state-file"
    target_file.write_text("keep\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        dev.write_record(
            target_file, packet, results, decision="PENDING_CODEX_REVIEW"
        )
    assert target_file.read_text(encoding="utf-8") == "keep\n"

    parent_file = tmp_path / "parent-file"
    parent_file.write_text("keep-parent\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-directory ancestor"):
        dev.write_record(
            parent_file / "state",
            packet,
            results,
            decision="PENDING_CODEX_REVIEW",
        )
    assert parent_file.read_text(encoding="utf-8") == "keep-parent\n"


def _victim_state(tmp_path):
    victim = tmp_path / "victim-state"
    victim.mkdir()
    victim_file = victim / "keep.txt"
    victim_file.write_text("keep\n", encoding="utf-8")
    os.chmod(victim, 0o755)
    os.chmod(victim_file, 0o644)
    return victim, victim_file


def _assert_victim_unchanged(victim, victim_file, mode, content):
    assert stat.S_IMODE(victim.stat().st_mode) == mode
    assert victim_file.read_bytes() == content
    assert {item.name for item in victim.iterdir()} == {"keep.txt"}


def test_write_record_race_open_boundary_never_writes_victim(tmp_path, monkeypatch):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    results = _results(packet["test_commands"])
    victim, victim_file = _victim_state(tmp_path)
    before_mode = stat.S_IMODE(victim.stat().st_mode)
    before_content = victim_file.read_bytes()

    state_dir = tmp_path / "private-state"
    state_dir.mkdir()
    real_open = os.open
    raced = {"done": False}

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        if (
            not raced["done"]
            and path == state_dir.name
            and dir_fd is not None
            and flags & os.O_DIRECTORY
        ):
            raced["done"] = True
            os.rmdir(state_dir)
            os.symlink(victim, state_dir, target_is_directory=True)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(ValueError, match="symlink"):
        dev.write_record(
            state_dir, packet, results, decision="PENDING_CODEX_REVIEW"
        )

    assert raced["done"] is True
    _assert_victim_unchanged(victim, victim_file, before_mode, before_content)


def test_write_record_race_create_boundary_never_writes_victim(
    tmp_path, monkeypatch
):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    results = _results(packet["test_commands"])
    victim, victim_file = _victim_state(tmp_path)
    before_mode = stat.S_IMODE(victim.stat().st_mode)
    before_content = victim_file.read_bytes()

    state_dir = tmp_path / "private-state"
    real_mkdir = os.mkdir
    raced = {"done": False}

    def racing_mkdir(path, mode=0o777, *, dir_fd=None):
        if not raced["done"] and path == state_dir.name and dir_fd is not None:
            raced["done"] = True
            os.symlink(victim, path, target_is_directory=True, dir_fd=dir_fd)
        return real_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", racing_mkdir)
    with pytest.raises(ValueError, match="symlink"):
        dev.write_record(
            state_dir, packet, results, decision="PENDING_CODEX_REVIEW"
        )

    assert raced["done"] is True
    _assert_victim_unchanged(victim, victim_file, before_mode, before_content)


def test_write_record_pinned_leaf_fd_never_writes_victim_after_swap(
    tmp_path, monkeypatch
):
    root = _setup(tmp_path)
    packet = dev.make_packet(root, "VN01", _default_spec())
    results = _results(packet["test_commands"])
    victim, victim_file = _victim_state(tmp_path)
    before_mode = stat.S_IMODE(victim.stat().st_mode)
    before_content = victim_file.read_bytes()

    state_dir = tmp_path / "private-state"
    moved = tmp_path / "moved-private-state"
    real_open = os.open
    raced = {"done": False}

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        if (
            not raced["done"]
            and dir_fd is not None
            and not (flags & os.O_DIRECTORY)
        ):
            raced["done"] = True
            os.rename(state_dir, moved)
            os.symlink(victim, state_dir, target_is_directory=True)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", racing_open)
    dev.write_record(
        state_dir, packet, results, decision="PENDING_CODEX_REVIEW"
    )

    assert raced["done"] is True
    _assert_victim_unchanged(victim, victim_file, before_mode, before_content)
    receipts = list(moved.glob("receipt-*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text(encoding="utf-8"))["decision"] == (
        "PENDING_CODEX_REVIEW"
    )


def test_cli_list_ready_and_packet(tmp_path):
    root = _setup(tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_default_spec()), encoding="utf-8")

    listed = _cli(root, "list-ready")
    assert listed.returncode == 0, listed.stderr
    projection = json.loads(listed.stdout)
    assert "VN01" in projection["ready"]
    assert projection["git_head"] == _head(root)

    built = _cli(root, "packet", "VN01", "--spec", str(spec_path))
    assert built.returncode == 0, built.stderr
    packet = json.loads(built.stdout)
    assert packet["task_id"] == "VN01"
    assert packet["base_sha"] == _head(root)


def test_cli_gates_record_and_handoff(tmp_path):
    root = _setup(tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_default_spec()), encoding="utf-8")
    packet = dev.make_packet(root, "VN01", _default_spec())
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    commands = packet["test_commands"]
    results = _results(commands)
    commands_path = tmp_path / "commands.json"
    results_path = tmp_path / "results.json"
    commands_path.write_text(json.dumps(commands), encoding="utf-8")
    results_path.write_text(json.dumps(results), encoding="utf-8")

    snapshot_path = tmp_path / "snapshot.json"
    after_path = tmp_path / "after.json"
    allowed_path = tmp_path / "allowed.json"
    snapshot_path.write_text(json.dumps({"src/target.py": "old"}), encoding="utf-8")
    after_path.write_text(json.dumps({"src/target.py": "new"}), encoding="utf-8")
    allowed_path.write_text(json.dumps(["src/target.py"]), encoding="utf-8")

    scope = _cli(
        root,
        "check-scope",
        "--snapshot",
        str(snapshot_path),
        "--after",
        str(after_path),
        "--allowed",
        str(allowed_path),
    )
    assert scope.returncode == 0, scope.stderr
    assert json.loads(scope.stdout) == ["src/target.py"]

    tests = _cli(
        root,
        "check-tests",
        "--commands",
        str(commands_path),
        "--results",
        str(results_path),
    )
    assert tests.returncode == 0, tests.stderr
    assert json.loads(tests.stdout)["status"] == "VERIFIED_TESTS"

    record = _cli(
        root,
        "record",
        "--state-dir",
        str(tmp_path / "state"),
        "--packet",
        str(packet_path),
        "--results",
        str(results_path),
    )
    assert record.returncode == 0, record.stderr
    receipt = json.loads(record.stdout)
    assert receipt["decision"] == "PENDING_CODEX_REVIEW"
    assert receipt["provenance"] == "UNTRUSTED_INPUT"

    handoff = _cli(root, "handoff", "--packet", str(packet_path))
    assert handoff.returncode == 0, handoff.stderr
    assert "Codex review" in handoff.stdout


def test_cli_malformed_json_and_invalid_decision_fail(tmp_path):
    root = _setup(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = _cli(root, "packet", "VN01", "--spec", str(bad))
    assert result.returncode != 0
    assert "agent_task error" in result.stderr

    packet = dev.make_packet(root, "VN01", _default_spec())
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    results_path = tmp_path / "results.json"
    results_path.write_text(json.dumps([]), encoding="utf-8")
    rejected = _cli(
        root,
        "record",
        "--state-dir",
        str(tmp_path / "state"),
        "--packet",
        str(packet_path),
        "--results",
        str(results_path),
        "--decision",
        "ACCEPTED",
    )
    assert rejected.returncode != 0
    assert "decision" in rejected.stderr


def test_import_as_scripts_dev_package():
    sys.path.insert(0, str(REPO_ROOT))
    try:
        module = importlib.import_module("scripts.dev.agent_task")
    finally:
        sys.path.pop(0)
    for name in (
        "make_packet",
        "check_scope",
        "verify_source",
        "check_tests",
        "classify_failure",
        "handoff",
        "write_record",
        "main",
    ):
        assert callable(getattr(module, name))


def test_canonical_generation_drift_refuses_stale_ready(tmp_path, monkeypatch):
    root = _setup(tmp_path)
    original = dev.status_mod.load_status

    def read_then_revoke(current_root):
        result = original(current_root)
        path = current_root / dev.status_mod.STAGE_CANONICAL
        stage = json.loads(path.read_text())
        stage["tasks"][0]["category"] = "NOT_STARTED"
        stage["counts"] = {"NOT_STARTED": len(stage["tasks"])}
        path.write_text(json.dumps(stage))
        return result

    monkeypatch.setattr(dev.status_mod, "load_status", read_then_revoke)
    with pytest.raises(ValueError, match="canonical metadata changed"):
        dev.make_packet(root, "VN01", _default_spec())
