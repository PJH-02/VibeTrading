"""Focused packaging checks for the canonical flat domain packages.

These tests stay inside the R03B1 packaging subunit: import origin,
setuptools metadata discovery, live-data exclusion retention, and
structural Docker source/entrypoint compatibility. They do not install
packages, build images, or import the full product CLI.
"""

from __future__ import annotations

import configparser
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
PYTEST_INI = ROOT / "pytest.ini"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERFILE_CRYPTO = ROOT / "Dockerfile.crypto"
REQUIREMENTS = ROOT / "requirements.txt"
CANONICAL_NAMESPACES = ("core", "feeds", "market", "storage", "strategies", "execution",
                        "portfolio", "runtime", "operations", "timefolio")

LEGACY_NAMESPACES = ("vibetrading", "strategy", "shared", "services", "backtest")

IMPORT_PROBE = """
import importlib.util
import json
names = %r
result = {"legacy": importlib.util.find_spec("vibetrading") is not None, "domains": {}}
for name in names:
    spec = importlib.util.find_spec(name)
    result["domains"][name] = spec.origin if spec is not None else None
print(json.dumps(result))
"""


def _load_pyproject() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def test_import_discovery_prefers_src_package_without_root_alias() -> None:
    import importlib.util

    assert not (ROOT / "vibetrading").exists()
    for name in CANONICAL_NAMESPACES:
        spec = importlib.util.find_spec(name)
        assert spec is not None and spec.origin
        assert Path(spec.origin).resolve() == (ROOT / "src" / name / "__init__.py").resolve()
        assert not (ROOT / name / "__init__.py").exists()


def test_setuptools_metadata_discovers_canonical_and_legacy_packages(monkeypatch) -> None:
    # Package discovery resolves relative paths, so pin the config root.
    monkeypatch.chdir(ROOT)
    data = _load_pyproject()

    build = data["build-system"]
    assert build["build-backend"] == "setuptools.build_meta"
    assert "setuptools>=68" in build["requires"]
    assert "wheel" in build["requires"]

    project = data["project"]
    assert project["name"] == "vibetrading"
    assert project["version"] == "0.0.0"
    assert project["requires-python"] == ">=3.12"
    assert "dependencies" in project["dynamic"]
    assert "dependencies" not in project, "dependencies must stay dynamic from requirements.txt"

    setuptools_cfg = data["tool"]["setuptools"]
    assert "package-dir" not in setuptools_cfg

    find_cfg = setuptools_cfg["packages"]["find"]
    assert find_cfg["where"] == ["src"]
    include = set(find_cfg["include"])
    for name in CANONICAL_NAMESPACES:
        assert name in include and name + ".*" in include
    assert "vibetrading" not in include and "vibetrading.*" not in include
    assert not set(LEGACY_NAMESPACES) & include
    exclude = set(find_cfg["exclude"])
    for blocked in (
        "src",
        "src.*",
        "tests",
        "tests.*",
        "docs",
        "docs.*",
        "tmp",
        "tmp.*",
        "cache",
        "cache.*",
    ):
        assert blocked in exclude

    dynamic = setuptools_cfg["dynamic"]
    assert dynamic["dependencies"]["file"] == ["requirements.txt"]

    from setuptools import find_namespace_packages
    from setuptools.config.pyprojecttoml import apply_configuration
    from setuptools.dist import Distribution

    discovered: set[str] = set()
    for where in find_cfg["where"]:
        discovered.update(
            find_namespace_packages(
                where=str(ROOT / where),
                include=find_cfg["include"],
                exclude=find_cfg["exclude"],
            )
        )
    assert set(CANONICAL_NAMESPACES) <= discovered
    assert not set(LEGACY_NAMESPACES) & discovered

    # Execute setuptools' supported pyproject configuration expansion. This
    # resolves [tool.setuptools.packages.find] for real; no swallowed skip.
    dist = Distribution()
    apply_configuration(dist, str(PYPROJECT))
    expanded = set(dist.packages or ())
    assert set(CANONICAL_NAMESPACES) <= expanded
    assert not set(LEGACY_NAMESPACES) & expanded

    # requirements.txt remains the authoritative, unchanged dependency source.
    req_text = REQUIREMENTS.read_text(encoding="utf-8")
    assert "exchange-calendars==4.13.2" in req_text
    assert "pydantic>=2.5.0" in req_text


def test_pytest_ini_retains_live_exclusion_and_adds_pythonpath() -> None:
    parser = configparser.ConfigParser()
    read_ok = parser.read(PYTEST_INI)
    assert read_ok, "pytest.ini must remain readable"
    section = parser["pytest"]
    assert section["pythonpath"].split() == ["src", "docs/archive/MOV-05-legacy-oracles"]
    assert 'not live_data' in section["addopts"]
    assert "live_data" in section["markers"]
    assert section["testpaths"].strip() == "tests"


def test_fresh_source_subprocess_only_src_resolves_canonical_domains(tmp_path) -> None:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONPATH"] = str(ROOT / "src")
    script = IMPORT_PROBE % (CANONICAL_NAMESPACES,)
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
        assert set(probe["domains"]) == set(CANONICAL_NAMESPACES)
        for name, origin in probe["domains"].items():
            assert origin is not None
            assert Path(origin).resolve() == (ROOT / "src" / name / "__init__.py").resolve()


def test_dockerfiles_structurally_support_src_entrypoint() -> None:
    import json
    import shlex
    docker = DOCKERFILE.read_text(encoding="utf-8")
    crypto = DOCKERFILE_CRYPTO.read_text(encoding="utf-8")
    for recipe in (docker, crypto):
        copies = [line.split() for line in recipe.splitlines() if line.startswith("COPY ")]
        assert any(parts[-2:] == ["src", "/app/src"] for parts in copies)
        assert all("src/vibetrading" not in part for parts in copies for part in parts)
        assert recipe.index("rm -rf /app/src") < next(recipe.index(" ".join(parts)) for parts in copies if parts[-2:] == ["src", "/app/src"])
        cleanup = shlex.split(next(line for line in recipe.splitlines() if "rm -rf /app/src" in line))
        for old in ("vibetrading", "strategies", "services", "shared", "strategy", "backtest"):
            assert "/app/" + old in cleanup
        assert "PYTHONPATH=/app/src" in recipe
        assert "pip install --no-deps -e" not in recipe
        assert "requirements.txt" in recipe and "VIBETRADING_BUILD_REVISION" in recipe
    assert "useradd --uid 10001" in docker
    command = next(line[4:] for line in docker.splitlines() if line.startswith("CMD "))
    assert json.loads(command) == ["python", "-m", "runtime.cli", "status"]
    assert "config /app/config" in crypto and "main.py /app/main.py" in crypto
    assert "USER 10001:10001" in crypto
    assert "/data" not in crypto
