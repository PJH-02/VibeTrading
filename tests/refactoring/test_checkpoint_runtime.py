"""Checkpoint runtime option guards and asynchronous save ownership."""
import asyncio
import threading
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibetrading import local_runtime
from market.hot_path import HotPathStore
from market.local_engine import EngineBudgets, LocalMarketEngine
from vibetrading.market_checkpoint import CheckpointCodec
from vibetrading.market_spool import MarketSpool


def checkpoint_options(tmp_path):
    return {
        "checkpoint_path": tmp_path / "checkpoint.json",
        "checkpoint_codec": SimpleNamespace(max_bytes=64),
        "checkpoint_engine_incarnation": "engine-incarnation",
        "checkpoint_readiness": lambda: True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing",
    ["checkpoint_path", "checkpoint_codec", "checkpoint_engine_incarnation", "checkpoint_readiness"],
)
async def test_checkpoint_startup_rejects_each_missing_option_before_setup(tmp_path, missing):
    options = checkpoint_options(tmp_path)
    del options[missing]

    with pytest.raises(ValueError, match="Checkpoint startup requires"):
        await local_runtime.run_engine(
            SimpleNamespace(etf_enabled=False), SimpleNamespace(), **options
        )


@pytest.mark.asyncio
async def test_checkpoint_startup_rejects_etf_before_factory_or_account_initialization(tmp_path):
    options = checkpoint_options(tmp_path)
    factory_calls = []

    def etf_factory(**kwargs):
        factory_calls.append(kwargs)
        raise AssertionError("ETF construction must not begin")

    with pytest.raises(ValueError, match="ETF/Paper checkpoint startup"):
        await local_runtime.run_engine(
            SimpleNamespace(etf_enabled=True), SimpleNamespace(),
            etf_factory=etf_factory, **options
        )

    assert factory_calls == []


@pytest.mark.asyncio
async def test_checkpoint_startup_rejects_paper_before_runtime_access(tmp_path):
    options = checkpoint_options(tmp_path)

    class UntouchedPaperRuntime:
        def __getattribute__(self, name):
            raise AssertionError("Paper runtime must not be accessed")

    with pytest.raises(ValueError, match="ETF/Paper checkpoint startup"):
        await local_runtime.run_engine(
            SimpleNamespace(etf_enabled=False), SimpleNamespace(),
            paper_runtime=UntouchedPaperRuntime(), **options
        )


@pytest.mark.asyncio
async def test_checkpoint_save_is_single_flight_until_thread_write_finishes(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    writes = []
    active_writes = 0
    maximum_active_writes = 0
    payload = b"bounded-checkpoint"
    codec = SimpleNamespace(max_bytes=64)

    class FakeEngine:
        _checkpoint_file_task = None

        def __init__(self):
            self.capture_count = 0

        def capture_checkpoint(self, supplied_codec):
            assert supplied_codec is codec
            self.capture_count += 1
            return payload

    def blocked_write(path, content, max_bytes):
        nonlocal active_writes, maximum_active_writes
        with lock:
            active_writes += 1
            maximum_active_writes = max(maximum_active_writes, active_writes)
            writes.append((Path(path), content, max_bytes))
        started.set()
        try:
            if not release.wait(timeout=5):
                raise TimeoutError("test writer gate was not released")
        finally:
            with lock:
                active_writes -= 1

    monkeypatch.setattr("vibetrading.market_checkpoint._write_checkpoint_file", blocked_write)
    engine = FakeEngine()
    caller = asyncio.create_task(
        local_runtime.save_engine_checkpoint(engine, codec, tmp_path / "checkpoint.json")
    )
    retained_write = None
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
        retained_write = engine._checkpoint_file_task
        assert retained_write is not None and not retained_write.done()
        assert writes == [(tmp_path / "checkpoint.json", payload, codec.max_bytes)]

        with pytest.raises(RuntimeError, match="already pending"):
            await local_runtime.save_engine_checkpoint(engine, codec, tmp_path / "newer.json")
        assert engine.capture_count == 1

        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        assert not retained_write.done()
        with pytest.raises(RuntimeError, match="already pending"):
            await local_runtime.save_engine_checkpoint(engine, codec, tmp_path / "newer.json")
        assert len(writes) == 1
        assert maximum_active_writes == 1
    finally:
        release.set()
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        if retained_write is not None:
            await asyncio.wait_for(retained_write, timeout=3)

    assert engine.capture_count == 1
    assert len(writes) == 1
    assert maximum_active_writes == 1
    assert active_writes == 0
    assert engine._checkpoint_file_task.done()


@pytest.mark.asyncio
async def test_false_checkpoint_readiness_closes_before_transport_starts(tmp_path, monkeypatch):
    budgets = EngineBudgets(1, 1, 4096, 16, 65536, 60_000_000_000, 32, 2,
                           60_000_000_000, .01)
    hot_path = HotPathStore()
    codec = CheckpointCodec(max_bytes=262144, max_event_bytes=8192, max_producers=1,
        config_sha256="a" * 64, capacity=hot_path.checkpoint_capacity())
    source = LocalMarketEngine(producer_providers={"feed": "synthetic"}, budgets=budgets,
        on_invalidate=lambda *args: None, hot_path=hot_path,
        fresh_starts={"feed": "incarnation"}, verified_fresh_start=True)
    reader_limits = dict(max_disk_bytes=1_048_576, max_records=128,
        max_frame_bytes=4096, max_header_bytes=256)
    item = SimpleNamespace(producer="feed", provider="synthetic", incarnation="incarnation",
        spool_root=tmp_path / "spool", limits=SimpleNamespace(reader_kwargs=reader_limits),
        pipeline=SimpleNamespace(batch_records=16, max_bytes=65536), delivery=None)
    spool = MarketSpool(item.spool_root, item.producer, item.incarnation,
        **(reader_limits | dict(segment_max_bytes=65536, segment_max_records=64,
                                segment_max_age_seconds=60)))
    try:
        spool.flush()
    finally:
        spool.close()
    checkpoint_path = tmp_path / "checkpoint.bin"
    checkpoint_path.write_bytes(source.capture_checkpoint(codec))
    calls = {"start": 0, "close": 0}

    class FakeServer:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            calls["start"] += 1

        async def close(self):
            calls["close"] += 1

    monkeypatch.setattr(local_runtime, "LocalTransportServer", FakeServer)
    plan = SimpleNamespace(producers=(item,), engine=asdict(budgets), endpoint=tmp_path / "unused.sock",
        transport=object(), allowed_uid=0, etf_enabled=False, snapshot={})
    try:
        with pytest.raises(RuntimeError, match="checkpoint readiness validation did not permit startup"):
            await local_runtime.run_engine(plan, SimpleNamespace(state_dir=tmp_path),
                checkpoint_path=checkpoint_path, checkpoint_codec=codec,
                checkpoint_engine_incarnation=hot_path.engine_incarnation,
                checkpoint_readiness=lambda *args: False)
        assert calls == {"start": 0, "close": 1}
    finally:
        await source.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('restored',[False,True])
async def test_managed_journal_is_persisted_before_listen_and_never_initial_arms_restored(tmp_path,monkeypatch,restored):
    from test_checkpoint_startup import capture,producer
    from test_checkpoint_journal import journal
    item=producer(tmp_path)
    source,codec,_=capture([item])
    root=tmp_path/'journal'
    if restored:
        first=journal(root,codec);await first.rotate(source);await first.close()
    manager=journal(root,codec)
    calls=[];armed=[]
    original=LocalMarketEngine.arm_initial_resume
    def arm(self,*args):
        armed.append(True)
        return original(self,*args)
    class Stop(Exception):pass
    class Server:
        def __init__(self,*args,**kwargs):pass
        async def start(self):
            assert (root/'current.json').exists()
            calls.append('start')
            raise Stop()
        async def close(self):calls.append('close')
    monkeypatch.setattr(local_runtime,'LocalTransportServer',Server)
    monkeypatch.setattr(local_runtime,'HotPathStore',lambda:HotPathStore(max_trades=16,max_books=16,max_index_quotes=16))
    monkeypatch.setattr(LocalMarketEngine,'arm_initial_resume',arm)
    budgets=EngineBudgets(4,16,4096,16,65536,60_000_000_000,32,2,60_000_000_000,.01)
    item.delivery=None
    plan=SimpleNamespace(producers=(item,),engine=asdict(budgets),endpoint=tmp_path/'unused.sock',
        transport=object(),allowed_uid=0,etf_enabled=False,snapshot={})
    try:
        with pytest.raises(Stop):
            await local_runtime.run_engine(plan,SimpleNamespace(state_dir=tmp_path),
                checkpoint_journal=manager,checkpoint_readiness=lambda *args:True)
        assert calls==['start','close']
        assert bool(armed) is not restored
        assert manager._lock is None
    finally:await source.close()


@pytest.mark.asyncio
async def test_managed_journal_option_conflicts_reject_before_initialization(tmp_path):
    from test_checkpoint_startup import capture,producer
    from test_checkpoint_journal import journal
    item=producer(tmp_path)
    source,codec,_=capture([item]);manager=journal(tmp_path/'journal',codec)
    try:
        with pytest.raises(ValueError,match='without legacy'):
            await local_runtime.run_engine(SimpleNamespace(etf_enabled=False),SimpleNamespace(),
                checkpoint_journal=manager,checkpoint_codec=codec,checkpoint_readiness=lambda *args:True)
        assert not manager.root.exists()
    finally:await source.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario',['periodic_failure','headroom','readiness','corrupt'])
async def test_managed_failure_stops_before_listen_or_propagates_cadence(tmp_path,monkeypatch,scenario):
    import market.local_snapshot as snapshots
    from test_checkpoint_startup import capture,producer
    from test_checkpoint_journal import journal
    item=producer(tmp_path);item.delivery=None
    source,codec,_=capture([item]);root=tmp_path/'journal'
    if scenario!='periodic_failure':
        old=journal(root,codec);await old.rotate(source);await old.close()
    if scenario=='corrupt':(root/'current.json').write_bytes(b'{}')
    manager=journal(root,codec,max_generations=1,interval_seconds=.01)
    calls=[]
    class Server:
        def __init__(self,*args,**kwargs):pass
        async def start(self):calls.append('start')
        async def close(self):calls.append('close')
    class Snapshot:
        def __init__(self,*args):pass
        async def start(self):pass
        async def close(self):pass
    monkeypatch.setattr(local_runtime,'LocalTransportServer',Server)
    monkeypatch.setattr(local_runtime,'HotPathStore',lambda:HotPathStore(max_trades=16,max_books=16,max_index_quotes=16))
    monkeypatch.setattr(snapshots,'SnapshotBudgets',lambda **kwargs:None)
    monkeypatch.setattr(snapshots,'LocalSnapshotWriter',Snapshot)
    plan=SimpleNamespace(producers=(item,),engine=asdict(source.budgets),endpoint=tmp_path/'unused.sock',
        transport=object(),allowed_uid=0,etf_enabled=False,snapshot={})
    try:
        with pytest.raises(RuntimeError):
            await local_runtime.run_engine(plan,SimpleNamespace(state_dir=tmp_path),
                checkpoint_journal=manager,checkpoint_readiness=lambda *args:scenario!='readiness')
        assert calls==(['start','close'] if scenario=='periodic_failure' else ['close'])
        assert manager._lock is None
    finally:await source.close()
