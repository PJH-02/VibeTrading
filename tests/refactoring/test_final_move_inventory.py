"""Bounded tests for the readonly MOV-00 ownership inventory."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts" / "refactoring"
sys.path.insert(0, str(SCRIPT_DIR))

import final_move_inventory as inventory  # noqa: E402


B1 = inventory.normalize_b1_rows


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Inventory Tests")
    for relative, text in files.items():
        _write(root, relative, text)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "tiny fixture")
    return root


def _record(result: dict, old_path: str) -> dict:
    for record in [*result["records"], *result["legacy_records"]]:
        if record["old_path"] == old_path:
            return record
    raise AssertionError("record not found: " + old_path)


def _error_kinds(result: dict) -> set[str]:
    return {error["kind"] for error in result["errors"]}


def test_actual_ticket_document_parses_77_b1_rows_and_8_b2_bundles():
    text = (REPO_ROOT / "docs/final/02_DEVELOPER_TICKETS.md").read_text(
        encoding="utf-8"
    )
    b1 = inventory.parse_b1_document(text)
    b2 = inventory.parse_b2_document(text)
    assert len(b1) == 77
    assert len(b2) == 8
    assert {row["source"] for row in b2} == set(inventory.DEFAULT_B2_BUNDLES)


def test_mapping_relative_imports_and_core_ownership_exception(tmp_path):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/models.py": (
                "class Asset:\n"
                "    pass\n"
                "\n"
                "def make_asset():\n"
                "    return Asset()\n"
            ),
            "src/vibetrading/trading_contracts.py": (
                "from .models import Asset\n"
                "from . import models\n"
                "\n"
                "def contract():\n"
                "    return Asset\n"
            ),
            "src/vibetrading/cli.py": (
                "from importlib import import_module\n"
                "import os\n"
                "\n"
                "name = os.environ['DYNAMIC_MODULE']\n"
                "import_module('vibetrading.models')\n"
                "import_module(name)\n"
            ),
            "src/consumer.py": "from vibetrading.models import Asset\n",
            "tests/test_models.py": "import vibetrading.models\n",
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1(
            [
                ("models.py", "core/models.py", "core"),
                ("cli.py", "runtime/cli.py", "runtime"),
                ("trading_contracts.py", "core/trading_contracts.py", "core"),
            ]
        ),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )

    models = _record(result, "src/vibetrading/models.py")
    assert models["new_path"] == "src/core/models.py"
    assert "src/vibetrading/trading_contracts.py" in models["active_callers"]
    assert "src/vibetrading/cli.py" in models["active_callers"]
    assert "src/consumer.py" in models["active_callers"]
    assert "tests/test_models.py" in models["tests"]
    assert "Asset" in models["owning_symbol"]
    assert "make_asset" in models["owning_symbol"]

    contracts = _record(result, "src/vibetrading/trading_contracts.py")
    assert contracts["new_path"] == "src/execution/trading_contracts.py"
    assert contracts["owner"] == "execution"
    assert "Codex ownership exception" in contracts["reason"]

    cli = _record(result, "src/vibetrading/cli.py")
    expressions = {ref["expression"] for ref in cli["dynamic_refs"]}
    assert "vibetrading.models" in expressions
    assert "UNKNOWN" in expressions
    unknown = [
        entry
        for entry in result["dynamic_exceptions"]
        if entry["source"] == "src/vibetrading/cli.py"
    ]
    assert unknown and unknown[0]["blocking_wave"] == "MOV-05/CLN-02"
    assert unknown[0]["disposition"] == "DEFERRED_WITH_BLOCKER"
    # An explicit dynamic exception is classified, not a missing path.
    assert result["summary"]["missing_or_ambiguous"] == 0
    assert result["exit_code"] == 0


def test_all_eight_b2_packages_and_four_mm_bundles_deferred(tmp_path):
    files = {
        "src/vibetrading/providers/__init__.py": "",
        "src/vibetrading/providers/bybit.py": "VALUE = 1\n",
        "src/vibetrading/strategies/__init__.py": "",
        "src/vibetrading/dashboard/__init__.py": "",
        "src/vibetrading/timefolio/__init__.py": "",
        "src/vibetrading/fv_mm/__init__.py": "",
        "src/vibetrading/fv_pilot/__init__.py": "",
        "src/vibetrading/mm_state/__init__.py": "",
        "src/vibetrading/liquidity_mm/__init__.py": "",
    }
    root = _repo(tmp_path, files)
    result = inventory.run_inventory(
        root, b1_rows=[], b2_rows=inventory.DEFAULT_B2_BUNDLES
    )

    assert result["summary"]["subpackages"] == 8
    provider = _record(result, "src/vibetrading/providers/bybit.py")
    assert provider["new_path"] == "src/feeds/providers/bybit.py"
    assert provider["owner"] == "feeds"

    assert _record(
        result, "src/vibetrading/strategies/__init__.py"
    )["new_path"] == "src/strategies/__init__.py"
    assert _record(
        result, "src/vibetrading/dashboard/__init__.py"
    )["new_path"] == "src/operations/dashboard/__init__.py"
    assert _record(
        result, "src/vibetrading/timefolio/__init__.py"
    )["new_path"] == "src/timefolio/__init__.py"

    for bundle in ("fv_mm", "fv_pilot", "mm_state", "liquidity_mm"):
        record = _record(result, f"src/vibetrading/{bundle}/__init__.py")
        assert record["new_path"] is None
        assert record["disposition"] == "DEFERRED_WITH_BLOCKER"
        assert "MOV-05/CLN-02" in record["reason"]
        assert "mixed" in record["reason"]

    assert {item["source"] for item in result["b2_bundles"]} == set(
        inventory.DEFAULT_B2_BUNDLES
    )


def test_legacy_root_packages_are_deferred_with_inferred_owners(tmp_path):
    root = _repo(
        tmp_path,
        {
            "services/data_feed/feed.py": "VALUE = 1\n",
            "services/execution/order.py": "VALUE = 2\n",
            "services/monitoring/health.py": "VALUE = 3\n",
            "services/signal_gen/signal.py": "VALUE = 4\n",
            "shared/common.py": "VALUE = 5\n",
            "strategies/old.py": "VALUE = 6\n",
            "strategy/legacy.py": "VALUE = 7\n",
        },
    )
    result = inventory.run_inventory(
        root, b1_rows=[], b2_rows=inventory.DEFAULT_B2_BUNDLES
    )

    assert result["summary"]["legacy_files"] == 7
    expected = {
        "services/data_feed/feed.py": ("feeds", "src/feeds/feed.py"),
        "services/execution/order.py": (
            "execution",
            "src/execution/order.py",
        ),
        "services/monitoring/health.py": (
            "operations",
            "src/operations/health.py",
        ),
        "services/signal_gen/signal.py": (
            "strategies",
            "src/strategies/signal.py",
        ),
    }
    for path, (owner, proposed) in expected.items():
        record = _record(result, path)
        assert record["owner"] == owner
        assert record["proposed_new_path"] == proposed
        assert record["new_path"] is None
        assert record["disposition"] == "LEGACY_DEFERRED"
        assert "MOV-05/06/CLN-02" in record["reason"]

    for path in ("shared/common.py", "strategies/old.py", "strategy/legacy.py"):
        record = _record(result, path)
        assert record["new_path"] is None
        assert record["disposition"] == "LEGACY_DEFERRED"
    assert _record(result, "strategies/old.py")["owner"] == "strategies"
    assert result["summary"]["missing_or_ambiguous"] == 0


def test_dynamic_constant_and_unknown_are_both_recorded(tmp_path):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/target.py": "VALUE = 1\n",
            "src/vibetrading/loader.py": (
                "from importlib import import_module\n"
                "import builtins\n"
                "\n"
                "import_module('vibetrading.target')\n"
                "builtins.__import__('vibetrading.target')\n"
                "choice = 'vibetrading.target'\n"
                "import_module(choice)\n"
            ),
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1(
            [
                ("target.py", "core/target.py", "core"),
                ("loader.py", "runtime/loader.py", "runtime"),
            ]
        ),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )

    target = _record(result, "src/vibetrading/target.py")
    assert any(
        ref["expression"] == "vibetrading.target"
        for ref in target["dynamic_refs"]
    )
    loader = _record(result, "src/vibetrading/loader.py")
    unknown = [
        ref for ref in loader["dynamic_refs"] if ref["expression"] == "UNKNOWN"
    ]
    assert unknown
    assert unknown[0]["owner"] == "runtime"
    assert unknown[0]["blocking_wave"] == "MOV-05/CLN-02"
    assert len(result["dynamic_exceptions"]) == 1
    assert result["summary"]["missing_or_ambiguous"] == 0


def test_target_collision_and_existing_destination_are_rejected(tmp_path):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/a.py": "A = 1\n",
            "src/vibetrading/b.py": "B = 2\n",
            "src/market/shared.py": "EXISTING = 3\n",
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1(
            [
                ("a.py", "market/shared.py", "market"),
                ("b.py", "market/shared.py", "market"),
            ]
        ),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )

    assert result["summary"]["duplicate_targets"] == 1
    assert "duplicate_target" in _error_kinds(result)
    assert "existing_destination_collision" in _error_kinds(result)
    assert result["summary"]["missing_or_ambiguous"] >= 2
    assert result["exit_code"] == 2


def test_root_drift_reports_missing_and_extra_and_unclassified(tmp_path):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/a.py": "A = 1\n",
            "src/vibetrading/b.py": "B = 2\n",
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1(
            [
                ("a.py", "core/a.py", "core"),
                ("c.py", "core/c.py", "core"),
            ]
        ),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )

    assert result["summary"]["old_path_mismatch"] == 1
    assert result["summary"]["unclassified"] == 1
    assert result["summary"]["missing_or_ambiguous"] == 2
    assert {"missing_root_path", "extra_root_path"} <= _error_kinds(result)
    assert result["exit_code"] == 2


def test_syntax_failure_is_a_hard_inventory_error(tmp_path):
    root = _repo(
        tmp_path,
        {"src/vibetrading/broken.py": "def broken(:\n    pass\n"},
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1([("broken.py", "core/broken.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )

    assert "syntax_error" in _error_kinds(result)
    assert result["summary"]["missing_or_ambiguous"] == 1
    record = _record(result, "src/vibetrading/broken.py")
    assert record["disposition"] == "SYNTAX_ERROR"
    assert record["worktree_sha256"]
    assert record["source_ast_sha256"] is None
    assert result["exit_code"] == 2


def test_worktree_hash_is_distinct_from_head_blob_and_dirty_bytes_are_preserved(
    tmp_path,
):
    root = _repo(
        tmp_path,
        {"src/vibetrading/models.py": "VALUE = 1\n"},
    )
    before = inventory.run_inventory(
        root,
        b1_rows=B1([("models.py", "core/models.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    record_before = _record(before, "src/vibetrading/models.py")

    _write(root, "src/vibetrading/models.py", "VALUE = 2\n")

    after = inventory.run_inventory(
        root,
        b1_rows=B1([("models.py", "core/models.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    record_after = _record(after, "src/vibetrading/models.py")

    assert record_before["source_blob_sha"] == record_after["source_blob_sha"]
    assert record_before["source_sha"] == record_after["source_sha"]
    assert record_before["worktree_sha256"] != record_after["worktree_sha256"]
    assert (root / "src/vibetrading/models.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 2\n"


def test_sensitive_inputs_are_never_read_or_emitted(tmp_path):
    secret = "TOPSECRETVALUE"
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/a.py": "A = 1\n",
            ".env": f"API_KEY={secret}\n",
            ".env.local": f"OTHER={secret}\n",
            "config/app.yaml": (
                f"image: {secret}\n"
                "command: ['python', '-m', 'vibetrading.a']\n"
            ),
            "Dockerfile": (
                "FROM python:3.12\n"
                "COPY src/vibetrading/a.py /app/a.py\n"
                f"ENV API_TOKEN={secret}\n"
                "HEALTHCHECK CMD python -m vibetrading.a\n"
            ),
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1([("a.py", "core/a.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    serialized = json.dumps(result)
    assert secret not in serialized
    assert ".env" not in serialized

    record = _record(result, "src/vibetrading/a.py")
    assert any(ref["file"] == "config/app.yaml" for ref in record["entrypoint"])
    docker_tokens = {
        token
        for entry in result["config_docker_inventory"]
        for token in entry["tokens"]
    }
    assert "src/vibetrading/a.py" in docker_tokens
    assert "vibetrading.a" in docker_tokens
    assert all("line_text" not in entry for entry in result["config_docker_inventory"])


def test_init_retirement_and_strategy_contract_alias_are_explicit_exceptions(
    tmp_path,
):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/__init__.py": "",
            "src/vibetrading/strategy_contract.py": "VALUE = 1\n",
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1(
            [
                ("__init__.py", None, "packaging", "RETIRE_OLD_NAMESPACE"),
                (
                    "strategy_contract.py",
                    "strategies/compat_contract.py",
                    "strategies",
                    "REUSE_OR_RETIRE_AFTER_CALLER_AUDIT",
                ),
            ]
        ),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )

    init_record = _record(result, "src/vibetrading/__init__.py")
    assert init_record["new_path"] is None
    assert init_record["disposition"] == "RETIRE_OLD_NAMESPACE"
    assert "deferred exception" in init_record["reason"]

    contract = _record(result, "src/vibetrading/strategy_contract.py")
    assert contract["new_path"] is None
    assert contract["disposition"] == "DEFERRED_WITH_BLOCKER"
    assert "canonical compatibility alias" in contract["reason"]
    assert result["summary"]["missing_or_ambiguous"] == 0


def test_collisions_record_waves_obligations_and_no_sys_path_workaround(tmp_path):
    root = _repo(
        tmp_path,
        {
            "strategies/__init__.py": "",
            "src/vibetrading/execution.py": "VALUE = 1\n",
            "src/vibetrading/runtime.py": "VALUE = 2\n",
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1(
            [
                ("execution.py", "execution/contracts.py", "execution"),
                ("runtime.py", "runtime/runtime.py", "runtime"),
            ]
        ),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    by_kind = {item["kind"]: item for item in result["collisions"]}
    assert set(by_kind) == {
        "root_strategies_vs_src_strategies",
        "execution_module_vs_execution_package",
        "runtime_module_vs_runtime_package",
        "old_vibetrading_namespace",
    }
    assert by_kind["root_strategies_vs_src_strategies"]["present"]
    for item in by_kind.values():
        assert item["sys_path_workaround"] is False
        assert item["migration_wave"]
        assert item["baseline_cwd_wheel_import_precedence_obligations"]
    assert any(
        "no sys.path" in obligation
        for obligation in by_kind[
            "execution_module_vs_execution_package"
        ]["baseline_cwd_wheel_import_precedence_obligations"]
    )


def test_historical_path_map_is_provenance_only(tmp_path):
    sentinel = "SHOULD_NOT_APPEAR_IN_INVENTORY"
    root = _repo(
        tmp_path,
        {
            "docs/refactoring/vnext/path_map.json": json.dumps(
                {
                    "schema": "r00-v1",
                    "entries": [
                        {"old": "src/vibetrading/a.py", "payload": sentinel}
                    ],
                }
            )
        },
    )
    result = inventory.run_inventory(
        root, b1_rows=[], b2_rows=inventory.DEFAULT_B2_BUNDLES
    )
    assert len(result["historical_provenance"]) == 1
    provenance = result["historical_provenance"][0]
    assert provenance["path"] == "docs/refactoring/vnext/path_map.json"
    assert provenance["schema"] == "r00-v1"
    assert provenance["count"] == 1
    assert provenance["authoritative_targets"] is False
    assert provenance["source_blob_sha"]
    assert provenance["sha256"]
    assert sentinel not in json.dumps(result)


def test_historical_provenance_whitelists_only_vnext_and_explicit_fixtures(
    tmp_path,
):
    vnext_sentinel = "VNEXT_ONLY_PAYLOAD"
    ignored_sentinel = "RAW_RUN_LOG_MUST_NOT_BE_SCANNED"
    root = _repo(
        tmp_path,
        {
            "docs/refactoring/vnext/repo_inventory.json": json.dumps(
                {
                    "schema": "r00",
                    "files": [{"payload": vnext_sentinel}],
                }
            ),
            "docs/refactoring/vnext/path_map.json": json.dumps(
                {"schema": "r00-map", "map": {"a": "b"}}
            ),
            "docs/refactoring/runs/old/path_map.json": json.dumps(
                {"schema": "raw-run", "entries": [ignored_sentinel]}
            ),
            "docs/final/R00_path_map.json": json.dumps(
                {"schema": "not-whitelisted", "entries": [ignored_sentinel]}
            ),
            "docs/final/fixture_path_map.json": json.dumps(
                {"schema": "fixture", "entries": [{"payload": "FIXTURE"}]}
            ),
        },
    )
    result = inventory.run_inventory(
        root, b1_rows=[], b2_rows=inventory.DEFAULT_B2_BUNDLES
    )
    paths = {item["path"] for item in result["historical_provenance"]}
    assert paths == {
        "docs/refactoring/vnext/repo_inventory.json",
        "docs/refactoring/vnext/path_map.json",
    }
    serialized = json.dumps(result["historical_provenance"])
    assert vnext_sentinel not in serialized
    assert ignored_sentinel not in serialized

    tracked = inventory.tracked_paths(root)
    _, blobs = inventory.head_identity(root)
    explicit = inventory.historical_provenance(
        root,
        tracked,
        blobs,
        explicit_paths=["docs/final/fixture_path_map.json"],
    )
    assert {item["path"] for item in explicit} == {
        "docs/final/fixture_path_map.json",
        "docs/refactoring/vnext/path_map.json",
        "docs/refactoring/vnext/repo_inventory.json",
    }


def test_unknown_product_subpackage_is_rejected(tmp_path):
    root = _repo(
        tmp_path,
        {"src/vibetrading/unknown_bundle/mod.py": "VALUE = 1\n"},
    )
    result = inventory.run_inventory(
        root, b1_rows=[], b2_rows=inventory.DEFAULT_B2_BUNDLES
    )
    assert "unknown_product_subpackage" in _error_kinds(result)
    record = _record(result, "src/vibetrading/unknown_bundle/mod.py")
    assert record["disposition"] == "UNCLASSIFIED"
    assert record["new_path"] is None
    assert result["exit_code"] == 2


@pytest.mark.parametrize(
    "name",
    [
        "crypto-profile.yml",
        "dashboard-compose.yml",
        "ls-market-profile.yml",
        "release1-overlay.yml",
        "plain-overlay.yaml",
    ],
)
def test_all_deploy_overlays_are_inventoried(tmp_path, name):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/a.py": "A = 1\n",
            "deploy/" + name: "command: python -m vibetrading.a\n",
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1([("a.py", "core/a.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    assert any(
        entry["file"] == "deploy/" + name
        for entry in result["config_docker_inventory"]
    )


def test_deploy_dockerfile_compose_root_and_service_are_still_inventoried(
    tmp_path,
):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/a.py": "A = 1\n",
            "Dockerfile": "COPY src/vibetrading/a.py /app/a.py\n",
            "docker-compose.yml": "command: python -m vibetrading.a\n",
            "deploy/Dockerfile": "COPY src/vibetrading/a.py /app/a.py\n",
            "deploy/docker-compose.yml": "image: app\n",
            "deploy/app.service": "ExecStart=python -m vibetrading.a\n",
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1([("a.py", "core/a.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    files = {entry["file"] for entry in result["config_docker_inventory"]}
    assert {
        "Dockerfile",
        "docker-compose.yml",
        "deploy/Dockerfile",
        "deploy/docker-compose.yml",
        "deploy/app.service",
    } <= files


def test_untracked_destination_file_blocks_without_reading_contents(tmp_path):
    sentinel = "UNTRACKED_SECRET_BYTES"
    root = _repo(tmp_path, {"src/vibetrading/a.py": "A = 1\n"})
    _write(root, "src/core/a.py", sentinel + "\n")
    result = inventory.run_inventory(
        root,
        b1_rows=B1([("a.py", "core/a.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    assert "existing_destination_collision" in _error_kinds(result)
    assert result["summary"]["missing_or_ambiguous"] > 0
    assert result["exit_code"] == 2
    assert sentinel not in json.dumps(result)


def test_untracked_destination_parent_file_blocks(tmp_path):
    sentinel = "PARENT_SECRET_BYTES"
    root = _repo(tmp_path, {"src/vibetrading/a.py": "A = 1\n"})
    (root / "src" / "core").write_text(sentinel + "\n", encoding="utf-8")
    result = inventory.run_inventory(
        root,
        b1_rows=B1([("a.py", "core/a.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    assert "destination_parent_collision" in _error_kinds(result)
    assert result["exit_code"] == 2
    assert sentinel not in json.dumps(result)


def test_symlink_destination_and_parent_are_collisions(tmp_path):
    sentinel = "SYMLINK_SECRET_BYTES"
    outside = tmp_path / "outside.txt"
    outside.write_text(sentinel + "\n", encoding="utf-8")
    root = _repo(tmp_path, {"src/vibetrading/a.py": "A = 1\n"})

    destination = root / "src" / "core" / "a.py"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(outside)
    result = inventory.run_inventory(
        root,
        b1_rows=B1([("a.py", "core/a.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    assert "existing_destination_collision" in _error_kinds(result)
    assert result["exit_code"] == 2
    assert sentinel not in json.dumps(result)

    destination.unlink()
    destination.parent.rmdir()
    (root / "src" / "core").symlink_to(tmp_path, target_is_directory=True)
    result = inventory.run_inventory(
        root,
        b1_rows=B1([("a.py", "core/a.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    assert "destination_parent_collision" in _error_kinds(result)
    assert result["exit_code"] == 2
    assert sentinel not in json.dumps(result)


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink"])
def test_unavailable_tracked_source_is_a_hard_error(tmp_path, kind):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/a.py": "A = 1\n",
            "src/vibetrading/other.py": "OTHER = 1\n",
        },
    )
    source = root / "src/vibetrading/a.py"
    source.unlink()
    if kind == "directory":
        source.mkdir()
    elif kind == "symlink":
        source.symlink_to(root / "src/vibetrading/other.py")

    result = inventory.run_inventory(
        root,
        b1_rows=B1([("a.py", "core/a.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    assert "missing_source" in _error_kinds(result)
    assert result["summary"]["missing_or_ambiguous"] > 0
    assert result["exit_code"] == 2
    record = _record(result, "src/vibetrading/a.py")
    assert record["disposition"] == "MISSING_OR_AMBIGUOUS"
    assert record["worktree_sha256"] is None
    assert record["source_ast_sha256"] is None
    assert any(
        error.get("path") == "src/vibetrading/a.py"
        for error in result["errors"]
    )


def test_line_tokens_only_emit_project_module_and_path_references():
    assert inventory._line_tokens("image: fake.jwt.signature") == []
    assert inventory._line_tokens("url: https://example.invalid/x.y") == []
    module_tokens = inventory._line_tokens("command: python -m vibetrading.a")
    assert "vibetrading.a" in module_tokens
    path_tokens = inventory._line_tokens(
        "copy: src/vibetrading/a.py /app/a.py"
    )
    assert "src/vibetrading/a.py" in path_tokens


def test_duplicate_old_rows_and_casefold_targets_are_ambiguous(tmp_path):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/a.py": "A = 1\n",
            "src/vibetrading/b.py": "B = 1\n",
        },
    )
    duplicate_old = inventory.run_inventory(
        root,
        b1_rows=B1(
            [
                ("a.py", "core/a.py", "core"),
                ("a.py", "market/a.py", "market"),
            ]
        ),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    assert "duplicate_b1_source" in _error_kinds(duplicate_old)
    assert duplicate_old["exit_code"] == 2
    assert duplicate_old["summary"]["missing_or_ambiguous"] > 0

    casefold_target = inventory.run_inventory(
        root,
        b1_rows=B1(
            [
                ("a.py", "core/A.py", "core"),
                ("b.py", "core/a.py", "core"),
            ]
        ),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    assert "duplicate_target" in _error_kinds(casefold_target)
    assert casefold_target["summary"]["duplicate_targets"] == 1
    assert casefold_target["exit_code"] == 2

    old_case = inventory.run_inventory(
        root,
        b1_rows=B1([("A.py", "core/A.py", "core")]),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    assert {"missing_root_path", "extra_root_path"} <= _error_kinds(old_case)
    assert old_case["exit_code"] == 2


def test_summary_subpackages_is_the_actual_tracked_package_count(tmp_path):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/providers/__init__.py": "",
            "src/vibetrading/strategies/__init__.py": "",
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=[],
        b2_rows={"providers": "feeds/providers", "strategies": "strategies"},
    )
    assert result["summary"]["subpackages"] == 2
    assert result["summary"]["missing_or_ambiguous"] == 0
    assert result["exit_code"] == 0


def test_move_wave_reason_text_is_correct(tmp_path):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/__init__.py": "",
            "src/vibetrading/trading_contracts.py": "VALUE = 1\n",
        },
    )
    result = inventory.run_inventory(
        root,
        b1_rows=B1(
            [
                ("__init__.py", None, "packaging", "RETIRE_OLD_NAMESPACE"),
                (
                    "trading_contracts.py",
                    "core/trading_contracts.py",
                    "core",
                ),
            ]
        ),
        b2_rows=inventory.DEFAULT_B2_BUNDLES,
    )
    init_record = _record(result, "src/vibetrading/__init__.py")
    assert "MOV-07" in init_record["reason"]
    contracts = _record(result, "src/vibetrading/trading_contracts.py")
    assert contracts["new_path"] == "src/execution/trading_contracts.py"
    assert "MOV-04" in contracts["reason"]
    assert "MOV-01 moves only models/dedup" in contracts["reason"]
    assert "MOV-01 moves it" not in contracts["reason"]


def test_cli_refuses_tracked_protected_and_symlink_outputs(tmp_path):
    root = _repo(
        tmp_path,
        {
            "src/vibetrading/a.py": "A = 1\n",
            "docs/final/02_DEVELOPER_TICKETS.md": "AUTHORITY\n",
        },
    )
    tracked = root / "src/vibetrading/a.py"
    tracked_before = tracked.read_text(encoding="utf-8")
    assert (
        inventory.main(["--root", str(root), "--output", str(tracked)]) == 2
    )
    assert tracked.read_text(encoding="utf-8") == tracked_before

    authority = root / "docs/final/02_DEVELOPER_TICKETS.md"
    authority_before = authority.read_text(encoding="utf-8")
    assert (
        inventory.main(["--root", str(root), "--output", str(authority)]) == 2
    )
    assert authority.read_text(encoding="utf-8") == authority_before

    new_authority = root / "docs/final/00_EXECUTION_MAP.md"
    assert (
        inventory.main(
            ["--root", str(root), "--output", str(new_authority)]
        )
        == 2
    )
    assert not new_authority.exists()

    link = root / "out.json"
    link.symlink_to(tracked)
    assert inventory.main(["--root", str(root), "--output", str(link)]) == 2
    assert link.is_symlink()
    assert tracked.read_text(encoding="utf-8") == tracked_before

    codegen = root / "docs/final/new_codegen_map.json"
    assert (
        inventory.main(["--root", str(root), "--output", str(codegen)]) in {0, 2}
    )
    assert codegen.is_file()

    data_output = root / "src/vibetrading/generated.csv"
    assert (
        inventory.main(
            ["--root", str(root), "--output", str(data_output)]
        )
        == 2
    )
    assert not data_output.exists()
