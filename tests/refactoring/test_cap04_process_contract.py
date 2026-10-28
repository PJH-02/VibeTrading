"""CAP-04 bounded process/IPC contract tests (no network, no live accounts)."""
from __future__ import annotations

import json
import os
import site
from pathlib import Path

import pytest

from runtime.strategy_process import (
    IsolatedStrategyWorker,
    IsolationBudgets,
    ProtocolError,
    StrategyIsolation,
    StrategyProcessPool,
    decode_frame,
    encode_frame,
)


def _plugin(tmp_path: Path) -> Path:
    path = tmp_path / "cap04_fixture.py"
    path.write_text(
        "STRATEGY_ID = 'cap04_fixture'\n"
        "SUBSCRIPTIONS = []\n"
        "WARMUP = {'bars': 0}\n"
        "class Strategy:\n"
        "    def on_event(self, event, data):\n"
        "        return []\n"
        "def create_strategy():\n"
        "    return Strategy()\n",
        encoding="utf-8",
    )
    return path


def _worker(tmp_path, *, budgets=None, require_bubblewrap=False):
    path = _plugin(tmp_path)
    isolation = StrategyIsolation(
        budgets=budgets or IsolationBudgets(callback_timeout_ns=2_000_000_000),
        allowed_source_roots=(str(tmp_path),),
        readonly_paths=(site.getusersitepackages(),) if require_bubblewrap else (),
        require_bubblewrap=require_bubblewrap)
    return IsolatedStrategyWorker(
        strategy_id="cap04_fixture", module_name="cap04_fixture",
        module_path=str(path), config={}, registration_facts={"strategy_id": "cap04_fixture"},
        expected={"strategy_id": "cap04_fixture", "job_id": "job", "config_version": "v1"},
        isolation=isolation, parent_incarnation="parent", now_ns=lambda: 10**18)


def _await(worker, request):
    for _ in range(1000):
        for response, observed in worker.poll(max_frames=4):
            if response.get("request_id") == request["request_id"]:
                return worker.accept_response(response, now_ns=observed)
        if not worker.alive:
            break
        import time
        time.sleep(0.001)
    raise AssertionError("child did not answer: fault=%r" % (worker.fault,))


def test_isolation_budgets_are_explicit_and_positive():
    assert IsolationBudgets().max_processes == 8
    for name in ("max_processes", "max_queue_frames", "max_message_bytes",
                 "callback_timeout_ns", "max_total_rss_bytes"):
        with pytest.raises(ValueError):
            IsolationBudgets(**{name: 0})
        with pytest.raises(ValueError):
            IsolationBudgets(**{name: True})
    isolation = StrategyIsolation(public_settings={"visible": "yes"})
    document = isolation.document()
    assert document["security_sandbox"] is False
    assert document["public_settings_keys"] == ("visible",)
    with pytest.raises(ValueError):
        StrategyIsolation(allowed_source_roots=["not-a-tuple"])


def test_canonical_frames_reject_floats_duplicates_constants_and_extra_bytes():
    budgets = IsolationBudgets(max_message_bytes=256, max_depth=3)
    with pytest.raises(ProtocolError):
        encode_frame({"value": 1.5}, budgets=budgets)
    with pytest.raises(ProtocolError):
        encode_frame({"value": float("nan")}, budgets=budgets)
    with pytest.raises(ProtocolError):
        encode_frame({"a": {"b": {"c": {"d": 1}}}}, budgets=budgets)
    with pytest.raises(ProtocolError):
        decode_frame(b'{"a":1,"a":2}', budgets=budgets)
    with pytest.raises(ProtocolError):
        decode_frame(b'{"a":NaN}', budgets=budgets)
    with pytest.raises(ProtocolError):
        decode_frame(b'{"a":1} {}', budgets=budgets)
    assert decode_frame(b"[]", budgets=budgets) == []
    assert decode_frame(encode_frame({"a": ["1", None, True]}, budgets=budgets),
                        budgets=budgets) == {"a": ["1", None, True]}


def test_child_clears_secret_environment_uses_private_cwd_and_no_parent_pid(
        tmp_path, monkeypatch):
    monkeypatch.setenv("CAP04_SECRET_TOKEN", "do-not-inherit")
    worker = _worker(tmp_path)
    try:
        request = worker.start()
        _await(worker, request)
        request = worker.submit_probe()
        probe = _await(worker, request)
        assert probe["pid"] != os.getpid()
        assert "CAP04_SECRET_TOKEN" not in probe["env"]
        assert probe["env"]["HOME"] == probe["cwd"]
        assert str(tmp_path) not in probe["cwd"]
        assert probe["fd_count"] <= 32
        assert probe["has_instance"] is True
    finally:
        worker.close()


def test_bubblewrap_worker_is_confined_and_preserves_only_ipc_descriptor(tmp_path):
    import shutil
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is not installed; hard-isolation mode fails closed")
    worker = _worker(tmp_path, require_bubblewrap=True)
    try:
        request = worker.start()
        _await(worker, request)
        probe = _await(worker, worker.submit_probe())
        assert worker.pid != os.getpid()
        assert probe["fd_count"] <= 32
        assert probe["has_instance"] is True
        assert probe["cwd"] == probe["env"]["HOME"]
        assert probe["cwd"] not in (str(tmp_path), os.getcwd())
        address_limit = worker.budgets.max_worker_address_space_bytes
        assert probe["resource_limits"]["address_space"] == [address_limit, address_limit]
        assert probe["resource_limits"]["process_count"] == [1, 1]
        assert probe["no_new_privileges"] is True
        assert all(probe["env"][name] == "1" for name in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"))
    finally:
        worker.close()


def test_global_process_capacity_is_explicit(tmp_path):
    isolation = StrategyIsolation(budgets=IsolationBudgets(max_processes=1))
    pool = StrategyProcessPool(isolation, parent_incarnation="p", now_ns=lambda: 0)
    worker = _worker(tmp_path)
    pool.add(worker)
    with pytest.raises(ValueError):
        pool.add(_worker(tmp_path))
    pool.close()


def test_global_memory_budget_yields_usable_bounded_child_limit():
    budgets = IsolationBudgets()
    assert budgets.max_worker_address_space_bytes == 128 * 1024 * 1024
    with pytest.raises(ValueError, match="per-worker address-space"):
        IsolationBudgets(max_processes=16, max_total_rss_bytes=1_073_741_824)


def test_async_worker_cleanup_reaps_without_blocking_market_poll(tmp_path):
    import asyncio
    worker = _worker(tmp_path)
    request = worker.start()
    _await(worker, request)
    pid = worker.pid
    asyncio.run(worker.close_async())
    assert worker.process is None
    assert not Path(f"/proc/{pid}").exists()
