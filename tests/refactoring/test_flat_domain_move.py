"""Fixture tests for the small flat-domain move tool.

Every test builds a disposable git repository.  No product source is touched.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "refactoring"))
import flat_domain_move as fdm  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_git(root: Path, *args: str, check: bool = True):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False, env=env,
    )
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return proc


def init_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    run_git(root, "init", "-q")
    run_git(root, "config", "user.name", "Fixture")
    run_git(root, "config", "user.email", "fixture@example.invalid")
    run_git(root, "config", "core.autocrlf", "false")
    return root


def write(root: Path, rel: str, data: bytes | str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def commit(root: Path, message: str = "fixture") -> None:
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", message)


def head(root: Path) -> str:
    return run_git(root, "rev-parse", "HEAD").stdout.strip()


def make_map(tmp_path: Path, root: Path, files, aliases=()) -> Path:
    rows = []
    for old, new, owner in files:
        source = root / old
        rows.append({
            "old_path": old,
            "new_path": new,
            "owner": owner,
            "disposition": "MOVE_ONLY_CANDIDATE",
            "worktree_sha256": sha(source.read_bytes()) if source.is_file() else "0" * 64,
        })
    data = {
        "schema_version": 1,
        "source_sha": head(root),
        "core_compatibility_aliases": list(aliases),
        "files": rows,
    }
    path = tmp_path / "move-map.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def apply(root: Path, map_path: Path, owners):
    plan = fdm.build_plan(root, map_path, owners)
    result = fdm.apply_plan(root, plan)
    return plan, result


def pyproject_text(extra_include: str = "") -> str:
    return (
        "[build-system]\n"
        'requires = ["setuptools>=68", "wheel"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        'name = "vibetrading"\n'
        'version = "0.0.0"\n'
        'requires-python = ">=3.12"\n'
        'dynamic = ["dependencies"]\n\n'
        "[tool.setuptools.dynamic]\n"
        'dependencies = { file = ["requirements.txt"] }\n\n'
        "[tool.setuptools.package-dir]\n"
        'vibetrading = "src/vibetrading"\n\n'
        "[tool.setuptools.packages.find]\n"
        'where = ["src", "."]\n'
        "include = [\n"
        '    "vibetrading",\n'
        '    "vibetrading.*",\n'
        '    "strategy",\n'
        '    "strategy.*",\n'
        f"{extra_include}"
        "]\n"
        "exclude = [\n"
        '    "src",\n'
        '    "src.*",\n'
        "]\n"
    )


def core_repo(tmp_path: Path, consumer_extra: str = "", pyproject: bool = True) -> Path:
    root = init_repo(tmp_path)
    write(root, "src/vibetrading/__init__.py", "")
    write(
        root, "src/vibetrading/dedup.py",
        "class Dedup:\n"
        "    def __init__(self):\n"
        "        self.seen = set()\n",
    )
    write(
        root, "src/vibetrading/models.py",
        "from .dedup import Dedup\n\n"
        "class Model:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "        self.dedup = Dedup()\n",
    )
    write(
        root, "src/vibetrading/consumer.py",
        "import importlib\n"
        "from unittest import mock\n"
        "from vibetrading.models import Model\n"
        "from vibetrading import dedup as dedup_module\n"
        "import vibetrading.dedup as dedup_alias\n\n"
        "def load():\n"
        "    return importlib.import_module(\"vibetrading.models\")\n\n"
        "def patch_it():\n"
        "    return mock.patch(\"vibetrading.models.Model\")\n\n"
        "FIXTURE = \"from vibetrading.models import Model\"\n"
        "assert Model.__module__ == \"vibetrading.models\"\n"
        + consumer_extra,
    )
    if pyproject:
        write(root, "pyproject.toml", pyproject_text())
    commit(root)
    return root


def core_map(tmp_path: Path, root: Path, aliases=("vibetrading.models", "vibetrading.dedup")) -> Path:
    return make_map(
        tmp_path, root,
        [
            ("src/vibetrading/models.py", "src/core/models.py", "core"),
            ("src/vibetrading/dedup.py", "src/core/dedup.py", "core"),
        ],
        aliases,
    )


def python(root: Path, code: str, input_bytes: bytes | None = None):
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        input=input_bytes, capture_output=True, env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout


def test_core_move_aliases_pickle_and_callers(tmp_path):
    root = core_repo(tmp_path)
    old_pickle = python(
        root,
        "import pickle, sys\n"
        "from vibetrading.models import Model\n"
        "sys.stdout.buffer.write(pickle.dumps(Model('kept')))\n",
    )
    map_path = core_map(tmp_path, root)
    plan, result = apply(root, map_path, "core")

    assert result["applied"] is True
    assert not (root / "src/core/models.py").read_bytes() == b""
    assert not (root / "src/vibetrading/models.py").read_text().startswith("class")
    assert "sys.modules[__name__] = _canonical" in (root / "src/vibetrading/models.py").read_text()
    assert "class Model" not in (root / "src/vibetrading/models.py").read_text()
    assert "class Dedup" not in (root / "src/vibetrading/dedup.py").read_text()
    assert (root / "src/core/__init__.py").read_bytes() == b""
    assert not (root / "src/__init__.py").exists()

    assert python(
        root,
        "import vibetrading.models as old, core.models as new\n"
        "assert old is new\n",
    ) == b""
    assert python(
        root,
        "import pickle, sys\n"
        "obj = pickle.load(sys.stdin.buffer)\n"
        "print(obj.name, type(obj).__module__)\n",
        old_pickle,
    ).decode().strip() == "kept core.models"

    consumer = (root / "src/vibetrading/consumer.py").read_text()
    assert "from core.models import Model" in consumer
    assert "from core import dedup as dedup_module" in consumer
    assert "import core.dedup as dedup_alias" in consumer
    assert 'import_module("core.models")' in consumer
    assert 'mock.patch("core.models.Model")' in consumer
    assert 'assert Model.__module__ == "core.models"' in consumer
    assert 'FIXTURE = "from vibetrading.models import Model"' in consumer

    moved = (root / "src/core/models.py").read_text()
    assert "from .dedup import Dedup" in moved
    assert all("before_b64" in w and "after_b64" in w for w in plan["writes"])
    assert plan["head"] == head(root)


def test_cross_domain_relative_rebasing_market(tmp_path):
    root = init_repo(tmp_path)
    write(root, "src/vibetrading/__init__.py", "")
    write(root, "src/vibetrading/market_spool.py", "class Spool:\n    pass\n")
    write(root, "src/vibetrading/hot_path.py", "class Hot:\n    pass\n")
    write(
        root, "src/vibetrading/local_engine.py",
        "from . import market_spool\n"
        "from .market_spool import Spool\n"
        "from .hot_path import Hot\n\n"
        "class Engine:\n"
        "    def run(self):\n"
        "        return Spool(), Hot(), market_spool\n",
    )
    write(root, "src/vibetrading/consumer.py", "from .local_engine import Engine\n")
    write(root, "pyproject.toml", pyproject_text())
    commit(root)
    map_path = make_map(
        tmp_path, root,
        [
            ("src/vibetrading/local_engine.py", "src/market/local_engine.py", "market"),
            ("src/vibetrading/hot_path.py", "src/market/hot_path.py", "market"),
        ],
    )
    apply(root, map_path, "market")

    engine = (root / "src/market/local_engine.py").read_text()
    assert "from vibetrading import market_spool" in engine
    assert "from vibetrading.market_spool import Spool" in engine
    assert "from .hot_path import Hot" in engine
    assert (root / "src/vibetrading/consumer.py").read_text() == (
        "from market.local_engine import Engine\n"
    )
    assert (root / "src/market/__init__.py").read_bytes() == b""
    assert python(
        root,
        "from market.local_engine import Engine\n"
        "spool, hot, module = Engine().run()\n"
        "assert module.__name__ == 'vibetrading.market_spool'\n",
    ) == b""


def test_feeds_providers_package_and_aliased_imports(tmp_path):
    root = init_repo(tmp_path)
    write(root, "src/vibetrading/__init__.py", "")
    write(root, "src/vibetrading/providers/__init__.py", "PROVIDERS = ['base']\n")
    write(root, "src/vibetrading/providers/base.py", "class Base:\n    pass\n")
    write(
        root, "src/vibetrading/consumer.py",
        "from vibetrading.providers import base\n"
        "from vibetrading.providers.base import Base\n"
        "import vibetrading.providers.base as base_alias\n",
    )
    write(root, "pyproject.toml", pyproject_text())
    commit(root)
    map_path = make_map(
        tmp_path, root,
        [
            ("src/vibetrading/providers/__init__.py", "src/feeds/providers/__init__.py", "feeds"),
            ("src/vibetrading/providers/base.py", "src/feeds/providers/base.py", "feeds"),
        ],
    )
    apply(root, map_path, "feeds")

    assert (root / "src/feeds/providers/__init__.py").read_text() == "PROVIDERS = ['base']\n"
    assert (root / "src/feeds/__init__.py").read_bytes() == b""
    consumer = (root / "src/vibetrading/consumer.py").read_text()
    assert "from feeds.providers import base" in consumer
    assert "from feeds.providers.base import Base" in consumer
    assert "import feeds.providers.base as base_alias" in consumer
    assert python(
        root,
        "from feeds.providers.base import Base\n"
        "import feeds.providers as providers\n"
        "assert Base.__module__ == 'feeds.providers.base'\n"
        "assert providers.PROVIDERS == ['base']\n",
    ) == b""


def test_bare_dotted_import_is_rejected_without_mutation(tmp_path):
    root = core_repo(tmp_path, consumer_extra="import vibetrading.models\n")
    map_path = core_map(tmp_path, root)
    with pytest.raises(fdm.UnsupportedSyntax):
        fdm.build_plan(root, map_path, "core")
    assert (root / "src/vibetrading/models.py").read_text().startswith("from .dedup")
    assert not (root / "src/core").exists()


def test_utf8_bom_and_crlf_are_preserved(tmp_path):
    root = init_repo(tmp_path)
    write(root, "src/vibetrading/__init__.py", "")
    write(root, "src/vibetrading/dedup.py", "class Dedup:\n    pass\n")
    original = (
        b"\xef\xbb\xbf# caf\xc3\xa9\r\n"
        b"from .dedup import Dedup\r\n"
        b"\r\n"
        b"class Model:\r\n"
        b"    label = 'na\xc3\xafve'\r\n"
    )
    write(root, "src/vibetrading/models.py", original)
    commit(root)
    map_path = core_map(tmp_path, root)
    apply(root, map_path, "core")
    after = (root / "src/core/models.py").read_bytes()
    assert after.startswith(b"\xef\xbb\xbf# caf\xc3\xa9\r\n")
    assert b"from .dedup import Dedup\r\n" in after
    assert b"class Model:\r\n    label = 'na\xc3\xafve'\r\n" in after


def test_dynamic_targets_and_final_inventory_literals(tmp_path):
    root = core_repo(
        tmp_path,
        consumer_extra=(
            "import importlib.util\n"
            "importlib.util.find_spec(\"vibetrading.models\")\n"
            "__import__(\"vibetrading.dedup\")\n"
            "from unittest import TestCase\n"
            "class T(TestCase):\n"
            "    def test_name(self):\n"
            "        self.assertEqual(Model.__module__, \"vibetrading.models\")\n"
        ),
    )
    write(
        root, "src/vibetrading/final_move_inventory.py",
        "import importlib\n"
        "from vibetrading.models import Model\n"
        "OLD = \"vibetrading.models\"\n"
        "mod = importlib.import_module(\"vibetrading.models\")\n",
    )
    write(
        root, "tests/test_final_move_inventory.py",
        "FIXTURE = \"from vibetrading.models import Model\"\n",
    )
    write(
        root, "src/vibetrading/dynamic_only.py",
        "import importlib\n"
        "mod = importlib.import_module(\"vibetrading.models\")\n",
    )
    commit(root)
    map_path = core_map(tmp_path, root)
    apply(root, map_path, "core")

    consumer = (root / "src/vibetrading/consumer.py").read_text()
    assert 'find_spec("core.models")' in consumer
    assert '__import__("core.dedup")' in consumer
    assert 'assertEqual(Model.__module__, "core.models")' in consumer
    inventory = (root / "src/vibetrading/final_move_inventory.py").read_text()
    assert "from core.models import Model" in inventory
    assert 'OLD = "vibetrading.models"' in inventory
    assert 'import_module("vibetrading.models")' in inventory
    assert (root / "tests/test_final_move_inventory.py").read_text() == (
        "FIXTURE = \"from vibetrading.models import Model\"\n"
    )
    assert 'import_module("core.models")' in (root / "src/vibetrading/dynamic_only.py").read_text()


@pytest.mark.parametrize("scenario", ["casefold", "occupied", "traversal", "symlink", "missing", "absolute"])
def test_unsafe_targets_are_refused(tmp_path, scenario):
    root = init_repo(tmp_path)
    write(root, "src/vibetrading/__init__.py", "")
    write(root, "src/vibetrading/a.py", "X = 1\n")
    write(root, "src/vibetrading/b.py", "Y = 2\n")
    if scenario == "occupied":
        write(root, "src/core/existing.py", "OLD = 1\n")
    if scenario == "symlink":
        write(root, "real/.keep", "")
        (root / "src").mkdir(exist_ok=True)
        os.symlink("../real", root / "src/link")
    commit(root)
    if scenario == "casefold":
        files = [
            ("src/vibetrading/a.py", "src/core/Models.py", "core"),
            ("src/vibetrading/b.py", "src/core/models.py", "core"),
        ]
    elif scenario == "occupied":
        files = [("src/vibetrading/a.py", "src/core/existing.py", "core")]
    elif scenario == "traversal":
        files = [("src/vibetrading/a.py", "../escape.py", "core")]
    elif scenario == "symlink":
        files = [("src/vibetrading/a.py", "src/link/a.py", "core")]
    elif scenario == "missing":
        files = [("src/vibetrading/missing.py", "src/core/missing.py", "core")]
    else:
        files = [("src/vibetrading/a.py", str(tmp_path / "outside.py"), "core")]
    map_path = make_map(tmp_path, root, files)
    with pytest.raises(fdm.MoveError):
        fdm.build_plan(root, map_path, "core")
    assert (root / "src/vibetrading/a.py").exists()
    assert not (root / "src/core/models.py").exists()


def test_stale_head_map_source_and_tampered_plan_do_not_write(tmp_path):
    def planned(root):
        map_path = core_map(tmp_path, root)
        return map_path, fdm.build_plan(root, map_path, "core")

    # stale HEAD
    root = core_repo(tmp_path / "head")
    map_path, plan = planned(root)
    write(root, "unrelated.txt", "later\n")
    commit(root, "later")
    with pytest.raises(fdm.MoveError):
        fdm.apply_plan(root, plan)
    assert (root / "src/vibetrading/models.py").read_text().startswith("from .dedup")
    assert not (root / "src/core/models.py").exists()

    # stale map
    root = core_repo(tmp_path / "map")
    map_path, plan = planned(root)
    map_path.write_text(map_path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(fdm.MoveError):
        fdm.apply_plan(root, plan)
    assert not (root / "src/core/models.py").exists()

    # stale source (keep the old plan HEAD so the source check is reached)
    root = core_repo(tmp_path / "source")
    map_path, plan = planned(root)
    write(root, "src/vibetrading/models.py", (root / "src/vibetrading/models.py").read_text() + "# drift\n")
    commit(root, "source drift")
    plan["head"] = head(root)
    with pytest.raises(fdm.MoveError):
        fdm.apply_plan(root, plan)
    assert not (root / "src/core/models.py").exists()

    # unapproved write injected into the plan
    root = core_repo(tmp_path / "tamper")
    map_path, plan = planned(root)
    evil = dict(plan["writes"][0])
    evil["path"] = "src/evil.py"
    plan["writes"].append(evil)
    with pytest.raises(fdm.MoveError):
        fdm.apply_plan(root, plan)
    assert not (root / "src/evil.py").exists()
    assert not (root / "src/core/models.py").exists()

    # a tampered plan that also marks the extra file as approved is still refused
    root = core_repo(tmp_path / "approved")
    map_path, plan = planned(root)
    evil = dict(plan["writes"][0])
    evil["path"] = "src/evil.py"
    plan["writes"].append(evil)
    plan["approved_paths"].append("src/evil.py")
    plan["changed_paths"].append("src/evil.py")
    with pytest.raises(fdm.MoveError):
        fdm.apply_plan(root, plan)
    assert not (root / "src/evil.py").exists()
    assert not (root / "src/core/models.py").exists()


def test_pyproject_additions_only(tmp_path):
    root = core_repo(tmp_path)
    before = (root / "pyproject.toml").read_text()
    apply(root, core_map(tmp_path, root), "core")
    after = (root / "pyproject.toml").read_text()
    before_data = tomllib.loads(before)
    after_data = tomllib.loads(after)
    assert after_data["project"]["name"] == before_data["project"]["name"]
    assert after_data["project"]["dynamic"] == before_data["project"]["dynamic"]
    assert after_data["tool"]["setuptools"]["package-dir"] == before_data["tool"]["setuptools"]["package-dir"]
    assert after_data["tool"]["setuptools"]["dynamic"] == before_data["tool"]["setuptools"]["dynamic"]
    include = after_data["tool"]["setuptools"]["packages"]["find"]["include"]
    assert "core" in include and "core.*" in include
    for legacy in ("vibetrading", "vibetrading.*", "strategy", "strategy.*"):
        assert legacy in include
    assert after.count('"core",') == 1

    # a TOML include list without a trailing comma is still extended validly
    root = core_repo(tmp_path / "no-comma")
    raw = (root / "pyproject.toml").read_text().replace(
        '    "strategy.*",\n]', '    "strategy.*"\n]'
    )
    write(root, "pyproject.toml", raw)
    commit(root, "no comma")
    apply(root, core_map(tmp_path, root), "core")
    no_comma = tomllib.loads((root / "pyproject.toml").read_text())
    assert "core.*" in no_comma["tool"]["setuptools"]["packages"]["find"]["include"]


def test_business_ast_is_preserved_and_cli_has_no_source_content(tmp_path, capsys):
    root = core_repo(tmp_path)
    map_path = core_map(tmp_path, root)
    before_models = (root / "src/vibetrading/models.py").read_text()
    before_dedup = (root / "src/vibetrading/dedup.py").read_text()
    output = tmp_path / "plan.json"
    rc = fdm.main([
        "--root", str(root), "--map", str(map_path),
        "--owners", "core", "--output", str(output),
    ])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "class Model" not in printed
    assert "self.dedup" not in printed
    assert "src/core/models.py" in printed
    plan = json.loads(output.read_text())
    assert plan["head"] == head(root)
    assert plan["map_sha256"] == sha(map_path.read_bytes())
    assert fdm.main(["--root", str(root), "--apply", str(output)]) == 0
    after_models = (root / "src/core/models.py").read_text()
    after_dedup = (root / "src/core/dedup.py").read_text()

    def business(source):
        tree = ast.parse(source)
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                if hasattr(node, field):
                    setattr(node, field, [
                        ast.Pass() if isinstance(x, (ast.Import, ast.ImportFrom)) else x
                        for x in getattr(node, field)
                    ])
        return ast.dump(tree, include_attributes=False)

    assert business(before_models) == business(after_models)
    assert business(before_dedup) == business(after_dedup)


def test_output_inside_root_is_refused(tmp_path, capsys):
    root = core_repo(tmp_path)
    map_path = core_map(tmp_path, root)
    rc = fdm.main([
        "--root", str(root), "--map", str(map_path),
        "--owners", "core", "--output", str(root / "plan.json"),
    ])
    assert rc == 2
    assert "outside ROOT" in capsys.readouterr().err
    assert not (root / "plan.json").exists()


def test_nonconstant_dynamic_import_argument_is_not_dereferenced(tmp_path):
    root = core_repo(tmp_path, consumer_extra="\nmodule_name = 'math'\nloaded = importlib.import_module(module_name)\n")
    mapping = make_map(tmp_path, root, [
        ("src/vibetrading/models.py", "src/core/models.py", "core"),
        ("src/vibetrading/dedup.py", "src/core/dedup.py", "core"),
    ], aliases=["vibetrading.models", "vibetrading.dedup"])
    plan = fdm.build_plan(root, mapping, ["core"])
    assert plan["summary"]["moves"] == 2


def test_fstring_module_name_prefix_and_live_import(tmp_path):
    root = init_repo(tmp_path)
    write(root, "src/vibetrading/__init__.py", "")
    write(root, "src/vibetrading/legacy/__init__.py", "")
    write(root, "src/vibetrading/legacy/candle/__init__.py", "")
    write(root, "src/vibetrading/legacy/candle/thing.py", "VALUE = 'thing'\n")
    write(
        root, "src/vibetrading/consumer.py",
        "import importlib\n\n"
        "def module_name(name):\n"
        "    return f\"vibetrading.legacy.candle.{name}\"\n\n"
        "def load(name):\n"
        "    return importlib.import_module(module_name(name))\n",
    )
    write(root, "pyproject.toml", pyproject_text())
    commit(root)
    map_path = make_map(tmp_path, root, [
        ("src/vibetrading/legacy/__init__.py", "src/mov05/legacy/__init__.py", "mov05"),
        ("src/vibetrading/legacy/candle/__init__.py",
         "src/mov05/legacy/candle/__init__.py", "mov05"),
        ("src/vibetrading/legacy/candle/thing.py",
         "src/mov05/legacy/candle/thing.py", "mov05"),
    ])
    apply(root, map_path, "mov05")

    consumer = (root / "src/vibetrading/consumer.py").read_text()
    assert 'f"mov05.legacy.candle.{name}"' in consumer
    joined = next(n for n in ast.walk(ast.parse(consumer)) if isinstance(n, ast.JoinedStr))
    assert len(joined.values) == 2
    assert joined.values[0].value == "mov05.legacy.candle."
    assert isinstance(joined.values[1], ast.FormattedValue)
    assert isinstance(joined.values[1].value, ast.Name)
    assert joined.values[1].value.id == "name"
    assert python(
        root,
        "from vibetrading.consumer import load\n"
        "assert load('thing').VALUE == 'thing'\n",
    ) == b""


def test_binop_module_prefix_keeps_dynamic_right_operand(tmp_path):
    root = init_repo(tmp_path)
    write(root, "src/vibetrading/__init__.py", "")
    write(root, "src/vibetrading/research/__init__.py", "")
    write(root, "src/vibetrading/research/state/__init__.py", "")
    write(root, "src/vibetrading/research/state/thing.py", "VALUE = 'thing'\n")
    write(
        root, "src/vibetrading/consumer.py",
        "import importlib\n\n"
        "def load(key):\n"
        "    return importlib.import_module(\"vibetrading.research.state.\" + key)\n",
    )
    write(root, "pyproject.toml", pyproject_text())
    commit(root)
    map_path = make_map(tmp_path, root, [
        ("src/vibetrading/research/state/__init__.py",
         "src/mov05/research/state/__init__.py", "mov05"),
        ("src/vibetrading/research/state/thing.py",
         "src/mov05/research/state/thing.py", "mov05"),
    ])
    apply(root, map_path, "mov05")

    consumer = (root / "src/vibetrading/consumer.py").read_text()
    assert 'import_module("mov05.research.state." + key)' in consumer
    binop = next(n for n in ast.walk(ast.parse(consumer)) if isinstance(n, ast.BinOp))
    assert isinstance(binop.op, ast.Add)
    assert binop.left.value == "mov05.research.state."
    assert isinstance(binop.right, ast.Name) and binop.right.id == "key"
    assert python(
        root,
        "from vibetrading.consumer import load\n"
        "assert load('thing').VALUE == 'thing'\n",
    ) == b""


def test_prefix_edits_preserve_utf8_crlf_bytes(tmp_path):
    root = init_repo(tmp_path)
    write(root, "src/vibetrading/__init__.py", "")
    for package in ("legacy/candle", "research/state"):
        write(root, f"src/vibetrading/{package}/__init__.py", "")
        write(root, f"src/vibetrading/{package}/thing.py", "VALUE = 'thing'\n")
    original = (
        b"\xef\xbb\xbf# caf\xc3\xa9\r\n"
        b"import importlib\r\n"
        b"\r\n"
        b"def fstring_name(name):\r\n"
        b"    return f\"vibetrading.legacy.candle.{name}\"  # na\xc3\xafve\r\n"
        b"\r\n"
        b"def binop_name(key):\r\n"
        b"    return importlib.import_module(\"vibetrading.research.state.\" + key)  # \xc3\xa9\r\n"
    )
    write(root, "src/vibetrading/consumer.py", original)
    write(root, "pyproject.toml", pyproject_text())
    commit(root)
    map_path = make_map(tmp_path, root, [
        ("src/vibetrading/legacy/candle/__init__.py",
         "src/mov05/legacy/candle/__init__.py", "mov05"),
        ("src/vibetrading/legacy/candle/thing.py",
         "src/mov05/legacy/candle/thing.py", "mov05"),
        ("src/vibetrading/research/state/__init__.py",
         "src/mov05/research/state/__init__.py", "mov05"),
        ("src/vibetrading/research/state/thing.py",
         "src/mov05/research/state/thing.py", "mov05"),
    ])
    apply(root, map_path, "mov05")

    after = (root / "src/vibetrading/consumer.py").read_bytes()
    assert after.startswith(b"\xef\xbb\xbf# caf\xc3\xa9\r\n")
    assert b'f"mov05.legacy.candle.{name}"' in after
    assert b'"mov05.research.state." + key' in after
    assert b"# na\xc3\xafve\r\n" in after
    assert after.count(b"\r\n") == original.count(b"\r\n")
    assert after.count(b"\n") == original.count(b"\n")
    assert b"vibetrading.legacy.candle." not in after
    assert b"vibetrading.research.state." not in after


def test_unmapped_dynamic_arguments_and_unsafe_prefixes(tmp_path):
    root = init_repo(tmp_path / "unmapped")
    write(root, "src/vibetrading/__init__.py", "")
    write(root, "src/vibetrading/models.py", "class Model:\n    pass\n")
    unchanged = (
        "import importlib\n\n"
        "def by_variable(name):\n"
        "    return importlib.import_module(name)\n\n"
        "def unmapped_fstring(name):\n"
        "    return f\"other.pkg.{name}\"\n\n"
        "def unmapped_binop(name):\n"
        "    return importlib.import_module(\"other.pkg.\" + name)\n"
    )
    write(root, "src/vibetrading/consumer.py", unchanged)
    commit(root)
    map_path = make_map(tmp_path, root, [
        ("src/vibetrading/models.py", "src/core/models.py", "core"),
    ])
    apply(root, map_path, "core")
    assert (root / "src/vibetrading/consumer.py").read_text() == unchanged

    escaped = init_repo(tmp_path / "escaped")
    write(escaped, "src/vibetrading/__init__.py", "")
    write(escaped, "src/vibetrading/models.py", "class Model:\n    pass\n")
    write(
        escaped, "src/vibetrading/consumer.py",
        "import importlib\n"
        "def fstring_name(name):\n"
        "    return f\"\\x76ibetrading.models.{name}\"\n"
        "def binop_name(name):\n"
        "    return importlib.import_module(\"\\x76ibetrading.models.\" + name)\n",
    )
    commit(escaped)
    escaped_map = make_map(tmp_path / "escaped", escaped, [
        ("src/vibetrading/models.py", "src/core/models.py", "core"),
    ])
    original = (escaped / "src/vibetrading/consumer.py").read_bytes()
    with pytest.raises(fdm.UnsupportedSyntax):
        fdm.build_plan(escaped, escaped_map, "core")
    assert (escaped / "src/vibetrading/consumer.py").read_bytes() == original
    assert not (escaped / "src/core/models.py").exists()

    with pytest.raises(fdm.MoveError, match="overlapping"):
        fdm._apply_edits(b"abcdef", [(1, 4, "x"), (3, 5, "y")])


def test_caller_symlink_is_rejected_before_content_read(tmp_path, monkeypatch):
    root = core_repo(tmp_path)
    outside = tmp_path / "external.py"
    outside.write_text("SYNTHETIC = 1\n")
    link = root / "src/vibetrading/linked.py"
    link.symlink_to(outside)
    commit(root)
    mapping = make_map(tmp_path, root, [
        ("src/vibetrading/models.py", "src/core/models.py", "core"),
        ("src/vibetrading/dedup.py", "src/core/dedup.py", "core"),
    ])
    original_read = Path.read_bytes
    def refuse_link_read(path):
        if path == link:
            raise AssertionError("symlink content read before validation")
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", refuse_link_read)
    with pytest.raises(fdm.MoveError, match="symlink"):
        fdm.build_plan(root, mapping, ["core"])
