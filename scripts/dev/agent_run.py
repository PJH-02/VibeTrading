#!/usr/bin/env python3
"""O01B: private, credential-free execution adapter for the existing dispatcher.

The module is deliberately small. It reuses ``agent_task`` for packet, source,
scope and test gates, but never accepts work, applies patches, commits, merges or
deploys. Every successful run ends as PENDING_CODEX_REVIEW.
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError

try:
    from scripts.dev import agent_task as dev
except Exception:  # pragma: no cover - exercised by direct script loading
    import importlib.util

    _path = Path(__file__).resolve().with_name("agent_task.py")
    _spec = importlib.util.spec_from_file_location("_o01b_agent_task", _path)
    if _spec is None or _spec.loader is None:
        raise
    dev = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(dev)

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "codex-opencode" / "runs"
LOCK_ROOT = os.environ.get("O01B_LOCK_ROOT")
WORKER_HOME = "/home/pjh"
WORKER_ROOT = "/work"
MAX_RELAY_BODY = 1024 * 1024
MAX_RELAY_RESPONSE = 4 * 1024 * 1024
MAX_RELAY_TIMEOUT = 180
MAX_OBSERVABILITY_FILE = 4 * 1024 * 1024
MAX_OBSERVABILITY_FILES = 200
SUPPORTED_MODEL_PREFIXES = ("opencode/", "freellmapi/")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHELL_METACHARS = set(";|&<>$`\\\n\r()")
FAILURE_LOCK = "LOCK_BUSY"
FAILURE_CANCELLED = "CANCELLED"
FAILURE_SOURCE = "SOURCE_CHANGED"
FAILURE_DIAGNOSTICS = "NEW_DIAGNOSTICS"
FAILURE_BWRAP = "BWRAP_UNAVAILABLE"
FAILURE_RUNTIME = "RUNTIME_UNAVAILABLE"
FAILURE_SAFETY = dev.FAILURE_SAFETY


class _SourceDrift(ValueError):
    """The snapshot no longer matches the packet pins before inference."""


class _OriginChanged(ValueError):
    """The origin changed between packet creation and execution."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _canonical_hash(value):
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_no_symlink_ancestors(path):
    absolute = Path(path).absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"path traverses a symlink: {current}")


def _ensure_private_dir(path):
    absolute = Path(path).absolute()
    _assert_no_symlink_ancestors(absolute)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            os.mkdir(current, 0o700)
            info = os.lstat(current)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"private state path is not a directory: {current}")
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"private state path is a symlink: {current}")
    os.chmod(absolute, 0o700)
    return absolute


def _write_private(path, data, mode=0o600):
    path = Path(path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    _assert_no_symlink_ancestors(path.parent)
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode
    )
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json_private(path, value):
    _write_private(
        path,
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
    )


def _open_private_log(path):
    _assert_no_symlink_ancestors(Path(path).parent)
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "wb")


def _state_root(state_dir):
    return _ensure_private_dir(state_dir or DEFAULT_STATE_DIR)


def _validate_ttl(ttl_seconds):
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise ValueError("ttl_seconds must be a positive integer")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be a positive integer")


def parse_test_commands(commands):
    """Return argv lists after refusing anything except ``python3 -m pytest``."""
    if not isinstance(commands, list) or not commands:
        raise ValueError("test_commands must be a non-empty list")
    parsed = []
    for command in commands:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("test_commands entries must be non-empty strings")
        if any(ch in _SHELL_METACHARS for ch in command):
            raise ValueError(f"test command contains shell metacharacters: {command!r}")
        argv = shlex.split(command)
        if len(argv) < 3 or argv[:3] != ["python3", "-m", "pytest"]:
            raise ValueError(f"unsupported test program: {command!r}")
        parsed.append(argv)
    return parsed


def _normalized_origin(origin_root):
    return os.path.realpath(os.path.abspath(str(origin_root)))


def _resolved_lock_root():
    if LOCK_ROOT:
        return _ensure_private_dir(Path(LOCK_ROOT).expanduser())
    preferred = Path.home() / ".local" / "state" / "codex-opencode" / "locks"
    try:
        return _ensure_private_dir(preferred)
    except OSError:
        fallback = Path("/tmp") / f"codex-opencode-{os.getuid()}" / "locks"
        return _ensure_private_dir(fallback)


def _lock_path(state_root, origin_root):
    # Locks are host-shared and keyed only by the resolved origin, so different
    # artifact directories cannot create two writers for one working tree.
    key = hashlib.sha256(_normalized_origin(origin_root).encode("utf-8")).hexdigest()
    return Path(_resolved_lock_root()) / f"{key}.lock"


def _acquire_root_lock(state_root, origin_root):
    lock_dir = _resolved_lock_root()
    path = _lock_path(lock_dir, origin_root)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _cancel_marker(state_root, run_id):
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run id")
    return Path(state_root) / "cancel" / f"{run_id}.json"


def cancel_run(state_dir, run_id):
    """Request cancellation; the marker is polled before and during execution."""
    state_root = _state_root(state_dir)
    marker = _cancel_marker(state_root, run_id)
    _ensure_private_dir(marker.parent)
    _write_json_private(marker, {"run_id": run_id, "cancelled_utc": _utc_now()})
    return {"status": "CANCEL_REQUESTED", "run_id": run_id, "marker": str(marker)}


def _is_cancelled(state_root, run_id):
    marker = _cancel_marker(state_root, run_id)
    return marker.is_file() and not marker.is_symlink()


def _iter_states(state_root):
    try:
        entries = list(os.scandir(state_root))
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        state_file = Path(entry.path) / "state.json"
        try:
            if state_file.is_symlink() or not state_file.is_file():
                continue
            value = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            yield value


def _duplicate(state_root, origin_root, packet_sha):
    for state in _iter_states(state_root):
        if state.get("origin_root") != origin_root:
            continue
        if state.get("packet_sha256") != packet_sha:
            continue
        # Only a completed, non-cancelled/non-expired run is a duplicate.
        if state.get("status") == "PENDING_CODEX_REVIEW" and not state.get(
            "expired"
        ) and not state.get("cancelled"):
            return state
    return None


def _native_candidates():
    candidates = []
    current = shutil.which("opencode")
    if current:
        candidates.append(Path(current))
    candidates.append(Path("~/.opencode/bin/opencode").expanduser())
    nvm_root = Path(os.environ.get("NVM_DIR", "~/.nvm")).expanduser()
    try:
        versions = sorted(
            nvm_root.glob("versions/node/*/bin/opencode"),
            key=lambda item: item.parent.parent.name,
            reverse=True,
        )
    except OSError:
        versions = []
    candidates.extend(versions)
    return candidates


def _find_native_opencode():
    override = os.environ.get("O01B_NATIVE_OPENCODE")
    if override:
        return Path(override).expanduser()
    launcher = Path(os.environ.get("O01B_OPENCODE_LAUNCHER", "~/.local/bin/opencode")).expanduser().resolve()
    for candidate in _native_candidates():
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK) and candidate.resolve() != launcher:
                return candidate.resolve()
        except OSError:
            continue
    return Path("~/.opencode/bin/opencode").expanduser()


def _discover_runtime():
    env = os.environ
    return SimpleNamespace(
        launcher=Path(
            env.get("O01B_OPENCODE_LAUNCHER", "~/.local/bin/opencode")
        ).expanduser(),
        dispatcher=Path(
            env.get("O01B_DELEGATE_OPENCODE", "~/.local/bin/delegate-opencode")
        ).expanduser(),
        native=_find_native_opencode(),
        provider_config=Path(
            env.get("O01B_OPENCODE_CONFIG", "~/.config/opencode/opencode.json")
        ).expanduser(),
        python_deps=[
            Path(item)
            for item in env.get(
                "O01B_PYTHON_DEPS",
                "/home/pjh/.local/lib/python3.12/site-packages:"
                "/home/pjh/.cache/vt-refactor-deps",
            ).split(":")
            if item
        ],
    )


def _runtime_observations(runtime):
    native = Path(runtime.native)
    result = {
        "launcher": str(runtime.launcher),
        "dispatcher": str(runtime.dispatcher),
        "native": str(native),
        "native_exists": native.is_file(),
        "native_sha256": None,
        "version": None,
    }
    if native.is_file():
        try:
            result["native_sha256"] = _sha_file(native)
        except OSError:
            pass
        if os.access(native, os.X_OK):
            try:
                completed = subprocess.run(
                    [str(native), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                if completed.returncode == 0:
                    result["version"] = (completed.stdout or completed.stderr).strip()[:200]
            except (OSError, subprocess.SubprocessError):
                pass
    return result


def _bwrap_path():
    return shutil.which("bwrap")


def _snapshot_workspace(root, work, read_paths, edit_paths, *, exclude=()):
    root = Path(root)
    work = Path(work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, mode=0o700)
    edit_set = set(edit_paths)
    for rel in sorted(set(read_paths) | edit_set):
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ValueError(f"unsafe snapshot path: {rel!r}")
        _assert_no_symlink_ancestors(root / rel)

    excluded = {Path(item).resolve() for item in exclude}

    def ignore(directory, names):
        current = Path(directory).resolve()
        if current in excluded:
            return set(names)
        ignored = set()
        for name in names:
            if name == ".git":
                ignored.add(name)
                continue
            if (current / name).resolve() in excluded:
                ignored.add(name)
        return ignored

    shutil.copytree(root, work, symlinks=True, dirs_exist_ok=True, ignore=ignore)

    for rel in sorted(set(read_paths) | edit_set):
        source = root / rel
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file() and not source.is_symlink():
            if not target.exists():
                shutil.copyfile(source, target)
            os.chmod(target, 0o600)
        elif rel in edit_set:
            if not target.exists():
                _write_private(target, b"")
        else:
            raise ValueError(f"missing approved input: {rel!r}")
    return work


def _workspace_manifest(root):
    """Hash every regular file/symlink under *root*, ignoring only fresh .git."""
    root = Path(root)
    manifest = {}
    if not root.is_dir():
        return manifest
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        retained = []
        for name in sorted(dirnames):
            if name == ".git":
                continue
            target = current / name
            if target.is_symlink():
                rel = target.relative_to(root).as_posix()
                manifest[rel] = "symlink:" + os.readlink(target)
            else:
                retained.append(name)
        dirnames[:] = retained
        for name in sorted(filenames):
            target = current / name
            rel = target.relative_to(root).as_posix()
            try:
                info = os.lstat(target)
            except OSError:
                manifest[rel] = "unreadable"
                continue
            if stat.S_ISLNK(info.st_mode):
                manifest[rel] = "symlink:" + os.readlink(target)
            elif stat.S_ISREG(info.st_mode):
                manifest[rel] = _sha_file(target)
            else:
                manifest[rel] = f"other:{stat.S_IFMT(info.st_mode)}:{info.st_size}"
    return manifest


def _snapshot_hashes(work, paths=None):
    if paths is None:
        return _workspace_manifest(work)
    manifest = _workspace_manifest(work)
    return {rel: manifest.get(rel) for rel in paths}


def _verify_snapshot_pins(work, packet):
    for rel, expected in packet["source_hashes"].items():
        target = Path(work) / rel
        if expected is None:
            if target.exists():
                if target.is_symlink() or not target.is_file():
                    raise _SourceDrift(f"pinned new path is not a file: {rel}")
                if target.stat().st_size:
                    raise _SourceDrift(
                        f"pinned ADD placeholder was not empty in snapshot: {rel}"
                    )
            continue
        if not target.is_file() or target.is_symlink():
            raise _SourceDrift(f"snapshot is missing pinned source: {rel}")
        if _sha_file(target) != expected:
            raise _SourceDrift(f"snapshot drifted from packet pin: {rel}")


def _ticket_text(packet):
    read_paths = list(packet["read_paths"])
    prioritized_reads = read_paths[:5]
    remaining_reads = max(0, len(read_paths) - len(prioritized_reads))
    prerequisites = list(packet["prerequisites"])
    contracts = list(packet["contract_ids"])
    forbidden_paths = list(packet["forbidden_paths"])

    lines = [
        f"# Bounded task {packet['task_id']}",
        "",
        packet["objective"],
        "",
        f"Base SHA: {packet['base_sha']}",
        "Prerequisites: " + (", ".join(prerequisites) or "(none)"),
        f"Model: {packet['model']}",
        f"Provider: {packet['provider']}",
        f"Profile: {packet['profile']}",
        f"Review severity: {packet['review_severity']}",
        "",
        "Contracts:",
        *[f"- {item}" for item in contracts],
        "",
        "Forbidden paths:",
        *[f"- {item}" for item in forbidden_paths],
        "",
        "Read paths (first five prioritized; later entries are read-only dependency files):",
        *[f"- {item}" for item in prioritized_reads],
    ]
    if remaining_reads:
        lines.append(
            f"- ... {remaining_reads} additional read-only dependency files not "
            "listed in this bounded header"
        )
    lines += [
        "",
        "Edit paths:",
        *[f"- {item}" for item in packet["edit_paths"]],
        "",
        "Implementation steps:",
        *[f"- {item}" for item in packet["implementation_steps"]],
        "",
        "Acceptance:",
        *[f"- {item}" for item in packet["acceptance"]],
        "",
        "Exact test commands:",
        *[f"- {item}" for item in packet["test_commands"]],
        "",
        "Stop conditions:",
        *[f"- {item}" for item in packet["stop_conditions"]],
        "",
        "Do not apply, commit, merge, deploy or ask another agent. Return only the exact scoped edits.",
        "",
    ]
    return "\n".join(lines)


def _git(work, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(work), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _git_baseline(work):
    subprocess.run(["git", "init", "-q", str(work)], check=True, capture_output=True)
    _git(work, "add", "-A")
    _git(
        work,
        "-c",
        "user.name=O01B Adapter",
        "-c",
        "user.email=o01b@local.invalid",
        "commit",
        "-q",
        "-m",
        "Pinned bounded worker inputs",
    )


def _diff_check(work):
    completed = _git(work, "diff", "--check", check=False)
    return {
        "exit_code": completed.returncode,
        "output": (completed.stdout or "") + (completed.stderr or ""),
    }


def _git_observe(root, *args):
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"exit_code": None, "output": str(exc)}
    return {
        "exit_code": completed.returncode,
        "output": (completed.stdout or "") + (completed.stderr or ""),
    }


def _origin_baseline(root):
    probe = _git_observe(root, "rev-parse", "--is-inside-work-tree")
    is_repo = probe.get("exit_code") == 0 and "true" in (probe.get("output") or "")
    return {
        "is_repo": is_repo,
        "diff_check": _git_observe(root, "diff", "--check") if is_repo else None,
        "status": _git_observe(root, "status", "--porcelain") if is_repo else None,
    }


def _load_provider(provider_config, model):
    config = json.loads(Path(provider_config).read_text(encoding="utf-8"))
    selected = model.split("/", 1)[1]
    provider = (config.get("provider") or {}).get("freellmapi")
    if not isinstance(provider, dict):
        raise ValueError("freellmapi provider is not configured")
    models = provider.get("models")
    if not isinstance(models, dict) or selected not in models:
        raise ValueError(f"freellmapi model is not configured exactly: {selected!r}")
    options = provider.get("options") or {}
    reference = options.get("apiKey")
    base_url = options.get("baseURL")
    if not isinstance(reference, str) or not (
        reference.startswith("{file:") and reference.endswith("}")
    ):
        raise ValueError("freellmapi apiKey must use a file secret reference")
    if not isinstance(base_url, str):
        raise ValueError("freellmapi baseURL is missing")
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("freellmapi baseURL must be a loopback HTTP gateway")
    secret = Path(reference[6:-1]).expanduser().read_text(encoding="utf-8").strip()
    if not secret:
        raise ValueError("freellmapi secret is empty")
    return secret, base_url.rstrip("/"), models[selected]


def _validated_loopback_base(base_url):
    parsed = urlsplit(base_url)
    if parsed.scheme != "http":
        raise ValueError("relay upstream must be loopback HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("relay upstream must not carry userinfo, query or fragment")
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("relay upstream must be a loopback address")
    if parsed.path.rstrip("/") != "/v1":
        raise ValueError("relay upstream must expose exactly /v1")
    return base_url.rstrip("/")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class FreeLLMRelay:
    """Loopback relay that forwards only the exact selected chat completion."""

    def __init__(self, upstream_base, secret, selected_model, timeout=75):
        self.upstream_base = _validated_loopback_base(str(upstream_base))
        self._secret = secret
        self.selected_model = selected_model
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("relay timeout must be numeric")
        if timeout <= 0:
            raise ValueError("relay timeout must be positive")
        self.timeout = min(float(timeout), float(MAX_RELAY_TIMEOUT))
        self.statuses = []
        self.served_models = set()
        self.retry_after = []
        self.redirects_blocked = 0
        self.redactions = 0
        self.safety_blocker = False
        self._server = None
        self._opener = build_opener(_NoRedirect)

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def start(self):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *unused):
                return

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    self.send_error(400)
                    return
                if self.path != "/v1/chat/completions":
                    self.send_error(403)
                    return
                if length <= 0 or length > MAX_RELAY_BODY:
                    self.send_error(413)
                    return
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw)
                except ValueError:
                    self.send_error(400)
                    return
                if not isinstance(payload, dict) or payload.get("model") != relay.selected_model:
                    self.send_error(403)
                    return
                request = Request(
                    relay.upstream_base + "/chat/completions",
                    data=raw,
                    headers={
                        "Authorization": "Bearer " + relay._secret,
                        "Content-Type": "application/json",
                    },
                )
                try:
                    try:
                        response = relay._opener.open(request, timeout=relay.timeout)
                    except HTTPError as exc:
                        response = exc
                        if exc.code in (301, 302, 303, 307, 308):
                            relay.redirects_blocked += 1
                    with response:
                        code = response.status
                        content_type = response.headers.get(
                            "Content-Type", "application/json"
                        )
                        retry_after = response.headers.get("Retry-After")
                        body = response.read(MAX_RELAY_RESPONSE)
                    relay.statuses.append(code)
                    if retry_after:
                        relay.retry_after.append(
                            {"status": code, "retry_after": str(retry_after)[:128]}
                        )
                    if relay._secret.encode("utf-8") in body:
                        relay.redactions += 1
                        relay.safety_blocker = True
                        body = body.replace(
                            relay._secret.encode("utf-8"), b"[REDACTED]"
                        )
                    for line in body.decode(errors="replace").splitlines():
                        candidate = line[6:] if line.startswith("data: ") else line
                        try:
                            item = json.loads(candidate)
                        except ValueError:
                            continue
                        if isinstance(item, dict) and item.get("model"):
                            relay.served_models.add(str(item["model"]))
                    self.send_response(code)
                    self.send_header("Content-Type", content_type)
                    if retry_after:
                        self.send_header("Retry-After", retry_after)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (OSError, TimeoutError, ValueError):
                    self.send_error(502, "Temporary inference relay unavailable")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def observations(self):
        return {
            "http_statuses": list(self.statuses),
            "served_models": sorted(self.served_models),
            "retry_after": list(self.retry_after),
            "redirects_blocked": self.redirects_blocked,
            "redactions": self.redactions,
            "safety_blocker": self.safety_blocker,
        }


def _opencode_config(model, relay_url=None, model_entry=None):
    permission = {
        "*": "deny",
        "task": "deny",
        "question": "deny",
        "external_directory": "deny",
        "read": "allow",
        "edit": "allow",
        "bash": {"*": "deny", "python3 -m pytest*": "allow"},
        "glob": "deny",
        "grep": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "skill": "deny",
    }
    provider = {}
    if model.startswith("freellmapi/"):
        selected = model.split("/", 1)[1]
        provider = {
            "freellmapi": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "baseURL": relay_url,
                    "apiKey": "o01b-local-relay-placeholder",
                },
                "models": {selected: model_entry or {"name": selected}},
            }
        }
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": model,
        "small_model": model,
        "lsp": False,
        "formatter": False,
        "permission": permission,
        "provider": provider,
        "agent": {
            "plan": {
                "prompt": (
                    "Read-only bounded audit. Read only the exact frozen source "
                    "excerpt named by the attached ticket, then return concrete "
                    "findings. No exploration, metadata search, Bash, agents, "
                    "edits, or user questions. Codex alone accepts."
                )
            },
            "build": {
                "prompt": (
                    "You are a bounded implementation worker. Read the attached "
                    "ticket and exact relevant source files directly. No repository "
                    "exploration, no subagents, no metadata changes. Implement exact "
                    "scope, run the specified pytest command, and stop. The "
                    "orchestrator alone accepts work."
                )
            },
        },
    }


def _prepare_home(home, runtime, config):
    home = Path(home)
    _ensure_private_dir(home)
    bins = home / ".local" / "bin"
    _ensure_private_dir(bins)
    for source, name in (
        (runtime.launcher, "opencode"),
        (runtime.dispatcher, "delegate-opencode"),
    ):
        if not Path(source).is_file():
            raise ValueError(f"runtime executable is missing: {source}")
        target = bins / name
        shutil.copyfile(source, target)
        os.chmod(target, 0o700)
    _ensure_private_dir(home / ".opencode" / "bin")
    (home / ".opencode" / "bin" / "opencode").touch(mode=0o600)
    config_dir = home / ".config" / "opencode"
    _ensure_private_dir(config_dir)
    _write_json_private(config_dir / "opencode.json", config)
    return home


def _common_mounts(work, home, runtime, edit_paths, *, unshare_net=True):
    bwrap = _bwrap_path()
    if not bwrap:
        raise RuntimeError("bwrap is unavailable")
    args = [
        bwrap,
        "--unshare-user",
        "--unshare-pid",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
    ]
    if unshare_net:
        args += ["--unshare-net"]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/etc"]
    for source in ("/usr", "/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts",
                   "/etc/nsswitch.conf", "/etc/ld.so.cache", "/etc/alternatives"):
        if Path(source).exists():
            args += ["--ro-bind", source, source]
    for link, target in (("/bin", "usr/bin"), ("/lib", "usr/lib"), ("/lib64", "usr/lib64")):
        if Path(target).exists():
            args += ["--symlink", target, link]
        elif Path(link).exists():
            args += ["--ro-bind", link, link]
    args += ["--dir", "/home", "--bind", str(home), WORKER_HOME]
    if runtime is not None:
        native = Path(runtime.native)
        if native.is_file():
            args += [
                "--ro-bind",
                str(native.resolve()),
                f"{WORKER_HOME}/.opencode/bin/opencode",
            ]
        for dep in runtime.python_deps:
            if Path(dep).exists():
                args += ["--ro-bind", str(dep), str(dep)]
    args += ["--ro-bind", str(work), WORKER_ROOT]
    for rel in edit_paths:
        source = Path(work) / rel
        source.parent.mkdir(parents=True, exist_ok=True)
        args += ["--bind", str(source), str(Path(WORKER_ROOT) / rel)]
    return args


def _worker_command(runtime, work, home, shell, edit_paths, ticket_rel, role, model, ttl):
    # The API parent needs loopback/network; only Bash tool subprocesses are netless.
    args = _common_mounts(work, home, runtime, edit_paths, unshare_net=False)
    args += ["--ro-bind", str(shell), "/opt/bash"]
    args += [
        "--clearenv",
        "--setenv", "HOME", WORKER_HOME,
        "--setenv", "PATH", f"{WORKER_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin",
        "--setenv", "SHELL", "/opt/bash",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1",
        "--setenv", "PYTHONPATH",
        ":".join([WORKER_ROOT] + [str(item) for item in runtime.python_deps]),
        "--setenv", "GIT_OPTIONAL_LOCKS", "0",
        "--setenv", "OPENCODE_DELEGATE_TIMEOUT_SEC", str(ttl),
        "--setenv", "OPENCODE_PURE", "1",
        "--setenv", "OPENCODE_DISABLE_PROJECT_CONFIG", "1",
        "--setenv", "OPENCODE_DISABLE_DEFAULT_PLUGINS", "1",
        "--setenv", "OPENCODE_DISABLE_EXTERNAL_SKILLS", "1",
        "--setenv", "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", "1",
        "--setenv", "OPENCODE_DISABLE_LSP_DOWNLOAD", "1",
        "--chdir", WORKER_ROOT,
        "--",
        f"{WORKER_HOME}/.local/bin/delegate-opencode",
        role,
        ticket_rel,
        model,
    ]
    return args


def _test_sandbox_command(work, home, argv, runtime=None):
    args = _common_mounts(work, home, runtime, [], unshare_net=True)
    # Independent tests get a fresh tmpfs HOME so worker-installed executables
    # in HOME/.local/bin can never poison a later test command.
    args += ["--tmpfs", WORKER_HOME]
    # Fresh HOME hides prior submounts; expose only public dependencies again.
    if runtime is not None:
        for dep in runtime.python_deps:
            if Path(dep).exists():
                args += ["--ro-bind", str(dep), str(dep)]
    py_path = [WORKER_ROOT]
    if runtime is not None:
        py_path += [str(item) for item in runtime.python_deps]
    args += [
        "--clearenv",
        "--setenv", "HOME", WORKER_HOME,
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1",
        "--setenv", "PYTHONPATH", ":".join(py_path),
        "--setenv", "GIT_OPTIONAL_LOCKS", "0",
        "--chdir", WORKER_ROOT,
        "--",
        *argv,
    ]
    return args


def _kill_group(process):
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


def _scan_json(path):
    events = 0
    usage = None
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events, usage
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        events += 1
        if isinstance(item, dict) and isinstance(item.get("usage"), dict):
            usage = item["usage"]
    return events, usage


def _bounded_text(path):
    try:
        with Path(path).open("rb") as handle:
            return handle.read(MAX_OBSERVABILITY_FILE).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _find_tokens(value):
    if isinstance(value, dict):
        tokens = value.get("tokens")
        if isinstance(tokens, dict) and tokens:
            return tokens
        for key in ("part", "properties", "payload", "data", "message"):
            found = _find_tokens(value.get(key))
            if found is not None:
                return found
        for item in value.values():
            found = _find_tokens(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_tokens(item)
            if found is not None:
                return found
    return None


def _find_error(value):
    if isinstance(value, dict):
        error = value.get("error")
        if error:
            text = json.dumps(error, ensure_ascii=False) if not isinstance(error, str) else error
            return text[:400]
        if value.get("type") in ("error", "session.error") and value.get("message"):
            return str(value["message"])[:400]
        for key in ("properties", "part", "payload", "data"):
            found = _find_error(value.get(key))
            if found:
                return found
    return None


def _find_tool_state(value):
    if isinstance(value, dict):
        tool = value.get("tool")
        state = value.get("state")
        if isinstance(tool, str) and isinstance(state, (dict, str)):
            status = state.get("status") if isinstance(state, dict) else state
            return {"tool": tool, "status": str(status)[:80]}
        for key in ("part", "properties", "payload", "data"):
            found = _find_tool_state(value.get(key))
            if found:
                return found
    return None


def _collect_opencode_artifacts(home, run_dir):
    base = Path(home) / ".local" / "state" / "codex-opencode" / "runs"
    logs = []
    metadata = []
    worker_exits = []
    if base.is_dir() and not base.is_symlink():
        for directory, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = [name for name in sorted(dirnames) if not (Path(directory) / name).is_symlink()]
            for name in sorted(filenames):
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                if name == "opencode.log" or name.endswith(".jsonl") or name.endswith(".log"):
                    logs.append(path)
                elif name in ("metadata.json", "metadata") or name.endswith(".metadata.json"):
                    metadata.append(path)
                elif name == "worker-exit.txt":
                    worker_exits.append(path)
                if len(logs) + len(metadata) + len(worker_exits) >= MAX_OBSERVABILITY_FILES:
                    break
            if len(logs) + len(metadata) + len(worker_exits) >= MAX_OBSERVABILITY_FILES:
                break

    usage = None
    models = set()
    errors = []
    tool_states = []
    for path in logs + metadata:
        text = _bounded_text(path)
        items = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
        if not items and metadata and path in metadata:
            try:
                items.append(json.loads(text))
            except ValueError:
                pass
        for item in items:
            found = _find_tokens(item)
            if found is not None:
                usage = found
            if isinstance(item, dict):
                model = item.get("model")
                if isinstance(model, str) and model:
                    models.add(model)
                error = _find_error(item)
                if error:
                    errors.append(error)
                tool_state = _find_tool_state(item)
                if tool_state:
                    tool_states.append(tool_state)
    for path in worker_exits:
        text = _bounded_text(path).strip()
        if text and text not in errors:
            errors.append(text[:400])
    return {
        "opencode_logs": [str(path) for path in logs],
        "metadata_files": [str(path) for path in metadata],
        "worker_exit_files": [str(path) for path in worker_exits],
        "artifact_paths": [str(path) for path in logs + metadata + worker_exits],
        "token_usage": usage,
        "observed_models": sorted(models),
        "errors": errors[-20:],
        "tool_states": tool_states[-20:],
        "actual_weights": "UNKNOWN",
    }


def _run_worker_process(command, *, work, run_dir, deadline, cancel_check,
                        state_root, run_id, timeout_seconds):
    stdout_path = Path(run_dir) / "worker.stdout.log"
    stderr_path = Path(run_dir) / "worker.stderr.log"
    started = time.monotonic()
    timed_out = False
    cancelled = False
    with _open_private_log(stdout_path) as out, _open_private_log(stderr_path) as err:
        process = subprocess.Popen(
            command,
            stdout=out,
            stderr=err,
            start_new_session=True,
            cwd=str(work),
        )
        while process.poll() is None:
            if cancel_check():
                cancelled = True
                _kill_group(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _kill_group(process)
                break
            time.sleep(0.1)
        exit_code = process.wait()
    elapsed = time.monotonic() - started
    events, usage = _scan_json(stdout_path)
    return {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "elapsed_seconds": elapsed,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "json_event_count": events,
        "token_usage": usage,
        "effective_exit_code": 124 if timed_out else exit_code,
    }


def _run_test_process(argv, *, command, work, home, run_dir, index, runtime, deadline,
                      cancel_check=None, state_root=None, run_id=None):
    stdout_path = Path(run_dir) / f"test-{index}.stdout.log"
    stderr_path = Path(run_dir) / f"test-{index}.stderr.log"
    call = _test_sandbox_command(work, home, argv, runtime)
    cancelled = False
    timed_out = False
    with _open_private_log(stdout_path) as out, _open_private_log(stderr_path) as err:
        process = subprocess.Popen(
            call, stdout=out, stderr=err, start_new_session=True, cwd=str(work)
        )
        while process.poll() is None:
            if cancel_check is not None and cancel_check():
                cancelled = True
                _kill_group(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _kill_group(process)
                break
            time.sleep(0.1)
        exit_code = process.wait()
    raw_stdout = stdout_path.read_bytes()
    stdout = raw_stdout.decode("utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    return {
        "command": command,
        "exit_code": exit_code,
        "cancelled": cancelled,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_sha256": hashlib.sha256(raw_stdout).hexdigest(),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def _read_text(path):
    return Path(path).read_text(encoding="utf-8")


def _make_patch(root, work, before, after):
    chunks = []
    for rel in sorted(set(before) | set(after)):
        old_hash = before.get(rel)
        new_hash = after.get(rel)
        if old_hash == new_hash:
            continue
        old_text = "" if old_hash is None else _read_text(Path(root) / rel)
        new_text = "" if new_hash is None else _read_text(Path(work) / rel)
        from_file = "/dev/null" if old_hash is None else f"a/{rel}"
        to_file = "/dev/null" if new_hash is None else f"b/{rel}"
        chunks.extend(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=from_file,
                tofile=to_file,
                lineterm="\n",
            )
        )
    return "".join(chunks)


def _scrub_secret(paths, secret):
    if not secret:
        return 0
    marker = secret.encode("utf-8")
    redactions = 0
    for path in paths:
        try:
            data = Path(path).read_bytes()
        except OSError:
            continue
        if marker in data:
            redactions += data.count(marker)
            _write_private(path, data.replace(marker, b"[REDACTED]"))
    return redactions


def _failure_receipt(state, failure_class, message, *, state_root, host_execution_attested=False):
    state.update(
        status="FAILURE",
        failure_class=failure_class,
        failure_message=message,
        host_execution_attested=bool(host_execution_attested),
        finished_utc=_utc_now(),
    )
    _write_json_private(Path(state["run_dir"]) / "state.json", state)
    return {
        "status": "FAILURE",
        "decision": None,
        "failure_class": failure_class,
        "failure_message": message,
        "run_id": state["run_id"],
        "task_id": state["task_id"],
        "packet_sha256": state["packet_sha256"],
        "nonce": state["nonce"],
        "created_utc": state["created_utc"],
        "deadline_utc": state["deadline_utc"],
        "model": state["model"],
        "provider": state["provider"],
        "route": state.get("route"),
        "host_execution_attested": bool(host_execution_attested),
        "workspace": state.get("workspace"),
        "worker": state.get("worker"),
        "changed_paths": state.get("changed_paths", []),
        "patch_path": state.get("patch_path"),
        "modified_hashes": state.get("modified_hashes", {}),
        "tests": state.get("tests", {"status": "NOT_RUN", "results": []}),
        "relay": state.get("relay", {}),
        "safety_blocker": bool(state.get("safety_blocker")),
        "origin_baseline": state.get("origin_baseline"),
        "snapshot_baseline": state.get("snapshot_baseline"),
        "runtime": state.get("runtime", {}),
        "observability": state.get("observability", {}),
        "provenance": dev.PROVENANCE_UNTRUSTED,
    }


def _prepare_attempt(root, packet, state_root, run_id, role, ttl, relay_timeout=None):
    runtime = _discover_runtime()
    run_dir = Path(state_root) / run_id
    base = run_dir / "attempt"
    _ensure_private_dir(base)
    relay = None
    try:
        work = _snapshot_workspace(
            root,
            base / "work",
            packet["read_paths"],
            packet["edit_paths"],
            exclude=(state_root,),
        )
        ticket_rel = ".o01b-ticket.md"
        _write_private(work / ticket_rel, _ticket_text(packet), 0o600)
        _verify_snapshot_pins(work, packet)
        _git_baseline(work)
        baseline = _diff_check(work)
        if baseline.get("exit_code") not in (0, 2):
            raise ValueError(
                "fresh snapshot git diff --check exited unexpectedly: "
                f"{baseline.get('exit_code')}"
            )
        baseline_manifest = _workspace_manifest(work)

        model = packet["model"]
        secret = None
        model_entry = None
        if model.startswith("freellmapi/"):
            secret, upstream, model_entry = _load_provider(runtime.provider_config, model)
            timeout = relay_timeout
            if timeout is None:
                timeout = min(MAX_RELAY_TIMEOUT, ttl)
            relay = FreeLLMRelay(
                upstream, secret, model.split("/", 1)[1], timeout=timeout
            ).start()
        config = _opencode_config(
            model, relay.url if relay else None, model_entry
        )
        home = _prepare_home(base / "home", runtime, config)

        shell = base / "bash"
        _write_private(
            shell,
            "#!/bin/sh\n"
            "exec /usr/bin/bwrap --unshare-user --unshare-pid --unshare-net "
            "--die-with-parent --new-session --cap-drop ALL --ro-bind / / "
            "--proc /proc --dev /dev --bind /tmp /tmp -- /bin/bash \"$@\"\n",
            0o700,
        )
        os.chmod(shell, 0o700)
        command = _worker_command(
            runtime, work, home, shell, packet["edit_paths"], ticket_rel, role, model, ttl
        )
        return {
            "runtime": runtime,
            "runtime_observations": _runtime_observations(runtime),
            "run_dir": run_dir,
            "work": work,
            "home": home,
            "relay": relay,
            "secret": secret,
            "worker_command": command,
            "baseline_diff": baseline,
            "baseline_manifest": baseline_manifest,
        }
    except BaseException:
        if relay is not None:
            relay.stop()
        raise


def run_ticket(root, task_id, spec, *, state_dir=None, ttl_seconds=600, role="implement"):
    """Execute one bounded attempt and return an untrusted receipt."""
    _validate_ttl(ttl_seconds)
    if not isinstance(role, str) or not role.strip():
        raise ValueError("role must be a non-empty string")
    root = Path(root)
    packet = dev.make_packet(root, task_id, spec)
    parsed_commands = parse_test_commands(packet["test_commands"])
    if not packet["model"].startswith(SUPPORTED_MODEL_PREFIXES):
        raise ValueError(f"unknown or unsupported model route: {packet['model']!r}")
    packet_sha = _canonical_hash(packet)
    origin_root = _normalized_origin(root)
    state_root = _state_root(state_dir)

    lock_fd = _acquire_root_lock(state_root, origin_root)
    if lock_fd is None:
        return {
            "status": "FAILURE",
            "decision": None,
            "failure_class": FAILURE_LOCK,
            "failure_message": "another writer holds the origin-root lock",
            "task_id": task_id,
            "packet_sha256": packet_sha,
            "host_execution_attested": False,
            "tests": {"status": "NOT_RUN", "results": []},
            "provenance": dev.PROVENANCE_UNTRUSTED,
        }
    try:
        existing = _duplicate(state_root, origin_root, packet_sha)
        if existing is not None:
            return {
                "status": "DUPLICATE",
                "decision": None,
                "task_id": task_id,
                "packet_sha256": packet_sha,
                "original_run_id": existing.get("run_id"),
                "host_execution_attested": False,
                "tests": {"status": "NOT_RUN", "results": []},
                "provenance": dev.PROVENANCE_UNTRUSTED,
            }

        dev.verify_source(root, packet)
        if not _bwrap_path():
            return {
                "status": "FAILURE",
                "decision": None,
                "failure_class": FAILURE_BWRAP,
                "failure_message": "bwrap is not installed; refusing to launch",
                "task_id": task_id,
                "packet_sha256": packet_sha,
                "host_execution_attested": False,
                "tests": {"status": "NOT_RUN", "results": []},
                "provenance": dev.PROVENANCE_UNTRUSTED,
            }

        origin_baseline = _origin_baseline(root)
        run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + uuid.uuid4().hex[:12]
        )
        nonce = uuid.uuid4().hex
        run_dir = _ensure_private_dir(state_root / run_id)
        created = datetime.now(timezone.utc)
        deadline = time.monotonic() + ttl_seconds
        deadline_epoch = created.timestamp() + ttl_seconds
        state = {
            "run_id": run_id,
            "nonce": nonce,
            "task_id": task_id,
            "origin_root": origin_root,
            "created_utc": created.isoformat(),
            "deadline_utc": datetime.fromtimestamp(
                deadline_epoch, timezone.utc
            ).isoformat(),
            "ttl_seconds": ttl_seconds,
            "role": role,
            "model": packet["model"],
            "provider": packet["provider"],
            "route": packet["route"],
            "packet_sha256": packet_sha,
            "source_hashes_before": dict(packet["source_hashes"]),
            "origin_baseline": origin_baseline,
            "status": "RUNNING",
            "run_dir": str(run_dir),
            "expired": False,
            "cancelled": False,
        }
        _write_json_private(run_dir / "state.json", state)

        if _is_cancelled(state_root, run_id):
            return _failure_receipt(
                state, FAILURE_CANCELLED, "cancelled before launch",
                state_root=state_root,
            )
        if time.monotonic() >= deadline:
            state["expired"] = True
            return _failure_receipt(
                state, dev.FAILURE_TIMEOUT, "deadline expired before launch",
                state_root=state_root,
            )
        if origin_baseline.get("is_repo"):
            origin_exit = origin_baseline["diff_check"].get("exit_code")
            if origin_exit not in (0, 2):
                return _failure_receipt(
                    state, FAILURE_DIAGNOSTICS,
                    f"origin git diff --check exited unexpectedly: {origin_exit}",
                    state_root=state_root,
                )

        attempt = None
        worker_ran = False
        tests_ran = 0
        try:
            remaining = max(1, int(deadline - time.monotonic()))
            attempt = _prepare_attempt(
                root, packet, state_root, run_id, role, ttl_seconds,
                relay_timeout=min(MAX_RELAY_TIMEOUT, remaining),
            )
            state["workspace"] = str(attempt["work"])
            state["snapshot_baseline"] = {
                "git_diff_check": attempt["baseline_diff"],
                "manifest_count": len(attempt["baseline_manifest"]),
                "manifest_sha256": _canonical_hash(attempt["baseline_manifest"]),
            }
            state["runtime"] = attempt.get("runtime_observations", {})
            _write_json_private(run_dir / "state.json", state)
            if _is_cancelled(state_root, run_id):
                return _failure_receipt(
                    state, FAILURE_CANCELLED, "cancelled before worker launch",
                    state_root=state_root,
                )
            if time.monotonic() >= deadline:
                state["expired"] = True
                return _failure_receipt(
                    state, dev.FAILURE_TIMEOUT,
                    "deadline expired during preparation",
                    state_root=state_root,
                )

            worker = _run_worker_process(
                attempt["worker_command"],
                work=attempt["work"],
                run_dir=run_dir,
                deadline=deadline,
                cancel_check=lambda: _is_cancelled(state_root, run_id),
                state_root=state_root,
                run_id=run_id,
                timeout_seconds=ttl_seconds,
            )
            worker_ran = True

            raw_artifacts = _collect_opencode_artifacts(attempt["home"], run_dir)
            log_paths = [worker["stdout_path"], worker["stderr_path"]]
            log_paths += raw_artifacts.get("artifact_paths", [])
            if attempt["secret"]:
                redactions = _scrub_secret(log_paths, attempt["secret"])
                if redactions:
                    state["safety_blocker"] = True
                    state["safety_redactions"] = redactions
            artifacts = _collect_opencode_artifacts(attempt["home"], run_dir)
            worker["dispatcher_exit_code"] = worker.get("exit_code")
            worker["dispatcher_token_usage"] = worker.get("token_usage")
            if artifacts.get("token_usage") is not None:
                worker["token_usage"] = artifacts["token_usage"]
            worker["selected_route"] = packet["route"]
            worker["observed_models"] = artifacts.get("observed_models", [])
            worker["actual_weights"] = "UNKNOWN"
            worker["actual_errors"] = artifacts.get("errors", [])
            worker["tool_states"] = artifacts.get("tool_states", [])
            worker["opencode_logs"] = artifacts.get("opencode_logs", [])
            worker["metadata_files"] = artifacts.get("metadata_files", [])
            worker["worker_exit_files"] = artifacts.get("worker_exit_files", [])
            worker["raw_log_paths"] = log_paths
            state["worker"] = worker
            state["observability"] = {
                "selected_route": packet["route"],
                "observed_models": artifacts.get("observed_models", []),
                "actual_weights": "UNKNOWN",
                "actual_worker_exit": worker.get("exit_code"),
                "dispatcher_exit": worker.get("dispatcher_exit_code"),
                "raw_log_paths": log_paths,
                "tool_states": artifacts.get("tool_states", []),
            }
            if attempt["relay"]:
                observations = attempt["relay"].observations()
                state["relay"] = observations
                if observations.get("safety_blocker"):
                    state["safety_blocker"] = True
                observed = sorted(
                    set(worker.get("observed_models", []))
                    | set(observations.get("served_models", []))
                )
                worker["observed_models"] = observed
                state["observability"]["observed_models"] = observed
            _write_json_private(run_dir / "state.json", state)

            if state.get("safety_blocker"):
                return _failure_receipt(
                    state, FAILURE_SAFETY,
                    "secret material or relay safety blocker detected",
                    state_root=state_root,
                    host_execution_attested=False,
                )

            after_manifest = _workspace_manifest(attempt["work"])
            after = {
                rel: after_manifest.get(rel) for rel in packet["source_hashes"]
            }
            state["source_hashes_after"] = after
            scope_error = None
            changed = []
            try:
                changed = dev.check_scope(
                    attempt["baseline_manifest"], after_manifest, packet["edit_paths"]
                )
            except ValueError as exc:
                scope_error = str(exc)
            state["changed_paths"] = changed
            state["modified_hashes"] = {
                rel: after_manifest.get(rel) for rel in changed
            }
            new_diff = _diff_check(attempt["work"])
            state["diff_check"] = {
                "baseline": attempt["baseline_diff"],
                "new": new_diff,
            }
            baseline_output = attempt["baseline_diff"].get("output", "")
            if baseline_output:
                state["preexisting_diagnostics"] = baseline_output
            patch_error = None
            patch = ""
            try:
                patch_before = {
                    rel: packet["source_hashes"].get(rel) for rel in changed
                }
                patch_after = {
                    rel: after_manifest.get(rel) for rel in changed
                }
                patch = _make_patch(
                    root, attempt["work"], patch_before, patch_after
                )
            except (OSError, UnicodeError) as exc:
                patch_error = str(exc)
            patch_path = run_dir / "candidate.patch"
            _write_private(patch_path, patch)
            state["patch_path"] = str(patch_path)
            _write_json_private(run_dir / "state.json", state)

            if _is_cancelled(state_root, run_id) or worker.get("cancelled"):
                state["cancelled"] = True
                return _failure_receipt(
                    state, FAILURE_CANCELLED, "cancelled during worker execution",
                    state_root=state_root, host_execution_attested=False,
                )
            if worker.get("timed_out"):
                state["expired"] = True
                return _failure_receipt(
                    state, dev.FAILURE_TIMEOUT,
                    "worker deadline exceeded; partial workspace preserved",
                    state_root=state_root,
                )
            verify_error = None
            try:
                dev.verify_source(root, packet)
            except ValueError as exc:
                verify_error = str(exc)
            if verify_error:
                return _failure_receipt(
                    state, FAILURE_SOURCE, verify_error,
                    state_root=state_root,
                )
            if scope_error:
                return _failure_receipt(
                    state, dev.FAILURE_SCOPE, scope_error,
                    state_root=state_root,
                )
            if patch_error:
                return _failure_receipt(
                    state, dev.FAILURE_CONTRACT, patch_error,
                    state_root=state_root,
                )
            if new_diff.get("exit_code") not in (0, 2):
                return _failure_receipt(
                    state, FAILURE_DIAGNOSTICS,
                    "new diff-check exited unexpectedly: "
                    f"{new_diff.get('exit_code')}",
                    state_root=state_root,
                )
            if new_diff.get("output") and new_diff.get("output") != baseline_output:
                return _failure_receipt(
                    state, FAILURE_DIAGNOSTICS,
                    "new diff-check diagnostics detected",
                    state_root=state_root,
                )
            if worker.get("effective_exit_code") != 0:
                exit_code = worker.get("effective_exit_code")
                statuses = list(state.get("relay", {}).get("http_statuses", []))
                error_text = "\n".join(worker.get("actual_errors", []))
                stderr_text = _bounded_text(worker.get("stderr_path", ""))
                combined = (error_text + "\n" + stderr_text).strip()
                if 429 in statuses:
                    failure_class = dev.FAILURE_RATE_LIMIT
                elif 401 in statuses or 403 in statuses:
                    failure_class = dev.FAILURE_AUTH
                else:
                    failure_class = dev.classify_failure(
                        exit_code, combined or f"worker exited {exit_code}"
                    )
                message = f"worker exited {exit_code}"
                if combined:
                    message += ": " + combined.splitlines()[-1][:300]
                return _failure_receipt(
                    state, failure_class, message,
                    state_root=state_root,
                )

            results = []
            if time.monotonic() >= deadline:
                state["expired"] = True
                return _failure_receipt(
                    state, dev.FAILURE_TIMEOUT,
                    "deadline expired before independent tests",
                    state_root=state_root,
                )
            for index, (command, argv) in enumerate(
                zip(packet["test_commands"], parsed_commands), start=1
            ):
                if _is_cancelled(state_root, run_id):
                    state["cancelled"] = True
                    return _failure_receipt(
                        state, FAILURE_CANCELLED,
                        "cancelled between worker and tests",
                        state_root=state_root,
                        host_execution_attested=False,
                    )
                result = _run_test_process(
                    argv,
                    command=command,
                    work=attempt["work"],
                    home=attempt["home"],
                    run_dir=run_dir,
                    index=index,
                    runtime=attempt["runtime"],
                    deadline=deadline,
                    cancel_check=lambda: _is_cancelled(state_root, run_id),
                    state_root=state_root,
                    run_id=run_id,
                )
                tests_ran += 1
                results.append(result)
                if result.get("cancelled") or _is_cancelled(state_root, run_id):
                    state["cancelled"] = True
                    state["tests"] = {"status": "NOT_VERIFIED", "results": results}
                    return _failure_receipt(
                        state, FAILURE_CANCELLED,
                        "cancelled during independent tests",
                        state_root=state_root,
                        host_execution_attested=False,
                    )
                if result.get("timed_out"):
                    state["expired"] = True
                    state["tests"] = {"status": "NOT_VERIFIED", "results": results}
                    return _failure_receipt(
                        state, dev.FAILURE_TIMEOUT,
                        "deadline exceeded during independent tests",
                        state_root=state_root,
                        host_execution_attested=False,
                    )
                if result.get("exit_code") != 0:
                    state["tests"] = {"status": "NOT_VERIFIED", "results": results}
                    _write_json_private(run_dir / "state.json", state)
                    return _failure_receipt(
                        state, dev.FAILURE_TEST,
                        f"test command failed: {command}",
                        state_root=state_root,
                        host_execution_attested=False,
                    )
            verified = dev.check_tests(packet["test_commands"], results)
            post_test_error = None
            try:
                dev.verify_source(root, packet)
            except ValueError as exc:
                post_test_error = str(exc)
            if post_test_error:
                state["tests"] = {"status": "NOT_VERIFIED", "results": results}
                return _failure_receipt(
                    state, FAILURE_SOURCE, post_test_error,
                    state_root=state_root,
                )
            after_tests = _workspace_manifest(attempt["work"])
            if after_tests != after_manifest:
                return _failure_receipt(
                    state, dev.FAILURE_SCOPE,
                    "candidate changed while tests were running",
                    state_root=state_root,
                )
            core_receipt = dev.write_record(
                state_root, packet, results, decision=dev.DECISION_PENDING
            )
            state.update(
                status="PENDING_CODEX_REVIEW",
                decision=dev.DECISION_PENDING,
                tests={"status": verified["status"], "results": results},
                host_execution_attested=True,
                finished_utc=_utc_now(),
                core_receipt_path=core_receipt.get("path"),
            )
            _write_json_private(run_dir / "state.json", state)
            return {
                "status": "PENDING_CODEX_REVIEW",
                "decision": dev.DECISION_PENDING,
                "failure_class": None,
                "run_id": run_id,
                "task_id": task_id,
                "nonce": nonce,
                "created_utc": state["created_utc"],
                "deadline_utc": state["deadline_utc"],
                "model": packet["model"],
                "provider": packet["provider"],
                "route": packet["route"],
                "selected_route": packet["route"],
                "observed_models": worker.get("observed_models", []),
                "actual_weights": "UNKNOWN",
                "packet_sha256": packet_sha,
                "host_execution_attested": True,
                "workspace": str(attempt["work"]),
                "worker": worker,
                "changed_paths": changed,
                "modified_hashes": state["modified_hashes"],
                "patch_path": str(patch_path),
                "tests": {"status": verified["status"], "results": results},
                "relay": state.get("relay", {}),
                "safety_blocker": bool(state.get("safety_blocker")),
                "origin_baseline": state.get("origin_baseline"),
                "runtime": state.get("runtime", {}),
                "observability": state.get("observability", {}),
                "core_receipt_path": core_receipt.get("path"),
                "provenance": dev.PROVENANCE_UNTRUSTED,
            }
        except _SourceDrift as exc:
            return _failure_receipt(
                state, FAILURE_SOURCE, str(exc),
                state_root=state_root,
                host_execution_attested=False,
            )
        except Exception as exc:
            state["finished_utc"] = _utc_now()
            return _failure_receipt(
                state, dev.FAILURE_CONTRACT, str(exc),
                state_root=state_root,
                host_execution_attested=worker_ran and tests_ran == len(parsed_commands),
            )
        finally:
            if attempt is not None and attempt["relay"] is not None:
                attempt["relay"].stop()
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _load_spec(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_parser():
    parser = argparse.ArgumentParser(description="O01B private execution adapter")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--state-dir", default=None, help="private runs directory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("task_id")
    run.add_argument("--spec", required=True)
    run.add_argument("--ttl", type=int, default=600)
    run.add_argument("--role", default="implement")
    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("run_id")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "cancel":
            result = cancel_run(args.state_dir, args.run_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        result = run_ticket(
            Path(args.root),
            args.task_id,
            _load_spec(args.spec),
            state_dir=args.state_dir,
            ttl_seconds=args.ttl,
            role=args.role,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("PENDING_CODEX_REVIEW", "DUPLICATE") else 1
    except ValueError as exc:
        print(f"agent_run error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
