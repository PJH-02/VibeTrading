#!/usr/bin/env python3
"""Small deterministic move planner/applier for the flat-domain migration."""
import argparse, ast, base64, hashlib, json, os, re, subprocess, sys
from pathlib import Path, PurePosixPath
class MoveError(Exception): pass
class UnsupportedSyntax(MoveError): pass
INVENTORY_ROOTS = {"src", "tests", "scripts", "services", "shared", "strategies", "strategy", "backtest"}
def _sha(data): return hashlib.sha256(data).hexdigest()
def _b64(data): return base64.b64encode(data).decode("ascii")
def _git(root, *args, check=True):
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=False,
                          env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    if check and proc.returncode:
        raise MoveError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc
def _head(root): return _git(root, "rev-parse", "HEAD").stdout.strip()
def _require_clean(root):
    if _git(root, "status", "--porcelain", "--untracked-files=all").stdout.strip():
        raise MoveError("git worktree must be clean before planning or applying")
def _tracked_files(root):
    return [p for p in _git(root, "ls-files", "-z").stdout.split("\0") if p]
def _is_skipped(path):
    parts = [p.lower() for p in PurePosixPath(path).parts]
    if any(p in {".env", "runtime_data"} for p in parts) or (parts and parts[0] == "docs"):
        return True
    return any("deploy" in p or "rollback" in p for p in parts)
def _inventory(tracked):
    result = []
    for path in tracked:
        if path.endswith(".py") and not _is_skipped(path):
            parts = path.split("/")
            if len(parts) == 1 or parts[0] in INVENTORY_ROOTS:
                result.append(path)
    return sorted(result)
def _normalize_owners(owners):
    if isinstance(owners, str):
        owners = [owners]
    return {p for item in owners for p in re.split(r"[\s,]+", str(item)) if p}
def _safe_rel(path):
    if not isinstance(path, str) or not path or "\\" in path:
        raise MoveError(f"unsafe path: {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(p in {"", ".", ".."} for p in pure.parts):
        raise MoveError(f"unsafe path: {path!r}")
    if any(p in {".env", "runtime_data"} for p in pure.parts):
        raise MoveError(f"protected path: {path!r}")
    return pure.as_posix()
def _module_from_path(path):
    p = path[4:] if path.startswith("src/") else path
    if p.endswith(".py"):
        p = p[:-3]
    parts = p.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)
def _package_from_path(path):
    module = _module_from_path(path)
    if path.endswith("__init__.py"):
        return module
    return module.rsplit(".", 1)[0] if "." in module else ""
def _map_module(module, moved):
    if not module:
        return None
    if module in moved:
        return moved[module]
    for old in sorted(moved, key=len, reverse=True):
        if module.startswith(old + "."):
            return moved[old] + module[len(old):]
    return None
def _decode_source(raw, path):
    bom, body = (raw[:3], raw[3:]) if raw.startswith(b"\xef\xbb\xbf") else (b"", raw)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MoveError(f"{path}: unsupported encoding (UTF-8 required)") from exc
    for line in text.splitlines()[:2]:
        match = re.search(r"coding[:=]\s*([-\w.]+)", line)
        if match and match.group(1).lower().replace("_", "-") not in {"utf-8", "utf8"}:
            raise MoveError(f"{path}: unsupported source encoding")
    starts, offset = [], len(bom)
    for line in text.splitlines(keepends=True):
        starts.append(offset)
        offset += len(line.encode("utf-8"))
    return text, starts
def _span(node, starts, size):
    start = starts[node.lineno - 1] + node.col_offset
    end = starts[node.end_lineno - 1] + node.end_col_offset
    return start, min(end, size)
def _resolve_from_base(node, package):
    if node.level == 0:
        return node.module or ""
    parts = package.split(".") if package else []
    if not parts or node.level - 1 > len(parts):
        raise UnsupportedSyntax("relative import escapes the source root")
    base = parts[:len(parts) - (node.level - 1)]
    if node.module:
        base = base + node.module.split(".")
    return ".".join(base)
def _relative_import(package, target):
    current = package.split(".") if package else []
    wanted = target.split(".") if target else []
    common = 0
    while common < len(current) and common < len(wanted) and current[common] == wanted[common]:
        common += 1
    if common == 0:
        return None
    rest = wanted[common:]
    return len(current) - common + 1, ".".join(rest) if rest else None
def _alias_text(alias):
    return alias.name + (f" as {alias.asname}" if alias.asname else "")
def _render_from(level, module, names):
    return f"from {'.' * level + (module or '')} import {', '.join(names)}"
def _references_moved(node, package, moved):
    if isinstance(node, ast.Import):
        return any(_map_module(a.name, moved) for a in node.names)
    if isinstance(node, ast.ImportFrom):
        base = _resolve_from_base(node, package)
        if base and _map_module(base, moved):
            return True
        return any(a.name != "*" and _map_module(f"{base}.{a.name}" if base else a.name, moved)
                   for a in node.names)
    return False
def _rewrite_import(node, package, new_package, moved, is_moved):
    if isinstance(node, ast.Import):
        items, changed = [], False
        for alias in node.names:
            mapped = _map_module(alias.name, moved)
            if mapped and mapped != alias.name:
                if not alias.asname:
                    raise UnsupportedSyntax(f"bare dotted import {alias.name!r} needs an alias")
                items.append(f"{mapped} as {alias.asname}")
                changed = True
            else:
                items.append(_alias_text(alias))
        return "import " + ", ".join(items) if changed else None
    old_base = _resolve_from_base(node, package)
    mapped_base = _map_module(old_base, moved) if old_base else None
    info = []
    for alias in node.names:
        full = None if alias.name == "*" else (
            f"{old_base}.{alias.name}" if old_base else alias.name)
        info.append((alias, _map_module(full, moved) if full else None))
    changed = bool(mapped_base) or any(mapped for _, mapped in info)
    rebase = is_moved and node.level > 0
    if not changed and not rebase:
        return None
    nonstar = [(a, m) for a, m in info if a.name != "*"]
    if nonstar and all(m for _, m in nonstar):
        parents = set()
        for alias, mapped in nonstar:
            if not (mapped == alias.name or mapped.endswith("." + alias.name)):
                raise UnsupportedSyntax("submodule import changes its leaf name")
            parents.add(mapped.rsplit(".", 1)[0] if "." in mapped else "")
        if len(parents) != 1 or not next(iter(parents)):
            raise UnsupportedSyntax("imported submodules have different new packages")
        new_base = next(iter(parents))
    elif mapped_base:
        new_base = mapped_base
    elif rebase and not changed:
        new_base = old_base
    else:
        raise UnsupportedSyntax("mixed moved/unmoved names in one from-import")
    if not new_base:
        raise UnsupportedSyntax("cannot resolve import target")
    if node.level > 0:
        relative = _relative_import(new_package if is_moved else package, new_base)
        level, module = relative if relative is not None else (0, new_base)
    else:
        level, module = 0, new_base
    return _render_from(level, module, [_alias_text(a) for a, _ in info])
def _map_literal(value, moved, paths):
    mapped = _map_module(value, moved)
    if mapped and mapped != value:
        return mapped
    for old, new in paths.items():
        if value == old or value.startswith(old + ":") or value.startswith(old + "::"):
            return new + value[len(old):]
        if value.endswith(old) and (len(value) == len(old) or value[-len(old) - 1] == "/"):
            return value[:-len(old)] + new
    return None
def _call_kind(node):
    func = node.func
    name = func.id if isinstance(func, ast.Name) else (
        func.attr if isinstance(func, ast.Attribute) else None)
    if name in {"import_module", "__import__", "find_spec"}:
        return "module"
    if name in {"patch", "setattr"}:
        return "target"
    return "assert" if name and name.startswith("assert") else None
_STRING_HEAD = re.compile(rb"([rRuUbBfF]*)('''|\"\"\"|'|\")")
def _literal_content_start(token, require_f=False):
    match = _STRING_HEAD.match(token)
    if not match:
        raise UnsupportedSyntax("unsupported string token")
    if require_f and b"f" not in match.group(1).lower():
        raise UnsupportedSyntax("expected an f-string token")
    return match.end()
def _module_prefix(value, moved):
    if not isinstance(value, str) or not value:
        return None
    if value in moved and moved[value] != value:
        return value, moved[value]
    for old in sorted(moved, key=len, reverse=True):
        if moved[old] != old and value.startswith(old + "."):
            return old, moved[old]
    return None
def _literal_edits(tree, moved, paths, starts, size, raw):
    edits, pairs, seen, dynamic_moved = [], {}, set(), False
    joined_constants = {
        id(value)
        for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
        for value in node.values if isinstance(value, ast.Constant)
    }
    def add(const, new_value):
        if id(const) in seen or not isinstance(const.value, str):
            return
        seen.add(id(const))
        start, end = _span(const, starts, size)
        edits.append((start, end, json.dumps(new_value, ensure_ascii=False)))
        pairs[const.value] = const.value
        pairs[new_value] = const.value
    def prefix_edit(const, span, require_f):
        nonlocal dynamic_moved
        if id(const) in seen or not isinstance(const.value, str):
            return
        matched = _module_prefix(const.value, moved)
        if not matched:
            return
        old_prefix, new_prefix = matched
        start, end = span
        token = raw[start:end]
        content = _literal_content_start(token, require_f=require_f)
        old_bytes = old_prefix.encode("utf-8")
        if token[content:content + len(old_bytes)] != old_bytes:
            raise UnsupportedSyntax("escaped or unsupported module prefix")
        new_value = new_prefix + const.value[len(old_prefix):]
        seen.add(id(const))
        edits.append((start + content, start + content + len(old_bytes), new_prefix))
        pairs[const.value] = const.value
        pairs[new_value] = const.value
        dynamic_moved = True
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and node.values and isinstance(
                node.values[0], ast.Constant):
            prefix_edit(node.values[0], _span(node, starts, size), True)
    for node in ast.walk(tree):
        kind = _call_kind(node) if isinstance(node, ast.Call) else None
        if kind:
            if kind == "assert":
                consts = [a for a in node.args if isinstance(a, ast.Constant)] + [
                    kw.value for kw in node.keywords if isinstance(kw.value, ast.Constant)]
            else:
                consts = node.args[:1] or [
                    kw.value for kw in node.keywords
                    if kw.arg in {"name", "target"} and isinstance(kw.value, ast.Constant)][:1]
            for const in consts:
                if (kind == "module" and isinstance(const, ast.BinOp)
                        and isinstance(const.op, ast.Add)
                        and isinstance(const.left, ast.Constant)
                        and isinstance(const.left.value, str)):
                    prefix_edit(const.left, _span(const.left, starts, size), False)
                    continue
                if isinstance(const, ast.Constant) and isinstance(const.value, str):
                    if id(const) in joined_constants:
                        continue
                    new_value = _map_literal(const.value, moved, paths)
                    if new_value:
                        if kind != "assert":
                            dynamic_moved = True
                        add(const, new_value)
        elif isinstance(node, (ast.Compare, ast.Assert)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    if id(sub) in joined_constants:
                        continue
                    new_value = _map_literal(sub.value, moved, paths)
                    if new_value:
                        add(sub, new_value)
    return edits, pairs, dynamic_moved
def _normalized_ast(tree, mask):
    class Normalize(ast.NodeTransformer):
        def visit_Import(self, node): return ast.copy_location(ast.Pass(), node)
        def visit_ImportFrom(self, node): return ast.copy_location(ast.Pass(), node)
        def visit_Constant(self, node):
            if isinstance(node.value, str) and node.value in mask:
                return ast.copy_location(ast.Constant(value=mask[node.value]), node)
            return node
    normalized = Normalize().visit(tree)
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, include_attributes=False)
def _assert_business_ast(before, after, pairs):
    mask = {value: f"\x00mask:{key}" for value, key in pairs.items()}
    if _normalized_ast(before, mask) != _normalized_ast(after, mask):
        raise MoveError("business AST changed; refusing to plan this edit")
def _apply_edits(raw, edits):
    result, cursor = [], len(raw)
    for start, end, text in sorted(edits, reverse=True):
        if end > cursor:
            raise MoveError("overlapping AST edits")
        result.extend((raw[end:cursor], text.encode("utf-8")))
        cursor = start
    result.append(raw[:cursor])
    return b"".join(reversed(result))
def _rewrite_source(raw, old_path, new_path, moved, paths):
    try:
        text, starts = _decode_source(raw, old_path)
        tree = ast.parse(text, filename=old_path)
    except SyntaxError as exc:
        raise UnsupportedSyntax(f"{old_path}: cannot parse Python source") from exc
    package, new_package = _package_from_path(old_path), _package_from_path(new_path)
    is_moved = old_path != new_path
    edits, imports_moved = [], False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if _references_moved(node, package, moved):
                imports_moved = True
            replacement = _rewrite_import(node, package, new_package, moved, is_moved)
            if replacement is not None:
                start, end = _span(node, starts, len(raw))
                edits.append((start, end, replacement))
    pairs = {}
    if "final_move_inventory" not in old_path:
        literal_edits, pairs, dynamic_moved = _literal_edits(
            tree, moved, paths, starts, len(raw), raw)
        if is_moved or imports_moved or dynamic_moved:
            edits.extend(literal_edits)
        else:
            pairs = {}
    after = _apply_edits(raw, edits)
    if after != raw:
        after_text, _ = _decode_source(after, new_path)
        _assert_business_ast(tree, ast.parse(after_text, filename=new_path), pairs)
    return after, after != raw
def _alias_source(canonical):
    if "." in canonical:
        package, leaf = canonical.rsplit(".", 1)
        import_line = f"from {package} import {leaf} as _canonical"
    else:
        import_line = f"import {canonical} as _canonical"
    return f"import sys as _sys\n{import_line}\n_sys.modules[__name__] = _canonical\n"
def _read_map(map_path):
    raw = map_path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MoveError("map is not valid UTF-8 JSON") from exc
    if data.get("schema_version") != 1 or not isinstance(data.get("files"), list):
        raise MoveError("unsupported map schema")
    return raw, data
def _select_moves(data, owners):
    selected, old_seen, target_seen = [], set(), set()
    for row in data["files"]:
        if not isinstance(row, dict):
            raise MoveError("malformed map row")
        if row.get("disposition") != "MOVE_ONLY_CANDIDATE":
            continue
        if row.get("owner") not in owners or not row.get("new_path"):
            continue
        old, new = _safe_rel(row.get("old_path")), _safe_rel(row["new_path"])
        if old == new:
            continue
        if old in old_seen:
            raise MoveError(f"duplicate source mapping: {old}")
        if new.casefold() in target_seen:
            raise MoveError(f"duplicate/casefold target: {new}")
        old_seen.add(old)
        target_seen.add(new.casefold())
        selected.append({"old_path": old, "new_path": new, "owner": row["owner"]})
    return sorted(selected, key=lambda m: m["old_path"])
def _check_ancestors(root, rel):
    current = root
    for part in PurePosixPath(rel).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise MoveError(f"symlink ancestor for {rel}: {current}")
        if current.exists() and not current.is_dir():
            raise MoveError(f"non-directory ancestor for {rel}: {current}")
def _validate_sources(root, tracked, moves):
    for move in moves:
        old, new = move["old_path"], move["new_path"]
        if old not in tracked:
            raise MoveError(f"source is not tracked: {old}")
        source = root / old
        if not source.exists() or source.is_symlink() or not source.is_file():
            raise MoveError(f"missing or unsafe source: {old}")
        _check_ancestors(root, old)
        if os.path.lexists(root / new):
            raise MoveError(f"target already exists: {new}")
        if new in tracked:
            raise MoveError(f"target is already tracked: {new}")
        _check_ancestors(root, new)
        if any(t.casefold() == new.casefold() for t in tracked):
            raise MoveError(f"casefold target collision: {new}")
        parent = (root / new).parent
        if parent.is_dir() and any(
                child.name.casefold() == PurePosixPath(new).name.casefold()
                for child in parent.iterdir()):
            raise MoveError(f"casefold target collision: {new}")
def _add_write(writes, path, kind, before, after):
    if path in writes:
        raise MoveError(f"duplicate planned write: {path}")
    writes[path] = {"path": path, "kind": kind, "before_sha256": _sha(before),
                    "after_sha256": _sha(after), "before_b64": _b64(before),
                    "after_b64": _b64(after)}
def _domain_names(moves):
    return sorted({m["new_path"].split("/")[1] for m in moves
                   if len(m["new_path"].split("/")) >= 3 and m["new_path"].startswith("src/")})
def _update_pyproject(raw, domains):
    if not domains or b"[tool.setuptools.packages.find]" not in raw:
        return raw
    lines = raw.decode("utf-8").splitlines(keepends=True)
    section = next((i for i, line in enumerate(lines)
                    if line.strip() == "[tool.setuptools.packages.find]"), None)
    if section is None:
        return raw
    include = None
    for i in range(section + 1, len(lines)):
        if lines[i].lstrip().startswith("["):
            break
        if re.match(r"\s*include\s*=", lines[i]):
            include = i
            break
    if include is None:
        return raw
    close = include
    while close < len(lines) and "]" not in lines[close]:
        close += 1
    if close >= len(lines):
        return raw
    existing = set(re.findall(r'"([^"]+)"', "".join(lines[include:close + 1])))
    wanted = [name for domain in domains for name in (domain, f"{domain}.*")
              if name not in existing]
    if not wanted:
        return raw
    newline = "\r\n" if b"\r\n" in raw else "\n"
    indent = "    "
    for line in lines[include + 1:close]:
        if line.strip():
            indent = line[:len(line) - len(line.lstrip())]
            break
    addition = "".join(f'{indent}"{name}",{newline}' for name in wanted)
    if lines[close].strip() == "]":
        j = close - 1
        while j >= include and not lines[j].strip():
            j -= 1
        if j >= include:
            stripped = lines[j].rstrip("\r\n")
            if stripped and not stripped.endswith((",", "[")):
                lines[j] = stripped + "," + lines[j][len(stripped):]
        lines.insert(close, addition)
    else:
        before, after = lines[close].split("]", 1)
        prefix = before.rstrip()
        if prefix and not prefix.endswith((",", "[")):
            prefix += ","
        lines[close] = prefix + newline + addition + "]" + after
    return "".join(lines).encode("utf-8")
def build_plan(root, map_path, owners):
    root = Path(root).resolve()
    if not root.is_dir():
        raise MoveError(f"root is not a directory: {root}")
    map_path, owners_set = Path(map_path).resolve(), _normalize_owners(owners)
    _require_clean(root)
    head = _head(root)
    map_raw, data = _read_map(map_path)
    moves = _select_moves(data, owners_set)
    tracked = set(_tracked_files(root))
    _validate_sources(root, tracked, moves)
    moved = {_module_from_path(m["old_path"]): _module_from_path(m["new_path"]) for m in moves}
    paths = {m["old_path"]: m["new_path"] for m in moves}
    aliases = set(data.get("core_compatibility_aliases") or [])
    writes = {}
    for move in moves:
        old, new = move["old_path"], move["new_path"]
        raw = (root / old).read_bytes()
        after, _ = _rewrite_source(raw, old, new, moved, paths)
        _add_write(writes, new, "moved_source", raw, after)
        old_module = _module_from_path(old)
        if old_module in aliases:
            if old.endswith("__init__.py"):
                raise MoveError("package aliases are not supported")
            _add_write(writes, old, "alias", raw, _alias_source(moved[old_module]).encode("utf-8"))
    moved_targets = {m["new_path"] for m in moves}
    for move in moves:
        parts = move["new_path"].split("/")
        for depth in range(2, len(parts)):
            init = "/".join(parts[:depth] + ["__init__.py"])
            if init in moved_targets or init in writes or os.path.lexists(root / init):
                if os.path.islink(root / init):
                    raise MoveError(f"symlink package init: {init}")
                continue
            _check_ancestors(root, init)
            _add_write(writes, init, "new_init", b"", b"")
    old_paths = {m["old_path"] for m in moves}
    for path in _inventory(list(tracked)):
        if path in moved_targets or path in old_paths:
            continue
        _check_ancestors(root, path)
        if (root / path).is_symlink():
            raise MoveError(f"symlink caller: {path}")
        raw = (root / path).read_bytes()
        after, changed = _rewrite_source(raw, path, path, moved, paths)
        if changed:
            _add_write(writes, path, "caller", raw, after)
    if "pyproject.toml" in tracked:
        raw = (root / "pyproject.toml").read_bytes()
        after = _update_pyproject(raw, _domain_names(moves))
        if after != raw:
            _add_write(writes, "pyproject.toml", "pyproject", raw, after)
    for path in writes:
        _check_ancestors(root, path)
        if path not in tracked and (root / path).exists() and writes[path]["kind"] != "moved_source":
            raise MoveError(f"write target already exists: {path}")
    approved = {p for m in moves for p in (m["old_path"], m["new_path"])} | set(writes)
    target_absences = sorted({m["new_path"] for m in moves} |
                             {p for p, w in writes.items() if w["kind"] == "new_init"})
    source_hashes = {m["old_path"]: _sha((root / m["old_path"]).read_bytes()) for m in moves}
    caller_hashes = {p: w["before_sha256"] for p, w in writes.items()
                     if w["kind"] in {"caller", "pyproject"}}
    changed_paths = sorted(approved)
    return {"schema_version": 1, "tool": "flat_domain_move", "root": str(root),
            "head": head, "map_path": str(map_path), "map_sha256": _sha(map_raw),
            "map_source_sha": data.get("source_sha"), "owners": sorted(owners_set),
            "moves": moves, "writes": [writes[p] for p in sorted(writes)],
            "source_hashes": source_hashes, "caller_hashes": caller_hashes,
            "target_absences": target_absences, "approved_paths": changed_paths,
            "changed_paths": changed_paths,
            "summary": {"moves": len(moves), "writes": len(writes),
                        "callers": sum(w["kind"] == "caller" for w in writes.values()),
                        "aliases": sum(w["kind"] == "alias" for w in writes.values()),
                        "new_inits": sum(w["kind"] == "new_init" for w in writes.values())}}
def _plan_summary(plan):
    return {"head": plan.get("head"), "map_sha256": plan.get("map_sha256"),
            "counts": plan.get("summary", {}),
            "moves": [{"old_path": m["old_path"], "new_path": m["new_path"], "owner": m["owner"]}
                      for m in plan.get("moves", [])],
            "writes": [{"path": w["path"], "kind": w["kind"], "after_sha256": w["after_sha256"]}
                       for w in plan.get("writes", [])]}
def apply_plan(root, plan):
    root = Path(root).resolve()
    if plan.get("schema_version") != 1 or plan.get("tool") != "flat_domain_move":
        raise MoveError("unsupported plan schema")
    if plan.get("root") and Path(plan["root"]).resolve() != root:
        raise MoveError("plan root does not match --root")
    _require_clean(root)
    if _head(root) != plan.get("head"):
        raise MoveError("HEAD drifted since planning")
    map_path = Path(plan.get("map_path", ""))
    if not map_path.is_file():
        raise MoveError("map file is missing")
    rebuilt = build_plan(root, map_path, plan.get("owners", []))
    for key in ("head", "map_sha256", "map_source_sha", "owners", "moves", "writes", "source_hashes",
                "caller_hashes", "target_absences", "approved_paths", "changed_paths", "summary"):
        if rebuilt.get(key) != plan.get(key):
            raise MoveError(f"plan is stale, tampered, or unapproved ({key})")
    moves, writes = rebuilt["moves"], rebuilt["writes"]
    for move in moves:
        (root / move["new_path"]).parent.mkdir(parents=True, exist_ok=True)
    for write in writes:
        (root / write["path"]).parent.mkdir(parents=True, exist_ok=True)
    for move in moves:
        _git(root, "mv", "--", move["old_path"], move["new_path"])
    for write in writes:
        path, destination = write["path"], root / write["path"]
        after = base64.b64decode(write["after_b64"])
        if write["kind"] == "moved_source":
            if not destination.is_file() or _sha(destination.read_bytes()) != write["before_sha256"]:
                raise MoveError(f"git mv did not preserve bytes for {path}")
        elif write["kind"] in {"caller", "pyproject"}:
            if _sha(destination.read_bytes()) != write["before_sha256"]:
                raise MoveError(f"write input drifted during apply: {path}")
        elif os.path.lexists(destination):
            raise MoveError(f"write target appeared during apply: {path}")
        destination.write_bytes(after)
    return {"applied": True, "moves": len(moves), "writes": len(writes),
            "changed_paths": sorted({m["new_path"] for m in moves} | {w["path"] for w in writes})}
def _outside_root(root, output):
    try:
        output.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return False
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--owners", nargs="+")
    for flag in ("--map", "--output", "--apply"):
        parser.add_argument(flag)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        if args.apply:
            plan = json.loads(Path(args.apply).read_text(encoding="utf-8"))
            print(json.dumps(apply_plan(root, plan), indent=2, sort_keys=True))
        else:
            if not (args.map and args.owners and args.output):
                parser.error("planning requires --map, --owners and --output")
            output = Path(args.output).resolve()
            if not _outside_root(root, output):
                raise MoveError("--output must be outside ROOT")
            plan = build_plan(root, args.map, args.owners)
            output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps(_plan_summary(plan), indent=2, sort_keys=True))
        return 0
    except (MoveError, OSError, json.JSONDecodeError) as exc:
        print(f"flat_domain_move: {exc}", file=sys.stderr)
        return 2
if __name__ == "__main__":
    raise SystemExit(main())
