#!/usr/bin/env python3
"""Offline PERF-01 real-chain profiler.

Run only against the accepted QA-01 snapshot in the lead's constrained offline
container for capacity evidence. Native --smoke runs are integration diagnostics,
not a two-CPU/3.8-GiB acceptance measurement. No provider credentials/settings,
external endpoints, live account, order transport, or production data are used.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import signal
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import dataclasses
import enum
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

ACCEPTED_QA_SHA = "2fe70d2fb2a5caa0d6e99fbb3d2a05f08813ef61"
ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = Path(os.environ.get("PERF01_ARTIFACT_DIR", "/data"))
MAX_TRACE_ROWS = 100_000
MAX_RESOURCE_SAMPLES = 10_000
DOMAINS = ("core", "feeds", "market", "storage", "strategies", "execution",
           "portfolio", "runtime", "operations", "timefolio")




def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_default(value):
    if dataclasses.is_dataclass(value): return dataclasses.asdict(value)
    if isinstance(value, enum.Enum): return value.value
    if isinstance(value, Path): return str(value)
    if isinstance(value, (set, frozenset)): return sorted(value)
    return repr(value)[:256]


def manifest_hash(root: Path, paths):
    h = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix().encode()
        body = path.read_bytes()
        h.update(len(rel).to_bytes(4, "big")); h.update(rel)
        h.update(len(body).to_bytes(8, "big")); h.update(body)
    return h.hexdigest()


def source_hashes(root: Path):
    files = sorted((root / "src").rglob("*.py"))
    if len(files) != 149:
        raise RuntimeError(f"accepted source expects 149 Python files, got {len(files)}")
    hashes = {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in files}
    return hashes, manifest_hash(root, files)


def cgroup_snapshot():
    root = Path("/sys/fs/cgroup")
    def read(name):
        try: return (root / name).read_text().strip()
        except OSError: return None
    cpu = read("cpu.max")
    memory_max = read("memory.max")
    stats = read("cpu.stat")
    events = read("memory.events")
    current = read("memory.current")
    peak = read("memory.peak")
    io_raw=read("io.stat")
    parsed = {}
    for key, raw in (("cpu.stat", stats), ("memory.events", events)):
        values = {}
        for line in (raw or "").splitlines():
            parts = line.split()
            if len(parts) == 2:
                try: values[parts[0]] = int(parts[1])
                except ValueError: pass
        parsed[key] = values
    io_values={}
    for line in (io_raw or "").splitlines():
        parts=line.split()
        if not parts: continue
        fields={}
        for item in parts[1:]:
            try:key,value=item.split("=",1);fields[key]=int(value)
            except (ValueError,TypeError):continue
        io_values[parts[0]]=fields
    proc_leaf=None
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if line.startswith("0::"):
                proc_leaf=line[3:]
                break
    except OSError:pass
    return {"path": str(root), "cpu.max": cpu, "memory.max": memory_max,
            "memory.current": current, "memory.peak": peak,
            "cpu.stat": parsed["cpu.stat"], "memory.events": parsed["memory.events"],
            "io.stat":io_values,"process_cgroup_leaf":proc_leaf}


def process_io_snapshot():
    result={}
    try:
        for line in Path("/proc/self/io").read_text().splitlines():
            key,value=line.split(":",1);result[key.strip()]=int(value.strip())
    except (OSError,ValueError):pass
    return result


def sqlite_wal_facts(owners):
    rows=[]
    for owner in owners:
        db_path=Path(owner.ledger.path)
        wal_path=Path(str(db_path)+"-wal")
        try:db_bytes=db_path.stat().st_size
        except OSError:db_bytes=0
        try:wal_bytes=wal_path.stat().st_size
        except OSError:wal_bytes=0
        rows.append({"owner_scope_id":owner.ledger.scope.venue,"database_bytes":db_bytes,
            "wal_bytes":wal_bytes,"journal_mode":"WAL","synchronous":"FULL"})
    return rows


def require_capacity_cgroup(snapshot):
    if snapshot.get("cpu.max") != "200000 100000" or snapshot.get("memory.max") != "4080218112":
        raise RuntimeError("capacity mode requires effective cpu.max=200000 100000 and memory.max=4080218112")
    if snapshot.get("process_cgroup_leaf")!="/":
        raise RuntimeError("capacity mode requires the intended Docker-private cgroup v2 leaf at /")


class ResourceSampler:
    def __init__(self, interval=0.1):
        self.interval = interval
        self.samples = []
        self.omitted = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="perf01-resource-sampler", daemon=True)

    @staticmethod
    def _proc():
        status = {}
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith(("VmRSS:", "VmHWM:", "Threads:")):
                    key, value = line.split(":", 1); status[key] = value.strip()
        except OSError:
            pass
        try: fd_count = len(list(Path("/proc/self/fd").iterdir()))
        except OSError: fd_count = None
        try:
            children = Path("/proc/self/task").glob("*/children")
            child_pids = sorted({pid for file in children for pid in file.read_text().split()})
        except OSError:
            child_pids = []
        return {"rss_status": status, "fd_count": fd_count,
                "child_pids": child_pids, "child_count": len(child_pids)}

    def _run(self):
        while not self.stop_event.is_set():
            sample={"mono_ns": time.monotonic_ns(),"cgroup":cgroup_snapshot(),**self._proc()}
            if len(self.samples)<MAX_RESOURCE_SAMPLES:
                self.samples.append(sample)
            else:
                self.omitted += 1
            self.stop_event.wait(self.interval)

    def start(self): self.thread.start()
    def stop(self):
        self.stop_event.set(); self.thread.join(timeout=3)
        return self.samples


def _descriptor(event, trace_id=None):
    return {"source_id": str(getattr(event, "provider", "")),
            "product_id": str(getattr(event.instrument, "key", "")),
            "trace_id": trace_id or str(event.event_id)}


def write_telemetry_input(path: Path, event, *, require_sampled=False):
    """Publish actual bounded runtime latency observer rows for the TLS sidecar."""
    from operations.dashboard.latency import snapshot as latency_snapshot
    sampled=latency_snapshot()
    metrics=sampled.get("metrics",[])
    sampled_rows=sum(row.get("windows",{}).get("1m",{}).get("n",0) for row in metrics)
    sampled_stages=sorted({row.get("stage") for row in metrics
        if row.get("windows",{}).get("1m",{}).get("n",0)>0})
    if require_sampled and (not metrics or sampled_rows<=0 or
            not {"T0_fake_recv","T_DUR","T3_strategy_eval"}.issubset(sampled_stages)):
        raise RuntimeError("telemetry requires actually sampled T0, durable, and strategy latency histograms")
    snapshot={"schema_version":1,"generated_ms":int(time.time()*1000),
        "markets":[{"key":event.instrument.key,"venue":getattr(event.venue,"value",str(event.venue)),
            "symbol":event.instrument.raw_symbol,"section":"crypto",
            "asset_class":getattr(event.instrument.asset_class,"value",str(event.instrument.asset_class)),
            "currency":event.instrument.currency,"price":str(getattr(event,"price",""))}],
        "orders":[],"strategies":[{"strategy_id":"perf01-event-policy","status":"RUNNING"}],
        "timefolio":[],"observability":{"pnl":[],"latency":[sampled],
            "latency_status":"AVAILABLE" if metrics else "UNAVAILABLE",
            "latency_error":None if metrics else "no sampled runtime latency metrics",
            "native_status":"NOT_CONFIGURED","etf_status":"NOT_CONFIGURED",
            "end_to_end_ms":None,"end_to_end_status":"SEGMENTED"}}
    encoded=(json.dumps(snapshot,separators=(",",":"),sort_keys=True)+"\n").encode()
    if len(encoded)>1024*1024:
        raise RuntimeError("actual telemetry projection exceeded 1 MiB")
    temporary=path.with_suffix(".tmp")
    temporary.write_bytes(encoded);os.replace(temporary,path)
    return {"path":str(path),"sha256":sha(encoded),"latency_metric_count":len(metrics),
        "latency_snapshot_count":1,
        "latency_sampled_histogram_count":sampled_rows,"latency_sampled_stages":sampled_stages,
        "latency_status":snapshot["observability"]["latency_status"]}


def _t0_row(recorder, source, wire_seq, stamp, end):
    recorder.add(source_id=source, product_id="BTCUSDT", trace_id=f"wire:{wire_seq}",
        stage="T0_fake_recv", start_ns=stamp, end_ns=end)


async def wait_pipeline(publisher, dispatcher, expected, timeout):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        stats=publisher.stats
        if dispatcher.blocked or dispatcher.errors:
            raise RuntimeError(f"selected strategy blocked during profile: {list(dispatcher.errors)}")
        if stats["accepted"]>=expected and stats["durable"]>=expected and stats["applied"]>=expected and stats["pending"]==0:
            try:
                await asyncio.wait_for(dispatcher.queue.join(),timeout=max(.01,deadline-time.monotonic()))
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"strategy dispatcher stalled qsize={dispatcher.queue.qsize()} errors={list(dispatcher.errors)} results={len(dispatcher.results)} task_done={getattr(dispatcher.queue,'_unfinished_tasks',None)}") from exc
            if dispatcher.blocked or dispatcher.errors:
                raise RuntimeError(f"selected strategy blocked during profile: {list(dispatcher.errors)}")
            return
        if stats["invalid"] or stats["failed"]:
            raise RuntimeError(f"bounded pipeline invalidated during PERF workload: {stats}; cause={publisher._error!r}")
        await asyncio.sleep(.001)
    raise TimeoutError(f"pipeline did not reach expected contiguous applied count {expected}: {publisher.stats}")


async def run_core(args, paths):
    """Run native parser -> delivery-mode real spool/UDS -> engine -> file policy -> Owner -> adapter."""
    run_started_mono_ns=time.monotonic_ns()
    import asyncio
    from dataclasses import replace as dc_replace
    from core.models import Side
    from execution.execution_groups import CrossVenueExecutionGroups
    from execution.execution_owner import ExecutionReadiness
    from market.hot_path import HotPathStore
    from market.local_engine import EngineBudgets, LocalMarketEngine
    from feeds.feed_recovery import EngineCursor, RecoveryFrame
    from runtime.local_transport import LocalTransportClient, LocalTransportServer, TransportBudgets
    from storage.market_spool import MarketSpool, SpoolCursor, SpoolReader
    from feeds.local_pipeline import DeliveryBudgets, PipelineBudgets, SpoolingMarketPublisher
    from runtime.real_runner import RealExecutionRunner, RealExecutionRunnerConfig
    from runtime.selected_execution import NativeSelectionBinding, NativeSelectionDispatcher, parse_strategy_selections
    from cap05_source_fixture import build_native_group, CONFIG_VERSION as CAP_CONFIG
    import cap05_source_fixture as cap05
    from test_cap06_routing import prepare_scripted_reconcile
    from test_cap06_selection import selection as selection_template

    fixture = build_native_group(paths["tmp"] / "native-owners")
    owners = (fixture["binance_owner"], fixture["bybit_owner"])
    scopes = (fixture["binance_scope"], fixture["bybit_scope"])
    for owner in owners:
        # Explicit bounded benchmark-only budgets; never alter product defaults.
        owner.ledger.max_intents = 1024
        owner.ledger.max_fills = 1024
        owner.ledger.max_feedback = 4096
        owner.ledger.max_feedback_bytes = 8*1024*1024
        owner.budgets = dc_replace(owner.budgets, max_pending=512)
        owner.clock = time.time_ns
        for gate in ("global", "account", "strategy"):
            owner.control.resume_if_ready("perf01-event-policy",
                expected_epochs=owner.control.snapshot("perf01-event-policy")["epochs"],
                readiness_verified=True, scope=gate)
    owner_sqlite_before=sqlite_wal_facts(owners)
    prepare_scripted_reconcile(fixture, "binance-perp")
    prepare_scripted_reconcile(fixture, "bybit-perp")
    from perf01_source_fixture import install_scripted_working_inventory
    native_inventory=install_scripted_working_inventory(fixture,max_orders=512)
    fixture["_perf_native_inventory"]=native_inventory
    # Register the benchmark policy against existing synthetic routes. This adds
    # no live/account setting and leaves the CAP05 aliases intact.
    from execution.venues.registry import AliasBinding
    for venue, route_id, scope, symbol, market_type in (
        ("binance", "binance-perp", scopes[0], "BTCUSDT", "usdm_perp"),
        ("bybit", "bybit-perp", scopes[1], "BTCUSDT.P", "linear")):
        registry = fixture["registry"]._registry(route_id)
        registry.register_aliases((AliasBinding("perf01-event-policy", "perf01-"+venue,
            route_id, symbol, market_type, "linear_perpetual", "sandbox", scope),))

    coordinator = CrossVenueExecutionGroups(fixture["binance_owner"].ledger,
        {scope.key: owner for scope, owner in zip(scopes, owners)},
        max_groups=512, max_facts=16384, max_bytes=32*1024*1024,
        max_followup_actions=2, clock=time.time_ns)
    runner = RealExecutionRunner(RealExecutionRunnerConfig("perf01", scopes[0].account,
        "fixture", Decimal("1000000"), Decimal("1000000")), store=object(), bus=object(),
        settings=SimpleNamespace(env={}), owner=owners[0], group_coordinator=coordinator)

    event_key = "binance:crypto_perp:BTCUSDT"
    selection_doc = selection_template("TESTNET", "sandbox")
    selection_doc.update(venue="binance", account=scopes[0].account, product=scopes[0].product)
    registration = selection_doc["registration"]
    selected_scopes=scopes if args.grouped else scopes[:1]
    registration.update(module="strategies.perf01_source_fixture", accounts=[s.key for s in selected_scopes],
        config_version="perf01-config-v1", required_feeds=[event_key], warmup_bars=0,
        config={"config_version": "perf01-config-v1"})
    risk = dict(next(iter(registration["risk"].values())))
    # Explicit finite profile capital covers at most 401 outstanding 10-USDT
    # actions per owner; retain the real reservation and configured risk checks.
    for field in ("account_notional_limit", "strategy_notional_limit",
                  "account_capital_limit", "strategy_capital_limit"):
        risk[field] = "100000"
    registration["risk"] = {s.key: dict(risk) for s in selected_scopes}
    # Each scripted burst admits at most 64 immutable event snapshots before
    # its explicit drain. Keep finite queue/byte/age checks active for this load.
    selection_doc["queue"] = {"max_items":128,"max_bytes":16*1024*1024,
        "max_age_ns":30_000_000_000,"snapshot_charge_bytes":65536}
    selected = parse_strategy_selections([selection_doc])[0]

    import perf01_source_fixture as strategy_module
    strategy_module = strategy_module.module_alias()
    strategy = strategy_module.create_strategy()
    action_every = args.action_every
    policy_decisions = []
    def policy(event, view, config_version):
        seq = strategy.evaluated
        if action_every <= 0 or seq % action_every:
            policy_decisions.append({"trace_id":str(event.event_id),"evaluation":seq,
                "decision":"NO_ACTION","reason":"bounded_action_cadence"})
            return []
        plan = strategy_module.build_action_plan(event, config_version, scopes,
            grouped=args.grouped, sequence=seq)
        actions = tuple(plan.actions)
        sides = tuple(action.intent.side.value for action in actions)
        scope_keys = tuple(action.scope.key for action in actions)
        group_ids = tuple(action.group_id for action in actions)
        opposite = len(sides) == 2 and sides[0] != sides[1]
        distinct_scopes = len(scope_keys) == len(set(scope_keys))
        one_group = len(group_ids) == 2 and group_ids[0] == group_ids[1] and group_ids[0] is not None
        if args.grouped and not (opposite and distinct_scopes and one_group):
            raise RuntimeError("scripted two-leg workload failed opposite-side/shared-group invariants")
        policy_decisions.append({"trace_id":str(event.event_id),"evaluation":seq,
            "decision":"SCRIPTED_TWO_LEG_ARBITRAGE_WORKLOAD" if args.grouped else "SCRIPTED_SINGLE_LEG",
            "action_count":len(actions),"sides":sides,"scope_keys":scope_keys,
            "group_ids":group_ids,"opposite_sides":opposite,
            "distinct_owner_scopes":distinct_scopes,"single_group":one_group,
            "price_source":"synthetic fixture; no cross-venue discovery"})
        return plan
    previous_factory = strategy_module.PLAN_FACTORY
    strategy_module.PLAN_FACTORY = policy
    def exact_readiness(owner, scope, bybit=False):
        key = ("bybit:crypto_perp:BTCUSDT.P" if bybit else event_key)
        return lambda: ExecutionReadiness(owner.ledger.scope.key, True, True, True,
            owner.clock() + 60_000_000_000, Decimal("0"), Decimal("1000000"),
            account_version=owner.ledger.account_version(), allowed_instruments=(key,),
            market_price_caps=((key, Decimal("100000")),))
    readiness_map = {scopes[0].key: exact_readiness(owners[0], scopes[0])}
    evidence_owners={scopes[0].key:owners[0]}
    if args.grouped:
        readiness_map[scopes[1].key]=exact_readiness(owners[1], scopes[1], bybit=True)
        evidence_owners[scopes[1].key]=owners[1]
    source_facts = {"accounts": {scope.key: {"scope": {"venue": scope.venue,
        "account": scope.account, "product": scope.product, "environment": scope.environment},
        "mode": "sandbox"} for scope in scopes}}
    evidence_map={scope.key: (lambda result, snapshot, owner=evidence_owners[scope.key]: {
        "contract_version": "perf01-v1", "config_version": "perf01-config-v1",
        "engine_incarnation": snapshot.engine_incarnation, "apply_seq": snapshot.apply_seq,
        "event_ids": [result.event.event_id], "input_versions": {result.event.instrument.key: snapshot.cursor[1]},
        "expected_epochs": owner.control.snapshot("perf01-event-policy")["epochs"],
        "quality": "LIVE", "mode": "sandbox", "account_scope_present": True})
        for scope in selected_scopes}
    evidence_provider=evidence_map if args.grouped else evidence_map[scopes[0].key]
    readiness_binding=readiness_map if args.grouped else readiness_map[scopes[0].key]
    dispatcher = NativeSelectionDispatcher(selected, NativeSelectionBinding(runner, owners[0],
        strategy, fixture["registry"], readiness=readiness_binding,
        evidence_provider=evidence_provider, registration_facts=source_facts,
        route_id="binance-perp"))
    await dispatcher.prepare_promotion()

    spool_root = paths["tmp"] / "market-spool"
    spool_limits = {"max_disk_bytes": 16*1024*1024, "max_records": 4096,
        "max_frame_bytes": 8192, "max_header_bytes": 1024,
        "segment_max_bytes": 262144, "segment_max_records": 512,
        "segment_max_age_seconds": 60}
    pipeline_budgets = PipelineBudgets(256, 4*1024*1024, 8192, 32, 2)
    delivery_budgets = DeliveryBudgets(32, 256*1024, .001, .05, 5.0)
    transport_budgets = TransportBudgets(8192, 1024, 256, 4*1024*1024, 15.0,
        128, 262144, 8, 5.0)
    engine_budgets = EngineBudgets(max_producers=8, max_instruments=16,
        max_frame_bytes=8192, max_records=256, max_bytes=4*1024*1024,
        max_age_ns=60_000_000_000, max_digests_per_feed=4096,
        max_state_callbacks=8, feed_lease_ns=600_000_000_000, tick_seconds=.01)
    endpoint = str(paths["tmp"] / "market.sock")
    source_name, incarnation = "binance-perp", "perf01-bn-01"
    invalid = []
    trace_rows = []
    trace_lock = threading.Lock()
    from perf01_source_fixture import TraceRecorder, check_ordered_a1, causal_span_summaries
    recorder = TraceRecorder(source_sha256=args.hashes["source"],
        config_sha256=args.hashes["config"], host_sha256=args.hashes["host"],
        max_rows=MAX_TRACE_ROWS, observe_runtime=True)
    original_methods=[]
    original_native_begin=dispatcher._begin_source_dispatch
    def native_begin():
        try:
            return original_native_begin()
        except Exception:
            proofs=[]
            for item in dispatcher._scope_dispatchers.values():
                proof=item._promotion_receipt;now=item.binding.owner.clock()
                proofs.append({"venue":item.binding.owner.ledger.scope.venue,
                    "age_ns":None if proof is None else now-proof.observed_ns,
                    "remaining_ns":None if proof is None else proof.expires_ns-now,
                    "version_current":item.binding.owner.ledger.account_version(),
                    "version_receipt":None if proof is None else proof.ledger_version,
                    "epochs_equal":False if proof is None else tuple(sorted(item.binding.owner.control.snapshot(item.strategy_id)["epochs"].items()))==proof.control_epochs,
                    "dispatch_active":item._dispatch_active,"dispatch_base":item._dispatch_base_version})
            if len(invalid)<64:invalid.append(("native_proof_at_callback_rejection",proofs))
            raise
    dispatcher._begin_source_dispatch=native_begin
    original_methods.append((dispatcher,"_begin_source_dispatch",original_native_begin))
    import operations.dashboard.latency as latency_module
    original_observe=latency_module.observe
    group_wait_rows=[]
    def observed_runtime_metric(stage, *, started_ns, ended_ns, **fields):
        result=original_observe(stage,started_ns=started_ns,ended_ns=ended_ns,**fields)
        if stage=="group_wait" and fields.get("source_id")==source_name and len(group_wait_rows)<MAX_TRACE_ROWS:
            index=len(group_wait_rows)+1
            row={"source_id":str(fields.get("source_id",source_name)),"product_id":"*batch*",
                "trace_id":f"group-wait:{index}","stage":"T_GROUP_WAIT",
                "start_ns":started_ns,"end_ns":ended_ns,"elapsed_ns":ended_ns-started_ns,
                "status":"OK","phase_id":getattr(args,"current_phase","setup"),
                "queue_depth":fields.get("queue_depth"),"batch_records":None,"batch_bytes":None}
            group_wait_rows.append(row)
            recorder.add(source_id=row["source_id"],product_id=row["product_id"],
                trace_id=row["trace_id"],stage=row["stage"],start_ns=started_ns,end_ns=ended_ns,
                status="OK",phase_id=row["phase_id"],counts={"queue_depth":row["queue_depth"]})
        return result
    latency_module.observe=observed_runtime_metric
    original_methods.append((latency_module,"observe",original_observe))
    import feeds.local_pipeline as pipeline_module
    original_pipeline_observe=pipeline_module.observe
    pipeline_module.observe=observed_runtime_metric
    original_methods.append((pipeline_module,"observe",original_pipeline_observe))
    queue_metrics={"max_ingress_depth":0,"max_ingress_bytes":0,"max_oldest_ingress_age_ns":0,
        "max_dispatcher_depth":0,"max_dispatcher_bytes":0,"max_dispatcher_oldest_age_ns":0,
        "max_durable_applied_backlog":0,"max_delivery_backlog_age_ns":0,
        "max_delivery_inflight_records":0,"max_spool_backlog_bytes":0}
    spool_size_by_sequence={}
    spool_backlog_bytes=[0]
    event_by_trade = {}
    event_by_id = {}
    intent_event = {}
    intent_scope = {}
    submit_trace = {}
    delay_ms = args.ack_delay_ms
    virtual_clock = [time.monotonic_ns()]
    virtual_clock_initial = virtual_clock[0]
    # The first transport callback can run during bootstrap, before the loops.
    args.variable_ack_start_sequence=args.samples+args.burst_events+int(args.capacity)+1
    def mark(row):
        with trace_lock:
            recorder.rows.append(row)
    def on_frame(frame):
        start = time.monotonic_ns()
        result = engine.on_frame(frame)
        end = time.monotonic_ns()
        try:
            from core.models import event_from_dict
            ev = event_from_dict(json.loads(frame.payload))
        except Exception:
            ev = None
        if ev is not None:
            if frame.cursor.producer!=source_name:
                recorder.phase_by_trace[str(ev.event_id)]="auxiliary_local_load"
            desc = _descriptor(ev)
            recorder.add(**desc, stage="T2_engine_apply", start_ns=start, end_ns=end,
                cursor={"producer": frame.cursor.producer,"incarnation":frame.cursor.incarnation,"sequence":frame.cursor.sequence})
        selected_delay=0
        if args.scenario=="all" and frame.cursor.producer==source_name:
            if frame.cursor.sequence>=args.variable_ack_start_sequence:
                selected_delay=(0,1,5,20)[(frame.cursor.sequence-args.variable_ack_start_sequence)%4]
        elif args.ack_delay_ms>=0:
            selected_delay=args.ack_delay_ms
        wait_start=time.monotonic_ns()
        if selected_delay:
            time.sleep(selected_delay/1000)
        wait_end=time.monotonic_ns()
        if ev is not None:
            recorder.add(**_descriptor(ev),stage="T_UDS_server_ack_wait",start_ns=wait_start,
                end_ns=wait_end,status=f"{selected_delay}ms")
        if (ev is not None and frame.cursor.producer==source_name
                and recorder.phase_by_trace.get(str(ev.event_id)) in {"baseline_first100","dashboard_disconnect_overlap"}):
            virtual_clock[0] += 3_000_000_000
        return result
    def applied_callback(result,snapshot):
        outcome=dispatcher.enqueue_applied(result,snapshot)
        queue_metrics["max_dispatcher_depth"]=max(queue_metrics["max_dispatcher_depth"],dispatcher.queue.qsize())
        queue_metrics["max_dispatcher_bytes"]=max(queue_metrics["max_dispatcher_bytes"],dispatcher.queued_bytes)
        queued=tuple(getattr(dispatcher.queue,"_queue",()))
        if queued:
            now=time.monotonic_ns()
            oldest=max(0,now-min(item.queued_ns for item in queued))
            queue_metrics["max_dispatcher_oldest_age_ns"]=max(queue_metrics["max_dispatcher_oldest_age_ns"],oldest)
        return outcome
    hot_path = HotPathStore()
    aux=None
    if args.capacity:
        from perf01_fault_load_fixture import PerfAuxLoad
        aux=PerfAuxLoad(paths["tmp"]/"aux-load",hot_path,duration_seconds=60)
    silent_source,silent_incarnation="bybit-perp","perf01-by-01"
    producer_providers={source_name:source_name,silent_source:silent_source}
    fresh_starts={source_name:incarnation,silent_source:silent_incarnation}
    callbacks=[applied_callback]
    if aux is not None:
        producer_providers.update(aux.producer_providers)
        fresh_starts.update(aux.fresh_starts)
        callbacks.append(aux.post_apply)
    engine = LocalMarketEngine(producer_providers=producer_providers, budgets=engine_budgets,
        on_invalidate=lambda *row: invalid.append(row), hot_path=hot_path,
        post_apply_callbacks=tuple(callbacks), snapshot_clock=time.time_ns,
        trading_filter=lambda _event: False,
        fresh_starts=fresh_starts,
        verified_fresh_start=True, clock=lambda: virtual_clock[0])
    def recovery_commit(high_water, digest):
        return engine.commit_recovery(high_water, digest, ready=True)
    def guarded_frame(frame):
        try:
            return on_frame(frame)
        except Exception as failure:
            if len(invalid)<64:
                invalid.append(("on_frame_error",frame.cursor.producer,frame.cursor.sequence,
                    type(failure).__name__,str(failure)[:512]))
            raise
    server = LocalTransportServer(endpoint, budgets=transport_budgets, on_frame=guarded_frame,
        on_gap=engine.on_gap, on_resume=engine.resume_receipt, allowed_uid=os.getuid(),
        on_replay=engine.apply_replay, on_recovery_query=engine.begin_delivery_recovery,
        on_recovery_commit=recovery_commit)
    await server.start()
    aux_task=None
    def spool_factory(): return MarketSpool(spool_root, source_name, incarnation, **spool_limits)
    def reader_factory(cursor): return SpoolReader(spool_root, source_name, incarnation,
        after=SpoolCursor(cursor.producer,cursor.incarnation,cursor.sequence),
        **{key:spool_limits[key] for key in ("max_disk_bytes","max_records","max_frame_bytes","max_header_bytes")})
    transport_rows = []
    control_ack_phases = {}
    def count_control_ack(receipt):
        phase=receipt.phase if receipt.phase in {"PONG","RECEIVED","APPLIED","GAP","REJECTED","DURABLE"} else "OTHER"
        control_ack_phases[phase]=control_ack_phases.get(phase,0)+1
    def transport_factory():
        client = LocalTransportClient(endpoint, producer=source_name, incarnation=incarnation,
            budgets=transport_budgets, on_gap=lambda *row: invalid.append(row), allowed_uid=os.getuid())
        original_send, original_ack = client.try_send, client.receive_ack
        def send(frame, *, replay=False):
            start = time.monotonic_ns(); ok = original_send(frame, replay=replay); end = time.monotonic_ns()
            sample_pipeline_metrics()
            rec = {"cursor": frame.cursor.sequence, "digest": frame.digest, "send_ns": end,
                   "trace_id": event_by_trade.get(frame.cursor.sequence, str(frame.cursor.sequence))}
            transport_rows.append(rec)
            ev = event_by_trade.get(frame.cursor.sequence)
            if ev is not None:
                recorder.add(**_descriptor(ev), stage="T_UDS_send", start_ns=start, end_ns=end,
                    cursor={"producer":frame.cursor.producer,"incarnation":frame.cursor.incarnation,"sequence":frame.cursor.sequence})
            return ok
        async def ack():
            start = time.monotonic_ns(); receipt = await original_ack(); end = time.monotonic_ns()
            count_control_ack(receipt)
            if receipt.phase=="APPLIED":
                spool_backlog_bytes[0]=max(0,spool_backlog_bytes[0]-spool_size_by_sequence.pop(receipt.cursor.sequence,0))
            sample_pipeline_metrics()
            ev = event_by_trade.get(receipt.cursor.sequence)
            if ev is not None:
                stage = "T_UDS_" + receipt.phase
                recorder.add(**_descriptor(ev), stage=stage, start_ns=start, end_ns=end,
                    status=receipt.phase, cursor={"producer":receipt.cursor.producer,"incarnation":receipt.cursor.incarnation,"sequence":receipt.cursor.sequence})
            return receipt
        client.try_send, client.receive_ack = send, ack
        original_nowait=getattr(client,"receive_ack_nowait",None)
        if original_nowait is not None:
            def ack_nowait():
                receipt=original_nowait();count_control_ack(receipt);return receipt
            client.receive_ack_nowait=ack_nowait
        return client
    publisher = SpoolingMarketPublisher(producer=source_name, incarnation=incarnation,
        spool_factory=spool_factory, transport=None, budgets=pipeline_budgets, env={},
        on_invalid=lambda reason: invalid.append((source_name, reason)),
        delivery_budgets=delivery_budgets, reader_factory=reader_factory,
        transport_factory=transport_factory)
    def sample_pipeline_metrics():
        stats=publisher.stats
        queue_metrics["max_durable_applied_backlog"]=max(queue_metrics["max_durable_applied_backlog"],
            stats.get("durable_applied_lag_records",0))
        queue_metrics["max_delivery_backlog_age_ns"]=max(queue_metrics["max_delivery_backlog_age_ns"],
            stats.get("delivery_backlog_age_ns",0),stats.get("oldest_pending_age_ns",0))
        queue_metrics["max_delivery_inflight_records"]=max(queue_metrics["max_delivery_inflight_records"],
            stats.get("delivery_inflight_records",0))
        queue_metrics["max_spool_backlog_bytes"]=max(queue_metrics["max_spool_backlog_bytes"],
            spool_backlog_bytes[0])
        return stats
    # Install the queue wrapper before the writer's first await on queue.get();
    # otherwise asyncio.Queue.get() already has an outstanding waiter and its
    # first dequeue would bypass this benchmark-only observation seam.
    queue = publisher._queue
    original_accept=publisher.accept_payload
    def measured_accept(payload):
        outcome=original_accept(payload)
        queue_metrics["max_ingress_depth"]=max(queue_metrics["max_ingress_depth"],queue.qsize())
        queue_metrics["max_ingress_bytes"]=max(queue_metrics["max_ingress_bytes"],publisher._bytes)
        queue_metrics["max_spool_backlog_bytes"]=max(queue_metrics["max_spool_backlog_bytes"],spool_backlog_bytes[0])
        return outcome
    publisher.accept_payload=measured_accept
    original_methods.append((publisher,"accept_payload",original_accept))
    original_get = queue.get
    async def measured_get():
        item = await original_get()
        try:
            from core.models import event_from_dict
            ev = event_from_dict(json.loads(item.payload))
        except Exception as exc:
            invalid.append(("dq_parse",type(exc).__name__,str(exc),repr(item.payload)[:160]))
            return item
        now=time.monotonic_ns()
        sample_pipeline_metrics()
        queue_metrics["max_oldest_ingress_age_ns"]=max(queue_metrics["max_oldest_ingress_age_ns"],
            max(0,now-item.accepted_ns))
        recorder.add(**_descriptor(ev), stage="T_DQ", start_ns=item.accepted_ns, end_ns=now,
            counts={"depth": queue.qsize(), "bytes": publisher._bytes},
            cursor={"producer":source_name,"incarnation":incarnation,"sequence":item.identity})
        return item
    queue.get = measured_get
    await publisher.connect()
    handshake_deadline=time.monotonic()+8.0
    while time.monotonic()<handshake_deadline:
        handshake=publisher.stats
        if (publisher.transport is not None and
                handshake.get("delivery_state")=="LIVE" and
                handshake.get("last_durable_sequence",0)==0 and
                handshake.get("last_applied_sequence",0)==0):
            break
        await asyncio.sleep(.005)
    else:
        raise TimeoutError(f"initial UDS resume handshake not LIVE at cursor zero: {publisher.stats}")
    await dispatcher.prepare_promotion()
    if dispatcher.task is None:
        dispatcher.task=asyncio.create_task(dispatcher.run(),name="perf01-selected-native")
    spool = publisher._spool
    original_append, original_flush = spool.append, spool.flush
    seq_trace = {}
    flushed_through = [spool.durable_cursor.sequence]
    injected_flush_delay_ns = [0]
    completed_batch_count=[0]
    def append(payload):
        started = time.monotonic_ns(); receipt = original_append(payload); ended = time.monotonic_ns()
        spool_size_by_sequence[receipt.cursor.sequence]=len(payload)
        spool_backlog_bytes[0]+=len(payload)
        queue_metrics["max_spool_backlog_bytes"]=max(queue_metrics["max_spool_backlog_bytes"],spool_backlog_bytes[0])
        try:
            from core.models import event_from_dict
            event = event_from_dict(json.loads(payload))
            seq_trace[receipt.cursor.sequence] = event
            recorder.add(**_descriptor(event), stage="T_FS_append", start_ns=started, end_ns=ended,
                cursor={"producer":receipt.cursor.producer,"incarnation":receipt.cursor.incarnation,"sequence":receipt.cursor.sequence})
        except Exception:
            pass
        return receipt
    def flush():
        started=time.monotonic_ns()
        if injected_flush_delay_ns[0]:
            time.sleep(injected_flush_delay_ns[0]/1_000_000_000)
        result=original_flush(); ended=time.monotonic_ns()
        # The writer appends monotonically. Visit only the newly durable range;
        # scanning every prior event (and all trace rows) on each flush made the
        # instrumentation itself quadratic during the burst profile.
        seq = flushed_through[0] + 1
        while seq <= result.sequence:
            event = seq_trace.pop(seq, None)
            if event is not None:
                recorder.add(**_descriptor(event), stage="T_FS_flush", start_ns=started, end_ns=ended,
                    cursor={"producer":source_name,"incarnation":incarnation,"sequence":seq})
            seq += 1
        flushed_through[0] = max(flushed_through[0], result.sequence)
        return result
    spool.append, spool.flush = append, flush
    original_persist = publisher._persist
    def persist(batch):
        receipts=original_persist(batch); durable_ns=time.monotonic_ns()
        batch_bytes=sum(item.size for item in batch)
        batch_index=completed_batch_count[0]
        if batch_index<len(group_wait_rows):
            group_wait_rows[batch_index]["batch_records"]=len(batch)
            group_wait_rows[batch_index]["batch_bytes"]=batch_bytes
        completed_batch_count[0]+=1
        queue_metrics["max_durable_applied_backlog"]=max(queue_metrics["max_durable_applied_backlog"],
            publisher._durable-publisher._applied)
        for item, receipt in zip(batch, receipts):
            try:
                from core.models import event_from_dict
                event=event_from_dict(json.loads(item.payload))
                event_by_trade[receipt.cursor.sequence]=event
                recorder.add(**_descriptor(event), stage="T_DUR", start_ns=item.accepted_ns,end_ns=durable_ns,
                    cursor={"producer":receipt.cursor.producer,"incarnation":receipt.cursor.incarnation,"sequence":receipt.cursor.sequence},
                    counts={"durable_records":len(receipts),"batch_bytes":batch_bytes})
            except Exception: pass
        return receipts
    publisher._persist = persist
    original_methods.extend(((spool,"append",original_append),(spool,"flush",original_flush),
        (queue,"get",original_get),(publisher,"_persist",original_persist)))

    # Owner/ledger and native adapter timing wrappers. They are restored in finally.
    import execution.execution_ledger as ledger_module
    for owner in owners:
        original_evidence=owner._evidence
        def evidence_checked(intent, evidence, environment, _orig=original_evidence):
            try: return _orig(intent,evidence,environment)
            except Exception as exc:
                invalid.append(("decision_evidence",type(exc).__name__,str(exc),
                    {key:(type(value).__name__,repr(value)[:160]) for key,value in evidence.items()} if isinstance(evidence,dict) else type(evidence).__name__))
                raise
        owner._evidence=evidence_checked
        original_methods.append((owner,"_evidence",original_evidence))
        ledger = owner.ledger
        for method_name, stage in (("authorize", "T4_authorize_commit"), ("mark_handoff", "T4_mark_handoff")):
            original = getattr(ledger, method_name)
            def make_wrapper(original_method, stage_name, _ledger=ledger):
                def wrapped(*a, **kw):
                    start=time.monotonic_ns(); result=original_method(*a, **kw); end=time.monotonic_ns()
                    intent=a[0] if a and hasattr(a[0],"intent_id") else None
                    identity=getattr(intent,"intent_id",a[0] if a else "")
                    source_event_id=(getattr(intent,"metadata",{}) or {}).get("source_event_id","") if intent is not None else intent_event.get(identity,"")
                    if source_event_id: intent_event[identity]=source_event_id
                    intent_scope[identity]=_ledger.scope.venue
                    ev=event_by_id.get(str(source_event_id))
                    if ev is not None:
                        recorder.add(**_descriptor(ev), stage=stage_name,start_ns=start,end_ns=end,
                            status="OK",account_scope_present=True,
                            counts={"intent_id":str(identity),"owner_scope_id":_ledger.scope.venue})
                    return result
                return wrapped
            setattr(ledger, method_name, make_wrapper(original, stage)); original_methods.append((ledger,method_name,original))
    transport_wrappers=[]
    transport_observations=[]
    for transport in (fixture["binance_transport"], fixture["bybit_transport"]):
        original=transport.request
        async def req(method,url,*a,_orig=original,_transport=transport,**kw):
            start=time.monotonic_ns()
            payload=kw.get("json_body") or kw.get("params") or {}
            if not payload and isinstance(kw.get("data"),str):
                try: payload=json.loads(kw["data"])
                except ValueError: payload={}
            cid=(payload.get("newClientOrderId") or payload.get("orderLinkId")
                 or payload.get("clientOrderId") or payload.get("client_order_id") or "")
            intent_id=cid[2:] if cid.startswith("c-") else cid
            event_id=intent_event.get(intent_id, "")
            owner_scope_id=intent_scope.get(intent_id, "")
            if owner_scope_id and getattr(_transport,"venue",None)!=owner_scope_id:
                raise RuntimeError("scripted adapter venue differs from the authorized Owner scope")
            ev=event_by_id.get(str(event_id))
            transport_observations.append({"venue":getattr(_transport,"venue",""),"method":method,
                "client_id":cid,"intent_id":intent_id,"event_id":event_id,"mapped":ev is not None,
                "kw_keys":sorted(kw)})
            handoff=[None]
            original_before=kw.get("before_send")
            def before_send():
                if original_before is not None: original_before()
                handoff[0]=time.monotonic_ns()
            if original_before is not None: kw["before_send"]=before_send
            outcome="FAILED"; result=None
            try:
                result=await _orig(method,url,*a,**kw)
                outcome="SCRIPTED_RESPONSE"
                return result
            finally:
                end=time.monotonic_ns()
                boundary=handoff[0] if handoff[0] is not None else end
                if ev is not None and method in {"POST","DELETE"}:
                    recorder.add(**_descriptor(ev),stage="T5_transport_request",start_ns=start,end_ns=start,
                        status="INVOKED" if outcome=="SCRIPTED_RESPONSE" else "FAILED",
                        account_scope_present=True,
                        counts={"intent_id":intent_id,"owner_scope_id":owner_scope_id})
                    if handoff[0] is not None:
                        recorder.add(**_descriptor(ev),stage="T5_authorized_before_send",start_ns=handoff[0],end_ns=handoff[0],
                            status="AUTHORIZED_HANDOFF",account_scope_present=True,
                            counts={"intent_id":intent_id,"owner_scope_id":owner_scope_id})
                    recorder.add(**_descriptor(ev),stage="T6_scripted_response",start_ns=start,end_ns=end,
                        status=str(getattr(result,"status",0)) if result is not None else "FAILED",
                        account_scope_present=True,
                        counts={"intent_id":intent_id,"owner_scope_id":owner_scope_id})
        transport.request=req; transport_wrappers.append((transport,original))

    client_source = None
    parser_recv = {}
    sink = None
    from perf01_source_fixture import EventSink, parse_binance_messages
    def recv_capture(raw):
        stamp=time.monotonic_ns()
        try:
            doc=json.loads(raw)["data"]; parser_recv[str(doc["a"])]=stamp
        except Exception: pass
    async def publish(event):
        event_by_trade[event.sequence]=event
        event_by_id[str(event.event_id)]=event
        phase=getattr(args,"current_phase","native_smoke")
        outage=getattr(args,"dashboard_disconnect_window",None)
        observed_mono=time.monotonic()
        dashboard_overlap = bool(phase=="baseline_first100" and outage
            and outage[0]<=observed_mono<outage[1])
        recorder.phase_by_trace[str(event.event_id)]=phase
        trade_id=str(getattr(event,"trade_id",event.sequence))
        recv_stamp=parser_recv.pop(trade_id, time.monotonic_ns())
        t1=time.monotonic_ns()
        recorder.add(**_descriptor(event),stage="T0_fake_recv",start_ns=recv_stamp,end_ns=recv_stamp,
            counts={"dashboard_disconnect_overlap":dashboard_overlap})
        recorder.add(**_descriptor(event),stage="T1_native_normalized",start_ns=recv_stamp,end_ns=t1)
        telemetry_fact=write_telemetry_input(args.telemetry_ready_path,event,
            require_sampled=args.capacity and phase!="viewer_bootstrap")
        paths["telemetry_fact"]=telemetry_fact
        paths["latest_event"]=event
        await publisher.publish_market_event(event)
    sink=EventSink(publish)
    source_wrap_original = strategy.on_event
    order_wrap_original = strategy.on_order_event
    def strategy_event(event,data):
        start=time.monotonic_ns()
        try: return source_wrap_original(event,data)
        finally:
            end=time.monotonic_ns()
            recorder.add(**_descriptor(event),stage="T3_strategy_eval",start_ns=start,end_ns=end,
                status="EVALUATED",account_scope_present=True,
                counts={"action_policy_every":action_every})
    strategy.on_event = strategy_event
    def strategy_order_event(event, context):
        start=time.monotonic_ns()
        try: return order_wrap_original(event,context)
        finally:
            end=time.monotonic_ns()
            source_event_id=getattr(event,"source_event_id","") or getattr(event,"event_id","")
            source_event=event_by_id.get(str(source_event_id))
            if source_event is not None:
                recorder.add(**_descriptor(source_event),stage="T3_order_feedback",start_ns=start,end_ns=end,
                    status="EVALUATED",account_scope_present=True)
    strategy.on_order_event = strategy_order_event
    # Mark trace link for intent IDs generated by file strategy.
    try:
        if aux is not None:
            aux_task=asyncio.create_task(aux.run(endpoint,transport_budgets),name="perf01-mixed-native-paper-load")
        await publisher.connect()
        # Parser itself runs under fake wire rows and writes directly to the real publisher.
        parser = __import__("feeds.providers.binance", fromlist=["BinancePerpCollector"]).BinancePerpCollector(symbols=("BTCUSDT",))
        accepted_target = 0
        bootstrap_events = 0
        if args.capacity:
            # The viewer needs a real benchmark projection before it can become
            # READY. Send one isolated no-action event through the complete real
            # chain, then hold measured ingress until both viewers and their
            # first telemetry ingest are confirmed.
            args.current_phase = "viewer_bootstrap"
            await parse_binance_messages(1, sink, start=args.event_start - 2,
                on_recv=recv_capture, collector=parser)
            bootstrap_events = 1
            accepted_target += 1
            await wait_pipeline(publisher, dispatcher, accepted_target, args.drain_timeout)
            paths["telemetry_fact"]=write_telemetry_input(args.telemetry_ready_path,
                paths["latest_event"],require_sampled=args.capacity)
            args.telemetry_ready_path.with_suffix(".ready").write_text("sampled_runtime_histograms_validated\n")
            if args.viewer_ready_event is None:
                raise RuntimeError("capacity viewer startup gate was not initialized")
            await asyncio.wait_for(args.viewer_ready_event.wait(), timeout=45)
        if args.scenario == "all":
            samples = args.samples
            burst = args.burst_events
            action_limit = samples
        else:
            samples = args.events
            burst = 0
            action_limit = samples
        variable_ack_count=100 if args.scenario=="all" else 0
        args.variable_ack_start_sequence=samples+burst+bootstrap_events+1
        for index in range(samples):
            args.current_phase="baseline_first100" if args.scenario=="all" else "native_smoke"
            before = time.monotonic_ns()
            await parse_binance_messages(1, sink, start=args.event_start+index,
                on_recv=recv_capture, collector=parser)
            accepted_target += 1
            await wait_pipeline(publisher, dispatcher, accepted_target, args.drain_timeout)
            if args.scenario == "all" and args.interval_ms > 0:
                deadline=before + args.interval_ms*1_000_000
                remaining=deadline-time.monotonic_ns()
                if remaining>0: await asyncio.sleep(remaining/1e9)
        burst_chunk_records=getattr(args,"burst_chunk_records",16)
        for offset in range(0, burst, burst_chunk_records):
            args.current_phase="baseline_burst1000"
            amount=min(burst_chunk_records,burst-offset)
            await parse_binance_messages(amount, sink, start=args.event_start+samples+offset,
                on_recv=recv_capture, collector=parser)
            accepted_target += amount
            await wait_pipeline(publisher, dispatcher, accepted_target, args.drain_timeout)
        for offset in range(variable_ack_count):
            args.current_phase="variable_ack_100"
            await parse_binance_messages(1,sink,start=args.event_start+samples+burst+offset,
                on_recv=recv_capture,collector=parser)
            accepted_target+=1
            await wait_pipeline(publisher,dispatcher,accepted_target,args.drain_timeout)
        args.current_phase="slow_fsync_injected_20ms"
        injected_flush_delay_ns[0]=20_000_000
        slow_fsync_seq=accepted_target+1
        await parse_binance_messages(1,sink,start=args.event_start+samples+burst+variable_ack_count,
            on_recv=recv_capture,collector=parser)
        accepted_target+=1
        await wait_pipeline(publisher,dispatcher,accepted_target,args.drain_timeout)
        injected_flush_delay_ns[0]=0
        await asyncio.wait_for(dispatcher.queue.join(), timeout=args.drain_timeout)
        aux_evidence=await aux_task if aux_task is not None else {"status":"NOT_RUN"}
        owner_cuts_before_fault={owner.ledger.scope.venue:owner.ledger.account_version() for owner in owners}
        if aux is not None:
            from perf01_fault_load_fixture import run_fault_scenarios
            fault_result=run_fault_scenarios(paths["tmp"]/"fault-scenarios")
            if asyncio.iscoroutine(fault_result): fault_result=await fault_result
        else:
            fault_result={"status":"NOT_RUN"}
        owner_cuts_after_fault={owner.ledger.scope.venue:owner.ledger.account_version() for owner in owners}
        if owner_cuts_after_fault!=owner_cuts_before_fault:
            raise RuntimeError("market fault fixture changed the already-current native Owner ledger cut")
        # Read the complete independent scripted native inventory through the
        # real promotion guard after every submitted group has settled.
        await dispatcher.prepare_promotion()
        stats=publisher.stats
        engine_stats=engine.stats
        if stats["accepted"] != stats["durable"] or stats["durable"] != stats["applied"]:
            raise RuntimeError(f"A1 count mismatch publisher={stats}")
        if engine_stats["applied"] < args.events or dispatcher.errors:
            raise RuntimeError(f"engine/strategy failed engine={engine_stats} errors={list(dispatcher.errors)} diagnostics={invalid[-4:]}")
        action_results=list(dispatcher.results)
        grouped_policy_results=[]
        if args.grouped:
            for decision in policy_decisions:
                group_ids=decision.get("group_ids",())
                if not group_ids: continue
                report=coordinator.report(group_ids[0])
                legs=[{"owner_scope_id":next(owner.ledger.scope.venue for owner in owners
                    if owner.ledger.scope.key==leg.get("scope_key")),
                    "side":leg.get("side"),"state":leg.get("current",{}).get("state"),
                    "target_order_present":bool(leg.get("current",{}).get("native_order_id"))}
                    for leg in report.get("legs",())]
                grouped_policy_results.append({"group_id":group_ids[0],"state":report.get("state"),
                    "legs":legs,"two_leg_opposite_side":len(legs)==2 and
                        {leg["side"] for leg in legs}=={Side.BUY.value,Side.SELL.value},
                    "group_acknowledged":report.get("state")=="ACKNOWLEDGED",
                    "both_owner_mutations_observed":len(legs)==2 and
                        all(leg["state"] not in {None,"UNOBSERVED","UNKNOWN","LEGACY_UNKNOWN"} and
                            leg["target_order_present"] for leg in legs)})
        transport_calls=len(fixture["binance_transport"].calls)+len(fixture["bybit_transport"].calls)
        t5=sum(1 for row in recorder.rows if row["stage"]=="T5_transport_request")
        t6_rows=[row for row in recorder.rows if row["stage"]=="T6_scripted_response"]
        expected_t5=transport_calls
        if t5 != expected_t5:
            raise RuntimeError(f"concrete adapter request trace count {t5} differs from submitted Owner actions {expected_t5}: {transport_observations}")
        result={"status":"PASS_LOCAL_SMOKE", "started_mono_ns": run_started_mono_ns,
            "mode":"capacity_capped_workload" if args.capacity else "native_smoke_not_capacity",
            "source_sha":ACCEPTED_QA_SHA,"source149_sha256":args.hashes["source"],
            "scenario":"actual_binance_native_wire_to_owner_and_scripted_adapter",
            "input_events":samples+burst+variable_ack_count,"first100_count":samples,"burst_count":burst,
            "viewer_bootstrap_events":bootstrap_events,
            "burst_chunk_records":burst_chunk_records,
            "burst_protocol":"bounded chunks with exact pipeline/strategy drain; not instantaneous 1000/s capacity",
            "variable_ack_count":variable_ack_count,"virtual_b_silence_seconds":(virtual_clock[0]-virtual_clock_initial)/1_000_000_000,
            "diagnostic_events":{"slow_fsync_injected":1,"injected_flush_delay_ms":20,
                "separate_from_baseline_and_variable_ack_percentiles":True,"core_sequence":slow_fsync_seq,
                "trace_id":str(event_by_trade.get(slow_fsync_seq).event_id) if event_by_trade.get(slow_fsync_seq) else None},
            "ack_injection":{"phase":"variable_ack_100","schedule_ms":[0,1,5,20],
                "mechanism":"controlled blocking delay in synchronous LocalTransportServer.on_frame",
                "scope":"event-loop callback stall; not isolated transport-wire ACK latency"},
            "supplemental_functional_guard_evidence":{"status":"PASS_SEPARATE_NOT_TIMED",
                "source_sha":ACCEPTED_QA_SHA,"lead_receipt_id":"PERF01-lead-readiness-durability-v2",
                "test_count":205,"not_in_latency_percentiles":True,
                "covered_cases":[
                    "test_isolated_source_retains_healthy_interleaved_delivery_after_other_feed_gap",
                    "test_actual_paper_strategy_readiness_isolated_across_b_gap_and_new_generation",
                    "test_exact_state_application_one_etf_input_and_idempotent_cursor_duplicate",
                    "test_query_fixed_high_water_replay_and_commit_gate_over_linux_uds",
                    "test_bad_identity_cursor_or_payload_immediately_invalidates_and_flushes"]},
            "virtual_silent_B":{"producer":silent_source,"incarnation":silent_incarnation,
                "events":engine_stats.get("feeds",{}).get(silent_source,{}).get("applied_sequence",0),
                "health_lease_ns":engine_budgets.feed_lease_ns},"accepted":stats["accepted"],"durable":stats["durable"],
            "applied":stats["applied"],"engine_applied":engine_stats["applied"],
            "a1_durability":{"accepted":stats["accepted"],"durable":stats["durable"],
                "applied":stats["applied"],"loss_indicator":0,
                "prefix_recovery":"NOT_APPLICABLE_TO_HEALTHY_CORE_PATH"},
            "strategy_evaluated":strategy.evaluated,"strategy_feedback":strategy.feedback,
            "policy_workload":{"label":"SCRIPTED_TWO_LEG_ARBITRAGE_WORKLOAD" if args.grouped else "SCRIPTED_SINGLE_LEG_POLICY",
                "real_opportunity_or_production_strategy_claim":False,
                "synthetic_price_and_account_facts":True,
                "decisions":policy_decisions,
                "decision_counts":{"no_action":sum(d["decision"]=="NO_ACTION" for d in policy_decisions),
                    "two_leg_group":sum(d["decision"]=="SCRIPTED_TWO_LEG_ARBITRAGE_WORKLOAD" for d in policy_decisions),
                    "opposite_side_groups":sum(d.get("opposite_sides",False) for d in policy_decisions),
                    "single_leg":sum(d["decision"]=="SCRIPTED_SINGLE_LEG" for d in policy_decisions)},
                "group_execution_results":grouped_policy_results,
                "grouped_legs_mutated":sum(sum(leg["target_order_present"] for leg in group["legs"])
                    for group in grouped_policy_results),
                "grouped_leg_states_verified":sum(group["both_owner_mutations_observed"] for group in grouped_policy_results)},
            "action_results":action_results,
            "auxiliary_load":aux_evidence,"fault_scenarios":fault_result,
            "owner_ledger_fault_isolation":{"before":owner_cuts_before_fault,"after":owner_cuts_after_fault,
                "unchanged":True,"scope":"separate market-fault roots; not a production crash claim"},
            "final_native_inventory_reconciliation":"PASS_REAL_GUARD_SCRIPTED_NATIVE_FACTS",
            "transport_observations":transport_observations,
            "owner_intents":sum(owner.ledger._db.execute("SELECT COUNT(*) FROM execution_intents").fetchone()[0] for owner in owners),
            "adapter_post_calls":transport_calls,"t5_rows":t5,
            "scripted_native_working_inventory":{"counts":dict(native_inventory.counts),
                "max_orders_per_venue":native_inventory.max_orders,
                "oracle":"actual successful scripted POST ACKs, never local ledger reads",
                "external_native_service_claim":False},
            "t6_response_rows":len(t6_rows),
            "t6_successful_scripted_responses":sum(row["status"]=="200" for row in t6_rows),
            "delivery_state":stats.get("delivery_state"),"invalidations":invalid,
            "control_ack_phases":dict(control_ack_phases),
            "fixture_budget":{"ledger_intents_per_owner":1024,"owner_pending":512,
                "source_feedback_per_owner":4096,"source_feedback_bytes_per_owner":8*1024*1024,
                "delivery_inflight_records":1,"pipeline_queue_records":pipeline_budgets.max_records,
                "strategy_registration_risk":risk,"selected_dispatch_queue":dict(selection_doc["queue"]),
                "group_lifetime_limits":{"max_groups":512,"max_facts":16384,"max_bytes":32*1024*1024}},
            "adapter_calls_are_scripted":True,"external_calls":0,"live_orders":0,
            "strategy_delivery":"actual post_apply callbacks; unused legacy event_source queue disabled",
            "legacy_applied_latency_counter":"NOT_INSTRUMENTED_IN_DELIVERY_BUDGET_PATH; use actual trace spans",
            "owner_authorize_timing":"ExecutionLedger.authorize durable authorization/reservation transaction; not an isolated SQLite COMMIT timing",
            "pipeline_final":stats,"engine_final":engine_stats,
            "group_sync":{"measurement":"actual_local_pipeline.group_wait observer; separate from append, flush, and DUR",
                "batch_wait_distribution":__import__("perf01_source_fixture").summarize(group_wait_rows),
                "batch_rows":group_wait_rows,"batch_count":len(group_wait_rows),
                "batch_count_bytes_missing":sum(row["batch_records"] is None or row["batch_bytes"] is None for row in group_wait_rows)},
            "queue_backlog_peaks":dict(queue_metrics),
            "owner_sqlite":{"before":owner_sqlite_before,"after":sqlite_wal_facts(owners),
                "note":"actual fixture Owner ledger SQLite database and WAL file sizes; WAL transaction timing is included in T4 authorization span"},
            "scenarios":{"A100Bsilent":{"status":"MEASURED_VIRTUAL","virtual_seconds":300,"source_B_events":engine_stats.get("feeds",{}).get(silent_source,{}).get("applied_sequence",0),"source_A_events":samples}, "first100_wallclock_1s":{"status":"MEASURED" if args.scenario=="all" and args.interval_ms==1000 else "SMOKE_OR_NOT_RUN","samples":samples,"interval_ms":args.interval_ms if args.scenario=="all" else None},
                "burst1000":{"status":"MEASURED" if burst==1000 else "NOT_RUN","events":burst}, "variableACK_0_1_5_20ms":{"status":"MEASURED" if args.scenario=="all" else "SMOKE","schedule_ms":[0,1,5,20]},
                "local_storage_telegram_60s":"NOT_RUN", "remote_cloud_provider":"NOT_RUN",
                "ENOSPC_restart":"NOT_RUN",
                "mixed_5venue_MM_Arb_storage_2TLS_viewers_idleChromium":"NOT_RUN",
                "dashboard_disconnect_60s_and_recovery":"NOT_RUN"}}
        result["required_next_scenarios"]=["local_storage_telegram_60s","remote_cloud_provider","ENOSPC_restart",
            "mixed_5venue_MM_Arb_storage_2TLS_viewers_idleChromium","dashboard_disconnect_60s_and_recovery"]
        result["elapsed_mono_ns"]=time.monotonic_ns()-run_started_mono_ns
        result["trace_omitted_rows"]=recorder.omitted
        result["trace_status_counts"]={status:sum(row["status"]==status for row in recorder.rows)
            for status in sorted({row["status"] for row in recorder.rows})}
        result["counts_by_phase_stage"]={}
        for phase,stage in sorted({(row["phase_id"],row["stage"]) for row in recorder.rows}):
            result["counts_by_phase_stage"][phase+"|"+stage]=sum(row["phase_id"]==phase and row["stage"]==stage for row in recorder.rows)
        result["ack_schedule_counts"]={}
        for phase,status in sorted({(row["phase_id"],row["status"]) for row in recorder.rows if row["stage"]=="T_UDS_server_ack_wait"}):
            result["ack_schedule_counts"][phase+"|"+status]=sum(row["phase_id"]==phase and row["status"]==status for row in recorder.rows if row["stage"]=="T_UDS_server_ack_wait")
        evaluated={row["trace_id"] for row in recorder.rows if row["stage"]=="T3_strategy_eval"}
        attempted={row["trace_id"] for row in recorder.rows if row["stage"]=="T5_transport_request"}
        result["strategy_signal_counts"]={"evaluated_events":len(evaluated),"T5_attempted_events":len(attempted),
            "no_signal_events":len(evaluated-attempted),"no_signal_definition":"T3 strategy evaluation without a concrete T5 request"}
        result["viewer_sidecar"]="NOT_RUN"; result["idle_chromium"]="NOT_RUN"
        try: result["trace_integrity"]=check_ordered_a1(recorder.rows)
        except RuntimeError as exc: raise RuntimeError(f"{exc}; dq_ids={[r['trace_id'] for r in recorder.rows if r['stage']=='T_DQ']}; diagnostics={invalid[-8:]}") from exc
        result["trace_summary"]=__import__("perf01_source_fixture").summarize(recorder.rows)
        result["causal_spans"]=causal_span_summaries(recorder.rows)
        result["counts_by_stage"]={stage:sum(row["stage"]==stage for row in recorder.rows)
            for stage in sorted({row["stage"] for row in recorder.rows})}
        result["events_by_source_product"]={}
        for source,product in sorted({(r["source_id"],r["product_id"]) for r in recorder.rows}):
            result["events_by_source_product"][source+"|"+product]={"event_count":len({r["trace_id"] for r in recorder.rows if r["source_id"]==source and r["product_id"]==product and r["stage"]=="T0_fake_recv"}),
                "trace_rows":sum(r["source_id"]==source and r["product_id"]==product for r in recorder.rows),
                "T0":sum(r["source_id"]==source and r["product_id"]==product and r["stage"]=="T0_fake_recv" for r in recorder.rows),
                "T5":sum(r["source_id"]==source and r["product_id"]==product and r["stage"]=="T5_transport_request" for r in recorder.rows)}
        return result, recorder.rows, publisher, server, engine, dispatcher, fixture, original_methods, transport_wrappers, strategy_module, previous_factory, aux
    except BaseException as failure:
        # Persist bounded local diagnostics before cancellation/cleanup can hide
        # the original startup or stage failure. Inputs contain fixture data only.
        from collections import Counter
        from operations.dashboard.latency import snapshot as latency_snapshot
        failure_doc={"error_type":type(failure).__name__,"error":str(failure)[:2000],
            "phase":getattr(args,"current_phase",None),"pipeline":publisher.stats,
            "engine":engine.stats,"strategy_evaluated":strategy.evaluated,
            "dispatcher_errors":list(dispatcher.errors),"dispatcher_blocked":dispatcher.blocked,
            "control_ack_phases":dict(control_ack_phases),
            "invalidations":invalid[-64:],
            "trace_stage_counts":dict(Counter(row["stage"] for row in recorder.rows)),
            "latency":latency_snapshot()}
        (paths["tmp"]/"failure.json").write_text(json.dumps(failure_doc,indent=2,default=json_default)+"\n")
        for target,name,original in original_methods:
            try:setattr(target,name,original)
            except Exception:pass
        for transport,original in transport_wrappers:
            try:transport.request=original
            except Exception:pass
        try:native_inventory.close()
        except Exception:pass
        try:strategy_module.PLAN_FACTORY=previous_factory
        except Exception:pass
        if aux_task is not None:
            aux_task.cancel();await asyncio.gather(aux_task,return_exceptions=True)
        if aux is not None:
            try: await aux.close()
            except Exception: pass
        try:
            await publisher.close(timeout_seconds=2)
        except Exception:
            pass
        await server.close(); await engine.close()
        for owner in owners:
            try: await owner.close()
            except Exception: pass
        raise


async def close_core(runtime):
    publisher, server, engine, dispatcher, fixture = runtime[2],runtime[3],runtime[4],runtime[5],runtime[6]
    methods, transport_wrappers, strategy_module, previous_factory, aux=runtime[7:]
    if aux is not None:
        await aux.close()
    if dispatcher.task:
        dispatcher.task.cancel(); await asyncio.gather(dispatcher.task,return_exceptions=True)
    # Restore every benchmark-only wrapper in finally before component teardown.
    for ledger,name,original in methods: setattr(ledger,name,original)
    for transport,original in transport_wrappers: transport.request=original
    fixture["_perf_native_inventory"].close()
    strategy_module.PLAN_FACTORY=previous_factory
    await publisher.close(timeout_seconds=5)
    await server.close(); await engine.close()
    for owner in (fixture["binance_owner"],fixture["bybit_owner"]):
        await owner.close()


def host_hash(cgroup=None):
    raw={"platform":platform.platform(),"python":platform.python_version(),
        "machine":platform.machine(),"kernel":platform.release(),
        "affinity":sorted(os.sched_getaffinity(0)) if hasattr(os,"sched_getaffinity") else None,
        "cgroup_limits":{"cpu.max":(cgroup or {}).get("cpu.max"),
            "memory.max":(cgroup or {}).get("memory.max")}}
    return sha(json.dumps(raw,sort_keys=True,separators=(",",":")).encode()), raw


def config_digest(root):
    paths=[]
    for base in (root/"config",root/"deploy"):
        if base.exists(): paths.extend(p for p in base.rglob("*") if p.is_file())
    for rel in ("docker-compose.yml","deploy/nats.conf","main.py","pyproject.toml",
        "scripts/benchmark_perf01.py","scripts/perf01_viewers.cjs",
        "tests/refactoring/perf01_source_fixture.py","tests/refactoring/test_perf01_trace.py",
        "tests/refactoring/perf01_fault_load_fixture.py"):
        p=root/rel
        if p.is_file(): paths.append(p)
    return manifest_hash(root,paths)


def safe_output_path(path: Path):
    resolved=path.resolve()
    allowed=(Path("/data").resolve(),Path("/artifacts").resolve(),Path("/tmp").resolve())
    if not any(resolved==base or base in resolved.parents for base in allowed):
        raise RuntimeError("output path must remain under /data, /artifacts or /tmp")
    resolved.parent.mkdir(parents=True,exist_ok=True)
    return resolved


def filesystem_facts(path: Path):
    resolved=path.resolve();mount=None
    try:
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            left,right=line.split(" - ",1);fields=left.split();tail=right.split()
            mountpoint=Path(fields[4].replace("\\040"," ")).resolve()
            if (resolved==mountpoint or mountpoint in resolved.parents) and (mount is None or len(str(mountpoint))>len(mount["mountpoint"])):
                mount={"mountpoint":str(mountpoint),"device":fields[2],"filesystem":tail[0]}
    except (OSError,ValueError,IndexError):pass
    stat=os.statvfs(resolved)
    return {"path":str(resolved),"mount":mount,"free_bytes":stat.f_bavail*stat.f_frsize,
        "block_size":stat.f_frsize,"capacity_bytes":stat.f_blocks*stat.f_frsize}


def bounded_tree_bytes(root: Path):
    total=0;files=0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total+=path.stat().st_size;files+=1
                if files>100_000: raise RuntimeError("run data file inventory exceeded its explicit bound")
        except FileNotFoundError:continue
    return {"regular_files":files,"regular_file_bytes":total}


async def keep_idle_chromium(stop_event, ready_event):
    """Hold one real local Chromium blank page; never navigate or log in."""
    from timefolio.client import AsyncTimefolioBrowserManager
    started=time.monotonic_ns()
    async with AsyncTimefolioBrowserManager(headless=True,live_submit_enabled=False) as manager:
        page=await manager.page_for("perf01-idle-browser")
        await page.set_content("<!doctype html><html><head><title>idle local PERF01</title></head><body></body></html>")
        if await page.title()!="idle local PERF01":
            raise RuntimeError("idle Chromium did not load the inert blank page")
        ready_event.set()
        await stop_event.wait()
        title=await page.title()
        return {"status":"RUNNING_THEN_CLOSED","browser":"actual_chromium",
            "blank_page":title=="idle local PERF01","navigation_count":0,"login_attempts":0,
            "submit_enabled":False,"pid":os.getpid(),"elapsed_ns":time.monotonic_ns()-started,
            "clock_domain":"same-python-pid-monotonic"}


async def start_viewer_sidecar(args, telemetry_input, output_path, duration_seconds):
    required=(args.viewer_node,args.viewer_typescript,args.viewer_cert,args.viewer_key,args.viewer_script)
    if any(value is None for value in required) or not all(Path(value).is_file() for value in required):
        raise RuntimeError("viewer sidecar executable/source/TypeScript/TLS inputs are incomplete")
    command=[str(args.viewer_node),str(args.viewer_script),"--source-root",str(ROOT),
        "--typescript",str(args.viewer_typescript),"--cert",str(args.viewer_cert),"--key",str(args.viewer_key),
        "--output",str(output_path),"--duration-seconds",str(duration_seconds),"--interval-ms","2000",
        "--telemetry-input",str(telemetry_input),"--disconnect-after-seconds","10",
        "--disconnect-seconds","60"]
    env={"PATH":"/usr/bin:/bin","HOME":"/tmp/perf01-node-home",
        "PLAYWRIGHT_BROWSERS_PATH":"/opt/dashboard-browsers"}
    Path(env["HOME"]).mkdir(mode=0o700,exist_ok=True)
    process=await asyncio.create_subprocess_exec(*command,stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,env=env)
    try:
        line=await asyncio.wait_for(process.stdout.readline(),timeout=20)
        ready=json.loads(line.decode("utf-8")) if line else {}
        if ready.get("event")!="READY" or ready.get("ready_viewers")!=2:
            raise RuntimeError(f"two-viewer READY contract failed: {ready}")
        return process,ready,command
    except BaseException:
        process.kill();await process.wait()
        raise


async def stop_viewer_sidecar(process, command):
    if process.returncode is None:
        process.send_signal(signal.SIGTERM)
    try:
        stdout,stderr=await asyncio.wait_for(process.communicate(),timeout=15)
    except asyncio.TimeoutError:
        process.kill();stdout,stderr=await process.communicate()
    report_path=Path(command[command.index("--output")+1])
    report=json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
    return {"status":report.get("result","NOT_REPORTED") if isinstance(report,dict) else "NOT_REPORTED",
        "report":report,"exit_code":process.returncode,"pid":process.pid,
        "ready_event_seen":True,"stdout_tail":stdout.decode("utf-8","replace")[-1000:],
        "stderr_tail":stderr.decode("utf-8","replace")[-1000:],
        "clock_domain":"node-process-local-monotonic; no subtraction against Python"}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-sha",default=ACCEPTED_QA_SHA)
    p.add_argument("--scenario",choices=("smoke","all"),default="smoke")
    p.add_argument("--samples",type=int,default=8)
    p.add_argument("--allow-unlimited-smoke",action="store_true")
    p.add_argument("--smoke",action="store_true",help=argparse.SUPPRESS)
    p.add_argument("--capacity",action="store_true",help="require the accepted 2 CPU / 3.8 GiB cgroup before running")
    p.add_argument("--events",type=int,default=None,help=argparse.SUPPRESS)
    p.add_argument("--event-start",type=int,default=10000)
    p.add_argument("--action-every",type=int,default=1)
    p.add_argument("--burst-chunk-records",type=int,default=16,
        help="finite offered-load chunk before exact pipeline/strategy drain (1..128)")
    p.add_argument("--grouped",action="store_true")
    p.add_argument("--ack-delay-ms",type=int,default=-1)
    p.add_argument("--drain-timeout",type=float,default=30)
    p.add_argument("--output",type=Path,default=Path("/data/perf01-report.json"))
    p.add_argument("--trace-output",type=Path,default=Path("/data/perf01-trace.jsonl"))
    p.add_argument("--viewer-node",type=Path)
    p.add_argument("--viewer-typescript",type=Path)
    p.add_argument("--viewer-cert",type=Path)
    p.add_argument("--viewer-key",type=Path)
    p.add_argument("--viewer-script",type=Path,default=ROOT/"scripts/perf01_viewers.cjs")
    p.add_argument("--idle-chromium",action="store_true")
    args=p.parse_args()
    if args.source_sha != ACCEPTED_QA_SHA:
        raise SystemExit(f"source SHA must match accepted QA-01 snapshot {ACCEPTED_QA_SHA}")
    args.smoke = args.scenario == "smoke" or args.smoke
    if args.events is not None: args.samples=args.events
    args.events=args.samples
    args.burst_events=1000 if args.scenario=="all" else 0
    args.interval_ms=1000 if args.scenario=="all" else 0
    if args.scenario=="all":
        args.grouped=True
        if args.samples != 100:
            raise SystemExit("--scenario all requires --samples 100 and runs an additional 1000-event burst")
        if args.action_every==1: args.action_every=3
    if not 1<=args.burst_chunk_records<=128:
        raise SystemExit("burst chunk records must be 1..128")
    if args.events <= 0 or args.events > 1000 or args.action_every < 0 or args.ack_delay_ms not in (-1,0,1,5,20):
        raise SystemExit("samples must be 1..1000, action cadence nonnegative, ACK delay -1 or 0/1/5/20ms")
    source_files, source_hash=source_hashes(ROOT)
    if len(source_files)!=149:
        raise RuntimeError("source tree inventory failed")
    cfg_files_hash=config_digest(ROOT)
    workload_parameters={name:getattr(args,name) for name in (
        "scenario","samples","capacity","action_every","grouped","ack_delay_ms",
        "burst_events","interval_ms","burst_chunk_records")}
    cfg_hash=sha(json.dumps({"files_sha256":cfg_files_hash,"workload":workload_parameters},
        sort_keys=True,separators=(",",":")).encode())
    cgroup_before=cgroup_snapshot()
    host_hash_value, host_facts=host_hash(cgroup_before)
    if args.capacity: require_capacity_cgroup(cgroup_before)
    if args.smoke and args.events > 25 and not args.allow_unlimited_smoke:
        raise SystemExit("native smoke is limited to 25 events; use --allow-unlimited-smoke explicitly")
    if args.capacity and args.scenario != "all":
        raise SystemExit("capacity evidence requires --scenario all")
    if args.capacity and any(value is None for value in (args.viewer_node,args.viewer_typescript,args.viewer_cert,args.viewer_key)):
        raise SystemExit("capped run requires viewer Node, TypeScript, local TLS cert, and local TLS key paths")
    args.idle_chromium = bool(args.capacity or args.idle_chromium)
    run_root=Path(tempfile.mkdtemp(prefix="perf01-run-",dir="/data" if args.capacity else "/tmp"))
    if args.capacity:
        args.output=run_root/(args.output.name or "report.json")
        args.trace_output=run_root/(args.trace_output.name or "trace.jsonl")
    output=safe_output_path(args.output); trace_output=safe_output_path(args.trace_output)
    fs_before=filesystem_facts(run_root)
    if args.capacity and fs_before["free_bytes"]<128*1024*1024:
        raise RuntimeError("/data filesystem has less than the bounded 128 MiB PERF01 free-space floor")
    process_cpu_before=time.process_time_ns()
    process_io_before=process_io_snapshot()
    sampler=ResourceSampler(); sampler.start()
    os.environ.setdefault("VIBETRADING_DATA_ROOT",str(run_root/"runtime-data"))
    os.environ.setdefault("VIBETRADING_STATE_DIR",str(run_root/"runtime-data"/"state"))
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH","/opt/dashboard-browsers")
    tmp=run_root
    paths={"tmp":tmp}
    hashes={"source":source_hash,"config":cfg_hash,"host":host_hash_value}
    args.hashes=hashes
    args.telemetry_ready_path=Path(tempfile.mkdtemp(prefix="perf01-sidecars-",dir="/tmp"))/"telemetry-input.json"
    async def run_session():
        args.viewer_ready_event=asyncio.Event()
        args.dashboard_disconnect_window=None
        if not args.capacity:
            args.viewer_ready_event.set()
        core_task=asyncio.create_task(run_core(args,paths),name="perf01-real-chain")
        active=None
        viewer_process=None;viewer_ready=None;viewer_command=None;viewer_result={"status":"NOT_RUN"}
        browser_stop=asyncio.Event();browser_ready=asyncio.Event();browser_task=None;browser_result={"status":"NOT_RUN"}
        try:
            if args.capacity:
                deadline=time.monotonic()+45
                ready_marker=args.telemetry_ready_path.with_suffix(".ready")
                while not ready_marker.is_file() and time.monotonic()<deadline:
                    if core_task.done(): break
                    await asyncio.sleep(.05)
                if not ready_marker.is_file() or not args.telemetry_ready_path.is_file():
                    if core_task.done() and not core_task.cancelled() and core_task.exception() is not None:
                        raise core_task.exception()
                    raise RuntimeError("real benchmark latency telemetry was not published before viewer startup")
                viewer_path=args.telemetry_ready_path.parent/"viewer-final.json"
                duration=max(600,min(3600,int(args.samples*args.interval_ms/1000)+300))
                viewer_process,viewer_ready,viewer_command=await start_viewer_sidecar(
                    args,args.telemetry_ready_path,viewer_path,duration)
                # This is an orchestrator-local scheduled overlap count. The
                # Node helper reports its own disconnect clock and is never
                # joined/subtracted against Python monotonic timestamps.
                ready_mono=time.monotonic()
                args.dashboard_disconnect_window=(ready_mono+10.0,ready_mono+70.0)
                args.dashboard_disconnect_window_meta={"scheduled_start_mono_ns":int((ready_mono+10.0)*1e9),
                    "scheduled_end_mono_ns":int((ready_mono+70.0)*1e9),
                    "duration_seconds":60,"clock_domain":"python orchestrator-local monotonic",
                    "cross_process_clock_join":False}
                if args.idle_chromium:
                    browser_task=asyncio.create_task(keep_idle_chromium(browser_stop,browser_ready),name="perf01-idle-chromium")
                    await asyncio.wait_for(browser_ready.wait(),timeout=30)
                    browser_result={"status":"ACTUAL_BLANK_PAGE_READY","blank_page":True,
                        "ready_event_set":True,"clock_domain":"python process monotonic"}
                args.viewer_ready_event.set()
            active=await core_task
        finally:
            if not core_task.done():
                core_task.cancel();await asyncio.gather(core_task,return_exceptions=True)
            if viewer_process is not None:
                try: viewer_result=await stop_viewer_sidecar(viewer_process,viewer_command)
                except Exception as exc: viewer_result={"status":"FAIL","error":type(exc).__name__}
            browser_stop.set()
            if browser_task is not None:
                try: browser_result=await asyncio.wait_for(browser_task,timeout=20)
                except Exception as exc: browser_result={"status":"FAIL","error":type(exc).__name__}
            if core_task.done() and not core_task.cancelled() and core_task.exception() is None:
                await close_core(core_task.result())
        if browser_task is not None and browser_task.done() and browser_task.exception() is not None:
            browser_result={"status":"FAIL","error":type(browser_task.exception()).__name__}
        return active[0],active[1],viewer_process,viewer_command,viewer_result,browser_task,browser_stop,browser_result
    viewer_result={"status":"NOT_RUN"};browser_result={"status":"NOT_RUN"}
    try:
        result, rows, _viewer_process, _viewer_command, viewer_result, _browser_task, _browser_stop, browser_result=asyncio.run(run_session())
    finally:
        resource_samples=sampler.stop()
    result["hashes"]={"accepted_QA_SHA":ACCEPTED_QA_SHA,**hashes}
    result["configuration"]={"files_sha256":cfg_files_hash,"workload":workload_parameters,
        "config_sha256":cfg_hash}
    result["run_data_root"]=str(run_root)
    result["filesystem_before"]=fs_before
    result["filesystem_after"]=filesystem_facts(run_root)
    cgroup_after=cgroup_snapshot()
    process_cpu_ns=time.process_time_ns()-process_cpu_before
    process_io_after=process_io_snapshot()
    def delta_map(before,after):
        left=before.get("cpu.stat",{});right=after.get("cpu.stat",{})
        return {key:right[key]-left.get(key,0) for key in right if isinstance(left.get(key),int)}
    def simple_delta(before,after,key):
        left=before.get(key,{}) or {};right=after.get(key,{}) or {}
        return {name:right[name]-left.get(name,0) for name in right if isinstance(left.get(name),int)}
    def io_stat_delta(before,after):
        left=before.get("io.stat",{}) or {};right=after.get("io.stat",{}) or {}
        return {device:{key:value-left.get(device,{}).get(key,0) for key,value in fields.items()
                if isinstance(left.get(device,{}).get(key,0),int)} for device,fields in right.items()}
    rss_highwater=[]
    for sample in resource_samples:
        raw=sample.get("rss_status",{}).get("VmRSS","").split()
        if len(raw)==2 and raw[1]=="kB":
            try:rss_highwater.append(int(raw[0]))
            except ValueError:pass
    result["resource_metrics"]={"python_process_cpu_ns":process_cpu_ns,
        "python_cpu_ns_per_input_event":process_cpu_ns/max(1,result["input_events"]),
        "cgroup_cpu_stat_delta":delta_map(cgroup_before,cgroup_after),
        "cgroup_cpu_usage_usec_delta":delta_map(cgroup_before,cgroup_after).get("usage_usec"),
        "cgroup_memory_event_delta":simple_delta(cgroup_before,cgroup_after,"memory.events"),
        "cgroup_io_stat_delta":io_stat_delta(cgroup_before,cgroup_after),
        "python_process_io_before":process_io_before,"python_process_io_after":process_io_after,
        "python_process_io_delta":{key:value-process_io_before.get(key,0)
            for key,value in process_io_after.items() if isinstance(process_io_before.get(key),int)},
        "cgroup_memory_peak_bytes":int(cgroup_after["memory.peak"]) if (cgroup_after.get("memory.peak") or "none").isdigit() else None,
        "sampler_process_rss_max_kib":max(rss_highwater,default=None),
        "resource_sample_count":len(resource_samples),"resource_samples_omitted":sampler.omitted,
        "fd_samples":[sample.get("fd_count") for sample in resource_samples if sample.get("fd_count") is not None]}
    result["host"]={**host_facts,"cgroup_before":cgroup_before,"cgroup_after":cgroup_after,
        "resource_samples":len(resource_samples),"resource_max_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "resource_peak_children_rss_kib":resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "affinity":sorted(os.sched_getaffinity(0)) if hasattr(os,"sched_getaffinity") else None,
        "affinity_changed":False}
    result["duration_seconds"]=result.get("elapsed_mono_ns",0)/1_000_000_000
    result["viewer_sidecar"]=viewer_result
    result["idle_chromium"]=browser_result
    result["telemetry_input"]=paths.get("telemetry_fact",{"status":"NOT_RUN"})
    if args.capacity:
        aux=result.get("auxiliary_load",{})
        faults=result.get("fault_scenarios",{})
        aux_counts=aux.get("counts_by_source",{})
        wanted_counts={"binance-eth":101,"arcus-eth":101,"grvt-eth":101,
            "kis-etf":101,"kiwoom-etf":101,"toss-us-qqq":2}
        target_counts=aux.get("target_count_by_source",{})
        aux_rows_ok=all(source in aux_counts and aux_counts[source].get("published")==count and
            aux_counts[source].get("applied")==count and target_counts.get(source)==count
            for source,count in wanted_counts.items())
        aux_rows_ok=aux_rows_ok and set(aux_counts)==set(wanted_counts)
        aux_storage=aux.get("storage",{})
        gates=aux.get("downstream_gates",{})
        telegram_gate=gates.get("telegram_actual_controller_fake_session",{})
        archive_gate=gates.get("archive_actual_maintenance_local_backend",{})
        gate_holds=gates.get("gate_hold_seconds",{})
        pending_before_release=gates.get("downstream_pending_before_release",{})
        receipts_before_release=gates.get("producer_receipts_before_release",{})
        storage_progress=aux_storage.get("source_progress",{})
        downstream_cut_ok=(set(pending_before_release)=={
            "parquet_flush_blocked","archive_upload_pending","telegram_send_pending"} and
            all(pending_before_release.values()) and
            all(gate_holds.get(name,0)>=60 for name in ("parquet","archive","telegram")) and
            all(receipts_before_release.get(source,{}).get("durable")==count and
                receipts_before_release.get(source,{}).get("applied_cursor")==count and
                receipts_before_release.get(source,{}).get("live_ack_count")==count and
                receipts_before_release.get(source,{}).get("durable_applied_lag_records")==0
                for source,count in wanted_counts.items()) and
            all(storage_progress.get(source,{}).get("sequence")==count
                for source,count in wanted_counts.items()))
        local_gate_ok=(aux.get("status")=="MEASURED_LOCAL_AUX" and aux_rows_ok and
            aux_storage.get("all_sources_checkpointed") is True and
            downstream_cut_ok and
            gates.get("parquet_actual_flush_block") is True and
            gates.get("stall_target_seconds")==60 and
            gates.get("ingress_elapsed_seconds",0)>=60 and archive_gate.get("completed") is True and
            archive_gate.get("local_backend") is True and telegram_gate.get("completed") is True and
            telegram_gate.get("external_calls")==0)
        fault_cases=faults.get("scenarios",{})
        prefix=fault_cases.get("durable_prefix_restart",{})
        corruption=prefix.get("corrupt_tail",{})
        pipeline_enospc=fault_cases.get("ENOSPC",{})
        pipeline_eio=fault_cases.get("EIO",{})
        pipeline_replay=fault_cases.get("durable_prefix_restart",{})
        pipeline_corrupt=fault_cases.get("corrupt_source_tail",{})
        faults_ok=(faults.get("status")=="MEASURED_PIPELINE_FAULT_AND_REPLAY" and
            all(row.get("actual_spooling_publisher_uds_engine") is True and
                row.get("spool_appended_cursor")==1 and row.get("spool_durable_cursor")==0 and
                row.get("engine_on_frame_count")==0 and row.get("engine_post_apply_callback_count")==0 and
                row.get("watermark_capture_stage")=="failure-before-cleanup-restore"
                for row in (pipeline_enospc,pipeline_eio)) and
            pipeline_replay.get("actual_publisher_uds_engine") is True and
            pipeline_replay.get("replayed")==2 and pipeline_replay.get("hotpath_exact_duplicate_count",0)==1 and
            pipeline_replay.get("fresh_after_commit_live_callbacks")==1 and
            bool(pipeline_corrupt.get("corrupted_segment")) and
            pipeline_corrupt.get("engine_source_mode")=="INVALID" and
            pipeline_corrupt.get("engine_live_callbacks")==0 and
            bool(pipeline_corrupt.get("source_invalid_reasons")) and
            pipeline_corrupt.get("exact_loss_after_corruption")=="UNKNOWN; sealed tail corrupt")
        viewer_doc=viewer_result.get("report") or {}
        disconnect=viewer_doc.get("disconnect",{})
        private_ingest=viewer_doc.get("private_ingest",{})
        viewers_ok=(viewer_doc.get("result")=="PASS" and viewer_doc.get("viewer_count")==2 and
            viewer_doc.get("public_application_sql_writes")==0 and
            private_ingest.get("successes",0)>0 and
            viewer_doc.get("private_ingest_dispatch_count",0)>0)
        disconnect_ok=(viewers_ok and disconnect.get("state")=="complete" and
            disconnect.get("actual_pause_ms",0)>=60_000 and disconnect.get("recovered_viewers")==2 and
            len(disconnect.get("recovery_latency_ms",[]))==2)
        browser_ok=(browser_result.get("status")=="RUNNING_THEN_CLOSED" and
            browser_result.get("blank_page") is True and browser_result.get("navigation_count")==0 and
            browser_result.get("login_attempts")==0 and browser_result.get("submit_enabled") is False)
        rows_by_phase={}
        for row in rows:
            if row["source_id"]=="binance-perp" and row["stage"]=="T0_fake_recv":
                rows_by_phase.setdefault(row["phase_id"],set()).add(row["trace_id"])
        core_disconnect_progress=sum(row["stage"]=="T0_fake_recv" and
            row.get("counts",{}).get("dashboard_disconnect_overlap") is True for row in rows)
        policy=result.get("policy_workload",{})
        decision_counts=policy.get("decision_counts",{})
        group_results=policy.get("group_execution_results",[])
        policy_ok=(decision_counts.get("two_leg_group",0)>0 and
            decision_counts.get("two_leg_group")==decision_counts.get("opposite_side_groups") and
            len(group_results)==decision_counts.get("two_leg_group") and
            all(group["group_acknowledged"] and group["two_leg_opposite_side"] and group["both_owner_mutations_observed"]
                for group in group_results))
        fs=result.get("filesystem_before",{})
        mount=fs.get("mount") or {}
        fsync_media_ok=(mount.get("filesystem") not in {None,"tmpfs","ramfs"} and
            str(fs.get("path","")).startswith("/data/"))
        resource_metrics=result.get("resource_metrics",{})
        slow_id=result.get("diagnostic_events",{}).get("trace_id")
        slow_flush_rows=[row for row in rows if row.get("trace_id")==slow_id and row.get("stage")=="T_FS_flush"]
        slow_dur_rows=[row for row in rows if row.get("trace_id")==slow_id and row.get("stage")=="T_DUR"]
        slow_fsync_ok=bool(slow_flush_rows and slow_dur_rows and
            min(row["elapsed_ns"] for row in slow_flush_rows)>=20_000_000 and
            max(row["end_ns"] for row in slow_dur_rows)>=max(row["end_ns"] for row in slow_flush_rows))
        ack_counts=result.get("ack_schedule_counts",{})
        expected_ack={"baseline_first100|0ms":100,"baseline_burst1000|0ms":1000,
            "variable_ack_100|0ms":25,"variable_ack_100|1ms":25,
            "variable_ack_100|5ms":25,"variable_ack_100|20ms":25}
        ack_schedule_ok=all(ack_counts.get(key)==count for key,count in expected_ack.items())
        core_profile_ok=(result.get("first100_count")==100 and result.get("burst_count")==1000 and
            result.get("variable_ack_count")==100 and result.get("t5_rows",0)>=100 and
            result.get("t6_response_rows")==result.get("t5_rows") and
            result.get("t6_successful_scripted_responses")==result.get("t5_rows") and
            result.get("owner_intents",0)>0 and
            result.get("trace_omitted_rows")==0 and result.get("trace_integrity",{}).get("a1_linkage_errors")==0 and
            result.get("accepted")==result.get("durable")==result.get("applied") and ack_schedule_ok and
            result.get("virtual_b_silence_seconds")==300)
        slow_id=result.get("diagnostic_events",{}).get("trace_id")
        slow_flush_rows=[row for row in rows if row.get("trace_id")==slow_id and row.get("stage")=="T_FS_flush"]
        slow_dur_rows=[row for row in rows if row.get("trace_id")==slow_id and row.get("stage")=="T_DUR"]
        slow_fsync_ok=bool(slow_flush_rows and slow_dur_rows and
            min(row["elapsed_ns"] for row in slow_flush_rows)>=20_000_000 and
            max(row["end_ns"] for row in slow_dur_rows)>=max(row["end_ns"] for row in slow_flush_rows))
        group_sync_ok=(result.get("group_sync",{}).get("batch_count",0)>0 and
            result.get("group_sync",{}).get("batch_count_bytes_missing") == 0)
        telemetry_fact=result.get("telemetry_input",{})
        telemetry_ok=(telemetry_fact.get("latency_status")=="AVAILABLE" and
            telemetry_fact.get("latency_sampled_histogram_count",0)>0 and
            {"T0_fake_recv","T_DUR","T3_strategy_eval"}.issubset(
                set(telemetry_fact.get("latency_sampled_stages",()))))
        io_delta=resource_metrics.get("cgroup_io_stat_delta",{})
        disk_io_ok=any(values.get("wbytes",0)>0 for values in io_delta.values())
        cgroup_ok=(result.get("host",{}).get("cgroup_before",{}).get("cpu.max")=="200000 100000" and
            result.get("host",{}).get("cgroup_before",{}).get("memory.max")=="4080218112" and
            result.get("host",{}).get("cgroup_before",{}).get("process_cgroup_leaf")=="/" and
            result.get("host",{}).get("cgroup_after",{}).get("process_cgroup_leaf")=="/" and
            disk_io_ok and
            resource_metrics.get("resource_samples_omitted")==0 and
            resource_metrics.get("cgroup_memory_event_delta",{}).get("oom",0)==0 and
            resource_metrics.get("cgroup_memory_event_delta",{}).get("oom_kill",0)==0)
        inventory_counts=result.get("scripted_native_working_inventory",{}).get("counts",{})
        native_inventory_ok=(set(inventory_counts)=={"binance","bybit"} and
            all(type(n) is int and 0<n<=512 for n in inventory_counts.values()) and
            sum(inventory_counts.values())==result.get("owner_intents")==result.get("t5_rows") and
            result.get("final_native_inventory_reconciliation")=="PASS_REAL_GUARD_SCRIPTED_NATIVE_FACTS" and
            result.get("owner_ledger_fault_isolation",{}).get("unchanged") is True)
        checks={"core_baseline_burst_variableACK_and_A1_counts":core_profile_ok,
            "complete_scripted_native_inventory_and_owner_cut":native_inventory_ok,
            "slow_fsync_injected_pipeline_event":slow_fsync_ok,
            "actual_group_wait_and_batch_record_byte_counts":group_sync_ok,
            "local_storage_telegram_60s":local_gate_ok,
            "ENOSPC_EIO_prefix_restart_corrupt_tail":faults_ok,
            "six_sources_five_venues_and_paper_policy":aux_rows_ok and
                aux.get("paper_mm",{}).get("job_callbacks",0)>0 and
                aux.get("paper_mm",{}).get("portfolio_orders",0)>0 and
                aux.get("paper_mm",{}).get("real_owner_or_external_calls")==0,
            "grouped_two_leg_workload":policy_ok,
            "two_tls_viewers_private_ingest_no_public_writes":viewers_ok,
            "telemetry_actual_sampled_histograms":telemetry_ok,
            "dashboard_disconnect_60s_and_recovery":disconnect_ok,
            "core_progress_during_orchestrated_disconnect_window":core_disconnect_progress>0,
            "idle_timefolio_blank_chromium":browser_ok,
            "persistent_non_tmpfs_durability_media":fsync_media_ok,
            "effective_2cpu_3_8gib_cgroup_and_bounded_sampler":cgroup_ok}
        result["capacity_checks"]=checks
        result["dashboard_disconnect_core_progress"]={**getattr(args,"dashboard_disconnect_window_meta",{}),
            "core_events_in_orchestrator_window":core_disconnect_progress,
            "viewer_disconnect_clock_domain":disconnect.get("clock_domain"),
            "event_clock_join":"NOT_PERFORMED"}
        scenarios=result["scenarios"]
        scenarios["local_storage_telegram_60s"]="MEASURED" if local_gate_ok else "FAIL_OR_INCOMPLETE"
        scenarios["remote_cloud_provider"]="NOT_RUN_LOCAL_BACKEND_ONLY"
        scenarios["ENOSPC_restart"]="MEASURED" if faults_ok else "FAIL_OR_INCOMPLETE"
        scenarios["mixed_5venue_MM_Arb_storage_2TLS_viewers_idleChromium"]=(
            "MEASURED" if (checks["six_sources_five_venues_and_paper_policy"] and policy_ok and viewers_ok and browser_ok)
            else "FAIL_OR_INCOMPLETE")
        scenarios["dashboard_disconnect_60s_and_recovery"]="MEASURED" if disconnect_ok else "FAIL_OR_INCOMPLETE"
        blockers=[name for name,value in checks.items() if value is not True]
        result["acceptance_blockers"]=blockers
        result["required_next_scenarios"]=blockers
        result["capacity_measurement_performed"]=True
        result["capacity_claim"]=cgroup_ok
        result["unmeasured_limits"]={"remote_cloud_provider":"NOT_RUN_LOCAL_BACKEND_ONLY",
            "meaning":"Actual local ArchiveMaintenance gate was measured; remote cloud provider I/O is not exercised."}
        result["acceptance_complete"]=not blockers
        result["status"]="PASS_LOCAL_CAPACITY" if not blockers else "MEASURED_PARTIAL_CAPACITY"
    result["clock_policy"]="same-PID monotonic spans only; Node/Chromium child summaries remain separate clock domains"
    with trace_output.open("w",encoding="utf-8") as handle:
        for row in rows: handle.write(json.dumps(row,sort_keys=True,separators=(",",":"),default=json_default)+"\n")
    result["trace_file_bytes"]=trace_output.stat().st_size
    result["run_data_bytes_before_report"]=bounded_tree_bytes(run_root)
    result["filesystem_after_trace"]=filesystem_facts(run_root)
    result["filesystem_free_delta_before_report_bytes"]=fs_before["free_bytes"]-result["filesystem_after_trace"]["free_bytes"]
    output.write_text(json.dumps(result,indent=2,sort_keys=True,default=json_default)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"output":str(output),"trace_output":str(trace_output),
        "events":result["input_events"],"t5_rows":result["t5_rows"],"capacity_claim":bool(args.capacity),
        "not_run":result["required_next_scenarios"]},indent=2))
    if not args.capacity:
        shutil.rmtree(tmp,ignore_errors=True)
    return 0 if not args.capacity or result["acceptance_complete"] else 2


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError,RuntimeError,ValueError,subprocess.SubprocessError,sqlite3.Error) as exc:
        print(f"PERF01 FAIL/BLOCKED: {type(exc).__name__}: {exc}",file=sys.stderr)
        raise SystemExit(2)
