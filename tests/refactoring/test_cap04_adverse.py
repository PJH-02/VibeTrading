"""CAP-04 hostile-callback fairness and measured local benchmark checks."""
from __future__ import annotations

import json
from pathlib import Path


def test_strategy_manifest_reads_static_identity_without_importing_plugin(tmp_path):
    from strategies.contract import load_strategy_manifest

    marker = tmp_path / "plugin-imported"
    path = tmp_path / "isolated_manifest_fixture.py"
    path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
        "STRATEGY_ID = 'manifest_fixture'\n"
        "SUBSCRIPTIONS = [{'instrument_key': 'x'}]\n"
        "WARMUP = {'bars': 0}\n"
        "def create_strategy(): return object()\n",
        encoding="utf-8")
    module = load_strategy_manifest(path.stem, (str(tmp_path),))
    assert module.strategy_id == "manifest_fixture"
    assert module.module.__file__ == str(path)
    assert not marker.exists()


def test_strategy_manifest_rejects_oversized_source_before_parse_or_import(tmp_path, monkeypatch):
    import pytest
    import strategies.contract as contract

    path = tmp_path / "oversized_manifest.py"
    path.write_bytes(b"STRATEGY_ID = 'bounded'\n" + b"# padding\n" * 120_000)
    monkeypatch.setattr(contract.ast, "parse",
                        lambda *_args, **_kwargs: pytest.fail("oversized input was parsed"))
    monkeypatch.setattr(contract.importlib, "import_module",
                        lambda *_args, **_kwargs: pytest.fail("plugin was imported in parent"))
    with pytest.raises(ValueError, match="bounded byte limit"):
        contract.load_strategy_manifest(path.stem, (str(tmp_path),))


def test_strategy_manifest_rejects_deep_ast_before_literal_eval(tmp_path, monkeypatch):
    import pytest
    import strategies.contract as contract

    path = tmp_path / "deep_manifest.py"
    nesting = 140
    path.write_text(
        "STRATEGY_ID = 'deep_fixture'\n"
        + "SUBSCRIPTIONS = " + "[" * nesting + "0" + "]" * nesting + "\n"
        + "WARMUP = {'bars': 0}\n"
        + "def create_strategy(): return None\n", encoding="utf-8")
    monkeypatch.setattr(contract.ast, "literal_eval",
                        lambda *_args, **_kwargs: pytest.fail("oversized AST reached literal_eval"))
    with pytest.raises(ValueError, match="bounded nesting depth"):
        contract.load_strategy_manifest(path.stem, (str(tmp_path),))


def test_strategy_manifest_rejects_large_ast_before_literal_eval(tmp_path, monkeypatch):
    import pytest
    import strategies.contract as contract

    path = tmp_path / "wide_manifest.py"
    values = ",".join("0" for _ in range(26_000))
    path.write_text(
        "STRATEGY_ID = 'wide_fixture'\nSUBSCRIPTIONS = [" + values + "]\n"
        "WARMUP = {'bars': 0}\ndef create_strategy(): return None\n", encoding="utf-8")
    monkeypatch.setattr(contract.ast, "literal_eval",
                        lambda *_args, **_kwargs: pytest.fail("oversized AST reached literal_eval"))
    with pytest.raises(ValueError, match="bounded node count"):
        contract.load_strategy_manifest(path.stem, (str(tmp_path),))


def test_strategy_manifest_requires_literal_bounded_string_strategy_id(tmp_path):
    import pytest
    from strategies.contract import load_strategy_manifest

    path = tmp_path / "nonstring_manifest.py"
    path.write_text(
        "STRATEGY_ID = 42\nSUBSCRIPTIONS = []\nWARMUP = {'bars': 0}\n"
        "def create_strategy(): return None\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bounded literal string"):
        load_strategy_manifest(path.stem, (str(tmp_path),))


def test_two_cpu_spool_strategy_matcher_benchmark():
    from scripts.benchmark_strategy_isolation import run_benchmark

    report = run_benchmark(samples=200, action_every=10)
    Path("/artifacts/cap04-two-cpu-benchmark.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    assert report["status"] == "MEASURED_LOCAL_SYNTHETIC_PAPER"
    assert report["samples"] == 200
    assert report["evaluated_samples"] + report["stale_rejected_samples"] == 200
    assert report["action_samples"] == report["paper_orders"]
    assert report["action_samples"] > 0
    assert report["no_action_samples"] > 0
    assert len(report["affinity_selected"]) == 2
    assert report["durable_spool_sequence"] == 200
    assert report["warm_worker_action_latency_ns"]["p99"] > 0
    assert report["warm_worker_no_action_evaluation_ns"]["p99"] > 0
    assert report["rss_child_bytes"] > 0
    assert report["venue_adapter_T5"] == "NOT_MEASURED"


def test_throwing_plugin_faults_only_its_job_under_confined_worker(tmp_path):
    import site
    import time
    import pytest
    from runtime.strategy_process import IsolationBudgets, StrategyIsolation
    from strategies.contract import load_strategy_manifest
    import test_cap04_isolation as h

    if __import__("shutil").which("bwrap") is None:
        pytest.skip("bubblewrap is not installed; confined mode fails closed")
    ledger = h.ledger_at(tmp_path)
    host = None
    try:
        ledger.seed_balance("USDT", h.D(100000), {"source": "synthetic"})
        matcher, now = h.matcher_for(ledger), [h.T]
        host = h.runtime(ledger, matcher, now, isolation=StrategyIsolation(
            budgets=IsolationBudgets(callback_timeout_ns=100_000_000),
            allowed_source_roots=(str(tmp_path),),
            readonly_paths=(site.getusersitepackages(),), require_bubblewrap=True))
        path = h.write_plugin(tmp_path)
        source = path.read_text(encoding="utf-8").replace(
            "        self.seen += 1\n",
            "        self.seen += 1\n        raise RuntimeError('fixture failure')\n")
        path.write_text(source, encoding="utf-8")
        module = load_strategy_manifest(path.stem, (str(tmp_path),))
        host.register(h.registration(ledger, module))
        host.process_event(h.book(h.T))
        for _ in range(300):
            host.watchdog()
            if host.report()["strategies"][module.strategy_id]["state"] == "FAULTED":
                break
            time.sleep(.01)
        assert host.report()["strategies"][module.strategy_id]["state"] == "FAULTED"
        assert host.hot_path.apply_seq == 1
    finally:
        if host is not None:
            host.close()
        ledger.close()


def test_confined_resource_limits_block_oversize_allocation_and_fork_with_sibling_progress(tmp_path):
    import site
    import time
    import pytest
    from runtime.strategy_process import IsolationBudgets, StrategyIsolation
    from strategies.contract import load_strategy_manifest
    import test_cap04_isolation as h

    if __import__("shutil").which("bwrap") is None:
        pytest.skip("bubblewrap is not installed; confined mode fails closed")
    ledger = h.ledger_at(tmp_path)
    host = None
    try:
        ledger.seed_balance("USDT", h.D(100000), {"source": "synthetic"})
        matcher, now = h.matcher_for(ledger), [h.T]
        isolation = StrategyIsolation(budgets=IsolationBudgets(
            callback_timeout_ns=500_000_000, max_processes=4),
            allowed_source_roots=(str(tmp_path),),
            readonly_paths=(site.getusersitepackages(),), require_bubblewrap=True)
        host = h.runtime(ledger, matcher, now, isolation=isolation)
        base_path = h.write_plugin(tmp_path)
        base = base_path.read_text(encoding="utf-8")
        base_path.unlink()
        plugin_sources = {
            "cap04_memory_limited": (
                "        limit = resource.getrlimit(resource.RLIMIT_AS)[1]\n"
                "        if limit == resource.RLIM_INFINITY or limit > 256 * 1024 * 1024:\n"
                "            raise RuntimeError('finite address-space limit missing')\n"
                "        try:\n"
                "            bytearray(limit + 1)\n"
                "        except MemoryError:\n"
                "            return []\n"
                "        raise RuntimeError('address-space limit did not stop oversized allocation')\n"),
            "cap04_fork_limited": (
                "        try:\n"
                "            child_pid = os.fork()\n"
                "        except OSError as error:\n"
                "            if error.errno != errno.EAGAIN:\n"
                "                raise\n"
                "            return []\n"
                "        if child_pid == 0:\n"
                "            os._exit(0)\n"
                "        os.waitpid(child_pid, 0)\n"
                "        raise RuntimeError('process limit allowed an extra child')\n"),
        }
        manifests = []
        for strategy_id, callback in plugin_sources.items():
            path = tmp_path / (strategy_id + ".py")
            source = (base.replace("cap04_iso", strategy_id)
                .replace("        time.sleep(0.0)\n        self.seen += 1",
                         callback))
            # Imports belong at module level; callback body starts with its
            # first indented line in each bounded adverse strategy.
            if strategy_id == "cap04_memory_limited":
                source = source.replace("from decimal import Decimal\n",
                                        "from decimal import Decimal\nimport resource\n")
            else:
                source = source.replace("from decimal import Decimal\n",
                                        "from decimal import Decimal\nimport errno\nimport os\n")
            path.write_text(source, encoding="utf-8")
            manifest = load_strategy_manifest(path.stem, (str(tmp_path),))
            manifests.append(manifest)

        healthy_path = tmp_path / "cap04_resource_healthy.py"
        healthy_source = base.replace("cap04_iso", "cap04_resource_healthy")
        healthy_source = healthy_source.replace(
            "valid_until_ts_ns=event.receive_ts_ns+100000",
            "valid_until_ts_ns=event.receive_ts_ns+1000000000")
        healthy_path.write_text(healthy_source, encoding="utf-8")
        healthy = load_strategy_manifest(healthy_path.stem, (str(tmp_path),))
        for manifest in (*manifests, healthy):
            host.register(h.registration(ledger, manifest))

        started = time.monotonic_ns()
        host.process_event(h.book(h.T))
        parent_apply_ns = time.monotonic_ns() - started
        deadline = time.monotonic_ns() + 3_000_000_000
        while time.monotonic_ns() < deadline:
            host.poll_isolated()
            if all(host._jobs[item.strategy_id].callbacks >= 1 for item in (*manifests, healthy)):
                break
            time.sleep(.001)
        states = host.report()["strategies"]
        assert all(states[item.strategy_id]["state"] == "PAPER"
                   for item in (*manifests, healthy))
        assert all(host._jobs[item.strategy_id].callbacks == 1
                   for item in (*manifests, healthy))
        assert len(ledger.report()["orders"]) == 1
        assert host.hot_path.apply_seq == 1
        assert parent_apply_ns < 100_000_000
    finally:
        if host is not None:
            host.close()
        ledger.close()


def test_local_market_engine_apply_ack_and_other_feed_progress_while_plugin_spins(tmp_path):
    import asyncio
    import site
    import time
    import pytest
    from core.models import AssetClass, InstrumentId, Venue
    from feeds.feed_recovery import EngineCursor, RecoveryFrame
    from market.local_engine import EngineBudgets, LocalMarketEngine
    from runtime.strategy_process import IsolationBudgets, StrategyIsolation
    from strategies.contract import load_strategy_manifest
    import test_cap04_isolation as h

    if __import__("shutil").which("bwrap") is None:
        pytest.skip("bubblewrap is not installed; confined mode fails closed")

    async def scenario():
        ledger = h.ledger_at(tmp_path)
        host = None
        engine = None
        try:
            ledger.seed_balance("USDT", h.D(100000), {"source": "synthetic"})
            matcher, now = h.matcher_for(ledger), [time.time_ns()]
            eth = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
                raw_symbol="ETHUSDT", std_symbol="ETHUSDT", currency="USDT")
            isolation = StrategyIsolation(budgets=IsolationBudgets(
                callback_timeout_ns=50_000_000, max_queue_frames=8),
                allowed_source_roots=(str(tmp_path),),
                readonly_paths=(site.getusersitepackages(),), require_bubblewrap=True)
            host = h.runtime(ledger, matcher, now, isolation=isolation,
                             known_instruments={h.BTC.key: h.BTC, eth.key: eth})

            healthy_path = h.write_plugin(tmp_path)
            healthy_path.rename(tmp_path / "cap04_healthy.py")
            healthy_path = tmp_path / "cap04_healthy.py"
            healthy_path.write_text(healthy_path.read_text(encoding="utf-8")
                .replace("cap04_iso", "cap04_healthy")
                .replace("valid_until_ts_ns=event.receive_ts_ns+100000",
                         "valid_until_ts_ns=event.receive_ts_ns+1000000000"), encoding="utf-8")
            hostile_path = tmp_path / "cap04_infinite.py"
            hostile_path.write_text(healthy_path.read_text(encoding="utf-8")
                .replace("cap04_healthy", "cap04_infinite")
                .replace("        time.sleep(0.0)\n        self.seen += 1",
                         "        self.seen += 1\n        if self.seen > 1:\n            while True: pass"),
                encoding="utf-8")
            healthy = load_strategy_manifest(healthy_path.stem, (str(tmp_path),))
            hostile = load_strategy_manifest(hostile_path.stem, (str(tmp_path),))
            host.register(h.registration(ledger, healthy))
            host.register(h.registration(ledger, hostile))

            budgets = EngineBudgets(2, 8, 65536, 32, 1_048_576,
                10_000_000_000, 64, 8, 10_000_000_000, .01)
            engine = LocalMarketEngine(
                producer_providers={"btc-feed": "scripted", "other-feed": "scripted"},
                budgets=budgets, on_invalidate=lambda *_: None,
                hot_path=host.hot_path, post_apply_callbacks=(host.process_applied,),
                on_tick=host.advance, snapshot_clock=lambda: now[0],
                fresh_starts={"btc-feed": "btc-run", "other-feed": "other-run"},
                verified_fresh_start=True, clock=time.monotonic_ns)
            await engine.start()

            first_ts = time.time_ns()
            now[0] = first_ts
            first = h.book(first_ts).model_copy(update={"event_id": "lm-first", "sequence": 1,
                "final_update_id": 1, "receive_ts_ns": first_ts,
                "source_ts_ns": first_ts, "wallclock_ts_ns": first_ts})
            first_receipt = engine.on_frame(RecoveryFrame(
                EngineCursor("btc-feed", "btc-run", 1), first.model_dump_json().encode()))
            assert first_receipt.cursor.sequence == 1

            async def wait_for_two_orders():
                while len(ledger.report()["orders"]) < 2:
                    await asyncio.sleep(.001)
            await asyncio.wait_for(wait_for_two_orders(), timeout=3)

            second_ts = time.time_ns()
            now[0] = second_ts
            second = h.book(second_ts).model_copy(update={"event_id": "lm-second", "sequence": 2,
                "final_update_id": 2, "receive_ts_ns": second_ts,
                "source_ts_ns": second_ts, "wallclock_ts_ns": second_ts})
            second_receipt = engine.on_frame(RecoveryFrame(
                EngineCursor("btc-feed", "btc-run", 2), second.model_dump_json().encode()))
            assert second_receipt.cursor.sequence == 2
            infinite_job = host._jobs[hostile.strategy_id]
            assert infinite_job.worker.alive

            other_ts = time.time_ns()
            now[0] = other_ts
            other_event = h.book(other_ts).model_copy(update={
                "instrument": eth, "venue": eth.venue, "event_id": "other-while-infinite",
                "sequence": 1, "final_update_id": 1, "receive_ts_ns": other_ts,
                "source_ts_ns": other_ts, "wallclock_ts_ns": other_ts})
            started = time.monotonic_ns()
            other_receipt = engine.on_frame(RecoveryFrame(
                EngineCursor("other-feed", "other-run", 1), other_event.model_dump_json().encode()))
            other_apply_ack_ns = time.monotonic_ns() - started
            assert other_receipt.cursor == EngineCursor("other-feed", "other-run", 1)
            assert other_apply_ack_ns < 100_000_000
            assert host.hot_path.apply_seq == 3
            assert engine.stats["applied"] == 3
            assert engine.stats["feeds"]["other-feed"]["applied_sequence"] == 1

            async def wait_for_timeout_fence_and_cancel():
                while host.report()["strategies"][hostile.strategy_id]["state"] != "FAULTED":
                    now[0] = time.time_ns()
                    await asyncio.sleep(.005)
            await asyncio.wait_for(wait_for_timeout_fence_and_cancel(), timeout=3)
            assert host.report()["strategies"][healthy.strategy_id]["state"] == "PAPER"
            assert infinite_job.worker.alive is False
            paper_state = ledger.paper_state()
            assert any(order.get("cancel_ns") is not None
                and order.get("strategy") == hostile.strategy_id
                for order in paper_state["orders"].values())
            assert other_apply_ack_ns < 100_000_000
        finally:
            if engine is not None:
                await engine.close()
            if host is not None:
                await host.close_async()
            ledger.close()

    asyncio.run(scenario())


def test_infinite_callback_can_be_fenced_without_blocking_parent_or_sibling(tmp_path):
    import os
    import site
    import time
    from runtime.strategy_process import IsolationBudgets, StrategyIsolation
    from strategies.contract import load_strategy_manifest
    import test_cap04_isolation as h

    if __import__("shutil").which("bwrap") is None:
        import pytest
        pytest.skip("bubblewrap is not installed; confined mode fails closed")
    ledger = h.ledger_at(tmp_path)
    host = None
    try:
        ledger.seed_balance("USDT", h.D(100000), {"source": "synthetic"})
        matcher, now = h.matcher_for(ledger), [h.T]
        isolation = StrategyIsolation(budgets=IsolationBudgets(
            callback_timeout_ns=50_000_000, max_queue_frames=8),
            allowed_source_roots=(str(tmp_path),),
            readonly_paths=(site.getusersitepackages(),), require_bubblewrap=True)
        host = h.runtime(ledger, matcher, now, isolation=isolation)
        healthy_path = h.write_plugin(tmp_path)
        hostile_path = tmp_path / "cap04_infinite.py"
        hostile_path.write_text(healthy_path.read_text(encoding="utf-8")
            .replace("cap04_iso", "cap04_infinite")
            .replace("time.sleep(0.0)\n        self.seen += 1",
                     "self.seen += 1\n        if self.seen > 1:\n            while True: pass"),
            encoding="utf-8")
        healthy = load_strategy_manifest(healthy_path.stem, (str(tmp_path),))
        hostile = load_strategy_manifest(hostile_path.stem, (str(tmp_path),))
        host.register(h.registration(ledger, healthy))
        host.register(h.registration(ledger, hostile))

        first = h.book(h.T).model_copy(update={"event_id": "first", "sequence": 1,
            "final_update_id": 1, "receive_ts_ns": h.T, "source_ts_ns": h.T,
            "wallclock_ts_ns": h.T})
        host.process_event(first)
        for _ in range(500):
            host.poll_isolated()
            if len(ledger.report()["orders"]) >= 2:
                break
            time.sleep(.001)
        assert len(ledger.report()["orders"]) == 2

        now[0] = h.T + 100
        second = h.book(h.T + 100).model_copy(update={"event_id": "second", "sequence": 2,
            "final_update_id": 2, "receive_ts_ns": h.T + 100,
            "source_ts_ns": h.T + 100, "wallclock_ts_ns": h.T + 100})
        started = time.monotonic_ns()
        host.process_event(second)
        market_apply_ns = time.monotonic_ns() - started
        assert market_apply_ns < 100_000_000
        for _ in range(500):
            host.poll_isolated()
            if len(ledger.report()["orders"]) >= 3:
                break
            time.sleep(.001)
        assert len(ledger.report()["orders"]) >= 3
        time.sleep(.06)
        host.watchdog()
        state = host.report()["strategies"]
        assert state[hostile.strategy_id]["state"] == "FAULTED"
        assert state[healthy.strategy_id]["state"] == "PAPER"
        assert host._jobs[hostile.strategy_id].worker.alive is False
        assert host._jobs[healthy.strategy_id].worker.alive is True
        assert market_apply_ns < 100_000_000
        # Parent-owned cancellation was advanced despite the untrusted infinite
        # callback; matcher state records the cancellation request.
        paper_state = ledger.paper_state()
        assert any(order.get("cancel_ns") is not None and
                   order.get("strategy") == hostile.strategy_id
                   for order in paper_state["orders"].values())
    finally:
        if host is not None:
            host.close()
        ledger.close()
