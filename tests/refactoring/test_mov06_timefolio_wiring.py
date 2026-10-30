"""Dedicated engine wiring must reject duplicate consumers before any startup."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from runtime.local_config import LocalRuntimePlan
from runtime import local_runtime as runtime
from timefolio.runner import TimefolioStrategyRunnerConfig
from test_local_runtime import document


def config():
    return TimefolioStrategyRunnerConfig("fixture", "fixture", "strategies.example_signal",
                                        Decimal("1"), Decimal("100"), Decimal("200"))


def plan(tmp_path):
    doc = document(tmp_path)
    entry = {key: value for key, value in doc["producers"][0].items()
             if key not in {"provider", "symbols"}}
    doc["observation"] = entry | {"producer": "observations", "spool_root": str(tmp_path / "observations")}
    return LocalRuntimePlan.from_document(doc)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["etf", "paper", "checkpoint", "observation", "empty", "unknown", "duplicate", "implicit"])
async def test_unsafe_consumer_assembly_is_rejected_before_io(tmp_path, monkeypatch, fault):
    selected = plan(tmp_path)
    kwargs = dict(timefolio_config=config(), timefolio_producers=("kiwoom",))
    if fault == "etf":
        selected = replace(selected, etf_enabled=True)
    elif fault == "paper":
        kwargs["paper_runtime"] = object()
    elif fault == "checkpoint":
        kwargs["checkpoint_path"] = tmp_path / "unused"
    elif fault == "observation":
        selected = replace(selected, observation=None)
    else:
        kwargs["timefolio_producers"] = {"empty": (), "unknown": ("missing",),
                                         "duplicate": ("kiwoom", "kiwoom"), "implicit": None}[fault]
    monkeypatch.setattr(runtime, "LocalMarketEngine", lambda **kw: pytest.fail("engine created before safety gate"))
    with pytest.raises(ValueError):
        await runtime.run_engine(selected, SimpleNamespace(env={}), **kwargs)


@pytest.mark.asyncio
async def test_local_source_and_observation_are_wired_to_one_consumer_and_cleaned(tmp_path, monkeypatch):
    import market.local_snapshot as snapshots
    import operations.local_observation as observations
    import timefolio.runner as runner_module
    calls = []
    selected = plan(tmp_path)
    selected_config = config()

    class Engine:
        def __init__(self, **kwargs):
            self.filter = kwargs["trading_filter"]
            self.on_frame = self.on_gap = self.resume_receipt = lambda *a: None
        async def event_source(self, *, required_producers):
            calls.append(("dependencies", required_producers))
            yield {"fixture": "durable engine payload"}
        async def start(self):
            calls.append("engine-start")
        async def close(self):
            calls.append("engine-close")
    class Server:
        def __init__(self, *a, **kw): pass
        async def start(self): calls.append("server-start")
        async def close(self): calls.append("server-close")
    class Snapshot:
        def __init__(self, *a): pass
        async def start(self): calls.append("snapshot-start")
        async def close(self): calls.append("snapshot-close")
    class Observation:
        def __init__(self, **kw):
            assert kw["producer"] == "observations"
        async def close(self): calls.append("observation-close")
    class Runner:
        def __init__(self, cfg, *, bus, event_source):
            assert cfg is selected_config and isinstance(bus, Observation)
            self.source = event_source
        async def run(self, *, duration_sec):
            assert duration_sec == .25
            assert await anext(self.source) == {"fixture": "durable engine payload"}
            await self.source.aclose()
            calls.append("runner")
            return [{"status": "FAKE_ONLY"}]
    async def replay(*a, **kw): return ()
    monkeypatch.setattr(runtime, "LocalMarketEngine", Engine)
    monkeypatch.setattr(runtime, "LocalTransportServer", Server)
    monkeypatch.setattr(runtime, "replay_startup", replay)
    monkeypatch.setattr(snapshots, "LocalSnapshotWriter", Snapshot)
    monkeypatch.setattr(observations, "LocalObservationBus", Observation)
    monkeypatch.setattr(runner_module, "TimefolioStrategyRunner", Runner)
    result = await runtime.run_engine(selected, SimpleNamespace(env={}, state_dir=tmp_path),
        timefolio_config=selected_config, timefolio_producers=("kiwoom",), timefolio_duration=.25)
    assert result == [{"status": "FAKE_ONLY"}]
    assert calls.count("runner") == 1 and ("dependencies", ("kiwoom",)) in calls
    assert set(calls[-4:]) == {"server-close", "snapshot-close", "engine-close", "observation-close"}


@pytest.mark.asyncio
async def test_real_durable_spool_uds_engine_drives_fake_timefolio_without_nats(tmp_path, monkeypatch):
    import asyncio
    import json
    import os
    from dataclasses import asdict
    from feeds.local_pipeline import SpoolingMarketPublisher
    from market.hot_path import HotPathStore
    from market.local_engine import EngineBudgets, LocalMarketEngine
    from runtime.local_transport import LocalTransportClient, LocalTransportServer
    from storage.market_spool import MarketSpool, SpoolReader
    from test_timefolio_local_source import (FakeBus, FakeStrategy, install, make_runner,
                                             make_signal, trade_payload)
    async def absent_nats(*a, **kw):
        pytest.fail("NATS must never be contacted")
    monkeypatch.setattr("nats.connect", absent_nats)
    selected = plan(tmp_path)
    item = selected.producers[0]
    invalid = []
    engine = LocalMarketEngine(producer_providers={"kiwoom": "kiwoom"},
        budgets=EngineBudgets(**selected.engine), on_invalidate=lambda *a: invalid.append(a),
        hot_path=HotPathStore(), fresh_starts={"kiwoom": "run"}, verified_fresh_start=True)
    server = LocalTransportServer(selected.endpoint, budgets=selected.transport,
        on_frame=engine.on_frame, on_gap=engine.on_gap, on_resume=engine.request_resume,
        allowed_uid=os.getuid())
    await server.start()
    transport = LocalTransportClient(selected.endpoint, producer="kiwoom", incarnation="run",
        budgets=selected.transport, on_gap=lambda *a: invalid.append(a), allowed_uid=os.getuid())
    publisher = SpoolingMarketPublisher(producer="kiwoom", incarnation="run",
        spool_factory=lambda: MarketSpool(item.spool_root, "kiwoom", "run", **asdict(item.limits)),
        transport=transport, budgets=item.pipeline, env={}, on_invalid=lambda reason: invalid.append(reason))
    payload = json.dumps(trade_payload(), separators=(",", ":")).encode()
    class DurableStrategy(FakeStrategy):
        def on_event(self, event, view):
            # Real durable cursor + readback must precede the first strategy callback.
            assert publisher._spool.durable_cursor.sequence == 1
            reader = SpoolReader(item.spool_root, "kiwoom", "run", **item.limits.reader_kwargs)
            assert [record.payload for record in reader.read_batch(max_records=1, max_bytes=8192)] == [payload]
            return super().on_event(event, view)
    strategy = DurableStrategy([make_signal()])
    harness = install(monkeypatch, strategy=strategy)
    observations = FakeBus()
    runner = make_runner(bus=observations, source=engine.event_source(required_producers=("kiwoom",)))
    task = None
    try:
        await publisher.connect()
        task = asyncio.create_task(runner.run(duration_sec=.3))
        publisher.accept_payload(payload)
        results = await asyncio.wait_for(task, 3)
        assert len(results) == len(harness.browser.prepare_calls) == len(strategy.events) == 1
        assert harness.browser.prepare_calls[0][1].submit is False
        assert observations.subscribe_calls == 0 and len(observations.intents) == 1
        assert engine.hot_path.apply_seq == 1 and not invalid
        await publisher.close()
        assert publisher.stats["durable"] == publisher.stats["applied"] == 1
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if not publisher._closing:
            await publisher.close()
        await server.close()
        await engine.close()
