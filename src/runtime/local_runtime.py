"""NATS-free roles: collector -> local spool -> UDS engine, spool -> Parquet.

Invoke explicitly with ``python -m runtime.local_runtime``. Legacy CLI and
deployed services are not silently switched. Each storage role is a separate
process so Arrow/catalog/disk stalls cannot monopolize the trading loop.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace

from feeds.feed_recovery import EngineCursor, RecoveryFrame
from market.hot_path import HotPathStore
from .local_config import LocalRuntimePlan
from market.local_engine import EngineBudgets, LocalMarketEngine
from feeds.local_pipeline import SpoolingMarketPublisher
from .local_transport import AppliedReceipt, LocalTransportClient, LocalTransportServer
from market.market_apply_order import ApplyOrder
from storage.checkpoint_journal import CheckpointJournal
from storage.market_spool import MarketSpool, SpoolCorruption, SpoolCursor, SpoolReader, scan_spool
from .settings import RuntimeSettings


def validate_runtime(plan,settings):
    if sys.platform!='linux':raise RuntimeError('production local runtime requires Linux UDS')
    if settings.env.get('VIBETRADING_ENFORCE_MOUNTS','').lower()!='true':
        raise RuntimeError('local runtime requires enforced storage mount identities')
    from .runtime_paths import validate_runtime_paths
    from storage.local_storage import StorageBudgets, fact_storage_reserve
    from storage.local_events import FactsEventBudgets, checkpoint_storage_reserve
    from market.local_snapshot import SnapshotBudgets
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


@dataclass(frozen=True)
class _SegmentProof:
    dev: int
    ino: int
    size: int
    mtime_ns: int | None
    active: bool


def _open_checkpoint_segment(root,number):
    sealed=Path(root)/f"{number:020d}.sealed"
    active=Path(root)/f"{number:020d}.active"
    # Reuse the bounded active/sealed rename-open sequence. Descriptor
    # metadata, not a path re-stat, authorizes the frozen segment.
    for path,is_sealed in ((sealed,True),(active,False),(sealed,True)):
        try:
            return path,is_sealed,path.open("rb")
        except FileNotFoundError:
            continue
    raise SpoolCorruption("checkpoint spool segment is missing")


def _capture_segment_proofs(root,report):
    """Pin only metadata for the frozen scan inventory; no payloads/index."""
    proofs={}
    for number in range(1,report.segment_count+1):
        _,sealed,opened=_open_checkpoint_segment(root,number)
        try:
            stat=os.fstat(opened.fileno())
            was_active=number==report.active_segment_id
            if not was_active and not sealed:
                raise SpoolCorruption("checkpoint spool sealed segment regressed")
            proofs[number]=_SegmentProof(stat.st_dev,stat.st_ino,stat.st_size,
                stat.st_mtime_ns if sealed else None,was_active)
        finally:
            opened.close()
    return proofs


class _CheckpointSpoolReader(SpoolReader):
    def __init__(self,root,producer_id,incarnation,*,proofs,**kwargs):
        # Proofs precede the superclass constructor, which may open segments
        # while locating C.
        self._proofs=dict(proofs)
        self._sealed_observations={number:(proof.size,proof.mtime_ns)
            for number,proof in self._proofs.items() if proof.mtime_ns is not None}
        super().__init__(root,producer_id,incarnation,**kwargs)

    def _open_segment(self,number):
        proof=self._proofs.get(number)
        if proof is None:
            raise SpoolCorruption("checkpoint spool segment is not in frozen inventory")
        path,sealed,opened=super()._open_segment(number)
        try:
            stat=os.fstat(opened.fileno())
            if (stat.st_dev,stat.st_ino)!=(proof.dev,proof.ino):
                raise SpoolCorruption("checkpoint spool segment identity changed")
            if proof.active:
                # The active prefix may legitimately grow and later be sealed
                # under the same inode. Never require active size/mtime equality.
                if stat.st_size<proof.size:
                    raise SpoolCorruption("checkpoint spool active segment shrank")
                if sealed:
                    signature=(stat.st_size,stat.st_mtime_ns)
                    observed=self._sealed_observations.get(number)
                    if observed is None:
                        self._sealed_observations[number]=signature
                    elif signature!=observed:
                        raise SpoolCorruption("checkpoint spool sealed segment changed")
                elif number in self._sealed_observations:
                    raise SpoolCorruption("checkpoint spool sealed segment regressed")
            else:
                if not sealed:
                    raise SpoolCorruption("checkpoint spool sealed segment regressed")
                if (stat.st_size,stat.st_mtime_ns)!=(proof.size,proof.mtime_ns):
                    raise SpoolCorruption("checkpoint spool sealed segment changed")
            return path,sealed,opened
        except BaseException:
            opened.close()
            raise

    def validate_inventory(self):
        for number in sorted(self._proofs):
            _,_,opened=self._open_segment(number)
            opened.close()


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
    def transport_gap(cursor,reason):
        progress=publisher.stats
        orderly=(reason=='client_closed' and publisher._closing and progress['failed']==0
            and progress['accepted']==progress['durable']==progress['applied'])
        if not orderly:publisher._invalidate(RuntimeError(reason))
    def transport_factory():
        return LocalTransportClient(plan.endpoint,producer=item.producer,incarnation=item.incarnation,
            budgets=plan.transport,
            on_gap=(lambda cursor,reason:None) if item.delivery is not None else
                transport_gap,
            allowed_uid=plan.allowed_uid)
    delivery={}
    if item.delivery is not None:
        delivery={'delivery_budgets':item.delivery,'transport_factory':transport_factory,
            'reader_factory':lambda cursor:SpoolReader(item.spool_root,item.producer,item.incarnation,
                after=SpoolCursor(cursor.producer,cursor.incarnation,cursor.sequence),**item.limits.reader_kwargs)}
    publisher=SpoolingMarketPublisher(producer=item.producer,incarnation=item.incarnation,
        spool_factory=lambda:create_spool(item),transport=None if delivery else transport_factory(),
        budgets=item.pipeline,env=settings.env,on_invalid=invalid,resume_existing=True,**delivery)
    try:
        await publisher.connect()
        await collector.run(publisher,duration_sec=None)
    finally:
        await publisher.close()


async def replay_startup(engine,producers,*,promote=True):
    """Restore fresh process state from exact C=0..durable-H, never trade it."""
    if engine.hot_path is not None and engine.hot_path.apply_seq!=0:
        raise RuntimeError('startup replay requires a virgin state authority; verified state restore is not implemented')
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
            records=await asyncio.to_thread(reader.read_batch,
                max_records=min(item.pipeline.batch_records,high.sequence-reader.cursor.sequence),
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


async def save_engine_checkpoint(engine,codec,path,*,order_spool=None):
    """Capture on the owner loop; persist one immutable checkpoint off-loop.

    Cancellation cannot overlap an older write with a newer capture. The
    single retained task owns the write until fsync/readback finishes.
    """
    from storage.market_checkpoint import _write_checkpoint_file
    pending=getattr(engine,'_checkpoint_file_task',None)
    if pending is not None and not pending.done():
        raise RuntimeError('checkpoint write already pending')
    options={} if order_spool is None else {'order_spool':order_spool}
    payload=engine.capture_checkpoint(codec,**options)
    operation=asyncio.create_task(asyncio.to_thread(
        _write_checkpoint_file,path,payload,codec.max_bytes),name='local-market-checkpoint-write')
    engine._checkpoint_file_task=operation
    # A cancelled caller may no longer retrieve a failed write's exception.
    operation.add_done_callback(lambda task:None if task.cancelled() else task.exception())
    await asyncio.shield(operation)
    return hashlib.sha256(payload).hexdigest()


async def replay_checkpoint_startup(engine,producers,*,codec,payload,expected_engine_incarnation,apply_order=None):
    """Restore market state only, with independently checked C and fixed H.

    Multiple nonempty suffixes require an exact durable apply-order proof.
    This function never arms initial resume or grants execution readiness.
    """
    if not isinstance(producers,(tuple,list)) or not 0<len(producers)<=engine.budgets.max_producers:
        raise ValueError('Checkpoint startup requires a bounded producer inventory')
    metadata=engine.inspect_checkpoint(codec,payload)
    checkpoints={item.cursor.producer:item for item in metadata.producers}
    items={item.producer:item for item in producers}
    if len(items)!=len(producers) or set(items)!=set(checkpoints):
        raise RuntimeError('checkpoint producer inventory mismatch')
    highwaters={}
    verified={}
    readers={}
    for producer,item in items.items():
        point=checkpoints[producer]
        report=await asyncio.to_thread(_scan,item)
        if report.uncertain:
            raise RuntimeError('uncertain producer tail requires explicit repair')
        if (point.cursor.incarnation!=item.incarnation
                or point.cursor.sequence>report.durable_sequence):
            raise RuntimeError('checkpoint cursor exceeds matching durable producer prefix')
        highwaters[producer]=EngineCursor(producer,item.incarnation,report.durable_sequence)
        proofs=await asyncio.to_thread(_capture_segment_proofs,item.spool_root,report)
        after=(SpoolCursor(item.producer,item.incarnation,point.cursor.sequence-1)
               if point.cursor.sequence else None)
        reader=await asyncio.to_thread(lambda item=item,proofs=proofs,after=after:_CheckpointSpoolReader(
            item.spool_root,item.producer,item.incarnation,proofs=proofs,after=after,
            **item.limits.reader_kwargs))
        readers[producer]=reader
        if point.cursor.sequence==0:
            digest=hashlib.sha256(b'').hexdigest()
        else:
            rows=await asyncio.to_thread(reader.read_batch,max_records=1,max_bytes=item.pipeline.max_bytes)
            if len(rows)!=1 or rows[0].cursor.sequence!=point.cursor.sequence:
                raise RuntimeError('checkpoint original cursor is not retained')
            digest=hashlib.sha256(rows[0].payload).hexdigest()
        if digest!=point.digest:
            raise RuntimeError('checkpoint original cursor digest mismatch')
        verified[producer]=AppliedReceipt(point.cursor,digest)
    if apply_order is not None:
        if type(apply_order) is not ApplyOrder:
            raise ValueError('checkpoint startup requires an apply order proof')
        apply_order.validate(payload,[(point.cursor,highwaters[p]) for p,point in checkpoints.items()])
    elif sum(highwaters[p].sequence>checkpoints[p].cursor.sequence for p in items)>1:
        raise RuntimeError('multiple checkpoint suffixes require a verified global apply order')
    # The frozen inventory is rechecked before installing target state. A
    # pre-install failure leaves the still-virgin engine to its caller.
    for reader in readers.values():
        await asyncio.to_thread(reader.validate_inventory)
    handoffs=engine.restore_virgin(codec,payload,highwaters,
        verified_cursors=verified,expected_engine_incarnation=expected_engine_incarnation,
        apply_order=apply_order)
    try:
        # A restore hook may have replaced C=0 or an unopened tail. Recheck
        # before applying any frame; later opens still check their descriptor.
        for reader in readers.values():
            await asyncio.to_thread(reader.validate_inventory)
        if apply_order is not None:
            for receipt in apply_order.receipts:
                c=receipt.cursor
                item=items[c.producer]
                reader=readers.get(c.producer)
                if reader is None:
                    raise RuntimeError('checkpoint reader authority missing')
                rows=await asyncio.to_thread(reader.read_batch,max_records=1,max_bytes=item.pipeline.max_bytes)
                if len(rows)!=1:
                    raise RuntimeError('durable checkpoint suffix is incomplete')
                row=rows[0]
                frame=RecoveryFrame(EngineCursor(row.cursor.producer,row.cursor.incarnation,row.cursor.sequence),row.payload)
                if AppliedReceipt(frame.cursor,frame.digest)!=receipt:
                    raise RuntimeError('checkpoint apply order payload mismatch')
                engine.apply_replay(frame)
            return handoffs
        for initial,high in handoffs:
            if initial==high:continue
            item=items[initial.producer]
            reader=readers.get(initial.producer)
            if reader is None:
                raise RuntimeError('checkpoint reader authority missing')
            while reader.cursor.sequence<high.sequence:
                rows=await asyncio.to_thread(reader.read_batch,
                    max_records=min(item.pipeline.batch_records,high.sequence-reader.cursor.sequence),
                    max_bytes=item.pipeline.max_bytes)
                if not rows:raise RuntimeError('durable checkpoint suffix is incomplete')
                for row in rows:
                    engine.apply_replay(RecoveryFrame(EngineCursor(row.cursor.producer,
                        row.cursor.incarnation,row.cursor.sequence),row.payload))
        return handoffs
    except BaseException:
        # A partially restored candidate is never reused as a virgin fallback.
        await engine.close()
        raise


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


def etf_feed_dependencies(plan, producer):
    """Legacy ETF live inputs are Kiwoom; other collectors are independent.

    Unknown/shared-authority failures still fence every ETF symbol. An
    observation writer failure is an authority failure, not a market-feed
    dependency, and intentionally takes that conservative path.
    """
    from .etf_trading import SYMBOLS
    item=next((item for item in plan.producers if item.producer==producer),None)
    if item is None:return tuple(sorted(SYMBOLS))
    if item.provider!='kiwoom':return ()
    symbols=[]
    for raw in item.symbols.split(','):
        raw=raw.strip().upper()
        if len(raw)==7 and raw.startswith('A'):raw=raw[1:]
        if len(raw)!=6 or not raw.isdigit():return tuple(sorted(SYMBOLS))
        if raw in SYMBOLS:symbols.append(raw)
    return tuple(sorted(set(symbols)))


async def run_engine(plan,settings,*,etf_factory=None,paper_runtime=None,paper_feed_dependencies=None,
                     selected_action_builders=None,native_selection_bindings=None,
                     recovery_readiness=None,checkpoint_path=None,checkpoint_codec=None,
                     checkpoint_engine_incarnation=None,checkpoint_readiness=None,checkpoint_apply_order=None,
                     checkpoint_journal=None,runtime_checkpoint=None,on_command=None,
                     timefolio_config=None,timefolio_producers=None,timefolio_duration=None):
    from .selected_execution import (NativeSelectionBinding, NativeSelectionDispatcher,
                                     activate_paper_selections)
    selections=tuple(getattr(plan,'strategy_selections',()))
    paper_selections=tuple(item for item in selections if item.mode=='PAPER')
    native_selections=tuple(item for item in selections if item.mode in {'TESTNET','LIVE'})
    if paper_selections and (paper_runtime is None or selected_action_builders is None):
        raise ValueError('configured PAPER selections require their existing runtime and explicit action builders')
    if not paper_selections and selected_action_builders is not None:
        raise ValueError('PAPER action builders were supplied without configured PAPER selections')
    if native_selections:
        if (not isinstance(native_selection_bindings,dict)
                or set(native_selection_bindings)!={item.job_id for item in native_selections}):
            raise ValueError('configured native selections require one exact injected owner binding per job')
    elif native_selection_bindings:
        raise ValueError('native selection bindings were supplied without configured native selections')
    live_by_owner={}
    for selection in native_selections:
        if selection.mode!='LIVE':continue
        binding=native_selection_bindings[selection.job_id]
        coordinator=getattr(getattr(binding,'runner',None),'group_coordinator',None)
        owners=getattr(coordinator,'owners',None)
        if owners is not None:
            selected_owners=[owners[key] for key in selection.registration['accounts'] if key in owners]
            if len(selected_owners)!=len(selection.registration['accounts']):
                raise ValueError('LIVE group selection lacks an exact owner for every registered account')
        else:
            owner=getattr(binding,'owner',None)
            selected_owners=[] if owner is None else [owner]
        for owner in {id(item):item for item in selected_owners}.values():
            prior=live_by_owner.get(id(owner))
            if prior is not None:
                raise ValueError('one owner cannot be rebound by multiple LIVE selections without an exact multi-source authorization map')
            live_by_owner[id(owner)]=selection.job_id
    native_dispatchers=[]
    checkpoint_enabled=any(value is not None for value in (
        checkpoint_path,checkpoint_codec,checkpoint_engine_incarnation,checkpoint_apply_order)) or (
            checkpoint_readiness is not None and checkpoint_journal is None)
    if timefolio_config is not None:
        from timefolio.runner import TimefolioStrategyRunnerConfig
        if type(timefolio_config) is not TimefolioStrategyRunnerConfig:
            raise TypeError('Timefolio requires its explicit runner configuration')
        if (plan.etf_enabled or paper_runtime is not None or checkpoint_enabled
                or checkpoint_journal is not None or runtime_checkpoint is not None):
            raise ValueError('Timefolio requires a dedicated engine consumer, without ETF/Paper/checkpoint injection')
        names={item.producer for item in plan.producers}
        if (not isinstance(timefolio_producers,tuple) or not timefolio_producers
                or len(set(timefolio_producers))!=len(timefolio_producers)
                or not set(timefolio_producers)<=names or plan.observation is None):
            raise ValueError('Timefolio requires explicit configured producers and a local observation spool')
    elif timefolio_producers is not None or timefolio_duration is not None:
        raise ValueError('Timefolio source/duration requires an explicit runner configuration')
    if runtime_checkpoint is not None:
        from .runtime_checkpoint import RuntimeCheckpoint
        if type(runtime_checkpoint) is not RuntimeCheckpoint:
            raise ValueError('runtime_checkpoint requires the exact coupled checkpoint owner')
        if checkpoint_enabled or checkpoint_journal is not None:
            raise ValueError('Coupled runtime checkpoint is mutually exclusive with market/journal startup options')
        # No automatic readiness authority. Require the explicit synchronous
        # callback before inspecting any Paper/setup/factory/transport field.
        if recovery_readiness is None:
            raise ValueError('Coupled runtime checkpoint requires an explicit synchronous readiness callback')
        if (not callable(recovery_readiness)
                or inspect.iscoroutinefunction(recovery_readiness)):
            raise TypeError('Recovery readiness must be an explicit synchronous state validator')
        if paper_runtime is None and not plan.etf_enabled:
            raise ValueError('Coupled runtime checkpoint requires a selected Paper/ETF consumer')
        if paper_runtime is not None and paper_runtime.hot_path.apply_seq != 0:
            raise ValueError('Coupled runtime restore requires a virgin Paper hot path')
    if checkpoint_journal is not None:
        if (type(checkpoint_journal) is not CheckpointJournal or checkpoint_enabled
                or not callable(checkpoint_readiness) or inspect.iscoroutinefunction(checkpoint_readiness)):
            raise ValueError('Journal startup requires an explicit manager and synchronous readiness, without legacy checkpoint options')
        if plan.etf_enabled or paper_runtime is not None:
            raise ValueError('ETF/Paper checkpoint startup requires separate strategy and owner state recovery')
    if checkpoint_enabled:
        if (any(value is None for value in (checkpoint_path,checkpoint_codec,
                checkpoint_engine_incarnation,checkpoint_readiness))
                or type(checkpoint_engine_incarnation) is not str or not checkpoint_engine_incarnation
                or not callable(checkpoint_readiness) or inspect.iscoroutinefunction(checkpoint_readiness)):
            raise ValueError('Checkpoint startup requires explicit codec/path/incarnation and synchronous readiness validator')
        if plan.etf_enabled or paper_runtime is not None:
            raise ValueError('ETF/Paper checkpoint startup requires separate strategy and owner state recovery')
        if checkpoint_apply_order is not None and type(checkpoint_apply_order) is not ApplyOrder:
            raise ValueError('Checkpoint startup requires an apply order proof')
    if recovery_readiness is not None:
        if not callable(recovery_readiness) or inspect.iscoroutinefunction(recovery_readiness):
            raise TypeError('Recovery readiness must be an explicit synchronous state validator')
    if paper_feed_dependencies is not None:
        if (paper_runtime is None or set(paper_feed_dependencies)!={p.producer for p in plan.producers}
                or any(not isinstance(keys,tuple) or len(set(keys))!=len(keys)
                       or not set(keys)<=set(paper_runtime.known)
                       for keys in paper_feed_dependencies.values())):
            raise ValueError('Paper feed ownership requires an exact producer inventory and known feed keys')
    # Finish pure configuration/restore compatibility checks before any
    # selected native account read or Paper source registration.
    for selection in native_selections:
        binding=native_selection_bindings[selection.job_id]
        if not isinstance(binding,NativeSelectionBinding):
            raise TypeError('native selection binding must use the explicit typed assembly')
        native_dispatchers.append(NativeSelectionDispatcher(selection,binding))
    if native_dispatchers:
        snapshot_bound=int(plan.engine['max_frame_bytes'])*int(plan.engine['max_instruments'])
        for dispatcher in native_dispatchers:
            if dispatcher.selection.snapshot_charge_bytes < snapshot_bound:
                raise ValueError('selected native queue byte charge is below the configured immutable snapshot bound')
            # The bounded per-source drain proves native readiness before dispatch.
            # Native account I/O must not gate unrelated market apply or sources.
    activate_paper_selections(plan,paper_runtime,selected_action_builders)
    etf=None
    engine=None
    journal_restored=False
    def invalid(producer,reason):
        if native_dispatchers and engine is not None and producer in {p.producer for p in plan.producers}:
            affected=set(engine.feed_instruments(producer))
            for dispatcher in native_dispatchers:
                if affected.intersection(dispatcher.required_feeds):
                    dispatcher.invalidate(reason)
        if etf is not None:
            # Synchronous local gate closes before a timer/order can run again.
            for symbol in etf_feed_dependencies(plan,producer):etf._invalidate_market_feed(symbol)
        if paper_runtime is not None:
            if paper_feed_dependencies is not None and producer in paper_feed_dependencies:
                keys=paper_feed_dependencies[producer]
            else:
                # Observed exact ownership can narrow the fence. A producer
                # without any ownership proof cannot attest independence.
                keys=engine.feed_instruments(producer) if engine is not None and producer in {
                    p.producer for p in plan.producers} else ()
                keys=tuple(key for key in keys if key in paper_runtime.known) or tuple(paper_runtime.known)
            if keys:paper_runtime.invalidate_inputs(keys,'local_source_invalid',time.time_ns())
    from core.models import AssetClass
    timefolio_providers={item.provider for item in plan.producers
        if timefolio_producers is not None and item.producer in timefolio_producers}
    engine=LocalMarketEngine(producer_providers={p.producer:p.provider for p in plan.producers},
        budgets=EngineBudgets(**plan.engine),on_invalidate=invalid,
        hot_path=paper_runtime.hot_path if paper_runtime is not None else HotPathStore(),
        # Coupled restore installs market/plugin state with no callback attached.
        # The selected Paper callback is published only after every proof.
        post_apply_callbacks=(() if runtime_checkpoint is not None else
            ((paper_runtime.process_applied,) if paper_runtime is not None else ())+
            tuple(dispatcher.enqueue_applied for dispatcher in native_dispatchers)),
        on_tick=(getattr(paper_runtime,"advance",None) if paper_runtime is not None else None),
        snapshot_clock=time.time_ns,
        trading_filter=(lambda event:event.provider=='kiwoom' and event.instrument.asset_class==AssetClass.ETF)
            if plan.etf_enabled else (lambda event:event.provider in timefolio_providers
                and event.instrument.asset_class!=AssetClass.CRYPTO_PERP)
            if timefolio_config is not None else lambda event:False)
    def recovery_commit(high_water,digest):
        if checkpoint_enabled or journal_restored:
            receipt=engine.query_cursor(high_water.producer)
            if receipt.cursor!=high_water or receipt.digest!=digest:
                return False
            return engine.complete_checkpoint_recovery(checkpoint_readiness)
        ready=False
        if recovery_readiness is not None:
            ready=recovery_readiness(engine,high_water,digest)
            if inspect.isawaitable(ready):
                if inspect.iscoroutine(ready):ready.close()
                raise TypeError('Recovery readiness must not return an awaitable')
        return engine.commit_recovery(high_water,digest,ready=ready is True)
    recovery_hooks={}
    if any(p.delivery is not None for p in plan.producers):
        recovery_hooks={'on_replay':engine.apply_replay,'on_recovery_query':engine.begin_delivery_recovery,
                        'on_recovery_commit':recovery_commit}
    server=LocalTransportServer(plan.endpoint,budgets=plan.transport,on_frame=engine.on_frame,
        on_gap=engine.on_gap,on_resume=engine.resume_receipt,allowed_uid=plan.allowed_uid,
        on_command=on_command,**recovery_hooks)
    observation=None
    snapshot_writer=None
    task=None
    try:
        if runtime_checkpoint is not None:
            if plan.etf_enabled:
                from operations.local_observation import LocalObservationBus
                item=plan.observation
                observation=LocalObservationBus(producer=item.producer,incarnation=item.incarnation,
                    spool_factory=lambda:create_spool(item),
                    budgets=item.pipeline,on_invalid=lambda reason:invalid(item.producer,reason))
                await observation.connect()
                if etf_factory is None:
                    from .etf_trading import EtfTradingLoop
                    etf_factory=EtfTradingLoop
                etf=etf_factory(settings=settings,bus=observation,
                    event_source=engine.event_source(required_producers=()))
            handoffs=await runtime_checkpoint.restore(engine,plan.producers,
                paper_runtime=paper_runtime,etf=etf,readiness=recovery_readiness)
            engine.post_apply_callbacks=(
                ((paper_runtime.process_applied,) if paper_runtime is not None else ())+
                tuple(dispatcher.enqueue_applied for dispatcher in native_dispatchers))
        elif checkpoint_journal is not None:
            journal_restored=await checkpoint_journal.restore(engine,plan.producers,checkpoint_readiness)
            handoffs=() if journal_restored else await replay_startup(engine,plan.producers,promote=False)
        elif checkpoint_enabled:
            from storage.market_checkpoint import _read_checkpoint_file
            payload=await asyncio.to_thread(_read_checkpoint_file,checkpoint_path,checkpoint_codec.max_bytes)
            handoffs=await replay_checkpoint_startup(engine,plan.producers,codec=checkpoint_codec,
                payload=payload,expected_engine_incarnation=checkpoint_engine_incarnation,apply_order=checkpoint_apply_order)
        else:
            handoffs=await replay_startup(engine,plan.producers,promote=False)
        if plan.etf_enabled and etf is None:
            from operations.local_observation import LocalObservationBus
            item=plan.observation
            observation=LocalObservationBus(producer=item.producer,incarnation=item.incarnation,
                spool_factory=lambda:create_spool(item),
                budgets=item.pipeline,on_invalid=lambda reason:invalid(item.producer,reason))
            await observation.connect()
            if etf_factory is None:
                from .etf_trading import EtfTradingLoop
                etf_factory=EtfTradingLoop
            # ETF has its own per-symbol/group gates. A crypto collector gap
            # must not terminate this iterator and finally halt all ETFs.
            etf=etf_factory(settings=settings,bus=observation,
                event_source=engine.event_source(required_producers=()))
            # Reconcile accounts and prepare the legacy journal before any
            # local feed can be promoted or any strategy timer is scheduled.
            await etf.initialize()
            await restore_etf_startup(etf,plan.producers,handoffs)
        if checkpoint_enabled:
            if engine.complete_checkpoint_recovery(checkpoint_readiness) is not True:
                raise RuntimeError('checkpoint readiness validation did not permit startup')
        elif runtime_checkpoint is None and not journal_restored:
            for initial,high in handoffs:
                engine.arm_initial_resume(initial,high)
        if checkpoint_journal is not None:await checkpoint_journal.rotate(engine)
        timefolio=None
        if timefolio_config is not None:
            from operations.local_observation import LocalObservationBus
            from timefolio.runner import TimefolioStrategyRunner
            item=plan.observation
            observation=LocalObservationBus(producer=item.producer,incarnation=item.incarnation,
                spool_factory=lambda:create_spool(item),budgets=item.pipeline,
                on_invalid=lambda reason:invalid(item.producer,reason))
            timefolio=TimefolioStrategyRunner(timefolio_config,bus=observation,
                event_source=engine.event_source(required_producers=timefolio_producers))
        for dispatcher in native_dispatchers:
            dispatcher.task=asyncio.create_task(dispatcher.run(),name='selected-native-'+dispatcher.selection.job_id)
        await server.start()
        await engine.start()
        from market.local_snapshot import LocalSnapshotWriter, SnapshotBudgets
        snapshot_writer=LocalSnapshotWriter(engine,settings.state_dir/'local-market.json',SnapshotBudgets(**plan.snapshot))
        await snapshot_writer.start()
        if etf is not None:task=asyncio.create_task(etf.run(initialized=True),name='local-etf')
        if timefolio is not None:
            task=asyncio.create_task(timefolio.run(duration_sec=timefolio_duration),name='local-timefolio')
        if task is not None:
            results=await task
            if timefolio is not None:return results
        elif checkpoint_journal is not None:await checkpoint_journal.run()
        else:await asyncio.Event().wait()
    finally:
        for dispatcher in native_dispatchers:
            dispatcher.invalidate('runtime_closed')
        invalid('shutdown','runtime_closed')
        try:
            for dispatcher in native_dispatchers:
                if dispatcher.task is not None:dispatcher.task.cancel()
            if native_dispatchers:
                await asyncio.gather(*(item.task for item in native_dispatchers
                    if item.task is not None),return_exceptions=True)
            if task is not None:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
        finally:
            try:await server.close()
            finally:
                try:
                    if snapshot_writer is not None:await snapshot_writer.close()
                finally:
                    try:
                        try:
                            if checkpoint_journal is not None:await checkpoint_journal.close()
                        finally:
                            try:await engine.close()
                            finally:
                                if paper_runtime is not None:
                                    close_workers=getattr(paper_runtime,'close_async',None)
                                    if callable(close_workers):await close_workers()
                    finally:
                        try:
                            if observation is not None:await observation.close()
                        finally:
                            # Account initialization/replay/listen can fail
                            # before etf.run enters its own finally block.
                            if etf is not None:
                                if callable(getattr(etf,'close',None)):await etf.close()
                                else:etf.journal.db.close()


def main(argv=None, *, paper_runtime=None, paper_feed_dependencies=None,
         selected_action_builders=None,native_selection_bindings=None):
    parser=argparse.ArgumentParser(description='Explicit NATS-free local roles')
    parser.add_argument('role',choices=('check','collector','engine','storage','telegram','dashboard'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--producer')
    args=parser.parse_args(argv)

    # Load plan first for validation
    plan=LocalRuntimePlan.load(args.config)
    if plan.strategy_selections and args.role!='engine':
        raise ValueError('Configured strategy selections are consumed only by the local engine role')

    # This is explicit borrowed local assembly, not a capability promotion.
    if paper_feed_dependencies is not None and paper_runtime is None:
        raise ValueError('Paper dependencies require an explicit Paper runtime')
    if paper_runtime is not None:
        from .strategy_runtime import PaperStrategyRuntime
        from execution.trading_contracts import TradingMode
        if args.role != 'engine':
            raise ValueError('Paper injection is supported only by the engine role')
        if not isinstance(paper_runtime, PaperStrategyRuntime):
            raise TypeError('paper_runtime must be a PaperStrategyRuntime')
        if paper_runtime.phase != 'paper' or any(
                matcher.ledger.mode != TradingMode.PAPER for matcher in paper_runtime.accounts.values()):
            raise ValueError('Paper injection requires explicit PAPER mode and paper phase')
        if paper_feed_dependencies is not None and (
                not isinstance(paper_feed_dependencies, dict)
                or set(paper_feed_dependencies) != {item.producer for item in plan.producers}
                or any(not isinstance(keys, tuple) or len(set(keys)) != len(keys)
                       or not set(keys) <= set(paper_runtime.known)
                       for keys in paper_feed_dependencies.values())):
            raise ValueError('Paper dependencies require the exact configured producer and known-feed inventory')
    configured_paper=tuple(item for item in plan.strategy_selections if item.mode=='PAPER')
    if configured_paper and paper_runtime is None:
        raise ValueError('Configured PAPER selections require an explicitly injected PaperStrategyRuntime')
    configured_native=tuple(item for item in plan.strategy_selections if item.mode in {'TESTNET','LIVE'})
    if configured_native and (not isinstance(native_selection_bindings,dict)
            or set(native_selection_bindings)!={item.job_id for item in configured_native}):
        raise ValueError('Configured native selections require exact explicit owner bindings')
    if selected_action_builders is not None and not configured_paper:
        raise ValueError('PAPER action builders were supplied without configured PAPER selections')
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

    # Provide snapshot settings for telegram and dashboard roles
    if args.role in ('telegram', 'dashboard'):
        os.environ['LOCAL_MARKET_SNAPSHOT_FILE']=str(settings.state_dir/'local-market.json')
        os.environ['LOCAL_SNAPSHOT_BUDGETS']=json.dumps(plan.snapshot)
        settings.env['LOCAL_MARKET_SNAPSHOT_FILE']=str(settings.state_dir/'local-market.json')
        settings.env['LOCAL_SNAPSHOT_BUDGETS']=json.dumps(plan.snapshot)

    if args.role=='telegram':
        from storage.local_events import FactsEventBudgets, LocalFactsEventBus
        from operations.telegram import TelegramController
        factory=lambda:LocalFactsEventBus(settings.state_dir/'local_storage_facts.sqlite3',
            settings.state_dir/'telegram'/'local-facts.sqlite3',budgets=FactsEventBudgets(**plan.events))
        asyncio.run(TelegramController(settings,bus_factory=factory).run())
        return 0
    if args.role=='dashboard':
        from operations.dashboard.bridge import run
        asyncio.run(run())
        return 0
    if args.role=='collector':
        matches=[item for item in plan.producers if item.producer==args.producer]
        if len(matches)!=1:raise ValueError('select one configured producer')
        asyncio.run(run_collector(plan,matches[0],settings))
    elif args.role=='engine':
        selected_kwargs={}
        if paper_runtime is not None:
            selected_kwargs.update(paper_runtime=paper_runtime,
                paper_feed_dependencies=paper_feed_dependencies)
        if selected_action_builders is not None:
            selected_kwargs['selected_action_builders']=selected_action_builders
        if native_selection_bindings is not None:
            selected_kwargs['native_selection_bindings']=native_selection_bindings
        asyncio.run(run_engine(plan,settings,**selected_kwargs))
    else:
        from storage.local_storage import run_local_storage
        run_local_storage(plan,settings)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:raise SystemExit(130)
