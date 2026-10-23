#!/usr/bin/env python3
"""TX00 current-source static reuse audit checker.

Standard-library only.  It never imports product modules and never rewrites
product source.  ``--write`` emits the four JSON evidence files next to this
script; ``--check`` recomputes the same objects and compares parsed JSON.

Scope and evidence are deliberately conservative:
* only the baseline's 47 audited paths are inventoried;
* function AST identity is Python 3.12 ``ast.dump(..., include_attributes=False)``;
* caller evidence is direct, import-qualified AST references only;
* dynamic receivers, ``self``/``cls`` dispatch and wildcard imports stay
  unresolved and are omitted from claimed direct calls.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

OUTPUT_NAMES = (
    "inventory.json",
    "dispositions.json",
    "callers.json",
    "delta.json",
)
BASELINE_REL = Path("docs/tickets/TX00-current-audit-baseline.json")
DISPOSITIONS_REL = Path("docs/refactoring/vnext/reuse_audit/function_dispositions.json")
INPUT_ROOTS = ("src/", "services/", "scripts/", "tests/")
EXPECTED_SCOPE_PATHS = 47
EXPECTED_HISTORICAL_FUNCTIONS = 692
AST_RECIPE = (
    "sha256(ast.dump(node, include_attributes=False).encode('utf-8'))"
)
RECOVERY_SEAM_PATHS = {
    "src/vibetrading/etf_journal.py",
    "src/vibetrading/etf_trading.py",
    "src/vibetrading/execution_owner.py",
    "src/vibetrading/kis_execution.py",
    "src/vibetrading/paper_execution.py",
    "src/vibetrading/portfolio_ledger.py",
    "src/vibetrading/strategy_runtime.py",
}
STATIC_SCOPE_NOTE = (
    "Static preservation/reuse evidence only; not a functional-completeness, "
    "runtime, testnet, live, or deployment certificate."
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def ast_sha256(node: ast.AST) -> str:
    return sha256_text(ast.dump(node, include_attributes=False))


def load_json(path: Path, label: str, errors: list[str]) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        errors.append(f"missing {label}: {path}")
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"malformed {label}: {path}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"malformed {label}: {path}: expected a JSON object")
        return {}
    return value


def safe_unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def decorator_text(node: ast.AST) -> str:
    return safe_unparse(node)


def argument_names(args: ast.arguments) -> set[str]:
    names = {
        item.arg
        for item in (
            list(args.posonlyargs)
            + list(args.args)
            + list(args.kwonlyargs)
            + ([args.vararg] if args.vararg is not None else [])
            + ([args.kwarg] if args.kwarg is not None else [])
        )
    }
    return names


def module_name_for_path(path: str) -> str:
    relative = path[4:] if path.startswith("src/") else path
    if relative.endswith(".py"):
        relative = relative[:-3]
    parts = [part for part in relative.split("/") if part]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def package_for_path(path: str) -> str:
    module = module_name_for_path(path)
    if path.endswith("/__init__.py"):
        return module
    if "." in module:
        return module.rsplit(".", 1)[0]
    return module


def relative_import_base(module: str | None, level: int, current_path: str) -> str | None:
    package = package_for_path(current_path)
    package_parts = package.split(".") if package else []
    drop = level - 1
    if drop < 0 or drop > len(package_parts):
        return None
    base_parts = package_parts[: len(package_parts) - drop] if drop else package_parts
    if module:
        base_parts = base_parts + module.split(".")
    value = ".".join(part for part in base_parts if part)
    return value or None


def import_bindings(node: ast.Import | ast.ImportFrom, current_path: str):
    """Yield ``(bound_name, fully_qualified_target)`` for one import node."""
    wildcard = False
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.asname:
                yield alias.asname, alias.name
            else:
                root = alias.name.split(".", 1)[0]
                yield root, root
        return
    if node.level:
        base = relative_import_base(node.module, node.level, current_path)
    else:
        base = node.module
    if not base:
        return
    for alias in node.names:
        if alias.name == "*":
            wildcard = True
            continue
        name = alias.asname or alias.name
        yield name, base + "." + alias.name
    if wildcard:
        # Wildcard imports cannot be resolved lexically; the caller records a
        # limitation and never guesses a target.
        return


def target_name(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, (ast.Tuple, ast.List)):
        return None
    if isinstance(node, ast.Starred):
        return target_name(node.value)
    return None


def collect_scope_bindings(
    body: list[ast.stmt], path: str, initial_assigned: set[str] | None = None
) -> tuple[dict[str, str | None], bool]:
    """Collect one lexical scope's imports and locally-bound names.

    Names imported in the scope are resolved to fully-qualified strings.
    Names that are also assigned/parameter names are recorded as ``None`` so a
    lookup cannot fall through to an outer binding (shadowed -> unresolved).
    Nested function/class bodies are separate scopes and are not traversed.
    """
    imported: list[tuple[str, str | None]] = []
    assigned: set[str] = set(initial_assigned or ())
    wildcard = False

    def add_targets(value: ast.AST | None) -> None:
        name = target_name(value)
        if name:
            assigned.add(name)

    def walk(node: ast.AST) -> None:
        nonlocal wildcard
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            assigned.add(node.name)
            return
        if isinstance(node, ast.Lambda):
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for bound, target in import_bindings(node, path):
                imported.append((bound, target))
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in node.names
            ):
                wildcard = True
            return
        if isinstance(node, ast.Assign):
            for item in node.targets:
                add_targets(item)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            add_targets(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            add_targets(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    add_targets(item.optional_vars)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                assigned.add(node.name)
        elif isinstance(node, ast.comprehension):
            add_targets(node.target)
        elif isinstance(node, ast.Delete):
            for item in node.targets:
                add_targets(item)
        for child in ast.iter_child_nodes(node):
            walk(child)

    for statement in body:
        walk(statement)

    # A name imported from two different modules (for example in alternate
    # branches) is ambiguous; never let the last AST-walk binding win.
    imported_targets: dict[str, set[str | None]] = defaultdict(set)
    for name, target in imported:
        imported_targets[name].add(target)

    bindings: dict[str, str | None] = {}
    for name, targets in imported_targets.items():
        if name in assigned or len(targets) != 1:
            bindings[name] = None
        else:
            bindings[name] = next(iter(targets))
    for name in assigned:
        bindings.setdefault(name, None)
    return bindings, wildcard


class Scope:
    def __init__(
        self,
        kind: str,
        qualname: str,
        parent: "Scope | None",
        bindings: dict[str, str | None],
        wildcard: bool = False,
    ):
        self.kind = kind
        self.qualname = qualname
        self.parent = parent
        self.bindings = bindings
        self.wildcard = wildcard

    def lookup(self, name: str) -> tuple[bool, str | None]:
        scope: Scope | None = self
        while scope is not None:
            # A wildcard import in this lexical scope (or an ancestor) can
            # shadow a name that is visible from elsewhere.  Stay unresolved
            # instead of claiming a definite import-qualified target.
            if scope.wildcard:
                return True, None
            if name in scope.bindings:
                return True, scope.bindings[name]
            scope = scope.parent
        return False, None


class CallCollector(ast.NodeVisitor):
    """Collect direct import-qualified calls, without dynamic inference."""

    def __init__(self, path: str, tree: ast.Module, audited_modules: set[str]):
        self.path = path
        self.module = module_name_for_path(path)
        self.audited_modules = audited_modules
        self.calls: list[dict] = []
        self.unresolved_unknown_name = 0
        self.unresolved_shadowed = 0
        self.unresolved_receiver = 0
        self.wildcard_scopes = 0
        bindings, wildcard = collect_scope_bindings(tree.body, path)
        self.scope = Scope("module", "module", None, bindings, wildcard)
        if wildcard:
            self.wildcard_scopes += 1

    def caller_symbol(self) -> str:
        if self.scope.kind == "module":
            return "module.<module>"
        if self.scope.kind == "class":
            return self.scope.qualname + ".<classbody>"
        return self.scope.qualname

    def _resolve_expr(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            found, value = self.scope.lookup(node.id)
            return value if found else None
        if isinstance(node, ast.Attribute):
            base = self._resolve_expr(node.value)
            return base + "." + node.attr if base else None
        if isinstance(node, ast.Call):
            # The call's returned object has UNKNOWN type.  Never infer a
            # method from a factory/constructor spelling such as
            # ``broker_for_name('toss').submit_order``.  Generic visiting still
            # records the inner direct factory call exactly once.
            return None
        return None

    @staticmethod
    def _root_name(node: ast.AST | None) -> str | None:
        while isinstance(node, ast.Attribute):
            node = node.value
        if isinstance(node, ast.Name):
            return node.id
        return None

    def _record_unresolved(self, func: ast.AST) -> None:
        root = self._root_name(func)
        if root is None:
            self.unresolved_receiver += 1
            return
        found, value = self.scope.lookup(root)
        if found and value is None:
            self.unresolved_shadowed += 1
        else:
            self.unresolved_unknown_name += 1

    def visit_Call(self, node: ast.Call) -> None:
        target = self._resolve_expr(node.func)
        if target is None:
            self._record_unresolved(node.func)
        elif any(
            target == module or target.startswith(module + ".")
            for module in self.audited_modules
        ):
            self.calls.append(
                {
                    "caller": self.path,
                    "caller_symbol": self.caller_symbol(),
                    "line": node.lineno,
                    "target": target,
                }
            )
        self.generic_visit(node)

    def _visit_arguments_in_outer_scope(self, args: ast.arguments) -> None:
        for arg in (
            list(args.posonlyargs)
            + list(args.args)
            + list(args.kwonlyargs)
            + ([args.vararg] if args.vararg is not None else [])
            + ([args.kwarg] if args.kwarg is not None else [])
        ):
            if arg.annotation is not None:
                self.visit(arg.annotation)
        for default in list(args.defaults) + list(args.kw_defaults):
            if default is not None:
                self.visit(default)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments_in_outer_scope(node.args)
        if node.returns is not None:
            self.visit(node.returns)

        if self.scope.kind == "class":
            parent = self.scope.parent
            qualname = self.scope.qualname + "." + node.name
        else:
            parent = self.scope
            qualname = self.scope.qualname + "." + node.name
        bindings, wildcard = collect_scope_bindings(
            node.body, self.path, argument_names(node.args)
        )
        child = Scope("function", qualname, parent, bindings, wildcard)
        if wildcard:
            self.wildcard_scopes += 1
        previous = self.scope
        self.scope = child
        for statement in node.body:
            self.visit(statement)
        self.scope = previous

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        if self.scope.kind == "module":
            qualname = node.name
        else:
            qualname = self.scope.qualname + "." + node.name
        bindings, wildcard = collect_scope_bindings(node.body, self.path)
        child = Scope("class", qualname, self.scope, bindings, wildcard)
        if wildcard:
            self.wildcard_scopes += 1
        previous = self.scope
        self.scope = child
        for statement in node.body:
            self.visit(statement)
        self.scope = previous

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments_in_outer_scope(node.args)
        bindings = {name: None for name in argument_names(node.args)}
        child = Scope(
            "function", self.scope.qualname + ".<lambda>", self.scope, bindings
        )
        previous = self.scope
        self.scope = child
        self.visit(node.body)
        self.scope = previous

    def visit_Import(self, node: ast.Import) -> None:
        return

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        return


def current_function_rows(
    root: Path, scope_paths: list[str]
) -> tuple[list[dict], dict[str, list[str]], dict[str, str], list[dict], list[str]]:
    rows: list[dict] = []
    classes: dict[str, list[str]] = defaultdict(list)
    raw_hashes: dict[str, str] = {}
    trees: dict[str, ast.Module] = {}
    errors: list[str] = []

    def make_row(node: ast.FunctionDef | ast.AsyncFunctionDef, symbol: str) -> dict:
        return {
            "path": path,
            "symbol": symbol,
            "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "async": isinstance(node, ast.AsyncFunctionDef),
            "signature": safe_unparse(node.args),
            "returns": safe_unparse(node.returns),
            "decorators": [decorator_text(item) for item in node.decorator_list],
            "ast_sha256": ast_sha256(node),
        }

    for path in scope_paths:
        source_path = root / path
        try:
            raw = source_path.read_bytes()
        except FileNotFoundError:
            errors.append(f"missing source file: {path}")
            continue
        except OSError as exc:
            errors.append(f"unreadable source file: {path}: {exc}")
            continue
        raw_hashes[path] = sha256_bytes(raw)
        try:
            tree = ast.parse(raw.decode("utf-8"), filename=path)
        except (UnicodeDecodeError, SyntaxError) as exc:
            errors.append(f"malformed source file: {path}: {exc}")
            continue
        trees[path] = tree
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rows.append(make_row(node, node.name))
            elif isinstance(node, ast.ClassDef):
                classes[path].append(node.name)
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        rows.append(make_row(item, node.name + "." + item.name))

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["path"], row["symbol"])].append(row)
    for (path, symbol), items in groups.items():
        items.sort(key=lambda item: (item["line"], item["end_line"], item["symbol"]))
        for ordinal, item in enumerate(items, 1):
            item["definition_id"] = f"{symbol}@{ordinal}"
        duplicate = len(items) > 1
        for item in items:
            item["duplicate_symbol"] = duplicate

    duplicate_ids = [
        key for key, count in Counter(
            (item["path"], item["definition_id"]) for item in rows
        ).items() if count != 1
    ]
    for path, definition_id in duplicate_ids:
        errors.append(f"duplicate current definition_id: {path}:{definition_id}")

    rows.sort(key=lambda item: (item["path"], item["line"], item["end_line"], item["symbol"]))
    return rows, classes, raw_hashes, trees, errors


def validate_baseline(baseline: dict, errors: list[str]) -> None:
    for key in (
        "audit_source_sha",
        "historical_source_sha",
        "scope_paths",
        "historical_functions",
        "historical_files_sha256",
    ):
        if key not in baseline:
            errors.append(f"baseline missing key: {key}")
    scope_paths = baseline.get("scope_paths", [])
    if not isinstance(scope_paths, list) or len(scope_paths) != EXPECTED_SCOPE_PATHS:
        errors.append(
            f"baseline scope_paths must contain {EXPECTED_SCOPE_PATHS} paths"
        )
    if len(set(scope_paths)) != len(scope_paths):
        errors.append("baseline scope_paths contains duplicates")
    for path in scope_paths:
        if not isinstance(path, str) or not path.endswith(".py"):
            errors.append(f"baseline scope path is not a relative .py path: {path!r}")
            continue
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts or path.startswith("/"):
            errors.append(f"baseline scope path is not safely relative: {path!r}")
    historical = baseline.get("historical_functions", [])
    if not isinstance(historical, list) or len(historical) != EXPECTED_HISTORICAL_FUNCTIONS:
        errors.append(
            "baseline historical_functions must contain "
            f"{EXPECTED_HISTORICAL_FUNCTIONS} rows"
        )
    seen: set[tuple[str, str]] = set()
    for item in historical if isinstance(historical, list) else []:
        if not isinstance(item, dict):
            errors.append("baseline historical_functions row is not an object")
            continue
        for key in ("path", "symbol", "ast_sha256", "line", "end_line", "definition_id"):
            if key not in item:
                errors.append(f"baseline historical function missing {key}: {item!r}")
        key = (item.get("path"), item.get("definition_id"))
        if key in seen:
            errors.append(f"baseline duplicate historical definition_id: {key}")
        seen.add(key)
        expected_id = f"{item.get('symbol')}@1"
        if item.get("duplicate_symbol") is False and item.get("definition_id") != expected_id:
            errors.append(
                f"baseline definition_id does not match non-duplicate symbol: {key}"
            )
    serialization = baseline.get("ast_serialization", {})
    if not isinstance(serialization, dict):
        errors.append("baseline ast_serialization is not an object")
        return
    if serialization.get("python_version") != platform.python_version():
        # The ticket fixes 3.12.3 for both sides.  Record the mismatch as an
        # error rather than silently producing incomparable hashes.
        errors.append(
            "baseline AST Python version mismatch: baseline="
            f"{serialization.get('python_version')} runtime={platform.python_version()}"
        )
    example_dump = serialization.get("example_dump")
    example_sha = serialization.get("example_sha256")
    if not isinstance(example_dump, str) or not isinstance(example_sha, str):
        errors.append("baseline AST reproducibility example is missing")
    elif sha256_text(example_dump) != example_sha:
        errors.append("baseline AST reproducibility example hash does not match recipe")


def disposition_index(dispositions: dict, errors: list[str]) -> tuple[dict, dict]:
    by_line: dict[tuple[str, str, int], dict] = {}
    by_symbol: dict[tuple[str, str], list[dict]] = defaultdict(list)
    rows = dispositions.get("functions")
    if not isinstance(rows, list):
        errors.append("historical function_dispositions.json has no functions array")
        return by_line, by_symbol
    for item in rows:
        if not isinstance(item, dict):
            errors.append("historical disposition row is not an object")
            continue
        path = item.get("path")
        symbol = item.get("symbol")
        line = item.get("line")
        key = (path, symbol)
        by_symbol[key].append(item)
        if isinstance(line, int):
            by_line[(path, symbol, line)] = item
    return by_line, by_symbol


def lookup_disposition(
    historical: dict,
    by_line: dict,
    by_symbol: dict,
) -> tuple[dict | None, str | None]:
    path = historical.get("path")
    symbol = historical.get("symbol")
    line = historical.get("line")
    exact = by_line.get((path, symbol, line))
    if exact is not None:
        return exact, None
    candidates = by_symbol.get((path, symbol), [])
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, f"missing historical disposition for {path}:{symbol}:{line}"
    return (
        None,
        "ambiguous historical disposition for duplicated symbol without line match: "
        f"{path}:{symbol}:{line}",
    )


def build(root: Path) -> tuple[dict[str, dict], dict]:
    errors: list[str] = []
    baseline = load_json(root / BASELINE_REL, "baseline", errors)
    dispositions = load_json(root / DISPOSITIONS_REL, "historical dispositions", errors)
    if errors:
        return {}, {"errors": errors}
    validate_baseline(baseline, errors)
    if errors:
        return {}, {"errors": errors}

    scope_paths = list(baseline["scope_paths"])
    historical_functions = list(baseline["historical_functions"])
    historical_files = baseline["historical_files_sha256"]
    for path in scope_paths:
        if path not in historical_files:
            errors.append(f"baseline missing historical raw hash for: {path}")

    current, classes, raw_hashes, trees, current_errors = current_function_rows(
        root, scope_paths
    )
    errors.extend(current_errors)
    if errors:
        return {}, {"errors": errors}

    source_set_sha = sha256_text(
        "\n".join(f"{path}:{raw_hashes[path]}" for path in sorted(scope_paths)) + "\n"
    )

    historical_by_id = {
        (item["path"], item["definition_id"]): item for item in historical_functions
    }
    by_line, by_symbol = disposition_index(dispositions, errors)
    if errors:
        return {}, {"errors": errors}

    current_ids = {(item["path"], item["definition_id"]) for item in current}
    removed_historical = [
        item
        for item in historical_functions
        if (item["path"], item["definition_id"]) not in current_ids
    ]

    disposition_rows: list[dict] = []
    changed_functions: list[dict] = []
    added_functions: list[dict] = []
    retained_unchanged = 0
    retained_changed = 0

    for item in current:
        key = (item["path"], item["definition_id"])
        historical = historical_by_id.get(key)
        if historical is None:
            recovery = item["path"] in RECOVERY_SEAM_PATHS
            classification = "REUSE" if recovery else "UNKNOWN"
            if recovery:
                scope_note = (
                    "Accepted VN07/VN08 ETF/Paper recovery context; helper-only "
                    "code addition (code-only evidence, not new runtime/platform "
                    "functionality)."
                )
                reason = (
                    "New helper definition in an accepted VN07/VN08 ETF/Paper "
                    "recovery seam; no historical AST. Code-only static evidence, "
                    "not runtime/live proof."
                )
            else:
                scope_note = (
                    "New definition outside accepted recovery seams; UNKNOWN "
                    "classification pending review."
                )
                reason = (
                    "No historical disposition and not within an accepted "
                    "recovery seam; visible addition, not silently accepted."
                )
            row = {
                "path": item["path"],
                "symbol": item["symbol"],
                "definition_id": item["definition_id"],
                "line": item["line"],
                "end_line": item["end_line"],
                "classification": classification,
                "classification_scope": scope_note,
                "reason": reason,
                "historical_status": "added",
                "historical_ast_sha256": None,
                "current_ast_sha256": item["ast_sha256"],
                "changed_since_historical": False,
                "accepted_context": (
                    "VN07/VN08 ETF/Paper recovery seam; helper-only"
                    if recovery
                    else None
                ),
                "code_only": True,
                "duplicate_symbol": item["duplicate_symbol"],
            }
            disposition_rows.append(row)
            added_functions.append(
                {
                    "path": item["path"],
                    "symbol": item["symbol"],
                    "definition_id": item["definition_id"],
                    "line": item["line"],
                    "end_line": item["end_line"],
                    "classification": classification,
                    "accepted_context": row["accepted_context"],
                    "code_only": True,
                    "current_ast_sha256": item["ast_sha256"],
                    "reason": reason,
                }
            )
            continue

        disposition, disposition_error = lookup_disposition(historical, by_line, by_symbol)
        if disposition_error is not None:
            errors.append(disposition_error)
            continue
        classification = str(disposition.get("classification", "UNKNOWN"))
        changed = item["ast_sha256"] != historical["ast_sha256"]
        if changed:
            retained_changed += 1
            reason = (
                "CHANGED since historical audit (AST SHA differs); historical "
                "preservation disposition retained; no blanket AST-preservation "
                "claim. Static evidence only."
            )
            historical_status = "retained_changed"
            changed_functions.append(
                {
                    "path": item["path"],
                    "symbol": item["symbol"],
                    "definition_id": item["definition_id"],
                    "line": item["line"],
                    "end_line": item["end_line"],
                    "classification": classification,
                    "historical_ast_sha256": historical["ast_sha256"],
                    "current_ast_sha256": item["ast_sha256"],
                    "reason": reason,
                }
            )
        else:
            retained_unchanged += 1
            reason = (
                "Historical definition AST unchanged; preservation disposition "
                "retained. Static evidence only."
            )
            historical_status = "retained_unchanged"
        row = {
            "path": item["path"],
            "symbol": item["symbol"],
            "definition_id": item["definition_id"],
            "line": item["line"],
            "end_line": item["end_line"],
            "classification": classification,
            "classification_scope": disposition.get(
                "classification_scope", STATIC_SCOPE_NOTE
            ),
            "reason": reason,
            "historical_status": historical_status,
            "historical_ast_sha256": historical["ast_sha256"],
            "current_ast_sha256": item["ast_sha256"],
            "changed_since_historical": changed,
            "accepted_context": None,
            "code_only": False,
            "duplicate_symbol": item["duplicate_symbol"],
        }
        disposition_rows.append(row)

    removed_functions: list[dict] = []
    for historical in removed_historical:
        disposition, disposition_error = lookup_disposition(historical, by_line, by_symbol)
        if disposition_error is not None:
            errors.append(disposition_error)
            continue
        removed_functions.append(
            {
                "path": historical["path"],
                "symbol": historical["symbol"],
                "definition_id": historical["definition_id"],
                "line": historical["line"],
                "end_line": historical["end_line"],
                "historical_ast_sha256": historical["ast_sha256"],
                "historical_classification": disposition.get(
                    "classification", "UNKNOWN"
                ),
                "reason": (
                    "Historical definition is absent from current source; visible "
                    "removal only, with no inferred replacement, retirement, or "
                    "deletion approval."
                ),
            }
        )

    if errors:
        return {}, {"errors": errors}

    disposition_rows.sort(
        key=lambda item: (item["path"], item["line"], item["end_line"], item["symbol"])
    )
    changed_functions.sort(
        key=lambda item: (item["path"], item["line"], item["definition_id"])
    )
    added_functions.sort(
        key=lambda item: (item["path"], item["line"], item["definition_id"])
    )
    removed_functions.sort(
        key=lambda item: (item["path"], item["line"], item["definition_id"])
    )

    example = baseline.get("ast_serialization", {})
    inventory = {
        "schema": "tx00-current-inventory-v1",
        "audit_source_sha": baseline["audit_source_sha"],
        "historical_source_sha": baseline["historical_source_sha"],
        "python_version": platform.python_version(),
        "ast_recipe": AST_RECIPE,
        "ast_recipe_example": {
            "path": example.get("example_path"),
            "line": example.get("example_line"),
            "sha256": example.get("example_sha256"),
            "recipe_self_check": (
                isinstance(example.get("example_dump"), str)
                and sha256_text(example["example_dump"])
                == example.get("example_sha256")
            ),
        },
        "scope_paths": scope_paths,
        "scope_path_count": len(scope_paths),
        "source_set_recipe": (
            "sha256 of sorted 'path:raw_sha256' lines joined with LF plus a final LF"
        ),
        "source_set_sha256": source_set_sha,
        "source_files": {
            path: {
                "sha256": raw_hashes[path],
                "historical_sha256": historical_files.get(path),
                "raw_matches_historical": raw_hashes[path]
                == historical_files.get(path),
            }
            for path in sorted(scope_paths)
        },
        "functions": current,
        "counts": {
            "files": len(scope_paths),
            "functions": len(current),
            "historical_functions": len(historical_functions),
            "duplicate_symbols": sum(1 for item in current if item["duplicate_symbol"]),
            "duplicate_definition_ids": 0,
        },
    }

    classifications = Counter(item["classification"] for item in disposition_rows)
    statuses = Counter(item["historical_status"] for item in disposition_rows)
    disposition_output = {
        "schema": "tx00-current-dispositions-v1",
        "audit_source_sha": baseline["audit_source_sha"],
        "historical_source_sha": baseline["historical_source_sha"],
        "classification_scope": STATIC_SCOPE_NOTE,
        "functions": disposition_rows,
        "counts": {
            "functions": len(disposition_rows),
            "by_classification": dict(sorted(classifications.items())),
            "by_historical_status": dict(sorted(statuses.items())),
            "retained_unchanged": retained_unchanged,
            "retained_changed": retained_changed,
            "added": len(added_functions),
            "removed": len(removed_functions),
        },
    }

    # Direct caller evidence is independent of the disposition table but uses
    # the same audited modules and current class/function identities.
    audited_modules = {module_name_for_path(path) for path in scope_paths}
    function_targets: dict[str, list[str]] = defaultdict(list)
    for item in current:
        target = module_name_for_path(item["path"]) + "." + item["symbol"]
        function_targets[target].append(item["definition_id"])
    class_targets = {
        module_name_for_path(path) + "." + name
        for path, names in classes.items()
        for name in names
    }

    candidate_source = "git ls-files -- '*.py'"
    candidate_paths: list[str] = []
    try:
        git = subprocess.run(
            ["git", "ls-files", "--", "*.py"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        errors.append(f"git ls-files failed: {exc}")
        return {}, {"errors": errors}
    if git.returncode != 0:
        diagnostic = (git.stderr or "").strip() or "no diagnostic"
        errors.append(
            f"git ls-files failed with exit {git.returncode}: {diagnostic}"
        )
        return {}, {"errors": errors}
    candidate_paths = [
        line.strip() for line in (git.stdout or "").splitlines() if line.strip()
    ]
    candidate_paths = sorted(
        {
            path
            for path in candidate_paths
            if path.startswith(INPUT_ROOTS) and path.endswith(".py")
        }
    )

    candidate_inputs: list[dict] = []
    direct_calls: list[dict] = []
    unresolved_unknown = 0
    unresolved_shadowed = 0
    unresolved_receiver = 0
    wildcard_scopes = 0
    parse_errors: list[dict] = []
    for path in candidate_paths:
        source_path = root / path
        try:
            raw = source_path.read_bytes()
        except OSError as exc:
            parse_errors.append({"path": path, "error": str(exc)})
            continue
        candidate_inputs.append(
            {
                "path": path,
                "sha256": sha256_bytes(raw),
                "module": module_name_for_path(path),
            }
        )
        try:
            tree = ast.parse(raw.decode("utf-8"), filename=path)
        except (UnicodeDecodeError, SyntaxError) as exc:
            parse_errors.append({"path": path, "error": str(exc)})
            continue
        collector = CallCollector(path, tree, audited_modules)
        for statement in tree.body:
            collector.visit(statement)
        unresolved_unknown += collector.unresolved_unknown_name
        unresolved_shadowed += collector.unresolved_shadowed
        unresolved_receiver += collector.unresolved_receiver
        wildcard_scopes += collector.wildcard_scopes
        for call in collector.calls:
            target = call["target"]
            if target in class_targets:
                kind = "class_constructor_or_class_reference"
                evidence = (
                    "DIRECT_AST_CALL; class-identity reference only, not proof "
                    "__init__ or any method executes; not runtime/execution proof"
                )
            elif target in function_targets:
                kind = "module_function_or_method_reference"
                evidence = (
                    "DIRECT_AST_CALL; import-qualified lexical reference; not "
                    "runtime/execution proof"
                )
            else:
                kind = "attribute_reference_into_audited_module"
                evidence = (
                    "DIRECT_AST_CALL; import-qualified attribute reference; not "
                    "runtime/execution proof"
                )
            direct_calls.append(
                {
                    "caller": call["caller"],
                    "caller_symbol": call["caller_symbol"],
                    "line": call["line"],
                    "target": target,
                    "target_kind": kind,
                    "target_definition_ids": function_targets.get(target, []),
                    "evidence": evidence,
                }
            )

    if parse_errors:
        for item in parse_errors:
            errors.append(
                f"unreadable/unparseable tracked candidate: {item['path']}: "
                f"{item['error']}"
            )
        return {}, {"errors": errors}

    direct_calls.sort(
        key=lambda item: (
            item["caller"],
            item["line"],
            item["target"],
            item["caller_symbol"],
        )
    )
    target_modules = Counter(
        max(
            (
                module
                for module in audited_modules
                if item["target"] == module
                or item["target"].startswith(module + ".")
            ),
            key=len,
        )
        for item in direct_calls
    )
    callers_output = {
        "schema": "tx00-current-callers-v1",
        "audit_source_sha": baseline["audit_source_sha"],
        "historical_source_sha": baseline["historical_source_sha"],
        "method": (
            "Direct AST import-qualified Name/Attribute call references only; "
            "lexical scope and relative imports resolved; no dynamic receiver "
            "inference and no runtime/execution proof."
        ),
        "candidate_input_source": candidate_source,
        "candidate_input_scope": list(INPUT_ROOTS),
        "candidate_inputs": candidate_inputs,
        "candidate_input_count": len(candidate_inputs),
        "audited_modules": sorted(audited_modules),
        "direct_calls": direct_calls,
        "counts": {
            "direct_calls": len(direct_calls),
            "by_target_module": dict(sorted(target_modules.items())),
            "candidate_inputs": len(candidate_inputs),
            "unresolved_calls_omitted": (
                unresolved_unknown + unresolved_shadowed + unresolved_receiver
            ),
            "unresolved_unknown_or_builtin_name": unresolved_unknown,
            "unresolved_shadowed_or_reassigned": unresolved_shadowed,
            "unresolved_unknown_receiver": unresolved_receiver,
            "wildcard_import_scopes": wildcard_scopes,
            "parse_errors": len(parse_errors),
        },
        "parse_errors": parse_errors,
        "limitations": [
            "Direct AST import-qualified references only; no reflection, getattr, dynamic dispatch, duck-typed receiver inference, or runtime tracing.",
            "self/cls/unknown/local receiver calls are conservatively omitted and never resolved to venue functions.",
            "Class constructor references identify class identity only; they are not proof that __init__ or any method executes.",
            "Static lexical evidence is not testnet/live/deployment proof, and caller counts are not execution counts.",
            "Only tracked .py inputs under src/, services/, scripts/, and tests/ are candidate inputs; untracked files, data, credentials, .env, and runtime stores are excluded.",
            "Wildcard imports and shadowed/reassigned names stay unresolved instead of being guessed.",
        ],
    }

    current_raw_changed = sorted(
        path
        for path in scope_paths
        if raw_hashes[path] != historical_files.get(path)
    )
    changed_by_path: dict[str, dict] = {
        path: {"changed": [], "added": [], "removed": []}
        for path in current_raw_changed
    }
    for item in changed_functions:
        changed_by_path.setdefault(
            item["path"], {"changed": [], "added": [], "removed": []}
        )["changed"].append(item["definition_id"])
    for item in added_functions:
        changed_by_path.setdefault(
            item["path"], {"changed": [], "added": [], "removed": []}
        )["added"].append(item["definition_id"])
    for item in removed_functions:
        changed_by_path.setdefault(
            item["path"], {"changed": [], "added": [], "removed": []}
        )["removed"].append(item["definition_id"])

    changed_files = []
    for path in sorted(changed_by_path):
        entry = changed_by_path[path]
        changed_files.append(
            {
                "path": path,
                "historical_sha256": historical_files.get(path),
                "current_sha256": raw_hashes[path],
                "raw_source_changed": raw_hashes[path] != historical_files.get(path),
                "changed_functions": entry["changed"],
                "added_functions": entry["added"],
                "removed_functions": entry["removed"],
            }
        )

    unchanged_paths = sorted(
        path
        for path in scope_paths
        if path not in {item["path"] for item in changed_files}
    )
    recovery_changes = sorted(
        {
            item["definition_id"]
            for item in added_functions
            if item["path"] in RECOVERY_SEAM_PATHS
        }
    )
    delta_output = {
        "schema": "tx00-current-delta-v1",
        "audit_source_sha": baseline["audit_source_sha"],
        "historical_source_sha": baseline["historical_source_sha"],
        "comparison_key": "(path, definition_id)",
        "changed_files": changed_files,
        "changed_functions": changed_functions,
        "added_functions": added_functions,
        "removed_functions": removed_functions,
        "unchanged_paths": unchanged_paths,
        "recovery_context": {
            "seams": sorted(RECOVERY_SEAM_PATHS),
            "accepted_context": "VN07/VN08 ETF/Paper recovery",
            "added_definition_ids": recovery_changes,
            "note": (
                "New helper-only definitions in these seams are linked to the "
                "accepted VN07/VN08 recovery context; they are code-only evidence "
                "and not a claim of new runtime/platform functionality."
            ),
        },
        "exchange_wrapper_note": (
            "Exchange/venue wrapper modules with no listed changed/added/removed "
            "definitions are unchanged static references; current line numbers for "
            "unchanged files match the frozen historical source. Historical raw "
            "reports and matrix line references remain historical."
        ),
        "counts": {
            "changed_source_files": len(changed_files),
            "changed_functions": len(changed_functions),
            "added_functions": len(added_functions),
            "removed_functions": len(removed_functions),
            "unchanged_paths": len(unchanged_paths),
            "current_functions": len(current),
            "historical_functions": len(historical_functions),
        },
        "notes": [
            "Current line/end_line values come from the current AST; matrix and raw-report lines remain historical references.",
            "Removed definitions are visible here and are not silently dropped; this file grants no replacement, retirement, or deletion approval.",
            "Retained changed definitions keep their historical preservation disposition but make no blanket AST-preservation claim.",
            "Historical JSON reports, the matrix, and baseline remain read-only evidence.",
        ],
    }

    outputs = {
        "inventory.json": inventory,
        "dispositions.json": disposition_output,
        "callers.json": callers_output,
        "delta.json": delta_output,
    }
    summary = {
        "errors": errors,
        "files": len(scope_paths),
        "functions": len(current),
        "historical_functions": len(historical_functions),
        "retained_unchanged": retained_unchanged,
        "retained_changed": retained_changed,
        "added": len(added_functions),
        "removed": len(removed_functions),
        "direct_calls": len(direct_calls),
        "candidate_inputs": len(candidate_inputs),
        "changed_source_files": len(changed_files),
    }
    return outputs, summary


def render_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_json(path: Path, value: dict) -> None:
    """Write one declared evidence sibling; any write failure is fatal."""
    text = render_json(value)
    if not text.strip():
        raise OSError(f"refusing to write empty evidence file: {path}")
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def read_expected(path: Path) -> object:
    """Read one intended evidence sibling; there is no mirror fallback."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def print_summary(prefix: str, summary: dict) -> None:
    print(
        f"{prefix}: files={summary['files']} functions={summary['functions']} "
        f"historical={summary['historical_functions']} "
        f"retained_unchanged={summary['retained_unchanged']} "
        f"retained_changed={summary['retained_changed']} "
        f"added={summary['added']} removed={summary['removed']} "
        f"direct_calls={summary['direct_calls']} "
        f"candidate_inputs={summary['candidate_inputs']} "
        f"changed_source_files={summary['changed_source_files']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="repository root to audit")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true", help="write evidence JSON")
    action.add_argument("--check", action="store_true", help="verify existing evidence JSON")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    outputs, summary = build(root)
    errors = summary.get("errors", [])
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    output_dir = Path(__file__).resolve().parent
    if args.write:
        try:
            for name in OUTPUT_NAMES:
                write_json(output_dir / name, outputs[name])
        except OSError as exc:
            print(f"ERROR: cannot write evidence output: {exc}", file=sys.stderr)
            return 1
        print_summary("TX00 current audit write OK", summary)
        return 0

    mismatches: list[str] = []
    for name in OUTPUT_NAMES:
        path = output_dir / name
        try:
            expected = read_expected(path)
        except FileNotFoundError:
            mismatches.append(f"missing {name}")
            continue
        except (OSError, json.JSONDecodeError) as exc:
            mismatches.append(f"malformed {name}: {exc}")
            continue
        if expected != outputs[name]:
            mismatches.append(f"content mismatch: {name}")
    if mismatches:
        for mismatch in mismatches:
            print(f"ERROR: {mismatch}", file=sys.stderr)
        return 1
    print_summary("TX00 current audit check OK", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
