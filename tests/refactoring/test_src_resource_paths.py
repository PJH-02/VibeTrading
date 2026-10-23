from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_SOURCE = REPO_ROOT / "src" / "vibetrading" / "settings.py"
HEALTH_SOURCE = REPO_ROOT / "src" / "vibetrading" / "health.py"
FV_MM_RUNNER_SOURCE = REPO_ROOT / "src" / "vibetrading" / "fv_mm" / "runner.py"
FV_PILOT_RUNNER_SOURCE = REPO_ROOT / "src" / "vibetrading" / "fv_pilot" / "runner.py"
PILOT_FV_MM_SELECTED = ("types.py", "market.py", "journal.py", "calibration.py")


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _parse(source_path: Path) -> ast.Module:
    return ast.parse(source_path.read_text(encoding="utf-8"))


def _exec_nodes(nodes: list[ast.stmt], namespace: dict[str, object], filename: str) -> dict[str, object]:
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, filename, "exec"), namespace)
    return namespace


def _top_level_assignments(source_path: Path, names: tuple[str, ...]) -> list[ast.stmt]:
    tree = _parse(source_path)
    found: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    found[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in names:
            found[node.target.id] = node
    missing = [name for name in names if name not in found]
    assert not missing, f"missing assignments in {source_path}: {missing}"
    return [found[name] for name in names]


def _method(source_path: Path, class_name: str, method_name: str) -> ast.FunctionDef:
    tree = _parse(source_path)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
    assert len(classes) == 1, f"expected one class {class_name} in {source_path}"
    methods = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and node.name == method_name]
    assert len(methods) == 1, f"expected one method {class_name}.{method_name} in {source_path}"
    return methods[0]


def _function(source_path: Path, function_name: str) -> ast.FunctionDef:
    tree = _parse(source_path)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name]
    assert len(functions) == 1, f"expected one function {function_name} in {source_path}"
    return functions[0]


def _layout(tmp_path: Path, kind: str) -> tuple[Path, Path]:
    root = tmp_path.resolve()
    if kind == "src":
        anchor = root
        package = root / "src" / "vibetrading"
        _write(root / "pyproject.toml", "[project]\nname = 'fixture'\n")
    elif kind == "flat":
        anchor = root
        package = root / "vibetrading"
    elif kind == "src_named_without_marker":
        anchor = root / "src"
        package = anchor / "vibetrading"
    else:
        raise AssertionError(f"unknown layout fixture: {kind}")
    package.mkdir(parents=True, exist_ok=True)
    return anchor, package


def _settings_root(tmp_path: Path, kind: str) -> tuple[Path, dict[str, object]]:
    anchor, package = _layout(tmp_path, kind)
    settings_file = package / "settings.py"
    _write(settings_file, "# fixture\n")
    namespace = _exec_nodes(
        _top_level_assignments(SETTINGS_SOURCE, ("_PACKAGE_ROOT", "PROJECT_ROOT", "DEFAULT_DATA_ROOT")),
        {"Path": Path, "__file__": str(settings_file)},
        str(settings_file),
    )
    return anchor, namespace


def _digest_files(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.parametrize("kind", ("src", "flat", "src_named_without_marker"))
def test_settings_defaults_follow_layout_anchor(tmp_path: Path, kind: str) -> None:
    anchoring_root, namespace = _settings_root(tmp_path, kind)

    assert namespace["PROJECT_ROOT"] == anchoring_root
    assert namespace["DEFAULT_DATA_ROOT"] == anchoring_root / "runtime_data"


def test_canonical_src_settings_anchor_is_repository_root() -> None:
    namespace = _exec_nodes(
        _top_level_assignments(SETTINGS_SOURCE, ("_PACKAGE_ROOT", "PROJECT_ROOT", "DEFAULT_DATA_ROOT")),
        {"Path": Path, "__file__": str(SETTINGS_SOURCE)},
        str(SETTINGS_SOURCE),
    )

    assert namespace["PROJECT_ROOT"] == REPO_ROOT
    assert namespace["DEFAULT_DATA_ROOT"] == REPO_ROOT / "runtime_data"


class _PathFactory:
    def __init__(self, app_config: Path) -> None:
        self.app_config = app_config

    def __call__(self, value: object) -> Path:
        if str(value) == "/app/config":
            return self.app_config
        return Path(value)


class _FakeOs:
    def __init__(self, environ: dict[str, str]) -> None:
        self.environ = environ

    def getenv(self, key: str, default: object = None) -> object:
        return self.environ.get(key, default)


def _provenance(namespace_file: Path, app_config: Path, environ: dict[str, str]) -> dict[str, str]:
    namespace = {
        "Path": _PathFactory(app_config),
        "os": _FakeOs(environ),
        "hashlib": hashlib,
        "json": json,
        "__file__": str(namespace_file),
    }
    _exec_nodes([_function(HEALTH_SOURCE, "_build_provenance")], namespace, str(namespace_file))
    return namespace["_build_provenance"]()  # type: ignore[operator,no-any-return]


def _config_files(config_root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(config_root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".json", ".toml"}:
            files[path.relative_to(config_root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _expected_provenance_hash(config_files: dict[str, str], environment: dict[str, str]) -> str:
    payload = {"environment": environment, "config_files": config_files}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("kind", ("src", "flat", "src_named_without_marker"))
def test_health_config_hash_uses_project_root_for_layout(tmp_path: Path, kind: str) -> None:
    anchor, package = _layout(tmp_path, kind)
    config_root = anchor / "config"
    _write(config_root / "settings.yaml", "mode: fixture\n")
    _write(config_root / "nested" / "data.json", '{"fixture": true}\n')
    _write(config_root / "ignored.txt", "not hashed\n")
    environ = {"VIBETRADING_FIXTURE": "1", "NATS_URL": "nats://fixture", "SECRET_KEY": "excluded"}
    expected_environment = {"VIBETRADING_FIXTURE": "1", "NATS_URL": "nats://fixture"}

    result = _provenance(package / "health.py", anchor / "no-app-config", environ)

    assert result["runtime_config_hash"] == _expected_provenance_hash(
        _config_files(config_root), expected_environment
    )


def test_health_config_hash_prefers_app_config(tmp_path: Path) -> None:
    anchor, package = _layout(tmp_path, "src")
    _write(anchor / "config" / "project.yaml", "source: project\n")
    app_config = anchor / "app-config"
    _write(app_config / "app.yaml", "source: app\n")
    environ = {"VIBETRADING_FIXTURE": "1"}

    result = _provenance(package / "health.py", app_config, environ)

    assert result["runtime_config_hash"] == _expected_provenance_hash(
        _config_files(app_config), environ
    )


def _fv_mm_fixture(tmp_path: Path, kind: str) -> tuple[Path, Path, Path]:
    anchor, package = _layout(tmp_path, kind)
    runner_file = package / "fv_mm" / "runner.py"
    _write(runner_file, "# runner\n")
    _write(package / "fv_mm" / "alpha.py", "ALPHA = 1\n")
    _write(package / "fv_mm" / "zeta.py", "ZETA = 1\n")
    _write(package / "mm_state" / "alpha.py", "STATE_ALPHA = 1\n")
    _write(package / "mm_state" / "zeta.py", "STATE_ZETA = 1\n")
    _write(anchor / "strategy" / "alpha.py", "STRATEGY_ALPHA = 1\n")
    _write(anchor / "strategy" / "zeta.py", "STRATEGY_ZETA = 1\n")
    return anchor, package, runner_file


def _call_source_hash(source_path: Path, class_name: str, runner_file: Path) -> str:
    namespace = {"Path": Path, "hashlib": hashlib, "__file__": str(runner_file)}
    _exec_nodes([_method(source_path, class_name, "source_hash")], namespace, str(runner_file))
    return namespace["source_hash"]()  # type: ignore[operator,no-any-return]


@pytest.mark.parametrize("kind", ("src", "flat", "src_named_without_marker"))
def test_fv_mm_source_hash_covers_physical_package(tmp_path: Path, kind: str) -> None:
    anchor, package, runner_file = _fv_mm_fixture(tmp_path, kind)
    folders = (anchor / "strategy", package / "fv_mm", package / "mm_state")
    files = [path for folder in folders for path in sorted(folder.glob("*.py"))]

    before = _call_source_hash(FV_MM_RUNNER_SOURCE, "Runner", runner_file)

    assert before == _digest_files(files)
    _write(package / "fv_mm" / "alpha.py", "ALPHA = 2\n")
    after = _call_source_hash(FV_MM_RUNNER_SOURCE, "Runner", runner_file)
    assert after != before
    assert after == _digest_files(files)


def _fv_pilot_fixture(tmp_path: Path, kind: str) -> tuple[Path, Path, Path]:
    anchor, package = _layout(tmp_path, kind)
    runner_file = package / "fv_pilot" / "runner.py"
    _write(runner_file, "# pilot runner\n")
    _write(package / "fv_pilot" / "alpha.py", "PILOT_ALPHA = 1\n")
    _write(package / "fv_pilot" / "zeta.py", "PILOT_ZETA = 1\n")
    _write(package / "mm_state" / "alpha.py", "STATE_ALPHA = 1\n")
    _write(anchor / "strategy" / "alpha.py", "STRATEGY_ALPHA = 1\n")
    for name in PILOT_FV_MM_SELECTED:
        _write(package / "fv_mm" / name, f"{name} = 1\n")
    _write(package / "fv_mm" / "unselected.py", "IGNORED = 1\n")
    return anchor, package, runner_file


def _pilot_expected_files(anchor: Path, package: Path) -> list[Path]:
    folders = (anchor / "strategy", package / "fv_pilot", package / "mm_state")
    files = [path for folder in folders for path in sorted(folder.glob("*.py"))]
    files.extend(package / "fv_mm" / name for name in PILOT_FV_MM_SELECTED)
    return files


@pytest.mark.parametrize("kind", ("src", "flat", "src_named_without_marker"))
def test_fv_pilot_source_hash_keeps_selected_fv_mm_files(tmp_path: Path, kind: str) -> None:
    anchor, package, runner_file = _fv_pilot_fixture(tmp_path, kind)
    files = _pilot_expected_files(anchor, package)
    before = _call_source_hash(FV_PILOT_RUNNER_SOURCE, "PilotRunner", runner_file)

    assert before == _digest_files(files)
    _write(package / "fv_mm" / "unselected.py", "IGNORED = 2\n")
    assert _call_source_hash(FV_PILOT_RUNNER_SOURCE, "PilotRunner", runner_file) == before
    _write(package / "fv_mm" / "types.py", "types = 2\n")
    after = _call_source_hash(FV_PILOT_RUNNER_SOURCE, "PilotRunner", runner_file)
    assert after != before
    assert after == _digest_files(files)
