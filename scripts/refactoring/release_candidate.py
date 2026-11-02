"""Strict, deterministic offline release inventory and artifact verification."""
from __future__ import annotations

import ast
import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tomllib
import zipfile
import zlib
from pathlib import Path, PurePosixPath

import yaml

SCHEMA = 2
OUTPUTS = {".deploy-release.json", "deploy/release.env", "deploy/app-release.env", "deploy/crypto-release.env"}
INPUTS = ("src", "config", "deploy", "scripts", "dashboard", "main.py", "run_strategy.py",
          "pyproject.toml", "requirements.txt", "Dockerfile", "Dockerfile.crypto", ".dockerignore",
          "docker-compose.yml", "docs/final/01_FINAL_PLAN.md", "docs/final/02_DEVELOPER_TICKETS.md",
          "docs/final/03_ARCHITECTURE_REQUIREMENTS.md", "docs/final/04_COMPLETED_MANIFEST.json",
          "docs/final/05_TASK_STATUS.json", "docs/evidence/refactoring_20261018/server_readonly_snapshot.json")
MAX_RELEASE_FILES = 12000
MAX_RELEASE_FILE_BYTES = 64 * 1024 * 1024
MAX_RELEASE_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_WHEEL_BYTES = 128 * 1024 * 1024
MAX_WHEEL_MEMBERS = 12000
MAX_WHEEL_MEMBER_BYTES = 32 * 1024 * 1024
MAX_WHEEL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
SENSITIVE_PARTS = {".git", ".ssh", ".aws", "secrets", "runtime_data", "node_modules", "__pycache__"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db", ".parquet", ".log"}
SECRET_ASSIGN = re.compile(r"(?im)\b(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|access[_-]?token|private[_-]?key)\b[ \t]*[:=][ \t]*(?:\"([^\"]+)\"|'([^']+)'|([^\s,#]+))")
TOKEN_LITERALS = re.compile(r"(?:\b(?:gh[pousr]_[A-Za-z0-9]{24,}|AKIA[0-9A-Z]{16})\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
BEARER_LITERAL = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")
DOMAIN_PACKAGES = ("core", "feeds", "market", "storage", "strategies", "execution", "portfolio", "runtime", "operations", "timefolio")
IMAGE_CLASSES = ("app", "crypto", "ledger", "browser")
IMAGE_FIELDS = {"id", "tag", "source_revision", "source_sha256", "config_sha256", "input_sha256", "base_id", "command", "user", "pythonpath", "dependency_versions"}
IMAGE_OPTIONAL_FIELDS = {"browser_path"}
WHEEL_FIELDS = {"filename", "sha256", "distribution", "version", "metadata", "wheel_metadata", "record", "record_sha256", "source_file_count", "source_sha256", "verification_scope"}
EXPECTED_IMAGE_CMD = {"app": ["python", "-m", "runtime.cli", "status"],
                      "crypto": ["python", "-m", "runtime.cli", "status"],
                      "ledger": ["python", "-m", "runtime.cli", "status"],
                      "browser": ["python", "-m", "operations.dashboard.timefolio_monitor"]}
EXPECTED_IMAGE_USER = {"app": "vibetrading", "crypto": "10001:10001", "ledger": "10001:10001", "browser": "vibetrading"}
ROLE_IMAGE = {"runtime-check": "app", "collector": "app", "engine": "app", "storage": "app", "telegram": "app",
              "dashboard": "app", "timefolio-order": "browser", "crypto-profile": "crypto", "crypto-ledger": "ledger", "dashboard-monitor": "browser"}


def _run(root: Path, *args: str) -> bytes:
    p = subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return p.stdout


def _dirty_source(root: Path) -> bool:
    raw = _run(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    records = raw.split(b"\0")
    i = 0
    while i < len(records) and records[i]:
        record = records[i]
        i += 1
        if len(record) < 4:
            return True
        status, path = record[:2], record[3:].decode("utf-8", "surrogateescape")
        if status in (b"R ", b" R", b"C ", b" C"):
            i += 1
        if path not in OUTPUTS:
            return True
    return False


def _safe(root: Path, rel: str) -> Path:
    if not isinstance(rel, str) or not rel or "\\" in rel or "\x00" in rel:
        raise ValueError("noncanonical release path")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise ValueError("absolute release path rejected")
    raw_parts = rel.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        raise ValueError("noncanonical or unsafe release path")
    if root.is_symlink():
        raise ValueError("release root symlink rejected")
    absolute_root = root.absolute()
    if any(part.is_symlink() for part in (absolute_root, *absolute_root.parents)):
        raise ValueError("release root ancestor symlink rejected")
    base = root.resolve(strict=True)
    cur = root
    for part in raw_parts:
        cur = cur / part
        if cur.is_symlink():
            raise ValueError("release symlink rejected")
    out = cur
    if not out.resolve(strict=False).is_relative_to(base):
        raise ValueError("release path escapes repository")
    return out


def _load_yaml(root: Path, rel: str) -> dict:
    try:
        with (root / rel).open(encoding="utf-8") as stream:
            result = yaml.safe_load(stream) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ValueError(f"Compose parse failed: {rel}") from None
    if not isinstance(result, dict):
        raise ValueError(f"Compose document shape invalid: {rel}")
    return result


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f"JSON input invalid: {path.name}") from None
    if not isinstance(value, dict):
        raise ValueError(f"JSON input shape invalid: {path.name}")
    return value


def _excluded(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    if any(p in SENSITIVE_PARTS or p.startswith(".env") for p in parts):
        return True
    name = parts[-1].lower()
    if Path(name).suffix in SENSITIVE_SUFFIXES or name.endswith("release.env"):
        return True
    if name in {".deploy-release.json"}:
        return True
    if rel.startswith("dashboard/scripts/test-"):
        return True
    return False


def _tracked_paths(root: Path) -> list[str]:
    names = _run(root, "ls-files", "-z").decode("utf-8", "surrogateescape").split("\0")
    result = []
    for name in filter(None, names):
        if not any(name == top or name.startswith(top + "/") for top in INPUTS):
            continue
        if name in OUTPUTS:
            continue
        if _excluded(name):
            # Credentials/private key material are never silently hidden when tracked.
            if any(part == ".env" or part.startswith(".env.") for part in PurePosixPath(name).parts) or any(part in {"config", "deploy", "secrets"} for part in PurePosixPath(name).parts) or Path(name).suffix.lower() in SENSITIVE_SUFFIXES:
                raise ValueError(f"sensitive tracked release input rejected: {name}")
            continue
        result.append(name)
    result.sort()
    if len(result) > MAX_RELEASE_FILES:
        raise ValueError("release input file count exceeds bound")
    return result


def _reject_workspace_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ValueError("release root symlink rejected")
    # os.walk is bounded to explicit selected roots and prunes ignored/user-data trees.
    for top in INPUTS:
        start = root / top
        if start.is_symlink():
            raise ValueError("release symlink rejected")
        if not start.is_dir():
            continue
        for directory, dirs, files in os.walk(start, followlinks=False):
            dirs[:] = [d for d in dirs if d not in SENSITIVE_PARTS and not d.startswith(".env")]
            for name in dirs + files:
                if (Path(directory) / name).is_symlink():
                    raise ValueError("release symlink rejected")


def _entry(path: Path) -> dict:
    size = path.stat().st_size
    if size > MAX_RELEASE_FILE_BYTES:
        raise ValueError("release input file exceeds size bound")
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_digest(obj: dict) -> str:
    body = {k: v for k, v in obj.items() if k != "manifest_sha256"}
    return _digest_bytes(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def _secret_scan(rel: str, raw: bytes) -> None:
    text = raw.decode("utf-8", "ignore")
    if TOKEN_LITERALS.search(text) or BEARER_LITERAL.search(text):
        raise ValueError(f"possible secret literal in release input: {rel}")
    for match in SECRET_ASSIGN.finditer(text):
        value = next((g for g in match.groups() if g is not None), "")
        value = value.strip("\"'` ")
        key_match = re.match(r"[^A-Za-z0-9_]*([A-Za-z_][A-Za-z0-9_-]*)", match.group(0))
        key = key_match.group(1).lower() if key_match else ""
        if match.group(3) is not None and ("(" in value or value.lower() == key or
                    re.fullmatch(r"[A-Za-z_]\w*\[[0-9]*:[0-9]*\]", value) or
                    (":" in match.group(0) and value.lower() in {"str", "string", "int", "none", "bool", "float"})):
            continue
        if value.startswith(("${", "$env:", "os.getenv(", "os.environ", "getenv(", "env.get(", "data.get(", "settings.env.get(", "String(", "str(", "Path(", "await", "None", "<")):
            continue
        if value.lower() in {"password", "secret", "secret_key", "api_key", "token", "string", "str"} or re.match(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\(", value):
            continue
        if len(value) >= 8 and not value.startswith(("{{", "$(")):
            raise ValueError(f"possible secret literal in release input: {rel}")


def _compose_role_inventory(root: Path) -> dict:
    result = {}
    root_services = _load_yaml(root, "docker-compose.yml").get("services", {})
    def merge_compose(base, override):
        merged = dict(base or {})
        override = dict(override or {})
        for key in ("environment",):
            if key in override:
                merged[key] = {**(merged.get(key) or {}), **(override.get(key) or {})}
        for key in ("volumes", "env_file", "secrets"):
            if key in override:
                values = list(merged.get(key) or [])
                for item in override.get(key) or []:
                    target = str(item).split(":")[1] if key == "volumes" and len(str(item).split(":")) > 1 else str(item)
                    values = [old for old in values if (str(old).split(":")[1] if key == "volumes" and len(str(old).split(":")) > 1 else str(old)) != target]
                    values.append(item)
                merged[key] = values
        merged.update({k: v for k, v in override.items() if k not in ("environment", "volumes", "env_file", "secrets")})
        return merged
    compose_files = {"primary": ("deploy/primary-compose.yml", "ACTIVE_LOCAL"),
                     "timefolio-order": ("deploy/timefolio-order-compose.yml", "BLOCKED_SUBMIT_FALSE"),
                     "crypto-profile": ("deploy/crypto-profile.yml", "LEGACY_UNVERIFIED"),
                     "dashboard-compose": ("deploy/dashboard-compose.yml", "READ_ONLY_MONITOR_UNVERIFIED"),
                     "ls-market-profile": ("deploy/ls-market-profile.yml", "OPTIONAL_UNVERIFIED"),
                     "release1-overlay": ("deploy/release1-overlay.yml", "LEGACY_UNVERIFIED"),
                     "root-compose": ("docker-compose.yml", "ROLLBACK_ONLY")}
    for profile, (relative, state) in compose_files.items():
        document = _load_yaml(root, relative)
        services = document.get("services", {})
        def resolved(service, seen=()):
            body = dict(services[service] or {})
            parent = (body.get("extends") or {}).get("service")
            if parent and parent not in seen and parent in services:
                merged = resolved(parent, seen + (service,))
                merged.update(body)
                body = merged
            return body
        for service in services:
            body = resolved(service)
            if profile in ("crypto-profile", "release1-overlay") and service in root_services:
                body = merge_compose(root_services[service], body)
            if not isinstance(body, dict):
                continue
            command = body.get("command") or body.get("entrypoint")
            if profile in ("primary", "timefolio-order") and not command:
                raise ValueError(f"selected service lacks explicit command: {service}")
            if profile == "primary" and service == "collector" and "--producer" not in command:
                raise ValueError("collector command lacks producer argument")
            if profile == "timefolio-order" and "--no-submit" not in (command or []):
                raise ValueError("Timefolio order role must retain --no-submit")
            if profile == "timefolio-order" and "--submit" in (command or []):
                raise ValueError("Timefolio order role contains an enabling submit argument")
            if profile in ("primary", "timefolio-order") and command and len(command) > 2 and command[0] in ("python", "python3") and command[1] == "-m":
                module_path = root / "src" / Path(*str(command[2]).split("."))
                if not module_path.with_suffix(".py").is_file() and not (module_path / "__init__.py").is_file():
                    raise ValueError(f"selected Compose role module missing: {profile}:{service}")
            image_text = str(body.get("image", ""))
            image_class = ("app" if profile == "primary" else "browser" if "timefolio" in service or "dashboard" in service or profile == "timefolio-order"
                           else "ledger" if "ledger" in service else "crypto" if "crypto" in service or "crypto" in image_text
                           else "external" if image_text.startswith("nats:") else "app")
            name = f"{profile}:{service}"
            result[name] = {"compose": relative, "service": service, "image_class": image_class,
                "profiles": body.get("profiles", []), "command": command,
                "environment_names": sorted(set((body.get("environment") or {}).keys())),
                "env_file_names": body.get("env_file", []), "secret_names": sorted(body.get("secrets", [])),
                "mounts": body.get("volumes", []), "healthcheck": body.get("healthcheck"),
                "restart": body.get("restart", "no"), "stop_grace_period": body.get("stop_grace_period"),
                "mount_source_identity": "compose declaration hashed; runtime device/path marker NOT_OBSERVED; DEPLOY_BLOCKED",
                "submit": (False if profile == "timefolio-order" or (profile == "dashboard-compose" and service == "timefolio") else "UNVERIFIED"),
                "activation": "BLOCKED_EXTERNAL" if profile == "timefolio-order" else "LOCAL_ONLY_BLOCKED" if profile == "primary" else "BLOCKED_UNVERIFIED",
                "inventory_state": state}
    return result


def _selected_compose_guard(root: Path) -> None:
    for relative in ("deploy/primary-compose.yml", "deploy/timefolio-order-compose.yml"):
        try:
            document = yaml.safe_load((root / relative).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError, UnicodeError):
            raise ValueError(f"selected Compose parse failed: {relative}") from None
        strings = []
        def visit(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    strings.append(str(key))
                    visit(item)
            elif isinstance(value, list):
                for item in value: visit(item)
            elif isinstance(value, str):
                strings.append(value)
        visit(document)
        joined = "\n".join(strings).lower()
        if ("nats-init" in joined or "nats_url" in joined or "nats://" in joined or any(v.lower() == "nats" for v in strings) or "src/vibetrading" in joined
                or any(value.lower().startswith("vibetrading.") for value in strings)):
            raise ValueError(f"selected Compose contains forbidden dependency or retired path: {relative}")
        def check_environment(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower() == "environment" and isinstance(child, dict):
                        enabled = str(child.get("TIMEFOLIO_LIVE_SUBMIT_ENABLED", "false")).strip().lower()
                        if enabled in {"1", "true", "yes", "on", "enabled"}:
                            raise ValueError(f"selected Compose enables Timefolio submission: {relative}")
                    check_environment(child)
            elif isinstance(value, list):
                for child in value: check_environment(child)
        check_environment(document)
        for service in (document.get("services") or {}).values():
            command = service.get("command") or [] if isinstance(service, dict) else []
            if relative.endswith("timefolio-order-compose.yml") and ("--submit" in command or "--no-submit" not in command):
                raise ValueError("Timefolio submit mode is not safely disabled")


def _source_identities(root: Path, files: dict) -> tuple[dict, str, dict]:
    source = {k: v["sha256"] for k, v in files.items() if k.startswith("src/") and k.endswith(".py")}
    source_sha = _digest_bytes(json.dumps(source, sort_keys=True, separators=(",", ":")).encode())
    import_graph = {}
    for rel in source:
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            raise ValueError(f"source parse failed: {rel}") from None
        edges = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                edges.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                edges.append(node.module)
        import_graph[rel] = sorted(set(edges))
    graph_digest = _digest_bytes(json.dumps(import_graph, sort_keys=True, separators=(",", ":")).encode())
    return source, source_sha, {"sha256": graph_digest, "edges": import_graph}


def _ledger_schema_version(root: Path) -> int:
    tree = ast.parse((root / "src/execution/execution_ledger.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SCHEMA_VERSION" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
                return node.value.value
    raise ValueError("execution ledger schema version is not a static integer")


def _validate_images(images: dict, *, source_revision: str, source_sha: str, config_sha: str, input_sha: str) -> dict:
    if set(images) != set(IMAGE_CLASSES):
        raise ValueError("image evidence must contain app, crypto, ledger, and browser classes")
    normalized = {}
    for role, item in images.items():
        if not isinstance(item, dict) or not IMAGE_FIELDS.issubset(item) or set(item) - IMAGE_FIELDS - IMAGE_OPTIONAL_FIELDS:
            raise ValueError(f"incomplete image identity evidence: {role}")
        if role != "browser" and "browser_path" in item:
            raise ValueError(f"unexpected image metadata field: {role}")
        for key in ("id", "base_id"):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(item[key])):
                raise ValueError(f"invalid immutable {key}: {role}")
        if item["source_revision"] != source_revision or item["source_sha256"] != source_sha:
            raise ValueError(f"image source label mismatch: {role}")
        if item["config_sha256"] != config_sha or item["input_sha256"] != input_sha:
            raise ValueError(f"image config/input label mismatch: {role}")
        if item["command"] != EXPECTED_IMAGE_CMD[role] or item["user"] != EXPECTED_IMAGE_USER[role] or item["pythonpath"] != "/app/src":
            raise ValueError(f"image runtime identity mismatch: {role}")
        if item["tag"] != f"vibetrading-{role}:rel01-{input_sha[:20]}":
            raise ValueError(f"image tag identity mismatch: {role}")
        if not isinstance(item["dependency_versions"], dict) or not item["dependency_versions"]:
            raise ValueError(f"resolved dependency versions missing: {role}")
        if len(item["dependency_versions"]) > 2000 or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", str(k)) or
                not re.fullmatch(r"[A-Za-z0-9.+!_-]{1,128}", str(v)) for k, v in item["dependency_versions"].items()):
            raise ValueError(f"invalid dependency version inventory: {role}")
        if role == "browser" and item.get("browser_path") != "/opt/dashboard-browsers":
            raise ValueError("browser image path identity missing")
        normalized[role] = dict(item)
    return normalized


def verify_wheel(root: Path, wheel_path: Path, expected_sha256: str) -> dict:
    """Bounded archive verification against RECORD, project metadata and src bytes."""
    if root.is_symlink():
        raise ValueError("release root symlink rejected")
    _reject_workspace_symlinks(root)
    if wheel_path.is_symlink() or not wheel_path.is_file():
        raise ValueError("wheel must be a regular non-symlink file")
    if wheel_path.stat().st_size > MAX_WHEEL_BYTES:
        raise ValueError("wheel archive exceeds size bound")
    wheel_bytes = wheel_path.read_bytes()
    digest = _digest_bytes(wheel_bytes)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or digest != expected_sha256:
        raise ValueError("wheel archive digest mismatch")
    try:
        return _verify_wheel_archive(root, wheel_path, wheel_bytes, digest)
    except ValueError:
        raise
    except (zipfile.BadZipFile, OSError, UnicodeError, csv.Error, KeyError, IndexError, ValueError, zlib.error, EOFError):
        raise ValueError("wheel archive verification failed") from None


def _verify_wheel_archive(root: Path, wheel_path: Path, wheel_bytes: bytes, digest: str) -> dict:
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
        infos = archive.infolist()
        if not 1 <= len(infos) <= MAX_WHEEL_MEMBERS:
            raise ValueError("wheel member count exceeds bound")
        if sum(info.file_size for info in infos) > MAX_WHEEL_UNCOMPRESSED_BYTES:
            raise ValueError("wheel uncompressed size exceeds bound")
        names = set()
        for info in infos:
            name = info.filename
            parts = name.split("/")
            if info.is_dir() or not name or name.startswith("/") or "\\" in name or any(p in ("", ".", "..") for p in parts):
                raise ValueError("unsafe wheel path")
            if info.file_size > MAX_WHEEL_MEMBER_BYTES:
                raise ValueError("wheel member exceeds size bound")
            if name in names:
                raise ValueError("duplicate wheel member")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("wheel symlink rejected")
            if ".data/" in name:
                raise ValueError("wheel .data payload is not allowed")
            if ".dist-info/" not in name and not name.endswith(".py"):
                raise ValueError("wheel non-source payload is not allowed")
            if stat.S_IMODE(info.external_attr >> 16) & 0o111:
                raise ValueError("wheel executable payload is not allowed")
            names.add(name)
        record_names = [n for n in names if n.endswith(".dist-info/RECORD")]
        metadata_names = [n for n in names if n.endswith(".dist-info/METADATA")]
        wheel_specs = [n for n in names if n.endswith(".dist-info/WHEEL")]
        if len(record_names) != 1 or len(metadata_names) != 1 or len(wheel_specs) != 1:
            raise ValueError("wheel metadata or RECORD is ambiguous")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
        if not re.search(r"(?m)^Name:\s*vibetrading\s*$", metadata, re.I):
            raise ValueError("wheel distribution name mismatch")
        version_match = re.search(r"(?m)^Version:\s*([^\s]+)\s*$", metadata)
        try:
            project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        except (tomllib.TOMLDecodeError, UnicodeError, KeyError):
            raise ValueError("project metadata parse failed") from None
        if not version_match or version_match.group(1) != str(project.get("version", "")):
            raise ValueError("wheel version metadata mismatch")
        if metadata_names[0].rsplit("/", 1)[0] != f"vibetrading-{version_match.group(1)}.dist-info":
            raise ValueError("wheel metadata path/version mismatch")
        if "Wheel-Version:" not in archive.read(wheel_specs[0]).decode("utf-8"):
            raise ValueError("wheel WHEEL metadata missing")
        record_rows = list(csv.reader(io.StringIO(archive.read(record_names[0]).decode("utf-8"))))
        if any(len(row) != 3 for row in record_rows):
            raise ValueError("wheel RECORD row width invalid")
        rows = {row[0]: row for row in record_rows}
        if len(rows) != len(record_rows) or set(rows) != names:
            raise ValueError("wheel RECORD paths mismatch")
        for name, row in rows.items():
            if name == record_names[0]:
                if row[1] or row[2]:
                    raise ValueError("wheel RECORD self-row must omit hash and size")
                continue
            if not row[1] or not row[2]:
                raise ValueError("wheel RECORD hash and size are required")
            algorithm, _, expected = row[1].partition("=")
            member = archive.read(name)
            if algorithm != "sha256" or base64.urlsafe_b64encode(hashlib.sha256(member).digest()).decode().rstrip("=") != expected:
                raise ValueError("wheel RECORD content hash mismatch")
            if not row[2].isdigit() or int(row[2]) != len(member):
                raise ValueError("wheel RECORD size mismatch")
        source_files = {}
        for package in DOMAIN_PACKAGES:
            package_root = root / "src" / package
            if package_root.is_symlink() or not package_root.is_dir():
                raise ValueError("canonical source package root missing or symlinked")
            for path in sorted(package_root.rglob("*.py")):
                if path.is_symlink():
                    raise ValueError("canonical source symlink rejected")
                rel = path.relative_to(root / "src").as_posix()
                source_files[rel] = path.read_bytes()
        if len(source_files) > MAX_RELEASE_FILES or sum(map(len, source_files.values())) > MAX_RELEASE_TOTAL_BYTES:
            raise ValueError("wheel source set exceeds bounds")
        wheel_py = {n: archive.read(n) for n in names if n.endswith(".py") and ".dist-info/" not in n}
        if any(n.split("/", 1)[0] in {"vibetrading", "src"} for n in wheel_py):
            raise ValueError("wheel old or shadow namespace rejected")
        if wheel_py != source_files:
            raise ValueError("wheel package source set/content mismatch")
        return {"filename": wheel_path.name, "sha256": digest, "distribution": "vibetrading",
                "version": version_match.group(1), "metadata": metadata_names[0], "wheel_metadata": wheel_specs[0], "record": record_names[0],
                "record_sha256": _digest_bytes(archive.read(record_names[0])),
                "source_file_count": len(source_files), "source_sha256": _digest_bytes(b"".join(source_files[k] for k in sorted(source_files))),
                "verification_scope": "WHEEL_ARCHIVE_RECORD_AND_SOURCE_REOPENED"}


def _validate_wheel_receipt(root: Path, evidence: dict) -> dict:
    if not isinstance(evidence, dict) or set(evidence) != WHEEL_FIELDS:
        raise ValueError("wheel receipt fields are incomplete or unexpected")
    if (not isinstance(evidence["filename"], str) or Path(evidence["filename"]).name != evidence["filename"]
            or not evidence["filename"].endswith(".whl") or evidence["distribution"] != "vibetrading"):
        raise ValueError("wheel receipt identity mismatch")
    for key in ("sha256", "record_sha256", "source_sha256"):
        if not isinstance(evidence[key], str) or not re.fullmatch(r"[0-9a-f]{64}", evidence[key]):
            raise ValueError(f"wheel receipt digest invalid: {key}")
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    except (tomllib.TOMLDecodeError, UnicodeError, KeyError):
        raise ValueError("project metadata parse failed") from None
    if evidence["version"] != str(project.get("version", "")):
        raise ValueError("wheel receipt version does not match project")
    if not isinstance(evidence["source_file_count"], int) or isinstance(evidence["source_file_count"], bool):
        raise ValueError("wheel receipt source count invalid")
    sources = {}
    for package in DOMAIN_PACKAGES:
        package_root = root / "src" / package
        if package_root.is_symlink() or not package_root.is_dir():
            raise ValueError("canonical source package root missing or symlinked")
        for path in sorted(package_root.rglob("*.py")):
            if path.is_symlink():
                raise ValueError("canonical source symlink rejected")
            if path.stat().st_size > MAX_RELEASE_FILE_BYTES:
                raise ValueError("canonical source file exceeds size bound")
            sources[path.relative_to(root / "src").as_posix()] = path.read_bytes()
            if len(sources) > MAX_RELEASE_FILES or sum(len(v) for v in sources.values()) > MAX_RELEASE_TOTAL_BYTES:
                raise ValueError("canonical source set exceeds bounds")
    source_hash = _digest_bytes(b"".join(sources[k] for k in sorted(sources)))
    if evidence["source_file_count"] != len(sources) or evidence["source_sha256"] != source_hash:
        raise ValueError("wheel receipt source identity mismatch")
    for key, suffix in (("metadata", ".dist-info/METADATA"), ("wheel_metadata", ".dist-info/WHEEL"), ("record", ".dist-info/RECORD")):
        value = evidence[key]
        if not isinstance(value, str) or not value.endswith(suffix) or any(p in ("", ".", "..") for p in value.split("/")):
            raise ValueError(f"wheel receipt member identity invalid: {key}")
    if len({evidence[k].rsplit("/", 1)[0] for k in ("metadata", "wheel_metadata", "record")}) != 1:
        raise ValueError("wheel receipt metadata directory mismatch")
    dist_info = f"vibetrading-{evidence['version']}.dist-info"
    if evidence["metadata"].rsplit("/", 1)[0] != dist_info:
        raise ValueError("wheel receipt metadata version path mismatch")
    allowed_scope = {"WHEEL_ARCHIVE_RECORD_AND_SOURCE_REOPENED", "STRUCTURAL_RECEIPT_ONLY"}
    if evidence["verification_scope"] not in allowed_scope:
        raise ValueError("wheel receipt verification scope invalid")
    return dict(evidence)


def freeze(root: Path, *, expected_head: str | None = None, images: dict | None = None,
           wheel: dict | None = None, server_snapshot: dict | None = None) -> dict:
    if root.is_symlink():
        raise ValueError("release root symlink rejected")
    root = root.resolve(strict=True)
    head = _run(root, "rev-parse", "HEAD").decode().strip()
    if expected_head and expected_head != head:
        raise ValueError("stale source HEAD")
    _reject_workspace_symlinks(root)
    if _dirty_source(root):
        raise ValueError("release freeze requires clean tracked and untracked workspace")
    files = {}
    total_bytes = 0
    for rel in _tracked_paths(root):
        p = _safe(root, rel)
        if not p.is_file():
            continue
        size = p.stat().st_size
        if size > MAX_RELEASE_FILE_BYTES:
            raise ValueError("release input file exceeds size bound")
        total_bytes += size
        if total_bytes > MAX_RELEASE_TOTAL_BYTES:
            raise ValueError("release input total size exceeds bound")
        raw = p.read_bytes()
        _secret_scan(rel, raw)
        files[rel] = {"sha256": _digest_bytes(raw), "size": size}
    required = {"docker-compose.yml", "Dockerfile", "Dockerfile.crypto", "pyproject.toml", "requirements.txt",
                "deploy/primary-compose.yml", "deploy/timefolio-order-compose.yml", "deploy/vibetrading.service",
                "deploy/vibetrading.cron.example"}
    if not required.issubset(files):
        raise ValueError("required release assembly input missing")
    _selected_compose_guard(root)
    roles = _compose_role_inventory(root)
    source_files, source_sha, graph = _source_identities(root, files)
    config_files = {k: v["sha256"] for k, v in files.items() if k.startswith("config/")}
    config_sha = _digest_bytes(json.dumps(config_files, sort_keys=True, separators=(",", ":")).encode())
    input_sha = _digest_bytes(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())
    image_evidence = _validate_images(images, source_revision=head, source_sha=source_sha,
                                      config_sha=config_sha, input_sha=input_sha) if images else {}
    wheel_verified = False
    wheel_evidence = {}
    if wheel:
        if "path" in wheel and "expected_sha256" in wheel:
            wheel_evidence = verify_wheel(root, Path(wheel["path"]), wheel["expected_sha256"])
            wheel_verified = True
        else:
            receipt = dict(wheel)
            receipt["verification_scope"] = "STRUCTURAL_RECEIPT_ONLY"
            wheel_evidence = _validate_wheel_receipt(root, receipt)
    snapshot_path = root / "docs/evidence/refactoring_20261018/server_readonly_snapshot.json"
    historical = load_json(snapshot_path) if snapshot_path.is_file() else {}
    historical_map = {
        "vibetrading-telegram-1": "primary:telegram", "vibetrading-collectors-1": "primary:collector",
        "vibetrading-storage-1": "BLOCKED_UNMAPPED_LEGACY_STORAGE", "vibetrading-maintenance-1": "BLOCKED_UNMAPPED_LEGACY_MAINTENANCE",
        "vibetrading-etf-mock-1": "BLOCKED_UNMAPPED_LEGACY_ETF_EXECUTION", "vibetrading-etf-csv-sync-1": "BLOCKED_UNMAPPED_LEGACY_CSV_SYNC",
        "vibetrading-monitor-timefolio-1": "dashboard-compose:timefolio", "vibetrading-monitor-bridge-1": "dashboard-compose:bridge",
        "vibetrading-crypto-storage-1": "BLOCKED_LEGACY_crypto-profile:crypto-storage",
        "vibetrading-crypto-maintenance-1": "BLOCKED_LEGACY_crypto-profile:crypto-maintenance",
        "vibetrading-crypto-collector-arcus-1": "BLOCKED_LEGACY_crypto-profile:crypto-collector-arcus",
        "vibetrading-crypto-collector-binance-1": "BLOCKED_LEGACY_crypto-profile:crypto-collector-binance",
        "vibetrading-crypto-collector-grvt-1": "BLOCKED_LEGACY_crypto-profile:crypto-collector-grvt",
        "vibetrading-nats-1": "BLOCKED_ROLLBACK_ONLY_NATS"}
    historic_roles = []
    for c in historical.get("containers", []):
        name = str(c.get("name", "")).lstrip("/")
        historic_roles.append({"container": c.get("name"), "image": c.get("image"),
            "observed_utc": historical.get("observed_utc"),
            "mapping": historical_map.get(name, "BLOCKED_UNMAPPED_HISTORICAL"),
            "status": "HISTORICAL_UNVERIFIED_CURRENT_SERVER_UNKNOWN"})
    state = ("CANDIDATE_PENDING_INDEPENDENT_IMAGE_AND_COMPOSE_VERIFICATION" if image_evidence and wheel_verified
             else "DEPLOY_BLOCKED")
    supplied_snapshot = None
    if server_snapshot is not None:
        supplied_snapshot = {"sha256": _digest_bytes(json.dumps(server_snapshot, sort_keys=True, separators=(",", ":")).encode()),
                             "observed_utc": server_snapshot.get("observed_utc") if isinstance(server_snapshot, dict) else None,
                             "roles": "UNKNOWN_DEPLOY_BLOCKED"}
    result = {"schema_version": SCHEMA, "source_head": head, "source_sha256": source_sha,
              "config_sha256": config_sha, "input_sha256": input_sha,
              "files": files, "source_files": source_files, "import_graph": graph,
              "versioned_config_hashes": config_files,
              "dependency_inventory": {"requirements_sha256": files.get("requirements.txt", {}).get("sha256"),
                                        "lock_status": "NOT_A_LOCK; no dependency resolution is claimed",
                                        "resolved_image_versions": {k: v["dependency_versions"] for k, v in image_evidence.items()}},
              "execution_ledger_schema_version": _ledger_schema_version(root), "roles": roles,
              "images": image_evidence, "wheel": wheel_evidence,
              "server_inventory": {"state": "DEPLOY_BLOCKED_CURRENT_SERVER_UNKNOWN", "provided_snapshot": supplied_snapshot,
                                   "historical_observed_utc": historical.get("observed_utc"), "roles": historic_roles},
              "profiles": {"primary": "deploy/primary-compose.yml:NATS_FREE", "root_compose": "ROLLBACK_ONLY",
                           "crypto_profile": "LEGACY_UNVERIFIED", "dashboard_compose": "READ_ONLY_MONITOR_UNVERIFIED",
                           "timefolio_order": "BLOCKED_SUBMIT_FALSE", "systemd_service": "ROLLBACK_ONLY_NATS_INIT",
                           "cron_example": "HISTORICAL_UNVERIFIED", "rollback_hashes_in_files": True},
              "secret_values_included": False, "manifest_digest_scope": "TAMPER_EVIDENT_CONSISTENCY_NOT_SIGNATURE",
              "deployment_state": state}
    result["manifest_sha256"] = canonical_digest(result)
    return result


def verify(root: Path, manifest: dict, *, expected_head: str | None = None,
           images: dict | None = None, wheel: dict | None = None,
           server_snapshot: dict | None = None, expected_manifest_sha256: str | None = None) -> None:
    if manifest.get("manifest_sha256") != canonical_digest(manifest):
        raise ValueError("candidate manifest digest mismatch")
    if expected_manifest_sha256 and manifest.get("manifest_sha256") != expected_manifest_sha256:
        raise ValueError("candidate manifest trust-anchor mismatch")
    if images is not None and images != manifest.get("images"):
        raise ValueError("image evidence mismatch")
    if wheel is not None:
        if "path" in wheel:
            checked = verify_wheel(root, Path(wheel["path"]), wheel.get("expected_sha256") or manifest.get("wheel", {}).get("sha256", ""))
            if {k: v for k, v in checked.items() if k != "filename"} != {k: v for k, v in manifest.get("wheel", {}).items() if k != "filename"}:
                raise ValueError("wheel evidence mismatch")
        else:
            raise ValueError("verified wheel archive path is required")
    elif manifest.get("wheel"):
        raise ValueError("verified wheel archive path is required")
    actual = freeze(root, expected_head=expected_head, images=manifest.get("images"),
                    wheel=wheel if wheel and "path" in wheel else None, server_snapshot=server_snapshot)
    for key in ("source_head", "source_sha256", "config_sha256", "input_sha256", "files", "source_files", "import_graph", "versioned_config_hashes", "roles", "profiles", "execution_ledger_schema_version", "dependency_inventory", "server_inventory", "deployment_state"):
        if actual.get(key) != manifest.get(key):
            raise ValueError(f"candidate source/input identity mismatch: {key}")


def write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
