"""Bounded MOV-07 flat-namespace regression gate.

The historical archive helpers are identity aliases only. Release inventory
and fresh source imports must neither require nor resolve the retired legacy
package, while the archive alias objects stay interchangeable with the
canonical strategy-private modules.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.refactoring import build_release_manifest as manifest


ROOT = Path(__file__).resolve().parents[2]

ISOLATION_PROBE = """
import importlib.util
import json
result = {"legacy": importlib.util.find_spec("vibetrading") is not None, "domains": {}}
for name in %r:
    spec = importlib.util.find_spec(name)
    result["domains"][name] = spec.origin if spec is not None else None
print(json.dumps(result))
"""


def test_archive_helper_aliases_preserve_module_class_and_monkeypatch_identity(monkeypatch):
    import strategies.legacy.fair_value.contracts as contracts
    import strategies.research.state.features as features
    import strategies.research.state.policy as policy
    import vibetrading.fv_mm.types as archived_types
    import vibetrading.mm_state.features as archived_features
    import vibetrading.mm_state.policy as archived_policy

    assert sys.modules["vibetrading.fv_mm.types"] is contracts
    assert sys.modules["vibetrading.mm_state.features"] is features
    assert sys.modules["vibetrading.mm_state.policy"] is policy
    assert archived_types is contracts
    assert archived_features is features
    assert archived_policy is policy

    for name in ("Quote", "Signal", "Intent", "ActivityWindow"):
        assert getattr(archived_types, name) is getattr(contracts, name)
    for name in ("Features", "Microstructure", "StateStrategy"):
        assert getattr(archived_features, name) is getattr(features, name)
    for name in ("risk_state", "state_quote_intents"):
        assert getattr(archived_policy, name) is getattr(policy, name)

    sentinel = object()
    monkeypatch.setattr(archived_features, "direction_score", sentinel)
    assert features.direction_score is sentinel
    assert archived_features.direction_score is sentinel

    monkeypatch.setattr(archived_policy, "state_quote_intents", sentinel)
    assert policy.state_quote_intents is sentinel
    assert sys.modules["strategies.research.state.policy"].state_quote_intents is sentinel

    monkeypatch.setattr(archived_features.Microstructure, "snapshot", sentinel)
    assert features.Microstructure.snapshot is sentinel
    assert archived_features.Microstructure is features.Microstructure


def _flat_repo(tmp_path: Path) -> Path:
    root = tmp_path / "flat"
    for name in manifest.DOMAIN_PACKAGES:
        package = root / "src" / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_bytes(b"")
    (root / "src" / "runtime" / "cli.py").write_bytes(b"VALUE = 1\n")
    baseline = {"files": {}, "source_set_sha256": "baseline-set"}
    target = root / "docs" / "refactoring" / "baseline_manifest.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(baseline), encoding="utf-8")
    return root


def _snapshot() -> dict:
    return {
        "containers": [{
            "name": "role", "image": "image", "image_id": "id", "command": ["python"],
            "compose_files": ["compose.yml"], "mounts": [], "memory_limit": 1,
            "source_hashes": {},
        }],
        "memory": {"stdout": "Mem: 100\n"},
        "cpu_count": 4,
        "observed_utc": "2026-10-23T00:00:00Z",
    }


def test_flat_release_inventory_builds_without_legacy_package(tmp_path):
    root = _flat_repo(tmp_path)
    assert not (root / "vibetrading").exists()
    assert not (root / "src" / "vibetrading").exists()

    expected = manifest.canonical_source_hashes(root)
    result = manifest.build(root, _snapshot())

    assert result["source_hashes"] == expected
    assert "src/runtime/cli.py" in result["source_hashes"]
    assert result["source_hashes"]["src/runtime/cli.py"] == hashlib.sha256(b"VALUE = 1\n").hexdigest()
    assert not any(name.startswith("vibetrading/") for name in result["source_hashes"])
    assert set(result["changed_sources"]) == set(expected)
    assert all(entry["before_sha256"] is None for entry in result["changed_sources"].values())
    assert result["roles"][0]["deployment_status"] == "NOT_BUILT_NOT_DEPLOYED"


@pytest.mark.parametrize("layout", ("src", "flat"))
def test_legacy_shaped_fixtures_keep_vibetrading_manifest_keys(tmp_path, layout):
    root = tmp_path / ("legacy-" + layout)
    package = root / ("src/vibetrading" if layout == "src" else "vibetrading")
    package.mkdir(parents=True)
    (package / "same.py").write_bytes(b"SAME = 1\n")
    (package / "changed.py").write_bytes(b"CHANGED = 2\n")
    baseline = {
        "files": {
            "vibetrading/same.py": {"sha256": hashlib.sha256(b"SAME = 1\n").hexdigest()},
            "vibetrading/changed.py": {"sha256": "0" * 64},
        },
        "source_set_sha256": "baseline-set",
    }
    target = root / "docs" / "refactoring" / "baseline_manifest.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(baseline), encoding="utf-8")

    result = manifest.build(root, _snapshot())

    assert set(result["source_hashes"]) == {"vibetrading/same.py", "vibetrading/changed.py"}
    assert "vibetrading/same.py" not in result["changed_sources"]
    assert set(result["changed_sources"]) == {"vibetrading/changed.py"}


def test_fresh_source_only_process_isolates_archive_namespace(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONPATH"] = str(ROOT / "src")
    script = ISOLATION_PROBE % (manifest.DOMAIN_PACKAGES,)
    for cwd in (ROOT, tmp_path):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        probe = json.loads(completed.stdout)
        assert probe["legacy"] is False
        assert set(probe["domains"]) == set(manifest.DOMAIN_PACKAGES)
        for name, origin in probe["domains"].items():
            assert origin is not None
            assert Path(origin).resolve() == (ROOT / "src" / name / "__init__.py").resolve()
