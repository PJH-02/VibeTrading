from __future__ import annotations

import importlib.util
import json
import base64
import csv
import hashlib
import io
import subprocess
import zipfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from scripts.refactoring import release_candidate as rc
from scripts import build_release_images as builder


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for rel in ("src/runtime", "src/operations/dashboard", "src/timefolio", "src/execution", "src/core", "src/feeds", "src/market", "src/storage", "src/strategies", "src/portfolio", "src/operations", "config", "deploy", "scripts", "dashboard"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    modules = {
        "src/runtime/local_runtime.py": "# local\n", "src/runtime/cli.py": "# cli\n",
        "src/operations/dashboard/timefolio_monitor.py": "# monitor\n", "src/timefolio/runner.py": "# runner\n",
        "src/execution/execution_ledger.py": "SCHEMA_VERSION = 4\n",
    }
    for name, body in modules.items():
        (root / name).write_text(body)
    (root / "Dockerfile").write_text("FROM scratch\n")
    (root / "Dockerfile.crypto").write_text("FROM scratch\n")
    (root / ".dockerignore").write_text(".git\n")
    (root / "pyproject.toml").write_text("[project]\nname='vibetrading'\nversion='0.0.0'\n")
    (root / "requirements.txt").write_text("# requirements\n")
    (root / "main.py").write_text("pass\n")
    (root / "run_strategy.py").write_text("pass\n")
    (root / "config/runtime.yaml").write_text("safe: true\n")
    (root / "deploy/primary-compose.yml").write_text("""services:
  runtime-check: {command: [python, -m, runtime.local_runtime, check, --config, /run/config/local.json]}
  collector: {command: [python, -m, runtime.local_runtime, collector, --config, /run/config/local.json, --producer, '${PRODUCER}']}
""")
    (root / "deploy/timefolio-order-compose.yml").write_text("""services:
  engine: {profiles: [timefolio-order], command: [python, -m, runtime.cli, timefolio-strategy, --local-config, /run/config/local.json, --no-submit]}
""")
    for name in ("crypto-profile.yml", "dashboard-compose.yml", "ls-market-profile.yml", "release1-overlay.yml"):
        (root / "deploy" / name).write_text("services: {}\n")
    (root / "deploy/vibetrading.service").write_text("ExecStart=/usr/bin/nats-init\n")
    (root / "deploy/vibetrading.cron.example").write_text("# historical\n")
    (root / "deploy/Dockerfile.crypto-ledger").write_text("FROM scratch\n")
    (root / "deploy/Dockerfile.dashboard-timefolio").write_text("FROM scratch\n")
    (root / "docker-compose.yml").write_text("services: {legacy: {command: [python, old.py]}}\n")
    (root / "scripts/builder.py").write_text("pass\n")
    for name in ("01_FINAL_PLAN.md", "02_DEVELOPER_TICKETS.md", "03_ARCHITECTURE_REQUIREMENTS.md",
                 "04_COMPLETED_MANIFEST.json", "05_TASK_STATUS.json"):
        (root / "docs/final" / name).parent.mkdir(parents=True, exist_ok=True)
        (root / "docs/final" / name).write_text("{}");
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
    return root


def test_freeze_verify_deterministic_and_submit_blocked(tmp_path):
    root = repo(tmp_path)
    first = rc.freeze(root)
    second = rc.freeze(root)
    assert first == second
    assert first["roles"]["timefolio-order:engine"]["submit"] is False
    assert first["roles"]["primary:collector"]["command"][-2:] == ["--producer", "${PRODUCER}"]
    assert first["server_inventory"]["state"] == "DEPLOY_BLOCKED_CURRENT_SERVER_UNKNOWN"
    rc.verify(root, first)
    changed = dict(first)
    changed["source_head"] = "0" * 40
    with pytest.raises(ValueError, match="manifest digest"):
        rc.verify(root, changed)


def test_generated_release_outputs_stay_outside_candidate_inputs(tmp_path):
    root = repo(tmp_path)
    path = root / "deploy/release.env"
    path.write_text("LOCAL_PRIVATE_VALUE=not-an-input\n")
    subprocess.run(["git", "add", "deploy/release.env"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "release output fixture"], cwd=root, check=True)
    candidate = rc.freeze(root)
    assert "deploy/release.env" not in candidate["files"]
    assert "LOCAL_PRIVATE_VALUE" not in json.dumps(candidate)


@pytest.mark.parametrize("operation", ["modify", "delete", "untracked"])
def test_verify_rejects_input_drift(tmp_path, operation):
    root = repo(tmp_path)
    frozen = rc.freeze(root)
    target = root / "config/runtime.yaml"
    if operation == "modify":
        target.write_text("safe: false\n")
    elif operation == "delete":
        target.unlink()
    else:
        (root / "config/new.yaml").write_text("x: 1\n")
    with pytest.raises(ValueError):
        rc.verify(root, frozen)


def test_rejects_symlink_and_path_traversal(tmp_path):
    root = repo(tmp_path)
    with pytest.raises(ValueError, match="unsafe"):
        rc._safe(root, "../escape")
    (root / "config/link.yaml").symlink_to(root / "config/runtime.yaml")
    with pytest.raises(ValueError, match="symlink"):
        rc.freeze(root)


def test_rejects_secret_without_echoing_value(tmp_path):
    root = repo(tmp_path)
    secret = "abcDEF0123456789SUPERSECRET"
    (root / "config/runtime.yaml").write_text(f"api_key: {secret}\n")
    subprocess.run(["git", "add", "config/runtime.yaml"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    with pytest.raises(ValueError) as error:
        rc.freeze(root)
    assert secret not in str(error.value)
    assert "config/runtime.yaml" in str(error.value)


def test_identity_checks_and_nats_dependency_guard(tmp_path):
    root = repo(tmp_path)
    (root / "deploy/primary-compose.yml").write_text("services:\n  app:\n    depends_on: [nats-init]\n")
    subprocess.run(["git", "add", "deploy/primary-compose.yml"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "injected NATS"], cwd=root, check=True)
    with pytest.raises(ValueError, match="forbidden dependency"):
        rc.freeze(root)


def test_committed_stale_bind_injection_rejected(tmp_path):
    root = repo(tmp_path)
    compose = root / "deploy/primary-compose.yml"
    compose.write_text(compose.read_text() + "\n  stale: {command: [python, -m, vibetrading.runtime]}\n")
    subprocess.run(["git", "add", str(compose)], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "injected old bind"], cwd=root, check=True)
    with pytest.raises(ValueError, match="forbidden dependency or retired path"):
        rc.freeze(root)


@pytest.mark.parametrize("command", [
    "[python, -m, runtime.cli, timefolio-strategy, --no-submit, --submit]",
    "[python, -m, runtime.cli, timefolio-strategy, --no-submit]",
])
def test_timefolio_submit_flag_and_true_env_rejected(tmp_path, command):
    root = repo(tmp_path)
    overlay = root / "deploy/timefolio-order-compose.yml"
    tail = "    environment: {TIMEFOLIO_LIVE_SUBMIT_ENABLED: 'true'}\n" if "--submit" not in command else ""
    overlay.write_text(f"services:\n  engine:\n    profiles: [timefolio-order]\n    command: {command}\n" + tail)
    subprocess.run(["git", "add", "deploy/timefolio-order-compose.yml"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "unsafe timefolio fixture"], cwd=root, check=True)
    with pytest.raises(ValueError, match="Timefolio"):
        rc.freeze(root)


def test_stale_head_rejected(tmp_path):
    root = repo(tmp_path)
    with pytest.raises(ValueError, match="stale"):
        rc.freeze(root, expected_head="0" * 40)


def test_source_ast_parse_failure_is_not_silently_omitted(tmp_path):
    root = repo(tmp_path)
    path = root / "src/runtime/local_runtime.py"
    path.write_text("def broken(:\n")
    subprocess.run(["git", "add", str(path)], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "broken source fixture"], cwd=root, check=True)
    with pytest.raises(ValueError, match="source parse failed: src/runtime/local_runtime.py"):
        rc.freeze(root)


def test_derived_roles_and_ledger_metadata_are_recomputed(tmp_path):
    root = repo(tmp_path)
    candidate = rc.freeze(root)
    tampered = dict(candidate)
    tampered["roles"] = {**candidate["roles"], "primary:collector": {"command": ["unsafe"], "submit": True}}
    tampered["manifest_sha256"] = rc.canonical_digest(tampered)
    with pytest.raises(ValueError, match="roles"):
        rc.verify(root, tampered)
    tampered = dict(candidate)
    tampered["execution_ledger_schema_version"] = 99
    tampered["manifest_sha256"] = rc.canonical_digest(tampered)
    with pytest.raises(ValueError, match="execution_ledger_schema_version"):
        rc.verify(root, tampered)
    rc.verify(root, candidate, expected_manifest_sha256=candidate["manifest_sha256"])
    with pytest.raises(ValueError, match="trust-anchor"):
        rc.verify(root, candidate, expected_manifest_sha256="0"*64)


def test_supplied_server_snapshot_records_unknown_roles(tmp_path):
    root = repo(tmp_path)
    snapshot = {"observed_utc": "2026-10-31T00:00:00Z", "containers": [{"name": "unmapped"}]}
    candidate = rc.freeze(root, server_snapshot=snapshot)
    assert candidate["server_inventory"]["provided_snapshot"]["roles"] == "UNKNOWN_DEPLOY_BLOCKED"
    rc.verify(root, candidate, server_snapshot=snapshot)
    with pytest.raises(ValueError, match="server_inventory"):
        rc.verify(root, candidate, server_snapshot={"observed_utc": "different"})


@pytest.mark.parametrize("name", ["a//b", "a/./b", "a\\b", "../x", "/tmp/x"])
def test_path_aliases_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        rc._safe(tmp_path, name)


def test_symlink_root_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="root symlink"):
        rc._safe(link, "x")


def test_role_commands_are_derived_from_compose(tmp_path):
    root = repo(tmp_path)
    rows = rc._compose_role_inventory(root)
    assert rows["primary:collector"]["command"][-2:] == ["--producer", "${PRODUCER}"]
    assert rows["timefolio-order:engine"]["command"][-1] == "--no-submit"
    assert rows["primary:collector"]["image_class"] == "app"


def test_input_inventory_size_limit_is_enforced(tmp_path, monkeypatch):
    root = repo(tmp_path)
    monkeypatch.setattr(rc, "MAX_RELEASE_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="size bound"):
        rc.freeze(root)


def _make_wheel(root: Path, path: Path):
    entries = {p.relative_to(root / "src").as_posix(): p.read_bytes()
               for package in rc.DOMAIN_PACKAGES for p in sorted((root / "src" / package).rglob("*.py"))}
    prefix = "vibetrading-0.0.0.dist-info/"
    entries[prefix + "METADATA"] = b"Metadata-Version: 2.1\nName: vibetrading\nVersion: 0.0.0\n\n"
    entries[prefix + "WHEEL"] = b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    rows = []
    for name, content in entries.items():
        encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        rows.append((name, "sha256=" + encoded, str(len(content))))
    rows.append((prefix + "RECORD", "", ""))
    out = io.StringIO(); csv.writer(out).writerows(rows)
    entries[prefix + "RECORD"] = out.getvalue().encode()
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items(): zf.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_wheel_record_and_source_verification(tmp_path):
    root = repo(tmp_path)
    wheel = tmp_path / "candidate.whl"
    digest = _make_wheel(root, wheel)
    result = rc.verify_wheel(root, wheel, digest)
    assert result["source_file_count"] == sum(1 for p in (root / "src").rglob("*.py"))
    assert result["record_sha256"]
    with zipfile.ZipFile(wheel, "a") as zf: zf.writestr("runtime/added.py", b"pass\n")
    with pytest.raises(ValueError): rc.verify_wheel(root, wheel, hashlib.sha256(wheel.read_bytes()).hexdigest())


def test_wheel_rejects_source_tamper_and_archive_traversal(tmp_path):
    root = repo(tmp_path)
    wheel = tmp_path / "candidate.whl"
    _make_wheel(root, wheel)
    with zipfile.ZipFile(wheel) as zf:
        entries = {name: zf.read(name) for name in zf.namelist()}
    first = next(name for name in entries if name.startswith("runtime/") and name.endswith(".py"))
    entries[first] = b"tampered source\n"
    with zipfile.ZipFile(wheel, "w") as zf:
        for name, data in entries.items(): zf.writestr(name, data)
    with pytest.raises(ValueError, match="RECORD"):
        rc.verify_wheel(root, wheel, hashlib.sha256(wheel.read_bytes()).hexdigest())
    with zipfile.ZipFile(wheel, "a") as zf: zf.writestr("../escape.py", b"pass\n")
    with pytest.raises(ValueError, match="unsafe"):
        rc.verify_wheel(root, wheel, hashlib.sha256(wheel.read_bytes()).hexdigest())


def test_wheel_requires_every_record_hash_and_size_and_is_bounded(tmp_path, monkeypatch):
    root = repo(tmp_path)
    wheel = tmp_path / "candidate.whl"
    _make_wheel(root, wheel)
    with zipfile.ZipFile(wheel) as zf:
        entries = {name: zf.read(name) for name in zf.namelist()}
    record = next(n for n in entries if n.endswith("/RECORD"))
    rows = list(csv.reader(io.StringIO(entries[record].decode())))
    rows[0][1] = ""; rows[0][2] = ""
    out = io.StringIO(); csv.writer(out).writerows(rows); entries[record] = out.getvalue().encode()
    with zipfile.ZipFile(wheel, "w") as zf:
        for name, body in entries.items(): zf.writestr(name, body)
    with pytest.raises(ValueError, match="RECORD hash"):
        rc.verify_wheel(root, wheel, hashlib.sha256(wheel.read_bytes()).hexdigest())
    monkeypatch.setattr(rc, "MAX_WHEEL_BYTES", 1)
    with pytest.raises(ValueError, match="size bound"):
        rc.verify_wheel(root, wheel, hashlib.sha256(wheel.read_bytes()).hexdigest())


def test_images_require_four_full_identity_records():
    fields = {"id": "sha256:" + "1"*64, "tag": "vibetrading-app:rel01-" + "4"*20, "source_revision": "a"*40, "source_sha256": "2"*64,
              "config_sha256": "3"*64, "input_sha256": "4"*64, "base_id": "sha256:" + "5"*64,
              "command": ["python", "-m", "runtime.cli", "status"], "user": "vibetrading", "pythonpath": "/app/src",
              "dependency_versions": {"PyYAML": "6.0.1"}}
    bad = {k: {**dict(fields), "tag": f"vibetrading-{k}:rel01-" + "4"*20,
               "command": rc.EXPECTED_IMAGE_CMD[k], "user": rc.EXPECTED_IMAGE_USER[k],
               **({"browser_path": "/opt/dashboard-browsers"} if k == "browser" else {})} for k in rc.IMAGE_CLASSES}
    bad["browser"]["browser_path"] = "/opt/dashboard-browsers"
    with pytest.raises(ValueError): rc._validate_images({"app": fields}, source_revision="a"*40, source_sha="2"*64, config_sha="3"*64, input_sha="4"*64)
    bad["crypto"]["source_sha256"] = "0"*64
    with pytest.raises(ValueError, match="source"):
        rc._validate_images(bad, source_revision="a"*40, source_sha="2"*64, config_sha="3"*64, input_sha="4"*64)
    bad["crypto"]["source_sha256"] = "2"*64
    bad["crypto"]["config_sha256"] = "0"*64
    with pytest.raises(ValueError, match="config/input"):
        rc._validate_images(bad, source_revision="a"*40, source_sha="2"*64, config_sha="3"*64, input_sha="4"*64)
    bad["crypto"]["config_sha256"] = "3"*64
    bad["app"]["environment"] = {"API_TOKEN": "never-include"}
    with pytest.raises(ValueError, match="incomplete"):
        rc._validate_images(bad, source_revision="a"*40, source_sha="2"*64, config_sha="3"*64, input_sha="4"*64)


def test_wheel_receipt_needs_exact_shape_and_current_source_identity(tmp_path):
    root = repo(tmp_path)
    wheel = tmp_path / "candidate.whl"
    digest = _make_wheel(root, wheel)
    evidence = rc.verify_wheel(root, wheel, digest)
    assert rc.freeze(root, wheel=evidence)["deployment_state"] == "DEPLOY_BLOCKED"
    forged = {**evidence, "api_token": "never-include"}
    with pytest.raises(ValueError, match="unexpected"):
        rc.freeze(root, wheel=forged)
    forged = {**evidence, "source_sha256": "0"*64}
    with pytest.raises(ValueError, match="source identity"):
        rc.freeze(root, wheel=forged)


def test_secret_scan_skips_variable_expression_and_redacts_literal():
    rc._secret_scan("config/example.yml", b"api_key: ${API_KEY}\n")
    value = "sensitive-value-never-echo"
    with pytest.raises(ValueError) as exc: rc._secret_scan("config/private.yml", f"api_key: '{value}'".encode())
    assert value not in str(exc.value)


def test_real_current_inventory_paths_and_safe_input_scan():
    root = Path(__file__).parents[2].resolve()
    paths = set(rc._tracked_paths(root))
    assert "docker-compose.yml" in paths
    assert "deploy/vibetrading.service" in paths
    assert "deploy/vibetrading.cron.example" in paths
    assert "dashboard/pnpm-lock.yaml" in paths
    assert "deploy/rollback/MOV-06/docker-compose.yml" in paths
    rows = rc._compose_role_inventory(root)
    assert rows["primary:collector"]["command"] == ["python", "-m", "runtime.local_runtime", "collector", "--config", "/run/config/local.json", "--producer", "${VIBETRADING_LOCAL_PRODUCER:?select one measured producer}"]
    assert rows["timefolio-order:engine"]["command"][-1] == "--no-submit"
    historical = json.loads((root / "docs/evidence/refactoring_20261018/server_readonly_snapshot.json").read_text())
    assert len(historical["containers"]) == 14
    for rel in paths:
        rc._secret_scan(rel, rc._safe(root, rel).read_bytes())


class FakeDocker:
    def __init__(self, collision=False):
        self.images = {}
        self.commands = []
        self.collision = collision

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[:2] == ["docker", "tag"]:
            source = next((i for i in self.images.values() if i["Id"] == command[2]),
                          {"Id": "sha256:" + "0" * 64})
            self.images[command[3]] = source
            return SimpleNamespace(returncode=0, stdout="")
        if command[:3] == ["docker", "image", "inspect"]:
            ref = command[-1]
            if ref.startswith("base:"):
                return SimpleNamespace(returncode=0, stdout=json.dumps([{"Id": "sha256:" + "0"*64}]))
            image = self.images.get(ref)
            if image is None:
                image = next((i for i in self.images.values() if i["Id"] == ref), None)
            if image is None and ref.startswith("sha256:") and ref == "sha256:" + "0"*64:
                image = {"Id": ref}
            if image is None and self.collision:
                image = {"Id": "sha256:" + "9"*64, "Config": {"Labels": {"wrong": "tag collision"}, "Cmd": ["python"], "User": "10001", "Env": ["PYTHONPATH=/app/src"]}}
            return SimpleNamespace(returncode=0 if image else 1, stdout=json.dumps([image]) if image else "")
        if command[:2] == ["docker", "build"]:
            tag = command[command.index("--tag") + 1]
            args = {command[i+1].split("=", 1)[0]: command[i+1].split("=", 1)[1]
                    for i,v in enumerate(command) if v == "--build-arg" and i+1 < len(command)}
            labels = {
                "org.opencontainers.image.revision": args["VIBETRADING_BUILD_REVISION"],
                "org.vibetrading.config-hash": args["VIBETRADING_CONFIG_SHA256"],
                "org.vibetrading.source-sha256": args["VIBETRADING_SOURCE_SHA256"],
                "org.vibetrading.config-sha256": args["VIBETRADING_CONFIG_SHA256"],
                "org.vibetrading.input-sha256": args["VIBETRADING_INPUT_SHA256"],
                "org.vibetrading.base-id": args["VIBETRADING_BASE_ID"],
                "org.vibetrading.image-class": args["VIBETRADING_IMAGE_CLASS"],
            }
            role = args["VIBETRADING_IMAGE_CLASS"]
            env = ["PYTHONPATH=/app/src", "VIBETRADING_BUILD_CONFIG_HASH=" + args["VIBETRADING_CONFIG_SHA256"]] + (["PLAYWRIGHT_BROWSERS_PATH=/opt/dashboard-browsers"] if role == "browser" else [])
            self.images[tag] = {"Id": "sha256:" + ("1" + "2"*len(self.images)).ljust(64,"0")[:64],
                "Config": {"Labels": labels, "Cmd": rc.EXPECTED_IMAGE_CMD[role], "User": rc.EXPECTED_IMAGE_USER[role], "Env": env}}
            return SimpleNamespace(returncode=0, stdout="")
        if command[:2] == ["docker", "run"]:
            return SimpleNamespace(returncode=0, stdout='{"PyYAML":"6.0.1"}')
        raise AssertionError(command)


def test_cli_freeze_reopens_wheel_then_verify_accepts_same_archive(tmp_path, monkeypatch):
    root = repo(tmp_path)
    wheel = tmp_path / "candidate.whl"
    digest = _make_wheel(root, wheel)
    manifest = tmp_path / "release.json"
    monkeypatch.setattr(builder, "ROOT", root)
    options = ["--manifest", str(manifest), "--wheel", str(wheel), "--wheel-sha256", digest]
    assert builder.main(["freeze", *options]) == 0
    frozen = json.loads(manifest.read_text())
    assert frozen["wheel"]["verification_scope"] == "WHEEL_ARCHIVE_RECORD_AND_SOURCE_REOPENED"
    assert builder.main(["verify", *options]) == 0


def test_cli_build_refreshes_dependency_facts_and_rechecks_frozen_source(tmp_path, monkeypatch):
    root = repo(tmp_path)
    wheel = tmp_path / "candidate.whl"
    digest = _make_wheel(root, wheel)
    manifest = tmp_path / "release.json"
    monkeypatch.setattr(builder, "ROOT", root)
    original_build = builder._build_images
    fake = FakeDocker()
    environ = {}
    for role in ("APP", "CRYPTO"):
        environ[f"VIBETRADING_{role}_BASE_REF"] = "base:fixture"
        environ[f"VIBETRADING_{role}_BASE_ID"] = "sha256:" + "0" * 64
    monkeypatch.setattr(builder, "_build_images", lambda candidate, project:
                        original_build(candidate, project, runner=fake, environ=environ))
    options = ["--manifest", str(manifest), "--wheel", str(wheel), "--wheel-sha256", digest]
    assert builder.main(["freeze", *options]) == 0
    assert builder.main(["build", *options]) == 0
    frozen = json.loads(manifest.read_text())
    assert set(frozen["dependency_inventory"]["resolved_image_versions"]) == set(rc.IMAGE_CLASSES)
    assert builder.main(["verify", *options]) == 0


def test_offline_builder_builds_four_labels_without_pull_and_detects_collision(tmp_path):
    root = repo(tmp_path)
    candidate = rc.freeze(root)
    fake = FakeDocker()
    environ = {}
    for role, (ref, identity) in builder.BASE_ENV.items():
        environ[ref] = "base:" + role
        environ[identity] = "sha256:" + "0"*64
    receipts = builder._build_images(candidate, root, runner=fake, environ=environ)
    assert set(receipts) == set(rc.IMAGE_CLASSES)
    builds = [cmd for cmd in fake.commands if cmd[:2] == ["docker", "build"]]
    assert len(builds) == 4 and all("--pull=false" in cmd and "--network=none" in cmd for cmd in builds)
    runs = [cmd for cmd in fake.commands if cmd[:2] == ["docker", "run"]]
    assert len(runs) == 4 and all("--read-only" in cmd and "--cap-drop=ALL" in cmd and "--network=none" in cmd for cmd in runs)
    assert all("--pids-limit=64" in cmd and "--memory=256m" in cmd and "10001:10001" in cmd for cmd in runs)
    by_file = {cmd[cmd.index("--file") + 1]: cmd for cmd in builds}
    def arg_value(command, name):
        args = [command[i + 1] for i, value in enumerate(command[:-1]) if value == "--build-arg"]
        return next(value.split("=", 1)[1] for value in args if value.startswith(name + "="))
    cache_ref = lambda identity: "vibetrading-release-cache:sha256-" + identity.removeprefix("sha256:")
    assert arg_value(by_file["deploy/Dockerfile.crypto-ledger"], "VIBETRADING_BASE_IMAGE") == cache_ref(receipts["crypto"]["id"])
    assert arg_value(by_file["deploy/Dockerfile.dashboard-timefolio"], "DASHBOARD_APP_IMAGE") == cache_ref(receipts["app"]["id"])
    with pytest.raises(ValueError, match="collision"):
        builder._build_images(candidate, root, runner=FakeDocker(collision=True), environ=environ)


def test_cached_base_alias_collision_never_retags_existing_image():
    fake = FakeDocker()
    alias = "vibetrading-release-cache:sha256-" + "0" * 64
    fake.images[alias] = {"Id": "sha256:" + "9" * 64}
    with pytest.raises(ValueError, match="collision"):
        builder._cached_base_ref(fake, "sha256:" + "0" * 64)
    assert not any(command[:2] == ["docker", "tag"] for command in fake.commands)


def test_cached_base_retargeted_during_build_fails_before_publication(tmp_path):
    root = repo(tmp_path)
    candidate = rc.freeze(root)
    class RetargetedBase(FakeDocker):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if command[:2] == ["docker", "build"]:
                self.images["vibetrading-release-cache:sha256-" + "0" * 64] = {"Id": "sha256:" + "9" * 64}
            return result
    environ = {"VIBETRADING_APP_BASE_REF": "base:fixture", "VIBETRADING_APP_BASE_ID": "sha256:" + "0" * 64}
    with pytest.raises(ValueError, match="changed during build"):
        builder._build_images(candidate, root, runner=RetargetedBase(), environ=environ)


def test_bad_wheel_cli_returns_redacted_nonzero(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(builder, "ROOT", repo(tmp_path))
    path = tmp_path / "bad.whl"
    path.write_bytes(b"not a zip; sensitive-value")
    result = builder.main(["freeze", "--manifest", str(tmp_path / "manifest.json"),
                           "--wheel", str(path), "--wheel-sha256", hashlib.sha256(path.read_bytes()).hexdigest()])
    captured = capsys.readouterr()
    assert result == 1
    assert "NameError" not in captured.err
    assert "sensitive-value" not in captured.err
    assert "wheel archive verification failed" in captured.err


def test_metadata_collector_import_does_not_start_ssh(monkeypatch, tmp_path):
    import subprocess as sp
    def forbidden(*args, **kwargs):
        raise AssertionError("subprocess called during import")
    monkeypatch.setattr(sp, "run", forbidden)
    path = Path(__file__).parents[2] / "scripts/refactoring/collect_release_metadata.py"
    spec = importlib.util.spec_from_file_location("metadata_inert_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
    snapshot = Path(__file__).parents[2] / "docs/evidence/refactoring_20261018/server_readonly_snapshot.json"
    output = tmp_path / "_rel01_test_metadata.json"
    assert module.main(["--snapshot", str(snapshot), "--output", str(output)]) == 0
    data = json.loads(output.read_text())
    assert data["state"].startswith("HISTORICAL_UNVERIFIED") and len(data["roles"]) == 14
