"""O01B tests: private execution adapter, sandbox and relay boundaries."""

import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "dev" / "agent_run.py"
CORE_TEST = REPO_ROOT / "tests" / "refactoring" / "test_agent_task.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load("o01b_agent_run", SCRIPT)
harness = _load("o01b_core_fixtures", CORE_TEST)


@pytest.fixture(autouse=True)
def _isolated_lock_root(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOCK_ROOT", tmp_path / "host-locks")
    return tmp_path / "host-locks"


def _runtime(tmp_path):
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    launcher = host_bin / "opencode"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    delegate = host_bin / "delegate-opencode"
    delegate.write_text("#!/bin/sh\nexit 0\n")
    native = host_bin / "opencode-native"
    native.write_bytes(b"fake-elf")
    deps = tmp_path / "deps"
    deps.mkdir()
    for path in (launcher, delegate, native):
        os.chmod(path, 0o700)
    return SimpleNamespace(
        launcher=launcher,
        dispatcher=delegate,
        native=native,
        provider_config=tmp_path / "opencode.json",
        python_deps=[deps],
    )


def _install_fakes(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(runner, "_discover_runtime", lambda: runtime)
    monkeypatch.setattr(runner, "_bwrap_path", lambda: "/usr/bin/true")
    return runtime


def _fake_worker(write=None, exit_code=0, timed_out=False, on_run=None):
    def run(command, *, work, run_dir, deadline, cancel_check,
            state_root, run_id, timeout_seconds):
        if on_run is not None:
            on_run(Path(work), Path(run_dir), state_root, run_id)
        for rel, content in (write or {}).items():
            target = Path(work) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        stdout_path = Path(run_dir) / "worker.stdout.log"
        stderr_path = Path(run_dir) / "worker.stderr.log"
        stdout_path.write_text('{"usage":{"total_tokens":7}}\n', encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return {
            "exit_code": exit_code,
            "effective_exit_code": 124 if timed_out else exit_code,
            "timed_out": timed_out,
            "cancelled": False,
            "elapsed_seconds": 0.01,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "json_event_count": 1,
            "token_usage": {"total_tokens": 7},
        }

    return run


def _fake_test(exit_code=0, stdout="PASS\n", mutate=None):
    def run(argv, *, command, work, home, run_dir, index, runtime, deadline,
            cancel_check=None, state_root=None, run_id=None, **kwargs):
        if mutate is not None:
            mutate(Path(work))
        out = Path(run_dir) / f"test-{index}.stdout.log"
        err = Path(run_dir) / f"test-{index}.stderr.log"
        out.write_text(stdout, encoding="utf-8")
        err.write_text("", encoding="utf-8")
        return {
            "command": command,
            "exit_code": exit_code,
            "cancelled": False,
            "timed_out": False,
            "stdout": stdout,
            "stderr": "",
            "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
            "stdout_path": str(out),
            "stderr_path": str(err),
        }

    return run


def _prepare(tmp_path, monkeypatch):
    root = harness._setup(tmp_path)
    _install_fakes(monkeypatch, tmp_path)
    return root


def test_success_is_pending_review_with_patch_and_tests(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner,
        "_run_worker_process",
        _fake_worker({"src/target.py": "print('changed')\n"}),
    )
    monkeypatch.setattr(runner, "_run_test_process", _fake_test())

    receipt = runner.run_ticket(
        root,
        "VN01",
        harness._default_spec(),
        state_dir=tmp_path / "runs",
        ttl_seconds=30,
    )

    assert receipt["status"] == "PENDING_CODEX_REVIEW"
    assert receipt["decision"] == "PENDING_CODEX_REVIEW"
    assert receipt["host_execution_attested"] is True
    assert receipt["changed_paths"] == ["src/target.py"]
    assert receipt["tests"]["status"] == "VERIFIED_TESTS"
    assert receipt["relay"] == {}
    patch = Path(receipt["patch_path"]).read_text(encoding="utf-8")
    assert "print('changed')" in patch
    assert "a/src/target.py" in patch
    assert Path(receipt["workspace"]).is_dir()
    state = json.loads(
        (Path(receipt["workspace"]).parents[1] / "state.json").read_text()
    )
    assert state["status"] == "PENDING_CODEX_REVIEW"
    assert state["nonce"] == receipt["nonce"]


def test_duplicate_same_packet_does_not_dispatch(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    calls = {"worker": 0}

    def worker(*args, **kwargs):
        calls["worker"] += 1
        return _fake_worker()(*args, **kwargs)

    monkeypatch.setattr(runner, "_run_worker_process", worker)
    monkeypatch.setattr(runner, "_run_test_process", _fake_test())
    first = runner.run_ticket(
        root, "VN01", harness._default_spec(), state_dir=tmp_path / "runs"
    )
    second = runner.run_ticket(
        root, "VN01", harness._default_spec(), state_dir=tmp_path / "runs"
    )

    assert first["status"] == "PENDING_CODEX_REVIEW"
    assert second["status"] == "DUPLICATE"
    assert second["original_run_id"] == first["run_id"]
    assert calls["worker"] == 1


def test_live_origin_lock_is_not_stolen(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    state_root = runner._state_root(tmp_path / "runs")
    lock_path = runner._lock_path(state_root, str(root.absolute()))
    runner._ensure_private_dir(lock_path.parent)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        receipt = runner.run_ticket(
            root, "VN01", harness._default_spec(), state_dir=tmp_path / "runs"
        )
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert receipt["status"] == "FAILURE"
    assert receipt["failure_class"] == runner.FAILURE_LOCK
    assert receipt["tests"]["status"] == "NOT_RUN"


def test_origin_lock_is_shared_across_state_dirs_and_aliases(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    state_a = runner._state_root(tmp_path / "runs-a")
    state_b = runner._state_root(tmp_path / "runs-b")
    alias = str(root) + "/."
    path_a = runner._lock_path(state_a, str(root))
    path_b = runner._lock_path(state_b, alias)
    assert path_a == path_b
    fd = os.open(path_a, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        receipt = runner.run_ticket(
            root, "VN01", harness._default_spec(), state_dir=tmp_path / "runs-b"
        )
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert receipt["status"] == "FAILURE"
    assert receipt["failure_class"] == runner.FAILURE_LOCK


@pytest.mark.parametrize("ttl", [0, -1, True, 1.5, "10"])
def test_ttl_must_be_positive_int_not_bool(tmp_path, ttl):
    with pytest.raises(ValueError, match="ttl_seconds"):
        runner.run_ticket(tmp_path, "VN01", {}, ttl_seconds=ttl)


def test_deadline_preserves_partial_workspace(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner,
        "_run_worker_process",
        _fake_worker({"src/target.py": "partial\n"}, timed_out=True),
    )
    test_calls = {"count": 0}

    def test_process(*args, **kwargs):
        test_calls["count"] += 1
        return _fake_test()(*args, **kwargs)

    monkeypatch.setattr(runner, "_run_test_process", test_process)

    receipt = runner.run_ticket(
        root, "VN01", harness._default_spec(), state_dir=tmp_path / "runs"
    )

    assert receipt["status"] == "FAILURE"
    assert receipt["failure_class"] == "EXECUTION_TIMEOUT"
    assert receipt["worker"]["timed_out"] is True
    assert (Path(receipt["workspace"]) / "src/target.py").read_text() == "partial\n"
    assert test_calls["count"] == 0


def test_cancel_between_worker_and_tests_preserves_work(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)

    def cancel_during_worker(work, run_dir, state_root, run_id):
        (work / "src/target.py").write_text("cancelled work\n")
        runner.cancel_run(state_root, run_id)

    monkeypatch.setattr(
        runner, "_run_worker_process", _fake_worker(on_run=cancel_during_worker)
    )
    test_calls = {"count": 0}

    def test_process(*args, **kwargs):
        test_calls["count"] += 1
        return _fake_test()(*args, **kwargs)

    monkeypatch.setattr(runner, "_run_test_process", test_process)

    receipt = runner.run_ticket(
        root, "VN01", harness._default_spec(), state_dir=tmp_path / "runs"
    )

    assert receipt["status"] == "FAILURE"
    assert receipt["failure_class"] == "CANCELLED"
    assert receipt["host_execution_attested"] is False
    assert (Path(receipt["workspace"]) / "src/target.py").read_text() == "cancelled work\n"
    assert test_calls["count"] == 0


def test_source_change_during_attempt_is_refused(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)

    def mutate_origin(work, run_dir, state_root, run_id):
        (root / "src/target.py").write_text("origin changed\n")

    monkeypatch.setattr(
        runner, "_run_worker_process",
        _fake_worker({"src/target.py": "worker change\n"}, on_run=mutate_origin),
    )
    monkeypatch.setattr(runner, "_run_test_process", _fake_test())

    receipt = runner.run_ticket(
        root, "VN01", harness._default_spec(), state_dir=tmp_path / "runs"
    )

    assert receipt["status"] == "FAILURE"
    assert receipt["failure_class"] == "SOURCE_CHANGED"
    assert receipt["host_execution_attested"] is False


def test_cancel_during_live_test_is_cancelled_not_test_failure(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner, "_run_worker_process", _fake_worker({"src/target.py": "changed\n"})
    )

    def test_process(argv, *, command, work, home, run_dir, index, runtime,
                     deadline, cancel_check=None, state_root=None, run_id=None,
                     **kwargs):
        runner.cancel_run(state_root, run_id)
        out = Path(run_dir) / f"test-{index}.stdout.log"
        err = Path(run_dir) / f"test-{index}.stderr.log"
        out.write_text("still running\n", encoding="utf-8")
        err.write_text("", encoding="utf-8")
        return {
            "command": command,
            "exit_code": 1,
            "cancelled": True,
            "timed_out": False,
            "stdout": "still running\n",
            "stderr": "",
            "stdout_sha256": hashlib.sha256(b"still running\n").hexdigest(),
            "stdout_path": str(out),
            "stderr_path": str(err),
        }

    monkeypatch.setattr(runner, "_run_test_process", test_process)
    receipt = runner.run_ticket(
        root, "VN01", harness._default_spec(), state_dir=tmp_path / "runs"
    )

    assert receipt["status"] == "FAILURE"
    assert receipt["failure_class"] == "CANCELLED"
    assert receipt["tests"]["status"] != "VERIFIED_TESTS"
    assert (Path(receipt["workspace"]) / "src/target.py").read_text() == "changed\n"


def test_unknown_model_route_is_refused_before_dispatch(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    calls = {"worker": 0}

    def worker(*args, **kwargs):
        calls["worker"] += 1
        return _fake_worker()(*args, **kwargs)

    monkeypatch.setattr(runner, "_run_worker_process", worker)

    with pytest.raises(ValueError):
        runner.run_ticket(
            root,
            "VN01",
            harness._default_spec(model="opencode/not-qualified"),
            state_dir=tmp_path / "runs",
        )
    assert calls["worker"] == 0


def test_secret_and_symlink_paths_are_refused(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="blocked"):
        runner.run_ticket(
            root,
            "VN01",
            harness._default_spec(read_paths=[".env.local"]),
            state_dir=tmp_path / "runs",
        )

    (root / "src/link.py").symlink_to(root / "src/target.py")
    with pytest.raises(ValueError, match="symlink"):
        runner.run_ticket(
            root,
            "VN01",
            harness._default_spec(read_paths=["src/link.py"]),
            state_dir=tmp_path / "runs",
        )


def test_add_file_patch_has_dev_null_origin(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner,
        "_run_worker_process",
        _fake_worker(
            {
                "src/target.py": "print('modified')\n",
                "src/new.py": "print('new')\n",
            }
        ),
    )
    monkeypatch.setattr(runner, "_run_test_process", _fake_test())

    receipt = runner.run_ticket(
        root,
        "VN01",
        harness._default_spec(
            edit_paths=["src/target.py", "src/new.py"],
            read_paths=["src/target.py"],
        ),
        state_dir=tmp_path / "runs",
    )

    assert receipt["status"] == "PENDING_CODEX_REVIEW"
    assert receipt["changed_paths"] == ["src/new.py", "src/target.py"]
    patch = Path(receipt["patch_path"]).read_text(encoding="utf-8")
    assert "--- /dev/null" in patch
    assert "+++ b/src/new.py" in patch
    assert "+print('new')" in patch


def test_failed_test_is_not_success(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner, "_run_worker_process", _fake_worker({"src/target.py": "changed\n"})
    )
    monkeypatch.setattr(runner, "_run_test_process", _fake_test(exit_code=1))

    receipt = runner.run_ticket(
        root, "VN01", harness._default_spec(), state_dir=tmp_path / "runs"
    )

    assert receipt["status"] == "FAILURE"
    assert receipt["failure_class"] == "TEST_FAILURE"
    assert receipt["tests"]["status"] == "NOT_VERIFIED"
    assert receipt["host_execution_attested"] is False


def test_empty_test_commands_are_refused(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        runner.run_ticket(
            root,
            "VN01",
            harness._default_spec(test_commands=[]),
            state_dir=tmp_path / "runs",
        )


def test_nonzero_worker_keeps_partial_work_and_exit(tmp_path, monkeypatch):
    root = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner,
        "_run_worker_process",
        _fake_worker({"src/target.py": "partial change\n"}, exit_code=7),
    )
    monkeypatch.setattr(runner, "_run_test_process", _fake_test())

    receipt = runner.run_ticket(
        root, "VN01", harness._default_spec(), state_dir=tmp_path / "runs"
    )

    assert receipt["status"] == "FAILURE"
    assert receipt["worker"]["exit_code"] == 7
    assert receipt["changed_paths"] == ["src/target.py"]
    assert (Path(receipt["workspace"]) / "src/target.py").read_text() == "partial change\n"
    assert receipt["tests"]["status"] == "NOT_RUN"


def test_native_config_has_no_provider_secret():
    config = runner._opencode_config("opencode/model-a")
    text = json.dumps(config)
    assert config["model"] == config["small_model"] == "opencode/model-a"
    assert config["lsp"] is False and config["formatter"] is False
    assert config["provider"] == {}
    assert "apiKey" not in text
    assert config["permission"]["task"] == "deny"


def test_worker_env_disables_project_plugins_and_skills(tmp_path):
    runtime = _runtime(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    shell = tmp_path / "bash"
    shell.touch()
    command = runner._worker_command(
        runtime, work, home, shell, [], "ticket.md", "implement", "opencode/x", 30
    )
    env = {command[i + 1]: command[i + 2] for i, item in enumerate(command) if item == "--setenv"}
    assert env["OPENCODE_PURE"] == "1"
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert env["OPENCODE_DISABLE_DEFAULT_PLUGINS"] == "1"
    assert env["OPENCODE_DISABLE_EXTERNAL_SKILLS"] == "1"
    assert env["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] == "1"
    assert env["OPENCODE_DISABLE_LSP_DOWNLOAD"] == "1"
    assert "--auto" not in command


def _upstream_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.server.last = {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": self.rfile.read(length),
            }
            body = self.server.body
            self.send_response(self.server.code)
            self.send_header("Content-Type", "application/json")
            if self.server.retry_after:
                self.send_header("Retry-After", self.server.retry_after)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.code = 200
    server.retry_after = None
    server.body = b'{"model":"model-a","ok":true}\n'
    server.last = None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _post(url, payload):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.read(), dict(response.headers)
    except HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def test_relay_exact_model_path_status_and_secret_redaction():
    upstream = _upstream_server()
    secret = "synthetic-secret-value"
    relay = runner.FreeLLMRelay(
        f"http://127.0.0.1:{upstream.server_port}/v1", secret, "model-a", timeout=5
    ).start()
    try:
        code, body, _ = _post(
            relay.url + "/chat/completions", {"model": "model-a"}
        )
        assert code == 200
        assert upstream.last["path"] == "/v1/chat/completions"
        assert upstream.last["auth"] == "Bearer " + secret
        assert relay.observations()["served_models"] == ["model-a"]

        code, _, _ = _post(relay.url + "/v1/models", {"model": "model-a"})
        assert code == 403

        code, _, _ = _post(
            relay.url + "/chat/completions", {"model": "model-other"}
        )
        assert code == 403

        upstream.code = 429
        upstream.retry_after = "7"
        upstream.body = b'{"error":"rate limited"}\n'
        code, _, headers = _post(
            relay.url + "/chat/completions", {"model": "model-a"}
        )
        assert code == 429
        assert headers.get("Retry-After") == "7"

        upstream.code = 200
        upstream.retry_after = None
        upstream.body = (
            '{"model":"model-a","echo":"' + secret + '"}\n'
        ).encode()
        code, body, _ = _post(
            relay.url + "/chat/completions", {"model": "model-a"}
        )
        assert code == 200
        assert secret.encode() not in body
        assert b"[REDACTED]" in body
        assert relay.safety_blocker is True
        assert relay.redactions == 1
    finally:
        relay.stop()
        upstream.shutdown()
        upstream.server_close()


def test_relay_records_retry_after_and_rejects_non_loopback_bases():
    upstream = _upstream_server()
    secret = "synthetic-secret-value"
    upstream.code = 429
    upstream.retry_after = "11"
    upstream.body = b'{"error":"rate limited"}\n'
    relay = runner.FreeLLMRelay(
        f"http://127.0.0.1:{upstream.server_port}/v1",
        secret,
        "model-a",
        timeout=1_000,
    ).start()
    try:
        code, _, _ = _post(relay.url + "/chat/completions", {"model": "model-a"})
        assert code == 429
        observations = relay.observations()
        assert observations["retry_after"] == [{"status": 429, "retry_after": "11"}]
        assert relay.timeout == runner.MAX_RELAY_TIMEOUT
    finally:
        relay.stop()
        upstream.shutdown()
        upstream.server_close()

    with pytest.raises(ValueError):
        runner.FreeLLMRelay(
            f"http://example.com:{upstream.server_port}/v1", secret, "model-a"
        )
    with pytest.raises(ValueError):
        runner.FreeLLMRelay(
            f"http://127.0.0.1:{upstream.server_port}/v2", secret, "model-a"
        )


def test_real_bwrap_namespace_differs_and_candidate_is_read_only(tmp_path):
    if not runner._bwrap_path():
        pytest.skip("bwrap unavailable; real namespace probe intentionally skipped")
    work = tmp_path / "work"
    work.mkdir()
    candidate = work / "candidate.txt"
    candidate.write_text("unchanged\n")
    home = tmp_path / "home"
    home.mkdir()
    argv = [
        "/bin/sh",
        "-c",
        "readlink /proc/self/ns/net; "
        "(echo changed > /work/candidate.txt) 2>/dev/null || echo WRITE_DENIED",
    ]
    completed = subprocess.run(
        runner._test_sandbox_command(work, home, argv),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert lines[0] != os.readlink("/proc/self/ns/net")
    assert "WRITE_DENIED" in completed.stdout
    assert candidate.read_text() == "unchanged\n"


def test_native_discovery_skips_the_stable_launcher_itself(tmp_path, monkeypatch):
    launcher = tmp_path / "stable-opencode"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o700)
    native = tmp_path / "native-opencode"
    native.write_text("native fixture")
    native.chmod(0o700)
    monkeypatch.delenv("O01B_NATIVE_OPENCODE", raising=False)
    monkeypatch.setenv("O01B_OPENCODE_LAUNCHER", str(launcher))
    monkeypatch.setattr(runner, "_native_candidates", lambda: [launcher, native])
    assert runner._find_native_opencode() == native.resolve()


def test_real_pytest_remains_available_under_fresh_home(tmp_path):
    if not shutil.which("bwrap"):
        pytest.skip("bwrap is unavailable")
    work = tmp_path / "pytest-work"
    work.mkdir()
    (work / "test_probe.py").write_text("def test_probe(): assert True\n")
    home = tmp_path / "pytest-home"
    home.mkdir()
    runtime = SimpleNamespace(native=tmp_path / "missing-native", python_deps=[Path(pytest.__file__).parent.parent])
    command = runner._test_sandbox_command(work, home, ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_probe.py"], runtime)
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
