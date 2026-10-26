#!/usr/bin/env python3
"""Read-only source ownership inventory and codegen map.

The inventory is deliberately conservative:

* ``git ls-files -z`` is the only file inventory authority.
* Working-tree bytes are hashed separately from the HEAD blob identity.
* Imports and relative imports are resolved statically.
* Constant dynamic imports are resolved; unknown dynamic imports are recorded
  as explicitly deferred exceptions.
* No product, metadata, configuration, or dependency file is written.

The JSON emitted by this script is codegen/evidence input only. It is not an
acceptance decision; the state is always ``PENDING_CODEX_REVIEW``.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


DEFAULT_DOC_PATH = "docs/final/02_DEVELOPER_TICKETS.md"
PENDING_STATE = "PENDING_CODEX_REVIEW"

DEFAULT_B2_BUNDLES: dict[str, str | None] = {
    "providers": "feeds/providers",
    "strategies": "strategies",
    "dashboard": "operations/dashboard",
    "timefolio": "timefolio",
    "fv_mm": None,
    "fv_pilot": None,
    "mm_state": None,
    "liquidity_mm": None,
}

MM_DEFERRED_REASON = (
    "{bundle} has mixed strategy/venue/research ownership; new_path is null. "
    "Symbol reuse and caller audit are required in MOV-05/CLN-02 before any "
    "retirement or function-by-function extraction."
)

CORE_OWNER_EXCEPTION_REASON = (
    "Codex ownership exception: trading_contracts.py imports AccountScope and "
    "positive_decimal from execution_ledger, so execution owns it. The move is "
    "scheduled in MOV-04 to src/execution/trading_contracts.py (not core); "
    "MOV-01 moves only models/dedup. Ledger/order/account logic stays "
    "execution-owned; do not extract or rewrite types."
)

INIT_RETIRE_REASON = (
    "Explicit deferred exception: the old namespace __init__.py is retired in "
    "MOV-07 after callers migrate; it is not deleted immediately."
)

STRATEGY_CONTRACT_REASON = (
    "Explicit deferred exception: strategy_contract.py is a canonical compatibility "
    "alias whose caller audit is not final; retain until MOV-05/CLN-02."
)

LEGACY_REASON = (
    "legacy_deferred until MOV-05/06/CLN-02; preserve every current caller. "
    "No root active package is retired before import-precedence and rollback "
    "artifact obligations are resolved."
)

SENSITIVE_PARTS = {
    ".git",
    "runtime_data",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "node_modules",
}

ACTIVE_ROOTS = {
    "src",
    "services",
    "shared",
    "strategies",
    "strategy",
    "backtest",
    "scripts",
    "tests",
}
LEGACY_ROOTS = {"services", "shared", "strategies", "strategy"}
PRODUCT_ROOT = "src/vibetrading"

INVENTORY_KEYWORD_RE = re.compile(
    r"(?i)\b(command|entrypoint|copy|healthcheck|health|image|profiles?)\b"
)
SERVICE_START_RE = re.compile(r"(?i)\b(?:execstart|execreload)\b")

PROJECT_PATH_ROOTS = ACTIVE_ROOTS | {"deploy", "config", "src"}
PROJECT_MODULE_NAMES = ACTIVE_ROOTS | {"vibetrading"}

PATH_TOKEN_RE = re.compile(
    r"(?:src|services|shared|strategies|strategy|backtest|scripts|tests|deploy|"
    r"config|vibetrading)/[A-Za-z0-9_.\-/]+"
)
PY_PATH_TOKEN_RE = re.compile(r"\b(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.py\b")
MODULE_TOKEN_RE = re.compile(
    r"\b(?:"
    + "|".join(sorted(PROJECT_MODULE_NAMES))
    + r")(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b"
)
FILE_NAME_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_\-]*\.py\b")

HISTORICAL_RE = re.compile(r"(?i)(r00|path[_-]?map)")
HISTORICAL_PROVENANCE_PATHS = (
    "docs/refactoring/vnext/repo_inventory.json",
    "docs/refactoring/vnext/path_map.json",
)
PROTECTED_RE = re.compile(r"(?i)(rollback|historical|history|legacy|archive)")

PROTECTED_DOC_DIR = PurePosixPath("docs/final")
PROTECTED_DOC_PATHS = frozenset(
    {
        "docs/final/01_FINAL_PLAN.md",
        "docs/final/02_DEVELOPER_TICKETS.md",
        "docs/final/03_ARCHITECTURE_REQUIREMENTS.md",
        "docs/final/04_COMPLETED_MANIFEST.json",
        "docs/final/05_TASK_STATUS.json",
    }
)
UNSAFE_OUTPUT_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".cfg",
        ".service",
        ".md",
        ".rst",
        ".csv",
        ".tsv",
        ".parquet",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".log",
        ".pkl",
        ".pickle",
        ".npy",
        ".npz",
        ".pem",
        ".key",
        ".crt",
        ".p12",
    }
)



class InventoryError(RuntimeError):
    """Fatal inventory setup error (for example, not a git work tree)."""


# ---------------------------------------------------------------------------
# Safe tracked-file I/O


def _is_sensitive(path: str) -> bool:
    parts = PurePosixPath(path).parts
    for part in parts:
        low = part.lower()
        if low in SENSITIVE_PARTS:
            return True
        if low.startswith(".env"):
            return True
        if low.startswith("id_rsa"):
            return True
        if low.startswith("secret") or low.startswith("credential"):
            return True
    return False


def _git(root: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip()
        raise InventoryError(message or "git command failed: git " + " ".join(args))
    return proc.stdout


def tracked_paths(root: Path) -> list[str]:
    """Return tracked paths only; this is the inventory authority."""

    raw = _git(root, "ls-files", "-z")
    paths = [
        item.decode("utf-8", "surrogateescape")
        for item in raw.split(b"\0")
        if item
    ]
    return sorted(path for path in paths if path and not path.startswith(".git/"))


def head_identity(root: Path) -> tuple[str, dict[str, str]]:
    try:
        source_sha = _git(root, "rev-parse", "HEAD").decode().strip()
    except InventoryError:
        return "", {}
    blobs: dict[str, str] = {}
    try:
        raw = _git(root, "ls-tree", "-r", "-z", "HEAD")
    except InventoryError:
        return source_sha, blobs
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            meta, path_bytes = entry.split(b"\t", 1)
            fields = meta.split()
            sha = fields[2].decode()
            path = path_bytes.decode("utf-8", "surrogateescape")
        except (ValueError, IndexError):
            continue
        blobs[path] = sha
    return source_sha, blobs


def _has_symlink_component(root: Path, pure: PurePosixPath) -> bool:
    """Return True when any existing path component is a symlink."""

    current = root
    for part in pure.parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def read_worktree(root: Path, path: str) -> bytes | None:
    """Read a tracked path only if it stays inside the repository and is safe."""

    if _is_sensitive(path):
        return None
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    if _has_symlink_component(root, pure):
        return None
    candidate = root / Path(*pure.parts)
    try:
        resolved_root = root.resolve()
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
        mode = candidate.lstat().st_mode
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(mode) or not resolved.is_file():
        return None
    try:
        return resolved.read_bytes()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Ticket document parsing


def _codes(cell: str) -> list[str]:
    return [value.strip() for value in re.findall(r"`([^`]+)`", cell)]


def _section(text: str, start: str, end: str | None) -> str:
    pattern = re.escape(start) + r"(.*?)"
    if end:
        pattern += r"(?=" + re.escape(end) + r")"
    match = re.search(pattern, text, re.S)
    return match.group(1) if match else ""


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _is_header_or_separator(cells: list[str]) -> bool:
    if not cells:
        return True
    first = cells[0]
    if first.startswith("원본") or first.startswith("이전"):
        return True
    compact = first.replace(":", "").replace("-", "").replace(" ", "")
    return compact == ""


def parse_b1_document(text: str) -> list[dict[str, str | None]]:
    section = _section(text, "### B1", "### B2")
    rows: list[dict[str, str | None]] = []
    for line in section.splitlines():
        cells = _table_cells(line)
        if len(cells) < 4 or _is_header_or_separator(cells):
            continue
        old_codes = _codes(cells[0])
        if not old_codes:
            continue
        old_token = old_codes[0]
        old_name = PurePosixPath(old_token).name
        if not old_name.endswith(".py"):
            continue
        target: str | None = None
        for token in _codes(cells[1]):
            match = re.search(
                r"(?:src/)?([A-Za-z0-9_][A-Za-z0-9_./-]*\.py)\b", token
            )
            if match:
                target = match.group(1)
                break
        category_codes = _codes(cells[3])
        category = category_codes[0] if category_codes else cells[3].strip("` ")
        rows.append(
            {
                "old": old_token,
                "old_name": old_name,
                "target": target,
                "owner": cells[2].strip("` "),
                "category": category,
            }
        )
    return rows


def parse_b2_document(text: str) -> list[dict[str, str | None]]:
    section = _section(text, "### B2", "**이전 사유만으로")
    if not section:
        section = _section(text, "### B2", None)
    rows: list[dict[str, str | None]] = []
    for line in section.splitlines():
        cells = _table_cells(line)
        if len(cells) < 3 or _is_header_or_separator(cells):
            continue
        old_codes = _codes(cells[0])
        if not old_codes:
            continue
        match = re.search(r"src/vibetrading/([A-Za-z0-9_]+)/", old_codes[0])
        if not match:
            continue
        source = match.group(1)
        target: str | None = None
        for token in [*_codes(cells[1]), cells[1]]:
            target_match = re.search(
                r"src/([A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*)", token
            )
            if target_match:
                target = target_match.group(1)
                break
        rows.append({"source": source, "target": target, "rule": cells[2]})
    return rows


def normalize_b1_rows(
    rows: Iterable[dict[str, Any] | list[Any] | tuple[Any, ...]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            old = str(row.get("old") or row.get("old_path") or row.get("name") or "")
            target = row.get("target", row.get("new_path"))
            owner = row.get("owner")
            category = row.get("category", row.get("disposition"))
        else:
            values = list(row)
            old = str(values[0]) if values else ""
            target = values[1] if len(values) > 1 else None
            owner = values[2] if len(values) > 2 else None
            category = values[3] if len(values) > 3 else None
        if not old:
            continue
        old_name = PurePosixPath(old).name
        result.append(
            {
                "old": old,
                "old_name": old_name,
                "target": str(target) if target is not None else None,
                "owner": str(owner) if owner else "",
                "category": str(category) if category else "",
            }
        )
    return result


def normalize_b2_rows(
    rows: Iterable[dict[str, Any] | list[Any] | tuple[Any, ...]]
    | dict[str, Any]
    | None,
) -> dict[str, str | None]:
    if rows is None:
        return dict(DEFAULT_B2_BUNDLES)
    if isinstance(rows, dict):
        return {
            str(key): (str(value) if value else None)
            for key, value in rows.items()
        }
    result: dict[str, str | None] = {}
    for row in rows:
        if isinstance(row, dict):
            source = str(row.get("source") or row.get("old") or "")
            target = row.get("target")
        else:
            values = list(row)
            source = str(values[0]) if values else ""
            target = values[1] if len(values) > 1 else None
        if source:
            result[source] = str(target) if target else None
    return result


def _normalize_target(target: str | None) -> str | None:
    if not target:
        return None
    text = target.strip().strip("`").strip().replace("**", "")
    match = re.search(r"(?:src/)?([A-Za-z0-9_][A-Za-z0-9_./-]*\.py)\b", text)
    if not match:
        return None
    relative = match.group(1)
    if relative.startswith("src/"):
        return relative
    return "src/" + relative


def _target_has_retire(target: str | None) -> bool:
    return bool(target) and bool(re.search(r"(?i)retire|폐기|삭제", str(target)))


def _target_key(target: str) -> str:
    text = target.strip().strip("`").strip().replace("**", "")
    return str(PurePosixPath(text)).casefold()


def _lstat_mode(path: Path) -> int | None:
    try:
        return path.lstat().st_mode
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Module and AST analysis


def module_name_for_path(path: str) -> str:
    pure = PurePosixPath(path)
    parts = list(pure.with_suffix("").parts)
    if not parts:
        return ""
    if parts[-1] == "__init__":
        parts = parts[:-1]
    # ``src`` is a packaging directory, not an import package.  Likewise a
    # root legacy package is imported without a repository-prefix component.
    if parts and parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts)


def is_init_path(path: str) -> bool:
    return PurePosixPath(path).name == "__init__.py"


def is_active_python(path: str) -> bool:
    if not path.endswith(".py"):
        return False
    if path.startswith("docs/") or path.startswith(".git/"):
        return False
    if "/" not in path:
        return True
    return path.split("/", 1)[0] in ACTIVE_ROOTS


def is_product_path(path: str) -> bool:
    return path.startswith(PRODUCT_ROOT + "/") and path.endswith(".py")


def is_product_root_path(path: str) -> bool:
    remainder = path[len(PRODUCT_ROOT) + 1 :]
    return "/" not in remainder and remainder.endswith(".py")


def is_legacy_path(path: str) -> bool:
    return (
        path.endswith(".py")
        and "/" in path
        and path.split("/", 1)[0] in LEGACY_ROOTS
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ast_sha256(tree: ast.AST) -> str:
    dumped = ast.dump(tree, include_attributes=False)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def _symbols(tree: ast.AST) -> list[str]:
    symbols: list[str] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.ClassDef):
            symbols.append(node.name)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(node.name + "." + child.name)
    return sorted(dict.fromkeys(symbols))


def _resolve_relative(
    current_module: str, current_is_init: bool, level: int, imported: str | None
) -> str | None:
    if level <= 0:
        return imported
    package = current_module.split(".") if current_module else []
    if not current_is_init:
        package = package[:-1]
    keep = len(package) - (level - 1)
    if keep < 0:
        return None
    base = package[:keep]
    if imported:
        base.extend(imported.split("."))
    return ".".join(part for part in base if part)


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in {"import_module", "__import__"}:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in {
        "import_module",
        "__import__",
    }:
        return func.attr
    return None


def _resolve_dynamic_relative(
    current_module: str, current_is_init: bool, value: str, call: ast.Call
) -> str | None:
    if not value.startswith("."):
        return value
    level = len(value) - len(value.lstrip("."))
    remainder = value[level:]
    package = current_module.split(".") if current_module else []
    if not current_is_init:
        package = package[:-1]
    package_kw = None
    for keyword in call.keywords:
        if keyword.arg == "package" and isinstance(keyword.value, ast.Constant):
            if isinstance(keyword.value.value, str):
                package_kw = keyword.value.value
    if package_kw:
        package = package_kw.split(".")
    keep = len(package) - (level - 1)
    if keep < 0:
        return None
    base = package[:keep]
    if remainder:
        base.extend(remainder.split("."))
    return ".".join(part for part in base if part)


def analyze_python(path: str, data: bytes | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "module": module_name_for_path(path),
        "is_init": is_init_path(path),
        "syntax_error": None,
        "imports": [],
        "dynamic": [],
        "literals": [],
        "symbols": [],
        "ast_sha256": None,
    }
    if data is None:
        return result
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        result["syntax_error"] = "source is not valid UTF-8"
        return result
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as exc:
        result["syntax_error"] = f"{exc.msg} at line {exc.lineno}"
        return result

    result["ast_sha256"] = _ast_sha256(tree)
    result["symbols"] = _symbols(tree)
    current_module = result["module"]
    current_is_init = result["is_init"]

    def add_import(candidate: str | None, line: int, kind: str) -> None:
        if candidate:
            result["imports"].append(
                {"module": candidate, "line": line, "kind": kind}
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_import(alias.name, node.lineno, "import")
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_relative(
                current_module, current_is_init, node.level, node.module
            )
            add_import(base, node.lineno, "from")
            if base:
                for alias in node.names:
                    if alias.name != "*":
                        add_import(base + "." + alias.name, node.lineno, "from")
        elif isinstance(node, ast.Call):
            call_name = _call_name(node)
            if not call_name:
                continue
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                raw_value = node.args[0].value
                resolved = _resolve_dynamic_relative(
                    current_module, current_is_init, raw_value, node
                )
                result["dynamic"].append(
                    {
                        "expression": raw_value,
                        "kind": call_name,
                        "line": node.lineno,
                        "resolved_module": resolved,
                        "unknown": False,
                    }
                )
                add_import(resolved, node.lineno, "dynamic")
            else:
                result["dynamic"].append(
                    {
                        "expression": "UNKNOWN",
                        "kind": call_name,
                        "line": node.lineno,
                        "resolved_module": None,
                        "unknown": True,
                    }
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            result["literals"].append(node.value)
    return result


# ---------------------------------------------------------------------------
# Ownership helpers


def _owner_for_legacy(path: str) -> tuple[str, str | None, str]:
    rules = (
        (r"^services/data_feed(?:/|$)", "feeds", "feeds"),
        (r"^services/(?:execution|risk)(?:/|$)", "execution", "execution"),
        (r"^services/monitoring(?:/|$)", "operations", "operations"),
        (r"^services/signal_gen(?:/|$)", "strategies", "strategies"),
    )
    for pattern, owner, domain in rules:
        if re.search(pattern, path):
            parts = PurePosixPath(path).parts
            remainder = parts[2:]
            proposed = None
            if remainder:
                proposed = "src/" + domain + "/" + "/".join(remainder)
            return owner, proposed, domain
    first = PurePosixPath(path).parts[0]
    if first in {"strategies", "strategy"}:
        return "strategies", None, "strategies"
    if first == "shared":
        return "shared", None, "shared"
    if first == "services":
        return "services", None, "services"
    return "unclassified", None, "unclassified"


# ---------------------------------------------------------------------------
# Configuration / Docker inventory


def _inventory_category(path: str) -> str | None:
    pure = PurePosixPath(path)
    name = pure.name
    low = name.lower()
    if "/" not in path and low.startswith("dockerfile"):
        return "docker"
    if (
        "/" not in path
        and low.startswith("docker-compose")
        and low.endswith((".yml", ".yaml"))
    ):
        return "compose"
    if path.startswith("deploy/") and (
        low.startswith("dockerfile") or low.endswith((".yml", ".yaml"))
    ):
        return "deploy"
    if low.endswith(".service"):
        return "service"
    if path == "scripts/build_release_images.py":
        return "build_script"
    if path.startswith("config/") and low.endswith((".yml", ".yaml")):
        return "config"
    if path == "pyproject.toml":
        return "pyproject"
    return None


def _line_tokens(line: str) -> list[str]:
    tokens: set[str] = set()
    for match in PATH_TOKEN_RE.findall(line):
        token = match.strip(".,:;'\"()[]{}")
        if token:
            tokens.add(token)
    for match in PY_PATH_TOKEN_RE.findall(line):
        token = match.strip(".,:;'\"()[]{}")
        if token and token.split("/", 1)[0] in PROJECT_PATH_ROOTS:
            tokens.add(token)
    for match in MODULE_TOKEN_RE.findall(line):
        token = match.strip(".,:;'\"()[]{}")
        if token:
            tokens.add(token)
    for match in FILE_NAME_TOKEN_RE.findall(line):
        token = match.strip(".,:;'\"()[]{}")
        if token:
            tokens.add(token)
    filtered = []
    for token in sorted(tokens):
        if (
            token.endswith(".py")
            or "/" in token
            or "." in token
            or token.startswith("vibetrading")
        ):
            filtered.append(token)
    return filtered


def inventory_config_and_docker(
    root: Path, paths: list[str]
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in paths:
        category = _inventory_category(path)
        if not category or _is_sensitive(path):
            continue
        data = read_worktree(root, path)
        if data is None:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        protected = bool(PROTECTED_RE.search(path))
        for number, line in enumerate(text.splitlines(), start=1):
            keyword_match = INVENTORY_KEYWORD_RE.search(line)
            keyword = keyword_match.group(1).lower() if keyword_match else None
            if keyword is None and category == "service" and SERVICE_START_RE.search(line):
                keyword = "entrypoint"
            if keyword is None:
                continue
            entries.append(
                {
                    "file": path,
                    "line": number,
                    "keyword": keyword,
                    "tokens": _line_tokens(line),
                    "category": category,
                    "protected": protected,
                }
            )
    return entries


def _record_tokens(record: dict[str, Any]) -> dict[str, set[str]]:
    old_path = record.get("old_path") or ""
    new_path = record.get("new_path") or ""
    old_module = module_name_for_path(old_path)
    new_module = module_name_for_path(new_path) if new_path else ""
    return {
        "paths": {value for value in (old_path, new_path) if value},
        "modules": {value for value in (old_module, new_module) if value},
        "names": {
            PurePosixPath(value).name
            for value in (old_path, new_path)
            if value
        },
    }


def _token_matches_record(token: str, record: dict[str, Any]) -> bool:
    tokens = _record_tokens(record)
    normalized = token.strip().strip("`")
    if normalized in tokens["paths"] or normalized in tokens["modules"]:
        return True
    stripped = normalized[4:] if normalized.startswith("src/") else normalized
    for candidate in tokens["paths"]:
        candidate_stripped = (
            candidate[4:] if candidate.startswith("src/") else candidate
        )
        if stripped == candidate_stripped:
            return True
    for module in tokens["modules"]:
        if normalized == module or normalized.startswith(module + "."):
            return True
    name = PurePosixPath(normalized).name
    if name in tokens["names"] and ("/" in normalized or normalized.endswith(".py")):
        return True
    return False


def _matching_refs(
    record: dict[str, Any], entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    refs = []
    for entry in entries:
        if any(
            _token_matches_record(token, record) for token in entry["tokens"]
        ):
            refs.append(
                {
                    "file": entry["file"],
                    "line": entry["line"],
                    "keyword": entry["keyword"],
                    "tokens": entry["tokens"],
                    "protected": entry["protected"],
                }
            )
    return refs


# ---------------------------------------------------------------------------
# Historical provenance (metadata only, never authoritative targets)


def historical_provenance(
    root: Path,
    paths: list[str],
    blobs: dict[str, str],
    explicit_paths: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    allowed = set(HISTORICAL_PROVENANCE_PATHS)
    if explicit_paths is not None:
        allowed.update(str(path) for path in explicit_paths)
    tracked = set(paths)
    provenance = []
    for path in sorted(allowed):
        if path not in tracked:
            continue
        data = read_worktree(root, path)
        if data is None:
            continue
        schema: str | None = None
        count: int | None = None
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            document = None
        if isinstance(document, dict):
            schema = str(
                document.get("schema") or document.get("schema_version") or ""
            )
            for key in ("entries", "rows", "files", "paths", "map"):
                value = document.get(key)
                if isinstance(value, (list, dict)):
                    count = len(value)
                    break
            if count is None:
                count = len(document)
        elif isinstance(document, list):
            schema = "list"
            count = len(document)
        provenance.append(
            {
                "path": path,
                "schema": schema,
                "count": count,
                "source_blob_sha": blobs.get(path, ""),
                "sha256": _sha256(data),
                "authoritative_targets": False,
                "usage": "provenance only: schema/count/SHA/hash",
            }
        )
    return provenance


# ---------------------------------------------------------------------------
# Collision obligations


def collision_obligations(paths: set[str]) -> list[dict[str, Any]]:
    collisions = [
        {
            "kind": "root_strategies_vs_src_strategies",
            "paths": ["strategies/", "src/strategies/"],
            "present": any(path.startswith("strategies/") for path in paths)
            or any(path.startswith("src/strategies/") for path in paths),
            "migration_wave": "MOV-05/CLN-02",
            "resolution": (
                "Move active strategy code to src/strategies only after caller and "
                "import-precedence audit; retire the root package from new product "
                "imports while preserving rollback artifacts."
            ),
            "sys_path_workaround": False,
        },
        {
            "kind": "execution_module_vs_execution_package",
            "paths": ["src/vibetrading/execution.py", "src/execution/"],
            "present": "src/vibetrading/execution.py" in paths,
            "migration_wave": "MOV-01/MOV-02",
            "resolution": (
                "execution.py maps to src/execution/contracts.py; callers use the "
                "package import, never a module/package dual-resolution trick."
            ),
            "sys_path_workaround": False,
        },
        {
            "kind": "runtime_module_vs_runtime_package",
            "paths": ["src/vibetrading/runtime.py", "src/runtime/"],
            "present": "src/vibetrading/runtime.py" in paths,
            "migration_wave": "MOV-01/MOV-02",
            "resolution": (
                "runtime.py maps to src/runtime/runtime.py; callers use the package "
                "import and the old module is retired after the caller audit."
            ),
            "sys_path_workaround": False,
        },
        {
            "kind": "old_vibetrading_namespace",
            "paths": ["src/vibetrading/", "src/<domain>/"],
            "present": any(path.startswith("src/vibetrading/") for path in paths),
            "migration_wave": "MOV-05/CLN-02",
            "resolution": (
                "The old namespace is retired only after every product import and "
                "rollback artifact obligation is resolved."
            ),
            "sys_path_workaround": False,
        },
    ]
    obligations = [
        "baseline cwd import precedence must be captured before moving packages",
        "wheel contents and package-dir/package-find metadata must agree",
        "root packages remain rollback artifacts but have no authority in new releases",
        "no sys.path insertion, shim package, or cwd-dependent fallback is allowed",
    ]
    for collision in collisions:
        collision["baseline_cwd_wheel_import_precedence_obligations"] = obligations
    return collisions


# ---------------------------------------------------------------------------
# Core inventory


def _make_empty_summary() -> dict[str, int]:
    return {
        "root_rows": 0,
        "product_files": 0,
        "legacy_files": 0,
        "subpackages": 0,
        "unclassified": 0,
        "duplicate_targets": 0,
        "old_path_mismatch": 0,
        "missing_or_ambiguous": 0,
    }


def _result(
    *,
    root: Path,
    source_sha: str,
    b1_rows: list[dict[str, Any]],
    b2_bundles: dict[str, str | None],
    product_paths: list[str],
    legacy_paths: list[str],
    subpackages: int,
    records: list[dict[str, Any]],
    legacy_records: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    dynamic_exceptions: list[dict[str, Any]],
    inventory_entries: list[dict[str, Any]],
    provenance: list[dict[str, Any]],
    paths: set[str],
) -> dict[str, Any]:
    summary = _make_empty_summary()
    summary["root_rows"] = len(b1_rows)
    summary["product_files"] = len(product_paths)
    summary["legacy_files"] = len(legacy_paths)
    summary["subpackages"] = subpackages
    summary["unclassified"] = sum(
        1
        for record in [*records, *legacy_records]
        if record.get("disposition") == "UNCLASSIFIED"
    )
    summary["old_path_mismatch"] = sum(
        1 for error in errors if error.get("kind") == "missing_root_path"
    )
    duplicate_groups = {
        str(error.get("path"))
        for error in errors
        if error.get("kind") == "duplicate_target"
    }
    summary["duplicate_targets"] = len(duplicate_groups)
    summary["missing_or_ambiguous"] = len(errors)
    exit_code = 2 if summary["missing_or_ambiguous"] > 0 else 0
    return {
        "schema_version": 1,
        "state": PENDING_STATE,
        "root": str(root),
        "source_sha": source_sha,
        "summary": summary,
        "b1_rows": b1_rows,
        "b2_bundles": [
            {"source": source, "target": target, "deferred": target is None}
            for source, target in b2_bundles.items()
        ],
        "records": records,
        "legacy_records": legacy_records,
        "dynamic_exceptions": dynamic_exceptions,
        "collisions": collision_obligations(paths),
        "config_docker_inventory": inventory_entries,
        "historical_provenance": provenance,
        "errors": errors,
        "exit_code": exit_code,
    }


def run_inventory(
    root: str | Path,
    *,
    b1_rows: Iterable[dict[str, Any] | list[Any] | tuple[Any, ...]] | None = None,
    b2_rows: Any = None,
    doc_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the complete readonly ownership inventory for *root*.

    ``b1_rows`` and ``b2_rows`` are injection points for tiny synthetic test
    matrices. When omitted, the supplied developer-ticket document is parsed.
    """

    root_path = Path(root).resolve()
    errors: list[dict[str, Any]] = []
    source_sha, blobs = head_identity(root_path)
    try:
        paths = tracked_paths(root_path)
    except InventoryError as exc:
        errors.append({"kind": "git_inventory_error", "detail": str(exc)})
        paths = []

    need_b1 = b1_rows is None
    need_b2 = b2_rows is None
    if need_b1 or need_b2:
        effective_doc = (
            Path(doc_path)
            if doc_path is not None
            else root_path / DEFAULT_DOC_PATH
        )
        text = ""
        if effective_doc.is_file():
            try:
                text = effective_doc.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(
                    {
                        "kind": "doc_read_error",
                        "detail": str(exc),
                        "path": str(effective_doc),
                    }
                )
        elif need_b1:
            errors.append(
                {
                    "kind": "doc_missing",
                    "detail": "developer ticket document not found",
                    "path": str(effective_doc),
                }
            )
        if need_b1:
            parsed_b1 = parse_b1_document(text) if text else []
            if text and not parsed_b1:
                errors.append(
                    {
                        "kind": "b1_parse_empty",
                        "detail": "no B1 root rows could be parsed",
                        "path": str(effective_doc),
                    }
                )
            b1_rows = parsed_b1
        if need_b2:
            parsed_b2 = parse_b2_document(text) if text else []
            b2_rows = parsed_b2 or dict(DEFAULT_B2_BUNDLES)

    normalized_b1 = normalize_b1_rows(b1_rows or [])
    b2_bundles = normalize_b2_rows(b2_rows)

    active_paths = [path for path in paths if is_active_python(path)]
    product_paths = [path for path in active_paths if is_product_path(path)]
    product_root_paths = [
        path for path in product_paths if is_product_root_path(path)
    ]
    legacy_paths = [path for path in active_paths if is_legacy_path(path)]

    safe_active_paths = [
        path for path in active_paths if not _is_sensitive(path)
    ]
    analyses: dict[str, dict[str, Any]] = {}
    analysis_data: dict[str, bytes | None] = {}
    for path in safe_active_paths:
        data = read_worktree(root_path, path)
        analysis_data[path] = data
        analyses[path] = analyze_python(path, data)

    module_to_path: dict[str, str] = {}
    for path, analysis in analyses.items():
        module = analysis.get("module")
        if module and module not in module_to_path:
            module_to_path[module] = path
    module_to_path.setdefault(
        "vibetrading", PRODUCT_ROOT + "/__init__.py"
    )

    # Resolve import and dynamic edges against tracked modules.
    deps: dict[str, set[str]] = {path: set() for path in analyses}
    dynamic_edges: list[dict[str, Any]] = []
    string_refs: dict[str, list[str]] = {}
    for path, analysis in analyses.items():
        for imported in analysis.get("imports", []):
            candidate = imported["module"]
            if candidate in module_to_path:
                deps[path].add(module_to_path[candidate])
            prefix = candidate + "."
            for module, target in module_to_path.items():
                if module.startswith(prefix):
                    deps[path].add(target)
        for dynamic in analysis.get("dynamic", []):
            resolved_module = dynamic.get("resolved_module")
            dynamic_edges.append(
                {
                    "source": path,
                    "expression": dynamic["expression"],
                    "kind": dynamic["kind"],
                    "line": dynamic["line"],
                    "resolved_module": resolved_module,
                    "unknown": bool(dynamic.get("unknown")),
                }
            )
            if resolved_module and resolved_module in module_to_path:
                deps[path].add(module_to_path[resolved_module])
        string_refs[path] = analysis.get("literals", [])

    reverse_deps: dict[str, set[str]] = {}
    for source, targets in deps.items():
        for target in targets:
            if target != source:
                reverse_deps.setdefault(target, set()).add(source)

    # Root B1 reconciliation.
    expected_names = {row["old_name"] for row in normalized_b1}
    actual_names = {PurePosixPath(path).name for path in product_root_paths}
    missing_names = expected_names - actual_names
    extra_names = actual_names - expected_names
    for name in sorted(missing_names):
        errors.append(
            {
                "kind": "missing_root_path",
                "path": PRODUCT_ROOT + "/" + name,
                "detail": "B1 root row has no tracked source file",
            }
        )
    for name in sorted(extra_names):
        errors.append(
            {
                "kind": "extra_root_path",
                "path": PRODUCT_ROOT + "/" + name,
                "detail": "tracked root module is absent from B1 and is unclassified",
            }
        )

    old_groups: dict[str, list[str]] = {}
    for row in normalized_b1:
        old_groups.setdefault(row["old_name"].casefold(), []).append(
            row["old_name"]
        )
    duplicate_old_keys: set[str] = set()
    for key, names in sorted(old_groups.items()):
        if len(names) < 2:
            continue
        duplicate_old_keys.add(key)
        for old_name in sorted(set(names)):
            errors.append(
                {
                    "kind": "duplicate_b1_source",
                    "path": PRODUCT_ROOT + "/" + old_name,
                    "sources": sorted(names),
                    "detail": (
                        "duplicate B1 old source rows are ambiguous even when "
                        "their targets differ"
                    ),
                }
            )

    # Unknown product subpackages are rejected, never silently classified.
    product_subpackages = sorted(
        {
            PurePosixPath(path).parts[2]
            for path in product_paths
            if len(PurePosixPath(path).parts) > 3
        }
    )
    for subpackage in product_subpackages:
        if subpackage not in b2_bundles:
            errors.append(
                {
                    "kind": "unknown_product_subpackage",
                    "path": PRODUCT_ROOT + "/" + subpackage,
                    "detail": "product subpackage is absent from B2",
                }
            )

    # Build product records.
    records: list[dict[str, Any]] = []
    records_by_old: dict[str, dict[str, Any]] = {}
    new_targets: dict[str, list[str]] = {}
    new_target_display: dict[str, str] = {}

    def base_record(
        path: str,
        owner: str,
        disposition: str,
        reason: str,
        new_path: str | None,
    ) -> dict[str, Any]:
        analysis = analyses.get(path, {})
        data = read_worktree(root_path, path)
        syntax_error = analysis.get("syntax_error")
        if data is None:
            disposition = "MISSING_OR_AMBIGUOUS"
            reason = (
                "Tracked source is missing, unreadable, symlinked, or not a "
                "regular file; " + reason
            )
            syntax_error = None
        elif syntax_error and disposition not in {"MISSING_OR_AMBIGUOUS"}:
            disposition = "SYNTAX_ERROR"
            reason = "Syntax error: " + str(syntax_error) + "; " + reason
        return {
            "source_sha": source_sha,
            "old_path": path,
            "new_path": new_path,
            "source_blob_sha": blobs.get(path, ""),
            "worktree_sha256": _sha256(data) if data is not None else None,
            "source_ast_sha256": analysis.get("ast_sha256"),
            "owner": owner,
            "owning_symbol": analysis.get("symbols", []),
            "owning_symbols": analysis.get("symbols", []),
            "active_callers": [],
            "tests": [],
            "entrypoint": [],
            "config_refs": [],
            "docker_refs": [],
            "rollback_refs": [],
            "dynamic_refs": [],
            "disposition": disposition,
            "reason": reason,
            "syntax_error": syntax_error,
        }

    for row in normalized_b1:
        old_path = PRODUCT_ROOT + "/" + row["old_name"]
        target = row.get("target")
        owner = row.get("owner") or ""
        category = row.get("category") or ""
        old_name = row["old_name"]

        if old_name == "__init__.py":
            new_path = None
            owner = owner or "packaging"
            disposition = "RETIRE_OLD_NAMESPACE"
            reason = INIT_RETIRE_REASON
        elif old_name == "strategy_contract.py":
            new_path = None
            owner = owner or "strategies"
            disposition = "DEFERRED_WITH_BLOCKER"
            reason = STRATEGY_CONTRACT_REASON
        elif old_name == "trading_contracts.py":
            new_path = "src/execution/trading_contracts.py"
            owner = "execution"
            disposition = "MOVE_ONLY_CANDIDATE"
            reason = CORE_OWNER_EXCEPTION_REASON
        elif _target_has_retire(target):
            new_path = None
            owner = owner or "packaging"
            disposition = "RETIRE_OLD_NAMESPACE"
            reason = "B1 target retires this root module after caller migration."
        else:
            new_path = _normalize_target(target)
            disposition = category or "MOVE_ONLY_CANDIDATE"
            if not new_path:
                disposition = "DEFERRED_WITH_BLOCKER"
                reason = (
                    "B1 target is absent or ambiguous; explicit alias/caller audit "
                    "is required in MOV-05/CLN-02."
                )
            elif "REUSE_OR_RETIRE" in disposition:
                disposition = "DEFERRED_WITH_BLOCKER"
                reason = STRATEGY_CONTRACT_REASON
            else:
                reason = "B1 root row maps mechanically to the owning domain."
                owner = owner or (
                    new_path.split("/")[1]
                    if new_path and new_path.count("/") > 1
                    else "unclassified"
                )

        if old_name not in actual_names:
            record = base_record(
                old_path,
                owner,
                "MISSING_OR_AMBIGUOUS",
                "B1 row is missing from tracked src/vibetrading",
                new_path,
            )
            record["worktree_sha256"] = None
            record["source_ast_sha256"] = None
        else:
            record = base_record(
                old_path, owner, disposition, reason, new_path
            )
        if old_name.casefold() in duplicate_old_keys:
            record["disposition"] = "MISSING_OR_AMBIGUOUS"
            record["reason"] = (
                "Duplicate B1 old source rows are ambiguous; " + reason
            )
        records.append(record)
        records_by_old[old_path] = record
        if new_path:
            target_key = _target_key(new_path)
            new_targets.setdefault(target_key, []).append(old_path)
            new_target_display.setdefault(target_key, new_path)

    for name in sorted(extra_names):
        old_path = PRODUCT_ROOT + "/" + name
        if old_path in records_by_old:
            continue
        record = base_record(
            old_path,
            "unclassified",
            "UNCLASSIFIED",
            "Tracked root module is absent from B1; explicit ownership audit required.",
            None,
        )
        records.append(record)
        records_by_old[old_path] = record

    for path in product_paths:
        if is_product_root_path(path):
            continue
        parts = PurePosixPath(path).parts
        top = parts[2]
        remainder = "/".join(parts[3:])
        if top not in b2_bundles:
            record = base_record(
                path,
                "unclassified",
                "UNCLASSIFIED",
                "Product subpackage is absent from B2; explicit ownership audit required.",
                None,
            )
        elif b2_bundles[top] is None:
            record = base_record(
                path,
                top,
                "DEFERRED_WITH_BLOCKER",
                MM_DEFERRED_REASON.format(bundle=top),
                None,
            )
        else:
            target = b2_bundles[top] or ""
            new_path = (
                "src/" + target + "/" + "/".join(parts[3:])
                if remainder
                else "src/" + target + "/__init__.py"
            )
            owner = target.split("/")[0]
            reason = (
                "B2 subpackage mapping to the owning domain; retain B2 caller and "
                "dynamic-import audit obligations."
            )
            record = base_record(
                path, owner, "MOVE_ONLY_CANDIDATE", reason, new_path
            )
            target_key = _target_key(new_path)
            new_targets.setdefault(target_key, []).append(path)
            new_target_display.setdefault(target_key, new_path)
        records.append(record)
        records_by_old[path] = record

    # Duplicate targets and existing destination collisions.
    for target_key, old_paths in sorted(new_targets.items()):
        if len(old_paths) > 1:
            errors.append(
                {
                    "kind": "duplicate_target",
                    "path": new_target_display.get(target_key, target_key),
                    "sources": sorted(old_paths),
                    "detail": (
                        "multiple product files compile to the same normalized "
                        "target path"
                    ),
                }
            )
    destination_collisions: set[tuple[str, str]] = set()
    tracked_set = set(paths)
    tracked_casefold = {path.casefold(): path for path in paths}
    for record in records:
        new_path = record.get("new_path")
        old_path = record.get("old_path")
        if not new_path or new_path == old_path:
            continue
        pure_new = PurePosixPath(new_path)
        destination_mode = _lstat_mode(root_path / Path(*pure_new.parts))
        tracked_match = (
            new_path
            if new_path in tracked_set
            else tracked_casefold.get(new_path.casefold())
        )
        if destination_mode is not None or tracked_match is not None:
            key = (old_path, new_path)
            if key not in destination_collisions:
                destination_collisions.add(key)
                if tracked_match is not None:
                    detail = "destination path is already tracked"
                    if tracked_match != new_path:
                        detail += " (case-insensitive match)"
                elif stat.S_ISLNK(destination_mode or 0):
                    detail = "untracked destination is an existing symlink"
                elif stat.S_ISDIR(destination_mode or 0):
                    detail = "untracked destination is an existing directory"
                else:
                    detail = "untracked destination already exists"
                errors.append(
                    {
                        "kind": "existing_destination_collision",
                        "path": new_path,
                        "source": old_path,
                        "detail": detail,
                    }
                )
        parts = list(pure_new.parts)
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            parent_mode = _lstat_mode(root_path / Path(*parts[:index]))
            parent_case_match = tracked_casefold.get(parent.casefold())
            parent_blocked = parent_mode is not None and (
                stat.S_ISLNK(parent_mode) or not stat.S_ISDIR(parent_mode)
            )
            if parent in tracked_set or parent_case_match is not None or parent_blocked:
                key = (old_path, parent)
                if key not in destination_collisions:
                    destination_collisions.add(key)
                    if parent in tracked_set:
                        detail = "destination parent is an existing tracked file"
                    elif parent_case_match is not None:
                        detail = (
                            "destination parent is an existing tracked file "
                            "(case-insensitive match)"
                        )
                    elif stat.S_ISLNK(parent_mode or 0):
                        detail = "destination parent is an existing symlink"
                    else:
                        detail = "destination parent is an existing non-directory"
                    errors.append(
                        {
                            "kind": "destination_parent_collision",
                            "path": parent,
                            "source": old_path,
                            "detail": detail,
                        }
                    )
                break

    # Missing/unreadable source and syntax errors are hard inventory failures.
    source_seen: set[str] = set()
    for path in [*product_paths, *legacy_paths]:
        if path in source_seen:
            continue
        source_seen.add(path)
        if analysis_data.get(path) is None:
            errors.append(
                {
                    "kind": "missing_source",
                    "path": path,
                    "detail": (
                        "tracked source is missing, unreadable, symlinked, or "
                        "not a regular file"
                    ),
                }
            )
            continue
        error = analyses.get(path, {}).get("syntax_error")
        if error:
            errors.append(
                {"kind": "syntax_error", "path": path, "detail": str(error)}
            )

    # Build legacy records; all are explicitly deferred and never authoritative.
    legacy_records: list[dict[str, Any]] = []
    for path in legacy_paths:
        owner, proposed, domain = _owner_for_legacy(path)
        reason = LEGACY_REASON
        if path.startswith(("strategies/", "strategy/")):
            reason += (
                " Root strategies/source-strategies import precedence remains "
                "unresolved; no sys.path workaround is permitted."
            )
        record = base_record(
            path, owner, "LEGACY_DEFERRED", reason, None
        )
        record["proposed_new_path"] = proposed
        record["owning_domain"] = domain
        legacy_records.append(record)
        records_by_old[path] = record

    inventory_entries = inventory_config_and_docker(root_path, paths)
    provenance = historical_provenance(root_path, paths, blobs)

    # Owner lookup used by dynamic-import exceptions.
    def owner_for_source(path: str) -> str:
        record = records_by_old.get(path)
        if record:
            return str(record.get("owner") or "unclassified")
        if path.startswith(PRODUCT_ROOT + "/"):
            parts = PurePosixPath(path).parts
            if len(parts) > 3:
                bundle = parts[2]
                target = b2_bundles.get(bundle)
                return target.split("/")[0] if target else bundle
        if is_legacy_path(path):
            return _owner_for_legacy(path)[0]
        return "unclassified"

    def source_literal_matches(source: str, record: dict[str, Any]) -> bool:
        tokens = _record_tokens(record)
        candidates = {
            value
            for value in [*tokens["paths"], *tokens["modules"]]
            if value
        }
        for literal in string_refs.get(source, []):
            for candidate in candidates:
                if candidate and (
                    literal == candidate
                    or candidate in literal
                    or literal in candidate
                ):
                    return True
        return False

    dynamic_exceptions: list[dict[str, Any]] = []
    for edge in dynamic_edges:
        if not edge["unknown"]:
            continue
        dynamic_exceptions.append(
            {
                "source": edge["source"],
                "line": edge["line"],
                "kind": edge["kind"],
                "expression": "UNKNOWN",
                "owner": owner_for_source(edge["source"]),
                "blocking_wave": "MOV-05/CLN-02",
                "disposition": "DEFERRED_WITH_BLOCKER",
                "reason": (
                    "Dynamic import expression cannot be resolved statically; "
                    "explicit caller audit is required. It is not silently "
                    "resolved or removed."
                ),
            }
        )

    all_records = [*records, *legacy_records]
    for record in all_records:
        old_path = record["old_path"]
        old_module = module_name_for_path(old_path)
        new_module = (
            module_name_for_path(record["new_path"])
            if record.get("new_path")
            else ""
        )
        modules = {
            value for value in (old_module, new_module) if value
        }

        callers: set[str] = set(reverse_deps.get(old_path, set()))
        for source in string_refs:
            if source != old_path and source_literal_matches(source, record):
                callers.add(source)
        callers.discard(old_path)

        record["active_callers"] = sorted(
            path for path in callers if not path.startswith("tests/")
        )
        record["tests"] = sorted(
            path for path in callers if path.startswith("tests/")
        )

        matched_refs = _matching_refs(record, inventory_entries)
        record["entrypoint"] = [
            ref
            for ref in matched_refs
            if ref["keyword"] in {"command", "entrypoint"}
        ]
        record["config_refs"] = [
            ref
            for ref in matched_refs
            if ref["file"] == "pyproject.toml"
            or ref["file"].startswith("config/")
        ]
        record["docker_refs"] = [
            ref
            for ref in matched_refs
            if ref["file"] != "pyproject.toml"
            and not ref["file"].startswith("config/")
        ]
        record["rollback_refs"] = [
            ref for ref in matched_refs if ref["protected"]
        ]
        for source in sorted(callers):
            if PROTECTED_RE.search(source):
                record["rollback_refs"].append(
                    {
                        "file": source,
                        "line": None,
                        "keyword": "source_reference",
                        "tokens": [],
                        "protected": True,
                    }
                )

        seen_dynamic: set[tuple[str, int, str]] = set()
        for edge in dynamic_edges:
            attach = False
            if edge["source"] == old_path:
                attach = True
            resolved = edge.get("resolved_module")
            if resolved and any(
                resolved == module or resolved.startswith(module + ".")
                for module in modules
            ):
                attach = True
            if not attach:
                continue
            key = (edge["source"], edge["line"], edge["expression"])
            if key in seen_dynamic:
                continue
            seen_dynamic.add(key)
            record["dynamic_refs"].append(
                {
                    "source": edge["source"],
                    "line": edge["line"],
                    "kind": edge["kind"],
                    "expression": edge["expression"],
                    "resolved_module": resolved,
                    "unknown": edge["unknown"],
                    "owner": owner_for_source(edge["source"]),
                    "blocking_wave": (
                        "MOV-05/CLN-02" if edge["unknown"] else None
                    ),
                    "disposition": (
                        "DEFERRED_WITH_BLOCKER"
                        if edge["unknown"]
                        else "CLASSIFIED"
                    ),
                }
            )

    error_paths = {str(error.get("path")) for error in errors}
    for record in [*records, *legacy_records]:
        if record.get("disposition") != "MISSING_OR_AMBIGUOUS":
            continue
        if record["old_path"] in error_paths:
            continue
        errors.append(
            {
                "kind": "missing_or_ambiguous_record",
                "path": record["old_path"],
                "detail": "record is missing or ambiguous but had no explicit error",
            }
        )

    return _result(
        root=root_path,
        source_sha=source_sha,
        b1_rows=normalized_b1,
        b2_bundles=b2_bundles,
        product_paths=product_paths,
        legacy_paths=legacy_paths,
        subpackages=len(product_subpackages),
        records=records,
        legacy_records=legacy_records,
        errors=errors,
        dynamic_exceptions=dynamic_exceptions,
        inventory_entries=inventory_entries,
        provenance=provenance,
        paths=set(paths),
    )


def _failure_result(root: Path, message: str) -> dict[str, Any]:
    summary = _make_empty_summary()
    summary["missing_or_ambiguous"] = 1
    return {
        "schema_version": 1,
        "state": PENDING_STATE,
        "root": str(root),
        "source_sha": "",
        "summary": summary,
        "b1_rows": [],
        "b2_bundles": [],
        "records": [],
        "legacy_records": [],
        "dynamic_exceptions": [],
        "collisions": [],
        "config_docker_inventory": [],
        "historical_provenance": [],
        "errors": [{"kind": "inventory_failure", "detail": message}],
        "exit_code": 2,
    }


def _output_refusal(root: Path, output: Path) -> str | None:
    """Return a refusal reason when *output* is not a safe private artifact."""

    if output.is_symlink():
        return "output path is an existing symlink"
    try:
        resolved_root = root.resolve()
        resolved_output = output.resolve(strict=False)
    except OSError as exc:
        return f"output path cannot be resolved: {exc}"
    try:
        relative = resolved_output.relative_to(resolved_root).as_posix()
    except ValueError:
        relative = None
    if relative is not None:
        if relative == ".git" or relative.startswith(".git/"):
            return "output path targets git metadata"
        try:
            tracked = set(tracked_paths(resolved_root))
        except InventoryError:
            tracked = set()
        if relative in tracked:
            return "output path would overwrite a tracked input"
        tracked_casefold = {path.casefold(): path for path in tracked}
        case_match = tracked_casefold.get(relative.casefold())
        if case_match is not None and case_match != relative:
            return "output path would overwrite a tracked input (case-insensitive)"
        if _is_sensitive(relative):
            return "output path targets credentials or runtime data"
        pure = PurePosixPath(relative)
        if pure.parent == PROTECTED_DOC_DIR:
            explicit_codegen = (
                pure.suffix.lower() == ".json"
                and not resolved_output.exists()
            )
            if (
                relative in PROTECTED_DOC_PATHS
                or not explicit_codegen
            ):
                return "output path targets a protected docs/final authority file"
        if relative in PROTECTED_DOC_PATHS:
            return "output path targets a protected docs/final authority file"
        suffix = pure.suffix.lower()
        if suffix in UNSAFE_OUTPUT_SUFFIXES:
            return "output path targets source, configuration, or data"
        if pure.name.lower().startswith(
            (".env", "id_rsa", "secret", "credential")
        ):
            return "output path targets credentials"
    else:
        outside_pure = PurePosixPath(output.name)
        if _is_sensitive(str(output)):
            return "output path targets credentials or runtime data"
        if outside_pure.suffix.lower() in UNSAFE_OUTPUT_SUFFIXES:
            return "output path targets source, configuration, or data"
        if outside_pure.name.lower().startswith(
            (".env", "id_rsa", "secret", "credential")
        ):
            return "output path targets credentials"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only ownership inventory and codegen map."
    )
    parser.add_argument("--root", required=True, help="repository root")
    parser.add_argument("--output", required=True, help="JSON output file")
    args = parser.parse_args(argv)

    root = Path(args.root)
    try:
        result = run_inventory(root)
    except Exception as exc:  # Keep the CLI contract: bounded failure JSON.
        result = _failure_result(root, f"{type(exc).__name__}: {exc}")

    output = Path(args.output)
    refusal = _output_refusal(root, output)
    if refusal is not None:
        print(f"inventory output refused: {refusal}", file=sys.stderr)
        return 2

    try:
        if output.parent and str(output.parent) not in {"", "."}:
            output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"inventory output write failed: {exc}", file=sys.stderr)
        return 2

    summary = result["summary"]
    print(
        "wrote {output} state={state} missing_or_ambiguous={missing}".format(
            output=output,
            state=result["state"],
            missing=summary.get("missing_or_ambiguous", 0),
        )
    )
    return int(result.get("exit_code", 2))


if __name__ == "__main__":
    raise SystemExit(main())
