"""Focused packaging checks for the canonical src/vibetrading layout.

These tests stay inside the R03B1 packaging subunit: import origin,
setuptools metadata discovery, live-data exclusion retention, and
structural Docker source/entrypoint compatibility. They do not install
packages, build images, or import the full product CLI.
"""

from __future__ import annotations

import configparser
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
PYTEST_INI = ROOT / "pytest.ini"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERFILE_CRYPTO = ROOT / "Dockerfile.crypto"
REQUIREMENTS = ROOT / "requirements.txt"
CANONICAL_INIT = ROOT / "src" / "vibetrading" / "__init__.py"

LEGACY_NAMESPACES = ("strategy", "strategies", "shared", "services", "backtest")


def _load_pyproject() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def test_import_discovery_prefers_src_package_without_root_alias() -> None:
    import importlib.util

    spec = importlib.util.find_spec("vibetrading")
    assert spec is not None and spec.origin, "vibetrading must be import-discoverable"
    origin = Path(spec.origin).resolve()
    assert origin == CANONICAL_INIT.resolve()
    # No top-level compatibility wrapper/alias package at the repository root.
    assert not (ROOT / "vibetrading" / "__init__.py").exists()
    assert CANONICAL_INIT.is_file()


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
    package_dir = setuptools_cfg["package-dir"]
    assert package_dir["vibetrading"].replace("\\", "/").rstrip("/") == "src/vibetrading"

    find_cfg = setuptools_cfg["packages"]["find"]
    assert find_cfg["where"] == ["src", "."]
    include = set(find_cfg["include"])
    for name in ("vibetrading", "vibetrading.*", *LEGACY_NAMESPACES):
        assert name in include
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
    assert "vibetrading" in discovered
    for namespace in LEGACY_NAMESPACES:
        assert namespace in discovered, f"legacy namespace {namespace} must remain discoverable"

    # Execute setuptools' supported pyproject configuration expansion. This
    # resolves [tool.setuptools.packages.find] for real; no swallowed skip.
    dist = Distribution()
    apply_configuration(dist, str(PYPROJECT))
    expanded = set(dist.packages or ())
    assert "vibetrading" in expanded, "canonical package must expand from pyproject.toml"
    for namespace in LEGACY_NAMESPACES:
        assert namespace in expanded, f"legacy namespace {namespace} must expand from pyproject.toml"

    # requirements.txt remains the authoritative, unchanged dependency source.
    req_text = REQUIREMENTS.read_text(encoding="utf-8")
    assert "exchange-calendars==4.13.2" in req_text
    assert "pydantic>=2.5.0" in req_text


def test_pytest_ini_retains_live_exclusion_and_adds_pythonpath() -> None:
    parser = configparser.ConfigParser()
    read_ok = parser.read(PYTEST_INI)
    assert read_ok, "pytest.ini must remain readable"
    section = parser["pytest"]
    assert section["pythonpath"].strip() == "src"
    assert 'not live_data' in section["addopts"]
    assert "live_data" in section["markers"]
    assert section["testpaths"].strip() == "tests"


def test_dockerfiles_structurally_support_src_entrypoint() -> None:
    docker = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY . ." in docker
    # A stale flat package from a reused app base must be removed before the
    # editable install so /app does not shadow /app/src on sys.path.
    assert "rm -rf /app/vibetrading" in docker
    assert docker.index("rm -rf /app/vibetrading") < docker.index(
        "pip install --no-deps -e ."
    )
    assert "pip install --no-deps -e ." in docker
    assert "PYTHONPATH" in docker and "/app/src" in docker
    # Existing base/dependency/build-label/UID/CMD behavior preserved.
    assert "requirements.txt" in docker
    assert "VIBETRADING_BUILD_REVISION" in docker
    assert "useradd --uid 10001" in docker
    assert 'CMD ["python", "main.py", "status"]' in docker

    crypto = DOCKERFILE_CRYPTO.read_text(encoding="utf-8")
    assert "COPY --chown=10001:10001 src/vibetrading /app/src/vibetrading" in crypto
    assert "COPY --chown=10001:10001 pyproject.toml /app/pyproject.toml" in crypto
    assert "COPY --chown=10001:10001 requirements.txt /app/requirements.txt" in crypto
    # Stale flat base package is removed; root alias is not re-copied.
    assert "rm -rf /app/vibetrading" in crypto
    assert "COPY --chown=10001:10001 vibetrading /app/vibetrading" not in crypto
    assert "pip install --no-deps -e" in crypto
    # Config/strategies/main copies and UID behavior preserved.
    assert "config /app/config" in crypto
    assert "strategies /app/strategies" in crypto
    assert "main.py /app/main.py" in crypto
    assert "USER vibetrading" in crypto
    # Never touches /data.
    assert "/data" not in crypto
