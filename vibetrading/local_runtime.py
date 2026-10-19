"""NATS-free roles: collector -> local spool -> UDS engine, spool -> Parquet.

Invoke explicitly with ``python -m vibetrading.local_runtime``. Legacy CLI and
deployed services are not silently switched. Each storage role is a separate
process so Arrow/catalog/disk stalls cannot monopolize the trading loop.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

from .feed_recovery import EngineCursor, RecoveryFrame
from .hot_path import HotPathStore
from .local_config import LocalRuntimePlan
from .local_engine import EngineBudgets, LocalMarketEngine
from .local_pipeline import SpoolingMarketPublisher
from .local_transport import LocalTransportClient, LocalTransportServer
from .market_spool import MarketSpool, SpoolReader, scan_spool
from .settings import RuntimeSettings


def validate_runtime(plan,settings):
    if sys.platform!='linux':raise RuntimeError('production local runtime requires Linux UDS')
    if settings.env.get('VIBETRADING_ENFORCE_MOUNTS','').lower()!='true':
        raise RuntimeError('local runtime requires enforced storage mount identities')
    from .runtime_paths import validate_runtime_paths
    from .local_storage import StorageBudgets, fact_storage_reserve
    from .local_events import FactsEventBudgets, checkpoint_storage_reserve
    from .local_snapshot import SnapshotBudgets
    storage=StorageBudgets(**plan.storage)
    events=FactsEventBudgets(**plan.events)
    snapshot=SnapshotBudgets(**plan.snapshot)
    plans=(*plan.producers,*((plan.observation,) if plan.observation else ()))
    validate_runtime_paths(settings,extra_data_paths=tuple(item.spool_root for item in plans))
    state=settings.state_dir.resolve()
    ancestor=state
    while not ancestor.exists():ancestor=ancestor.parent
    reserved={ancestor.stat().st_dev:fact_storage_reserve(storage.max_fact_db_bytes)+checkpoint_storage_reserve(events.max_checkpoint_bytes)+2*snapshot.max_bytes}
    if reserved[ancestor.stat().st_dev]+plan.disk_reserve_bytes>shutil.disk_usage(ancestor).free:
        raise RuntimeError('fact ledger and WAL budgets exceed free state volume space')
    roots=[]
    for item in plans:
        item.capacity.verify_evidence({'producer':item.producer,'provider':getattr(item,'provider','observation'),
                                       'symbols':getattr(item,'symbols','')})
        path=item.spool_root.resolve()
        if not path.is_relative_to(settings.data_root.resolve()):
            raise RuntimeError('spool lies outside the reviewed data volume')
        for root in roots:
            if path.is_relative_to(root) or root.is_relative_to(path):
                raise RuntimeError('spool directories must not overlap')
        roots.append(path)
        ancestor=path
        while not ancestor.exists():ancestor=ancestor.parent
        device=ancestor.stat().st_dev
        # Reserve the full declared log before startup, not a few queued bytes.
        reserved[device]=reserved.get(device,0)+item.limits.max_disk_bytes
        if reserved[device]+plan.disk_reserve_bytes>shutil.disk_usage(ancestor).free:
            raise RuntimeError('combined spool budgets exceed free volume space and reserve')
    engine=EngineBudgets(**plan.engine)
    if len(plan.producers)>engine.max_producers or engine.max_frame_bytes<max(p.pipeline.max_frame_bytes for p in plan.producers):
        raise RuntimeError('engine producer/frame budgets cannot cover configured collectors')
    if snapshot.max_instruments<engine.max_instruments:
        raise RuntimeError('snapshot instrument budget cannot cover engine state')
    if plan.etf_enabled and settings.env.get('ETF_EXECUTION_GUARDS','').lower()!='true':
        raise RuntimeError('local ETF requires dispatch guards')
    return engine


def _scan(item):
    return scan_spool(item.spool_root,producer_id=item.producer,incarnation=item.incarnation,
                      **item.limits.reader_kwargs)


def create_spool(item):
    """Run only on the owning writer thread; retain existing records on failure."""
    spool=MarketSpool(item.spool_root,item.producer,item.incarnation,**asdict(item.limits))
    try:item.check_remaining(spool.scan())
    except BaseException:
        spool.close();raise
    return spool


async def run_collector(plan,item,settings,*,collector=None):
    # Import provider adapters only in this provider role; never create a bus.
    if collector is None:
        from .cli import _collector_for_args
        collector=_collector_for_args(SimpleNamespace(provider=item.provider,symbols=item.symbols))
    failures=[]
    def invalid(reason):failures.append(reason)
    transport=LocalTransportClient(plan.endpoint,producer=item.producer,incarnation=item.incarnation,
        budgets=plan.transport,on_gap=lambda cursor,reason:publisher._invalidate(RuntimeError(reason)),
        allowed_uid=plan.allowed_uid)
    publisher=SpoolingMarketPublisher(producer=item.producer,incarnation=item.incarnation,
        spool_factory=lambda:create_spool(item),transport=transport,budgets=item.pipeline,env=settings.env,
        on_invalid=invalid,resume_existing=True)
    try:
        await publisher.connect()
        await collector.run(publisher,duration_sec=None)
    finally:
        await publisher.close()


async def replay_startup(engine,producers,*,promote=True):
    """Restore fresh process state from exact C=0..durable-H, never trade it."""
    handoffs=[]
    for item in producers:
        report=await asyncio.to_thread(_scan,item)
        if report.uncertain:raise RuntimeError('uncertain producer tail requires explicit repair')
        initial=EngineCursor(item.producer,item.incarnation,0)
        high=EngineCursor(item.producer,item.incarnation,report.durable_sequence)
        engine.begin_recovery(initial,high,verified_checkpoint=True)
        reader=await asyncio.to_thread(lambda:SpoolReader(item.spool_root,item.producer,item.incarnation,
            **item.limits.reader_kwargs))
        while reader.cursor.sequence<high.sequence:
            records=await asyncio.to_thread(reader.read_batch,max_records=item.pipeline.batch_records,
                max_bytes=item.pipeline.max_bytes)
            if not records:raise RuntimeError('durable replay prefix is incomplete')
            for record in records:
                if record.cursor.sequence>high.sequence:raise RuntimeError('producer advanced during startup replay')
                engine.apply_replay(RecoveryFrame(EngineCursor(record.cursor.producer,
                    record.cursor.incarnation,record.cursor.sequence),record.payload))
        handoffs.append((initial,high))
    # No await between per-producer lease establishment and server startup.
    if promote:
        for initial,high in handoffs:
            engine.handoff(initial,high,verified=True,ready=True,lease_ns=engine.budgets.feed_lease_ns)
    return handoffs


async def restore_etf_startup(etf,producers,handoffs):
    """Restore ETF intraday bars only, with no quote/decision/order replay."""
    highs={high.producer:high for _,high in handoffs}
    from decimal import Decimal
    from .etf_trading import ETF_HISTORY_ROLES
    seen={}
    for item in producers:
        if item.provider not in {'kiwoom','toss'}:continue
        high=highs[item.producer]
        reader=await asyncio.to_thread(lambda:SpoolReader(item.spool_root,item.producer,item.incarnation,
            **item.limits.reader_kwargs))
        while reader.cursor.sequence<high.sequence:
            rows=await asyncio.to_thread(reader.read_batch,max_records=item.pipeline.batch_records,
                max_bytes=item.pipeline.max_bytes)
            if not rows:raise RuntimeError('ETF intraday replay prefix is incomplete')
            for row in rows:
                if row.cursor.sequence>high.sequence:raise RuntimeError('ETF replay moved beyond verified high water')
                data=json.loads(row.payload)
                instrument=data.get('instrument') or {}
                symbol=instrument.get('raw_symbol')
                if (data.get('event_type')=='bar_1m' and data.get('trading_date')==etf._day
                        and symbol in ETF_HISTORY_ROLES):
                    key=(symbol,data.get('open_ts_ns'),data.get('close_ts_ns'))
                    content=tuple(Decimal(str(data[name])) for name in ('open','high','low','close','volume'))
                    if key in seen and seen[key]!=content:
                        raise RuntimeError('overlapping ETF replay bars conflict; original cross-producer apply order is unproven')
                    # Only today's one-minute series is needed. Refuse an
                    # excessive/malformed series instead of growing a map.
                    if key not in seen and len(seen)>=len(ETF_HISTORY_ROLES)*1440:
                        raise RuntimeError('ETF intraday replay bar bound exceeded')
                    seen[key]=content
                await etf.event(data,state_only=True)


async def run_engine(plan,settings,*,etf_factory=None):
    etf=None
    def invalid(producer,reason):
        if etf is not None:
            # Synchronous local gate closes before a timer/order can run again.
            from .etf_trading import SYMBOLS
            for symbol in SYMBOLS:etf._invalidate_market_feed(symbol)
    engine=LocalMarketEngine(producer_providers={p.producer:p.provider for p in plan.producers},
        budgets=EngineBudgets(**plan.engine),on_invalidate=invalid,hot_path=HotPathStore(),
        trading_filter=None if plan.etf_enabled else lambda event:False)
    server=LocalTransportServer(plan.endpoint,budgets=plan.transport,on_frame=engine.on_frame,
        on_gap=engine.on_gap,on_resume=engine.initial_resume,allowed_uid=plan.allowed_uid)
    observation=None
    snapshot_writer=None
    task=None
    try:
        handoffs=await replay_startup(engine,plan.producers,promote=False)
        if plan.etf_enabled:
            from .local_observation import LocalObservationBus
            item=plan.observation
            observation=LocalObservationBus(producer=item.producer,incarnation=item.incarnation,
                spool_factory=lambda:create_spool(item),
                budgets=item.pipeline,on_invalid=lambda reason:invalid(item.producer,reason))
            await observation.connect()
            if etf_factory is None:
                from .etf_trading import EtfTradingLoop
                etf_factory=EtfTradingLoop
            etf=etf_factory(settings=settings,bus=observation,event_source=engine.event_source())
            # Reconcile accounts and prepare the legacy journal before any
            # local feed can be promoted or any strategy timer is scheduled.
            await etf.initialize()
            await restore_etf_startup(etf,plan.producers,handoffs)
        for initial,high in handoffs:
            engine.arm_initial_resume(initial,high)
        await server.start()
        await engine.start()
        from .local_snapshot import LocalSnapshotWriter, SnapshotBudgets
        snapshot_writer=LocalSnapshotWriter(engine,settings.state_dir/'local-market.json',SnapshotBudgets(**plan.snapshot))
        await snapshot_writer.start()
        if etf is not None:task=asyncio.create_task(etf.run(initialized=True),name='local-etf')
        if task is not None:await task
        else:await asyncio.Event().wait()
    finally:
        invalid('shutdown','runtime_closed')
        try:
            if task is not None:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
        finally:
            try:await server.close()
            finally:
                try:
                    if snapshot_writer is not None:await snapshot_writer.close()
                finally:
                    try:
                        await engine.close()
                    finally:
                        try:
                            if observation is not None:await observation.close()
                        finally:
                            # Account initialization/replay/listen can fail
                            # before etf.run enters its own finally block.
                            if etf is not None:etf.journal.db.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description='Explicit NATS-free local roles')
    parser.add_argument('role',choices=('check','collector','engine','storage','telegram','dashboard'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--producer')
    args=parser.parse_args(argv)
    plan=LocalRuntimePlan.load(args.config)
    # Validate mounts before any settings helper creates a data directory.
    os.environ['VIBETRADING_RUNTIME']='local'
    settings=RuntimeSettings.from_env()
    settings.env['LOCAL_SPOOL_DISK_BUDGET_BYTES']=str(sum(item.limits.max_disk_bytes
        for item in (*plan.producers,*((plan.observation,) if plan.observation else ()))))
    os.environ['LOCAL_SPOOL_DISK_BUDGET_BYTES']=settings.env['LOCAL_SPOOL_DISK_BUDGET_BYTES']
    validate_runtime(plan,settings)
    if args.role=='check':
        print(json.dumps({'runtime':'local-spool-uds-parquet','nats_required':False,
            'producers':[item.producer for item in plan.producers],'capacity_contract_verified':True}))
        return 0
    settings.ensure_dirs()
    if args.role=='telegram':
        from .local_events import FactsEventBudgets, LocalFactsEventBus
        from .telegram import TelegramController
        factory=lambda:LocalFactsEventBus(settings.state_dir/'local_storage_facts.sqlite3',
            settings.state_dir/'telegram'/'local-facts.sqlite3',budgets=FactsEventBudgets(**plan.events))
        asyncio.run(TelegramController(settings,bus_factory=factory).run())
        return 0
    if args.role=='dashboard':
        from .dashboard.bridge import run
        os.environ['LOCAL_MARKET_SNAPSHOT_FILE']=str(settings.state_dir/'local-market.json')
        os.environ['LOCAL_SNAPSHOT_BUDGETS']=json.dumps(plan.snapshot)
        asyncio.run(run())
        return 0
    if args.role=='collector':
        matches=[item for item in plan.producers if item.producer==args.producer]
        if len(matches)!=1:raise ValueError('select one configured producer')
        asyncio.run(run_collector(plan,matches[0],settings))
    elif args.role=='engine':asyncio.run(run_engine(plan,settings))
    else:
        from .local_storage import run_local_storage
        run_local_storage(plan,settings)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:raise SystemExit(130)
