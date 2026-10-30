import asyncio
import json
import os
import time
from types import SimpleNamespace

from operations.dashboard import latency

import pytest


@pytest.fixture(autouse=True)
def isolated_process_telemetry():
    # Other full-suite cases legitimately fill the finite production registry.
    # Each telemetry unit case starts with an independent process-sized buffer.
    latency._fresh_process_state()
    yield
    latency._fresh_process_state()


def test_observe_snapshot_has_process_provenance_and_bounded_labels():
    ended = time.monotonic_ns()
    latency.observe("unit", started_ns=ended - 2_000_000, ended_ns=ended,
                    account_scope="private-account", trace_id="private-trace",
                    queue_depth=3, oldest_queue_age_ns=4_000_000, source_age_ns=5_000_000)
    result = latency.snapshot()
    assert result["kind"] == "runtime_latency"
    assert result["end_to_end_status"] == "SEGMENTED"
    row = next(x for x in result["metrics"] if x["stage"] == "unit")
    assert row["clock_domain"] == result["clock_domain"]
    assert row["account_scope"] != "private-account"
    assert row["trace_ref"] != "private-trace"
    assert row["windows"]["1m"]["p50_ms"] == 2.0
    assert 5 <= row["source_latest_age_ms"] < 10
    assert row["queue_depth"] == 3
    assert "private-account" not in json.dumps(result)
    assert "private-trace" not in json.dumps(result)


def test_bad_or_slow_observation_never_raises_or_waits_on_lock():
    assert latency.observe("bad", started_ns=5, ended_ns=4) is False
    assert latency._LOCK.acquire(False)
    try:
        started = time.monotonic_ns()
        assert latency.observe("contended", started_ns=started, ended_ns=started) is False
    finally:
        latency._LOCK.release()


def test_receipt_clock_stamp_is_process_local_and_single_use():
    from feeds.collection_timing import is_local_receive_stamp, receipt_clock
    _,stamp=receipt_clock()
    assert is_local_receive_stamp(stamp)
    assert not is_local_receive_stamp(stamp)
    assert not is_local_receive_stamp(stamp+1)


def test_sampling_rate_and_full_scope_hash_avoid_prefix_collisions():
    end=time.monotonic_ns()
    prefix="account"*20
    latency.observe("sampling",started_ns=end-1000,ended_ns=end,account_scope=prefix+"x")
    latency.observe("sampling",started_ns=end-950,ended_ns=end,account_scope=prefix+"x")
    latency.observe("sampling",started_ns=end-900,ended_ns=end,account_scope=prefix+"y",error=True)
    rows=[x for x in latency.snapshot()["metrics"] if x["stage"] == "sampling"]
    assert len(rows)==2
    assert rows[0]["account_scope"] != rows[1]["account_scope"]
    assert sum(x["seen"] for x in rows)==3
    assert sum(x["sampled"] for x in rows)==2
    assert sum(x["sample_out"] for x in rows)==1


def test_reader_strictly_rejects_nested_secrets_and_clock_mismatch(tmp_path):
    end=time.monotonic_ns()
    latency.observe("reader",started_ns=end-1000,ended_ns=end,source_id="feed-a")
    valid=latency.snapshot()
    path=tmp_path/"snapshot.json"
    path.write_text(json.dumps(valid))
    assert latency.read_latency_snapshots([path],now_ms=valid["generated_ms"],max_age_ms=0)
    tampered=json.loads(json.dumps(valid))
    tampered["resources"]["api_key"]="secret"
    path.write_text(json.dumps(tampered))
    assert not latency.read_latency_snapshots([path],now_ms=valid["generated_ms"],max_age_ms=0)
    tampered=json.loads(json.dumps(valid))
    if tampered["metrics"]:
        tampered["metrics"][0]["clock_domain"]="monotonic_ns:foreign"
        path.write_text(json.dumps(tampered))
        assert not latency.read_latency_snapshots([path],now_ms=valid["generated_ms"],max_age_ms=0)


def test_fork_starts_a_fresh_metric_and_clock_domain():
    if not hasattr(os,"fork"):
        return
    now=time.monotonic_ns()
    latency.observe("before-fork",started_ns=now-1000,ended_ns=now)
    parent=latency.snapshot()
    rfd,wfd=os.pipe()
    pid=os.fork()
    if pid==0:
        try:
            os.close(rfd)
            os.write(wfd,json.dumps(latency.snapshot()).encode())
            os.close(wfd)
        finally:
            os._exit(0)
    os.close(wfd)
    chunks=[]
    while True:
        chunk=os.read(rfd,65536)
        if not chunk:break
        chunks.append(chunk)
    os.close(rfd)
    os.waitpid(pid,0)
    child=json.loads(b"".join(chunks))
    assert child["process_id"] != parent["process_id"]
    assert child["clock_domain"] != parent["clock_domain"]
    assert child["metrics"] == []


def test_exporter_write_failure_isolated_and_collector_exports_when_configured(tmp_path,monkeypatch):
    stop=asyncio.Event()
    def fail_write(*_):
        stop.set()
        raise OSError("monitor storage unavailable")
    monkeypatch.setattr(latency,"_atomic_write",fail_write)
    asyncio.run(latency.export_metrics(tmp_path,stop=stop,interval_seconds=1,instance_id="collector-test"))
    assert latency.snapshot()["resources"]["export_errors"] == 1
    assert latency.snapshot()["resources"]["export_write_errors"] == 1

    import runtime.local_runtime as local_runtime
    started=asyncio.Event()
    async def fake_export(directory,*,stop,interval_seconds,instance_id):
        assert directory==tmp_path
        assert instance_id.startswith("collector-")
        started.set()
        await stop.wait()
    class Publisher:
        _closing=False
        stats={"failed":0,"accepted":0,"durable":0,"applied":0}
        def __init__(self,**_):pass
        async def connect(self): await asyncio.sleep(0)
        async def close(self):pass
    class Collector:
        async def run(self,publisher,duration_sec=None):
            await asyncio.wait_for(started.wait(),1)
    class Transport:
        def __init__(self,*args,**kwargs):pass
    monkeypatch.setattr(local_runtime,"SpoolingMarketPublisher",Publisher)
    monkeypatch.setattr(local_runtime,"LocalTransportClient",Transport)
    monkeypatch.setattr(local_runtime,"create_spool",lambda _item:None)
    monkeypatch.setattr(latency,"export_metrics",fake_export)
    item=SimpleNamespace(provider="unit",symbols=(),producer="p",incarnation="i",delivery=None,pipeline=object())
    plan=SimpleNamespace(endpoint="unused",transport=object(),allowed_uid=os.geteuid())
    settings=SimpleNamespace(env={"DASHBOARD_TELEMETRY_DIR":str(tmp_path)})
    asyncio.run(local_runtime.run_collector(plan,item,settings,collector=Collector()))


def test_strategy_lifecycle_is_separate_from_latency_samples():
    latency.observe_strategy("s-1", "acct", "PAPER", "STOPPED")
    row = next(x for x in latency.snapshot()["strategies"] if x["strategy_id"] == "s-1")
    assert row["status"] == "STOPPED"
    assert row["account_scope"] != "acct"


def test_retention_ring_is_bounded_and_reports_overwrite_drops(monkeypatch):
    clock=[time.monotonic_ns()]
    monkeypatch.setattr(latency.time,"monotonic_ns",lambda:clock[0])
    for index in range(latency._MAX_SAMPLES_PER_KEY + 1):
        ended=clock[0]
        latency.observe("retention-test", started_ns=ended-1_000_000, ended_ns=ended)
        if index < latency._MAX_SAMPLES_PER_KEY:
            clock[0]+=latency._SAMPLE_PERIOD_NS
    row = next(x for x in latency.snapshot()["metrics"] if x["stage"] == "retention-test")
    assert row["windows"]["15m"]["n"] == latency._MAX_SAMPLES_PER_KEY
    assert row["drops"] == 1


def test_export_and_explicit_reader_reject_stale_and_oversized(tmp_path):
    stop = asyncio.Event()

    async def run_once():
        task = asyncio.create_task(latency.export_metrics(tmp_path, stop=stop, interval_seconds=1,instance_id="role-stable"))
        for _ in range(100):
            files = list(tmp_path.glob("runtime-latency-*.json"))
            if files:
                break
            await asyncio.sleep(.005)
        stop.set()
        await task
        return files

    files = asyncio.run(run_once())
    assert len(files) == 1
    parsed = json.loads(files[0].read_text())
    assert parsed["process_id"] == latency.snapshot()["process_id"]
    assert latency.read_latency_snapshots(files, now_ms=parsed["generated_ms"], max_age_ms=0)
    stale=latency.read_latency_snapshots(files, now_ms=parsed["generated_ms"] + 1, max_age_ms=0)
    assert stale and stale[0]["snapshot_stale"] is True
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (latency._MAX_SNAPSHOT_BYTES + 1))
    assert not latency.read_latency_snapshots([oversized], now_ms=parsed["generated_ms"])
    assert not latency.read_latency_snapshots("/tmp/ignored", now_ms=parsed["generated_ms"])


def test_export_instance_id_is_stable_across_process_domains_and_separates_roles(tmp_path):
    role_id=latency.telemetry_instance_id("collector","producer-safe")
    assert role_id==latency.telemetry_instance_id("collector","producer-safe")
    engine_id=latency.telemetry_instance_id("engine","endpoint-safe")
    assert role_id!=engine_id
    original_process=latency.snapshot()["process_id"]
    async def once(instance_id):
        stop=asyncio.Event()
        write=latency._atomic_write
        def write_and_stop(path,payload):
            write(path,payload)
            stop.set()
        latency._atomic_write=write_and_stop
        try: await latency.export_metrics(tmp_path,stop=stop,instance_id=instance_id)
        finally: latency._atomic_write=write
    asyncio.run(once(role_id))
    first=json.loads((tmp_path/f"runtime-latency-{role_id}.json").read_text())
    latency._fresh_process_state()
    assert latency.snapshot()["process_id"]!=original_process
    asyncio.run(once(role_id))
    asyncio.run(once(engine_id))
    files=list(tmp_path.glob("runtime-latency-*.json"))
    assert len(files)==2
    second=json.loads((tmp_path/f"runtime-latency-{role_id}.json").read_text())
    assert first["process_id"]!=second["process_id"]
    assert first["clock_domain"]!=second["clock_domain"]


def test_source_age_advances_and_metric_stales_after_fifteen_seconds(monkeypatch,tmp_path):
    clock=[5_000_000_000]
    monkeypatch.setattr(latency.time,"monotonic_ns",lambda:clock[0])
    latency.observe("freshness",started_ns=clock[0]-1_000_000,ended_ns=clock[0],source_age_ns=5_000_000)
    first=next(x for x in latency.snapshot()["metrics"] if x["stage"]=="freshness")
    assert first["source_latest_age_ms"]==5
    assert first["last_observation_age_ms"]==0
    assert first["stale"] is False
    clock[0]+=20_000_000_000
    second=next(x for x in latency.snapshot()["metrics"] if x["stage"]=="freshness")
    assert second["source_latest_age_ms"]==20005
    assert second["last_observation_age_ms"]==20000
    assert second["stale"] is True
    path=tmp_path/"fresh-export.json"
    exported=latency.snapshot()
    path.write_text(json.dumps(exported))
    accepted=latency.read_latency_snapshots([path],now_ms=exported["generated_ms"],max_age_ms=0)
    assert accepted and accepted[0]["snapshot_stale"] is False


def test_real_pipeline_hooks_measure_receipt_queue_flush_and_applied_ack():
    from core.models import AssetClass, DataGapEvent, InstrumentId, Venue
    from feeds.collection_timing import receipt_clock
    from feeds.local_pipeline import PipelineBudgets, SpoolingMarketPublisher
    from feeds.feed_recovery import EngineCursor
    from runtime.local_transport import TransportAck

    class Spool:
        def __init__(self): self.cursor=EngineCursor("collector","run",0)
        @property
        def appended_cursor(self): return self.cursor
        def append(self,payload):
            self.cursor=EngineCursor("collector","run",self.cursor.sequence+1)
            return SimpleNamespace(cursor=self.cursor)
        def flush(self): return self.cursor
        def close(self): pass

    class Transport:
        def __init__(self): self.acks=asyncio.Queue()
        async def connect(self): pass
        async def request_resume(self,cursor): return TransportAck("RESUMED",cursor,"0"*64)
        def try_send(self,frame):
            self.acks.put_nowait(TransportAck("APPLIED",frame.cursor,frame.digest))
            return True
        async def receive_ack(self): return await self.acks.get()
        async def close(self): pass

    async def exercise():
        transport=Transport()
        budgets=PipelineBudgets(max_records=4,max_bytes=4096,max_frame_bytes=1024,
                                batch_records=1,group_sync_ms=1)
        publisher=SpoolingMarketPublisher(producer="collector",incarnation="run",
            spool_factory=Spool,transport=transport,budgets=budgets,env={},on_invalid=lambda _:None)
        await publisher.connect()
        wall,stamp=receipt_clock()
        instrument=InstrumentId(venue=Venue.BINANCE,asset_class=AssetClass.CRYPTO_SPOT,
                                raw_symbol="BTCUSDT",std_symbol="BTCUSDT")
        event=DataGapEvent(instrument=instrument,venue=Venue.BINANCE,provider="local-engine",
                           reason="scripted",event_id="scripted-event",wallclock_ts_ns=wall,
                           receive_ts_ns=wall,receive_monotonic_ns=stamp)
        # Exercise the same already-prepared packet path used by protocol-1 bus dispatch.
        from runtime.bus import prepare_market_event
        await publisher.publish_market_packet(prepare_market_event(event))
        for _ in range(200):
            if publisher.stats["applied"]: break
            await asyncio.sleep(.005)
        assert publisher.stats["applied"] == 1
        await publisher.close()

    asyncio.run(exercise())
    rows={row["stage"]:row for row in latency.snapshot()["metrics"]}
    for stage in ("receive_normalize","ingress_queue","group_wait","spool_flush","ingress_to_apply_ack"):
        assert rows[stage]["seen"] == 1
        assert rows[stage]["sampled"] == 1


def test_scripted_native_runner_owner_records_provenance_and_mode(tmp_path,monkeypatch):
    import sys
    from pathlib import Path
    refactoring=Path(__file__).parent/"refactoring"
    sys.path.insert(0,str(refactoring))
    try:
        import test_exec01_owner
        from execution.execution_ledger import ExecutionLedger
        original_handoff=ExecutionLedger.mark_handoff
        def delayed_handoff(self,*args,**kwargs):
            time.sleep(.005)
            return original_handoff(self,*args,**kwargs)
        monkeypatch.setattr(ExecutionLedger,"mark_handoff",delayed_handoff)
        asyncio.run(test_exec01_owner.test_explicit_cross_venue_source_plan_uses_synthetic_stock_route_and_facts(tmp_path))
    finally:
        sys.path.remove(str(refactoring))
    from hashlib import sha256
    rows=latency.snapshot()["metrics"]
    expected_scope=sha256(b'["dummy-stock","nh-fixture","cash","sandbox"]').hexdigest()[:24]
    for stage in ("owner_preflight","adapter_send_begin","adapter_ack","strategy_callback"):
        assert any(row["stage"]==stage and row["strategy_id"]=="exec01_native_stock_fixture"
                   and row["account_scope"]==expected_scope and row["mode"]=="sandbox" for row in rows)
    presend=next(row for row in rows if row["stage"]=="adapter_send_begin")
    assert presend["windows"]["15m"]["p50_ms"] >= 4


def test_reader_roundtrips_canonical_mode_full_scope_and_lifecycle_currency(tmp_path):
    from hashlib import sha256
    raw_scope="example account scope exceeds the former 96-byte prefix limit-"+"x"*120
    expected=sha256(raw_scope.encode("utf-8")).hexdigest()[:24]
    now=time.monotonic_ns()
    latency.observe("roundtrip",started_ns=now-1000,ended_ns=now,account_scope=raw_scope,mode="PAPER")
    latency.observe_strategy("roundtrip-strategy",raw_scope,"PAPER","RUNNING",currency="KRW")
    data=latency.snapshot()
    path=tmp_path/"roundtrip.json"
    path.write_text(json.dumps(data))
    read=latency.read_latency_snapshots([path],now_ms=data["generated_ms"],max_age_ms=0)[0]
    metric=next(row for row in read["metrics"] if row["stage"]=="roundtrip")
    strategy=next(row for row in read["strategies"] if row["strategy_id"]=="roundtrip-strategy")
    assert metric["mode"]=="paper" and metric["account_scope"]==expected
    assert strategy["mode"]=="paper" and strategy["account_scope"]==expected
    assert strategy["currency"]=="KRW"


def test_engine_apply_hook_excludes_post_apply_strategy_work():
    import sys
    from pathlib import Path
    refactoring=Path(__file__).parent/"refactoring"
    sys.path.insert(0,str(refactoring))
    try:
        import test_local_engine
        import time as time_module
        engine,_,_=test_local_engine.build(post_apply_callbacks=(lambda *_:time_module.sleep(.05),),
                                            snapshot_clock=lambda:time.time_ns())
        try:
            engine.on_frame(test_local_engine.frame(1))
        finally:
            asyncio.run(engine.close())
    finally:
        sys.path.remove(str(refactoring))
    row=next(row for row in latency.snapshot()["metrics"] if row["stage"]=="market_apply"
             and row["source_id"]==test_local_engine.PRODUCER)
    assert row["trace_ref"]
    assert row["windows"]["15m"]["p50_ms"] < 40


def test_transport_ack_metric_requires_a_pending_applied_frame():
    import sys
    from pathlib import Path
    refactoring=Path(__file__).parent/"refactoring"
    sys.path.insert(0,str(refactoring))
    try:
        import test_local_transport
        asyncio.run(test_local_transport.test_explicit_resume_received_applied_and_separate_durable_receipts())
    finally:
        sys.path.remove(str(refactoring))
    rows=[row for row in latency.snapshot()["metrics"] if row["stage"]=="local_transport_applied_ack"]
    assert rows and rows[0]["sampled"]==1
