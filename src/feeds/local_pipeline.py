"""NATS-independent bounded collector ingress with a single spool writer thread.

Collector callbacks transfer owned bytes to a finite local queue. Group sync
and local transport run in persistent workers. The initial route forwards only
producer-DURABLE records; APPLIED remains a separate engine acknowledgement.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import inspect
import math
import time
from typing import Callable

from runtime.bus import MarketEventPacket, prepare_market_event, resolve_market_publication
from .feed_recovery import EngineCursor, RecoveryFrame


@dataclass(frozen=True)
class PipelineBudgets:
    max_records: int
    max_bytes: int
    max_frame_bytes: int
    batch_records: int
    group_sync_ms: int

    def __post_init__(self):
        for value in (self.max_records,self.max_bytes,self.max_frame_bytes,self.batch_records,self.group_sync_ms):
            if type(value) is not int or value <= 0:
                raise ValueError("pipeline budgets must be explicit positive integers")
        if self.max_frame_bytes > self.max_bytes or self.batch_records > self.max_records:
            raise ValueError("pipeline frame/batch exceeds its ownership budget")


@dataclass(frozen=True)
class DeliveryBudgets:
    max_batch_records: int
    max_batch_bytes: int
    poll_seconds: float
    reconnect_backoff_cap_seconds: float
    io_timeout_seconds: float

    def __post_init__(self):
        for value in (self.max_batch_records, self.max_batch_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("delivery budgets require explicit positive integers")
        for value in (self.poll_seconds, self.reconnect_backoff_cap_seconds, self.io_timeout_seconds):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("delivery timing budgets must be positive and finite")


@dataclass
class _Admission:
    identity: int
    payload: bytes
    size: int
    accepted_ns: int
    cursor: EngineCursor | None = None
    done: bool = False


class DeliveryIntegrityError(RuntimeError):
    pass


class SpoolingMarketPublisher:
    def __init__(self, *, producer: str, incarnation: str, spool_factory: Callable,
                 transport=None, budgets: PipelineBudgets, env: dict[str,str],
                 on_invalid: Callable[[str],None], resume_existing: bool=False,
                 delivery_budgets: DeliveryBudgets | None = None, reader_factory: Callable | None = None,
                 transport_factory: Callable | None = None) -> None:
        EngineCursor(producer,incarnation,0)
        if inspect.iscoroutinefunction(spool_factory) or inspect.iscoroutinefunction(on_invalid):
            raise TypeError("spool factory and invalidation callback must be synchronous")
        self.producer,self.incarnation=producer,incarnation
        self.spool_factory,self.transport,self.budgets=spool_factory,transport,budgets
        self.delivery_budgets=delivery_budgets
        self.reader_factory,self.transport_factory=reader_factory,transport_factory
        self._delivery_reader=None
        self._delivery_task=None
        self._delivery_state="BLOCKED" if delivery_budgets is not None else "LEGACY"
        self._delivery_backlog_started_ns=None
        self._delivery_inflight_records=0
        if delivery_budgets is None:
            if (transport is None or reader_factory is not None or transport_factory is not None
                    ):
                raise ValueError("legacy mode requires its transport and no delivery seams")
        elif reader_factory is None or transport_factory is None:
            raise ValueError("durable delivery requires explicit reader and transport factories")
        elif inspect.iscoroutinefunction(reader_factory):
            raise TypeError("reader factory must be synchronous and run on the spool executor")
        elif (delivery_budgets.max_batch_records > budgets.max_records
              or delivery_budgets.max_batch_bytes > budgets.max_bytes
              or delivery_budgets.max_batch_bytes < budgets.max_frame_bytes):
            raise ValueError("delivery batch exceeds pipeline ownership limits")
        self.env,self.on_invalid=dict(env),on_invalid
        if type(resume_existing) is not bool:raise TypeError("resume_existing must be boolean")
        self.resume_existing=resume_existing
        self._queue=asyncio.Queue(maxsize=budgets.max_records)
        self._owned: dict[int,_Admission]={}
        self._outstanding: dict[int,_Admission]={}
        self._bytes=0
        self._accepted=self._appended=self._durable=self._applied=self._failed=self._filtered=0
        self._last_applied=0
        self._last_durable=0
        self._last_durable_latency_ns=self._max_durable_latency_ns=0
        self._last_applied_latency_ns=self._max_applied_latency_ns=0
        self._delivery_catchup_overshoot_records=0
        self._delivery_stage="startup"
        self._last_delivery_error_type=""
        self._fault_stage=self._fault_reason=self._fault_cursor_sequence=None
        self._fault_oldest_pending_age_ns=0
        self._error: BaseException | None=None
        self._failure=asyncio.Event()
        self._settled=asyncio.Event();self._settled.set()
        self._workers=[]
        self._executor=None
        self._spool=None
        self._closing=False
        self._close_task=None
        self._ready=False
        self._startup_task=None
        self._factory_future=None
        self._cleanup_future=None
        self._cleanup_error=None
        self._cleanup_retained_bytes=0
        self._transport_close_task=None

    def _check(self):
        if self._error is not None:
            raise RuntimeError("local market ingress invalid") from self._error
        if self._closing:
            raise RuntimeError("local market ingress is closed")

    def _oldest_pending_age_ns(self, now_ns):
        return max((max(0,now_ns-item.accepted_ns) for item in self._owned.values()),default=0)

    def _invalidate(self, error: BaseException, *, stage="startup", reason="startup_failed", cursor=None):
        if self._error is not None:
            return
        # First-fault evidence is scalar and bounded; never retain error text,
        # payloads, or a growing per-record latency/error history.
        self._fault_stage=stage;self._fault_reason=reason
        self._fault_cursor_sequence=cursor.sequence if cursor is not None else None
        self._fault_oldest_pending_age_ns=self._oldest_pending_age_ns(time.monotonic_ns())
        self._error=error;self._failure.set()
        try:
            result=self.on_invalid(type(error).__name__)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):result.close()
                raise TypeError("local invalidation callback returned an awaitable")
        except BaseException:
            # Local latch is authoritative even if reporting is unavailable.
            pass

    async def connect(self, *, initialize: bool=False):
        del initialize
        self._check()
        if self._ready:return
        if self._startup_task is None:
            target = self._connect_delivery if self.delivery_budgets is not None else self._connect
            self._startup_task=asyncio.create_task(target(),name="local-ingress-startup")
        try:
            await asyncio.shield(self._startup_task)
        except BaseException as error:
            if not self._closing:
                self._invalidate(error)
                try:await self.close()
                except BaseException:pass
            raise

    def _create_spool(self):
        # This assignment belongs to the writer thread, even if the async
        # startup task was cancelled before the factory returned. Cleanup is
        # queued behind it on this same executor and cannot miss a late spool.
        self._spool=self.spool_factory()
        return self._spool.appended_cursor

    async def _connect(self):
        self._check()
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="market-spool")
        self._factory_future=self._executor.submit(self._create_spool)
        factory=asyncio.wrap_future(self._factory_future)
        factory.add_done_callback(self._observe_task)
        cursor=await asyncio.shield(factory)
        self._check()
        # Never skip pre-existing input or manufacture a fresh engine
        # checkpoint. Recovery must supply verified replay before restart.
        if cursor.sequence != 0 and not self.resume_existing:
            raise RuntimeError("existing spool requires explicit replay/handoff before collector startup")
        checkpoint=EngineCursor(self.producer,self.incarnation,cursor.sequence)
        if (cursor.producer,cursor.incarnation)!=(self.producer,self.incarnation):
            raise RuntimeError("spool startup identity mismatch")
        await self.transport.connect()
        self._check()
        # The engine accepts an existing checkpoint only after its separate,
        # state-only replay/handoff. Nothing from this prefix is republished.
        receipt=await self.transport.request_resume(checkpoint)
        self._check()
        if receipt.phase != "RESUMED":
            raise RuntimeError("local engine rejected ingress resume")
        self._last_applied=checkpoint.sequence
        self._last_durable=checkpoint.sequence
        self._ready=True
        self._workers=[asyncio.create_task(self._writer(),name="local-spool-writer"),
                       asyncio.create_task(self._acks(),name="local-engine-acks")]

    def _make_reader(self, cursor):
        return self.reader_factory(cursor)

    def _make_verified_reader(self, checkpoint, digest, high_water):
        if checkpoint.sequence>high_water.sequence:
            raise DeliveryIntegrityError("engine cursor exceeds producer durable high-water")
        if checkpoint.sequence==0:
            if digest!=hashlib.sha256(b"").hexdigest():
                raise DeliveryIntegrityError("empty checkpoint digest mismatch")
            return self._make_reader(checkpoint)
        previous=EngineCursor(checkpoint.producer,checkpoint.incarnation,checkpoint.sequence-1)
        try:
            reader=self._make_reader(previous)
            records=reader.read_batch(max_records=1,max_bytes=self.delivery_budgets.max_batch_bytes)
        except Exception as error:
            raise DeliveryIntegrityError("durable spool checkpoint cannot be read") from error
        if (len(records)!=1 or records[0].cursor.sequence!=checkpoint.sequence
                or (records[0].cursor.producer,records[0].cursor.incarnation)!=(self.producer,self.incarnation)
                or hashlib.sha256(records[0].payload).hexdigest()!=digest):
            raise DeliveryIntegrityError("engine checkpoint digest differs from durable spool")
        return reader

    def _read_delivery_batch(self, max_records=None):
        try:
            return self._delivery_reader.read_batch(max_records=max_records or self.delivery_budgets.max_batch_records,
                                                    max_bytes=self.delivery_budgets.max_batch_bytes)
        except Exception as error:
            raise DeliveryIntegrityError("durable spool delivery read failed") from error

    def _durable_cursor(self):
        cursor=self._spool.durable_cursor
        return EngineCursor(cursor.producer,cursor.incarnation,cursor.sequence)

    def _track_delivery_lag(self, accepted_ns=None):
        if self._last_durable<=self._last_applied:
            self._delivery_backlog_started_ns=None
        elif self._delivery_backlog_started_ns is None:
            self._delivery_backlog_started_ns=accepted_ns or time.monotonic_ns()

    async def _connect_delivery(self):
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="market-spool")
        self._factory_future=self._executor.submit(self._create_spool)
        factory=asyncio.wrap_future(self._factory_future);factory.add_done_callback(self._observe_task)
        cursor=await asyncio.shield(factory)
        self._check()
        if (cursor.producer,cursor.incarnation)!=(self.producer,self.incarnation):
            raise RuntimeError("spool startup identity mismatch")
        self._last_durable=cursor.sequence
        self._track_delivery_lag()
        self._ready=True
        self._workers=[asyncio.create_task(self._writer(),name="local-spool-writer")]
        self._delivery_task=asyncio.create_task(self._delivery_loop(),name="local-spool-delivery")
        self._workers.append(self._delivery_task)

    async def _deliver_live(self, transport, loop):
        self._delivery_state="LIVE"
        while not self._closing:
            self._delivery_stage="live_spool_read"
            records=await loop.run_in_executor(self._executor,self._read_delivery_batch)
            if not records:
                await asyncio.sleep(self.delivery_budgets.poll_seconds)
                continue
            for record in records:
                if (record.cursor.sequence!=self._last_applied+1
                        or (record.cursor.producer,record.cursor.incarnation)!=(self.producer,self.incarnation)):
                    raise DeliveryIntegrityError("spool delivery cursor is non-contiguous")
                frame=RecoveryFrame(EngineCursor(record.cursor.producer,record.cursor.incarnation,
                                                 record.cursor.sequence),record.payload)
                self._delivery_inflight_records=1
                self._delivery_stage="live_send"
                if not transport.try_send(frame):
                    raise RuntimeError("durable delivery send failed")
                deadline=asyncio.get_running_loop().time()+self.delivery_budgets.io_timeout_seconds
                remaining=deadline-asyncio.get_running_loop().time()
                self._delivery_stage="live_received_ack"
                received=await asyncio.wait_for(transport.receive_ack(),remaining)
                if (received.phase!="RECEIVED" or received.cursor!=frame.cursor
                        or received.digest!=frame.digest):
                    raise RuntimeError("durable delivery receipt mismatch")
                remaining=deadline-asyncio.get_running_loop().time()
                if remaining<=0:
                    raise asyncio.TimeoutError("durable delivery acknowledgement deadline exceeded")
                self._delivery_stage="live_applied_ack"
                receipt=await asyncio.wait_for(transport.receive_ack(),remaining)
                self._delivery_inflight_records=0
                if receipt.phase!="APPLIED" or receipt.cursor!=frame.cursor or receipt.digest!=frame.digest:
                    raise RuntimeError("durable delivery acknowledgement mismatch")
                self._delivery_stage="live_accounting"
                self._last_applied=frame.cursor.sequence
                self._applied+=1
                self._track_delivery_lag()

    async def _delivery_loop(self):
        delay=min(self.delivery_budgets.poll_seconds,self.delivery_budgets.reconnect_backoff_cap_seconds)
        try:
            while not self._closing:
                transport=None
                try:
                    self._delivery_stage="transport_factory"
                    transport=self.transport_factory()
                    if inspect.isawaitable(transport):
                        transport=await asyncio.wait_for(transport,self.delivery_budgets.io_timeout_seconds)
                    self.transport=transport
                    self._delivery_state="RECOVERING"
                    self._delivery_stage="transport_connect"
                    await asyncio.wait_for(transport.connect(),self.delivery_budgets.io_timeout_seconds)
                    loop=asyncio.get_running_loop()
                    self._delivery_stage="durable_cursor"
                    durable=await loop.run_in_executor(self._executor,self._durable_cursor)
                    self._delivery_stage="resume"
                    resume=await asyncio.wait_for(transport.request_resume(durable),self.delivery_budgets.io_timeout_seconds)
                    legacy_empty_proof=durable.sequence>0 and resume.digest==hashlib.sha256(b"").hexdigest()
                    resumed=resume.phase=="RESUMED" and resume.cursor==durable and not legacy_empty_proof
                    if resumed:
                        self._delivery_reader=await loop.run_in_executor(
                            self._executor,self._make_verified_reader,durable,resume.digest,durable)
                        self._last_applied=durable.sequence
                        self._track_delivery_lag()
                    else:
                        self._delivery_stage="query"
                        query=await asyncio.wait_for(transport.query_cursor(durable),self.delivery_budgets.io_timeout_seconds)
                        checkpoint=query.cursor
                        if (checkpoint.producer,checkpoint.incarnation)!=(self.producer,self.incarnation):
                            raise DeliveryIntegrityError("engine query identity mismatch")
                        self._delivery_reader=await loop.run_in_executor(
                            self._executor,self._make_verified_reader,checkpoint,query.digest,durable)
                        self._last_applied=checkpoint.sequence
                        self._track_delivery_lag()
                        digest=query.digest
                        while checkpoint.sequence < durable.sequence and not self._closing:
                            self._delivery_stage="replay_spool_read"
                            limit=min(self.delivery_budgets.max_batch_records,durable.sequence-checkpoint.sequence)
                            records=await loop.run_in_executor(self._executor,self._read_delivery_batch,limit)
                            if not records:
                                raise DeliveryIntegrityError("spool replay ended before fixed high-water")
                            for record in records:
                                if (record.cursor.sequence!=checkpoint.sequence+1
                                        or record.cursor.sequence>durable.sequence
                                        or (record.cursor.producer,record.cursor.incarnation)!=(self.producer,self.incarnation)):
                                    raise DeliveryIntegrityError("spool replay crossed fixed high-water or cursor")
                                frame=RecoveryFrame(EngineCursor(record.cursor.producer,record.cursor.incarnation,
                                                                 record.cursor.sequence),record.payload)
                                digest=frame.digest
                                self._delivery_inflight_records=1
                                self._delivery_stage="replay_send"
                                if not transport.try_send(frame,replay=True):
                                    raise RuntimeError("durable replay send failed")
                                self._delivery_stage="replay_applied_ack"
                                receipt=await asyncio.wait_for(transport.receive_ack(),self.delivery_budgets.io_timeout_seconds)
                                self._delivery_inflight_records=0
                                if receipt.phase!="APPLIED" or receipt.cursor!=frame.cursor or receipt.digest!=frame.digest:
                                    raise RuntimeError("durable replay acknowledgement mismatch")
                                checkpoint=frame.cursor
                                self._last_applied=checkpoint.sequence
                                self._track_delivery_lag()
                        high_water=checkpoint
                        committed=False
                        while not self._closing and not committed:
                            self._delivery_stage="commit"
                            committed=await asyncio.wait_for(
                                transport.commit_recovery(high_water,digest),self.delivery_budgets.io_timeout_seconds)
                            if not committed:await asyncio.sleep(self.delivery_budgets.poll_seconds)
                        if not committed:continue
                        latest=await loop.run_in_executor(self._executor,self._durable_cursor)
                        self._delivery_catchup_overshoot_records=max(
                            self._delivery_catchup_overshoot_records,max(0,latest.sequence-high_water.sequence))
                    await self._deliver_live(transport,loop)
                except asyncio.CancelledError:
                    raise
                except DeliveryIntegrityError as error:
                    self._delivery_state="INVALID"
                    self._last_delivery_error_type=type(error).__name__
                    self._invalidate(error,stage="delivery",reason="spool_replay_integrity_failed")
                    return
                except Exception as error:
                    self._delivery_state="DISCONNECTED"
                    self._last_delivery_error_type=type(error).__name__
                    if transport is not None:
                        try: await asyncio.wait_for(transport.close(),self.delivery_budgets.io_timeout_seconds)
                        except Exception: pass
                    self._delivery_inflight_records=0
                    await asyncio.sleep(delay)
                    delay=min(self.delivery_budgets.reconnect_backoff_cap_seconds,delay*2)
        except asyncio.CancelledError:
            raise

    async def publish_market_event(self, event):
        await self.publish_market_packet(prepare_market_event(event))

    _market_packet_protocol=1
    _packet_public_method=publish_market_event

    async def publish_market_packet(self, packet: MarketEventPacket):
        self._check()
        if not self._ready:
            raise RuntimeError("connect local ingress before starting the collector")
        publication=resolve_market_publication(packet,self.env)
        if publication is None:
            self._filtered+=1
            return
        self.accept_payload(publication.packet.payload)

    def accept_payload(self, payload: bytes):
        self._check()
        if not self._ready:
            raise RuntimeError("local ingress is not ready")
        size=len(payload) if type(payload) is bytes else 0
        if (type(payload) is not bytes or not payload or size>self.budgets.max_frame_bytes
                or len(self._owned)>=self.budgets.max_records or self._bytes+size>self.budgets.max_bytes
                or self._queue.full()):
            error=RuntimeError("local ingress ownership budget exhausted or invalid frame")
            self._invalidate(error,stage="admission",reason="invalid_frame_or_ownership_budget")
            raise error
        item=_Admission(self._accepted+1,payload,size,time.monotonic_ns())
        # No suspension between checks and ownership transfer.
        self._queue.put_nowait(item)
        self._owned[item.identity]=item;self._bytes+=size;self._accepted+=1
        self._settled.clear()

    def _persist(self, batch):
        from storage.market_spool import SpoolRotateRequired
        receipts=[]
        for item in batch:
            try:
                receipt=self._spool.append(item.payload)
            except SpoolRotateRequired:
                self._spool.seal()
                receipt=self._spool.append(item.payload)
            receipts.append(receipt)
        floor=self._spool.flush()
        if any(receipt.cursor.sequence>floor.sequence for receipt in receipts):
            raise RuntimeError("spool flush did not establish contiguous durability")
        return receipts

    async def _writer(self):
        while True:
            first=await self._queue.get()
            batch=[first]
            stage,reason,cursor="persist","persistence_failed",None
            try:
                # One deadline and one bounded gather task per group. The
                # child records each dequeue in batch before suspending again,
                # so timeout/cancellation cannot strand an owned queue item.
                loop=asyncio.get_running_loop()
                deadline=loop.time()+self.budgets.group_sync_ms/1000

                async def gather_group():
                    batch_bytes=first.size
                    while (len(batch)<self.budgets.batch_records
                           and batch_bytes<self.budgets.max_bytes and loop.time()<deadline):
                        item=await self._queue.get()
                        batch.append(item);batch_bytes+=item.size

                try:
                    await asyncio.wait_for(gather_group(),timeout=max(0,deadline-loop.time()))
                except asyncio.TimeoutError:
                    pass
                if self._error is not None:
                    raise RuntimeError("local ingress invalid before persistence") from self._error
                receipts=await asyncio.get_running_loop().run_in_executor(self._executor,self._persist,batch)
                self._appended+=len(receipts);self._durable+=len(receipts)
                if len(receipts)!=len(batch):
                    raise RuntimeError("spool receipt count mismatch")
                durable_ns=time.monotonic_ns()
                # Flush covers the entire returned batch even if delivery of
                # its first frame fails. Keep that durable watermark visible.
                for item,receipt in zip(batch,receipts):
                    if (receipt.cursor.producer,receipt.cursor.incarnation)==(self.producer,self.incarnation):
                        self._last_durable=max(self._last_durable,receipt.cursor.sequence)
                        self._last_durable_latency_ns=max(0,durable_ns-item.accepted_ns)
                        self._max_durable_latency_ns=max(self._max_durable_latency_ns,self._last_durable_latency_ns)
                if self.delivery_budgets is not None:
                    for receipt in receipts:
                        if ((receipt.cursor.producer,receipt.cursor.incarnation)!=(self.producer,self.incarnation)
                                or receipt.cursor.sequence>self._last_durable):
                            raise RuntimeError("spool receipt identity or durability mismatch")
                    for item in batch:self._release(item,failed=False)
                    self._track_delivery_lag(min((item.accepted_ns for item in batch),default=None))
                    continue
                for item,receipt in zip(batch,receipts):
                    stage,reason,cursor="persist","receipt_identity_mismatch",None
                    receipt_cursor=EngineCursor(receipt.cursor.producer,receipt.cursor.incarnation,receipt.cursor.sequence)
                    if (receipt_cursor.producer,receipt_cursor.incarnation)!=(self.producer,self.incarnation):
                        raise RuntimeError("spool receipt identity mismatch")
                    cursor=receipt_cursor
                    item.cursor=cursor;self._outstanding[cursor.sequence]=item
                    stage,reason="transport","durable_send_failed"
                    if not self.transport.try_send(RecoveryFrame(cursor,item.payload)):
                        raise RuntimeError("local transport rejected durable ingress; replay required")
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                self._invalidate(error,stage=stage,reason=reason,cursor=cursor)
                return
            finally:
                for item in batch:self._queue.task_done()

    async def _acks(self):
        reason,cursor="ack_receive_failed",None
        try:
            while True:
                reason,cursor="ack_receive_failed",None
                receipt=await self.transport.receive_ack()
                if receipt.phase in {"GAP","REJECTED"}:
                    reason="engine_rejected_input"
                    if (receipt.cursor.producer,receipt.cursor.incarnation)==(self.producer,self.incarnation):
                        cursor=receipt.cursor
                    raise RuntimeError("local engine rejected or invalidated input")
                if receipt.phase != "APPLIED":continue
                if (receipt.cursor.producer,receipt.cursor.incarnation)!=(self.producer,self.incarnation):
                    reason="ack_identity_mismatch"
                    raise RuntimeError("local engine ACK identity mismatch")
                cursor=receipt.cursor
                item=self._outstanding.get(cursor.sequence)
                if item is None:
                    reason="ack_unknown_or_duplicate"
                    raise RuntimeError("unknown or duplicate local engine ACK")
                if receipt.digest != hashlib.sha256(item.payload).hexdigest() or cursor.sequence!=self._last_applied+1:
                    reason="ack_content_or_order_mismatch"
                    raise RuntimeError("local engine ACK content/order mismatch")
                self._last_applied=cursor.sequence;self._applied+=1
                self._last_applied_latency_ns=max(0,time.monotonic_ns()-item.accepted_ns)
                self._max_applied_latency_ns=max(self._max_applied_latency_ns,self._last_applied_latency_ns)
                self._outstanding.pop(cursor.sequence)
                self._release(item,failed=False)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._invalidate(error,stage="ack",reason=reason,cursor=cursor)

    def _release(self,item,*,failed):
        if item.done:return
        item.done=True;self._owned.pop(item.identity,None);self._bytes-=item.size
        if failed:self._failed+=1
        if not self._owned:self._settled.set()

    @property
    def stats(self):
        return {"accepted":self._accepted,"appended":self._appended,"durable":self._durable,
            "applied":self._applied,"failed":self._failed,"pending":len(self._owned),
            "queued_bytes":self._bytes,"filtered":self._filtered,"invalid":self._error is not None,
            "last_applied_sequence":self._last_applied,
            "last_durable_sequence":self._last_durable,
            "durable_applied_lag_records":max(0,self._last_durable-self._last_applied),
            "delivery_catchup_overshoot_records":self._delivery_catchup_overshoot_records,
            "delivery_state":self._delivery_state,
            "delivery_pending_recovery":self._delivery_state=="RECOVERING",
            "delivery_backlog_age_ns":(max(0,time.monotonic_ns()-self._delivery_backlog_started_ns)
                if self._delivery_backlog_started_ns is not None else 0),
            "delivery_inflight_records":self._delivery_inflight_records,
            "delivery_stage":self._delivery_stage,"last_delivery_error_type":self._last_delivery_error_type,
            "oldest_pending_age_ns":self._oldest_pending_age_ns(time.monotonic_ns()),
            "last_durable_latency_ns":self._last_durable_latency_ns,
            "max_durable_latency_ns":self._max_durable_latency_ns,
            "last_applied_latency_ns":self._last_applied_latency_ns,
            "max_applied_latency_ns":self._max_applied_latency_ns,
            "fault_stage":self._fault_stage,"fault_reason":self._fault_reason,
            "fault_cursor_sequence":self._fault_cursor_sequence,
            "fault_oldest_pending_age_ns":self._fault_oldest_pending_age_ns,
            "cleanup_pending":self._cleanup_future is not None and not self._cleanup_future.done(),
            "cleanup_retained_bytes":self._cleanup_retained_bytes if self._cleanup_future is not None and not self._cleanup_future.done() else 0,
            "cleanup_error":self._cleanup_error,
            "transport_cleanup_pending":self._transport_close_task is not None and not self._transport_close_task.done()}

    async def close(self,timeout_seconds=5):
        if isinstance(timeout_seconds,bool) or not isinstance(timeout_seconds,(int,float)) or not math.isfinite(timeout_seconds) or timeout_seconds<=0:
            raise ValueError("close timeout must be positive and finite")
        if self._close_task is None:
            self._closing=True
            self._ready=False
            if self.delivery_budgets is not None:self._delivery_state="CLOSED"
            self._close_task=asyncio.create_task(self._close(timeout_seconds))
        await asyncio.shield(self._close_task)

    @staticmethod
    def _observe_task(task):
        if not task.cancelled():task.exception()

    async def _until(self, future, deadline):
        remaining=deadline-asyncio.get_running_loop().time()
        if future.done():
            future.result()
            return True
        if remaining<=0:return False
        done,_=await asyncio.wait((future,),timeout=remaining)
        if not done:return False
        future.result()
        return True

    def _cleanup_writer(self):
        if self._spool is not None:
            self._spool.close()

    def _cleanup_finished(self, future):
        try:
            error=future.exception()
            if error is not None:self._cleanup_error=type(error).__name__
        except BaseException as error:
            self._cleanup_error=type(error).__name__

    async def _close(self,timeout):
        deadline=asyncio.get_running_loop().time()+timeout
        drained=asyncio.gather(self._queue.join(),self._settled.wait())
        failed=asyncio.create_task(self._failure.wait())
        try:
            if self._startup_task is not None and not self._startup_task.done():
                self._startup_task.cancel()
            if self._workers:
                done,_=await asyncio.wait((drained,failed),timeout=max(0,deadline-asyncio.get_running_loop().time()),return_when=asyncio.FIRST_COMPLETED)
                if not done:self._invalidate(RuntimeError("local ingress drain timed out"),stage="shutdown",reason="drain_timeout")
        finally:
            drained.cancel();failed.cancel()
            for worker in self._workers:worker.cancel()
            tasks=[drained,failed,*self._workers]
            if self._startup_task is not None:tasks.append(self._startup_task)
            stopped=asyncio.gather(*tasks,return_exceptions=True)
            stopped.add_done_callback(self._observe_task)
            self._workers=[]
            self._cleanup_retained_bytes=self._bytes
            while not self._queue.empty():
                self._queue.get_nowait();self._queue.task_done()
            for item in tuple(self._owned.values()):self._release(item,failed=True)
            self._outstanding.clear()
            # Queue cleanup now, before any further await. A still-running
            # factory/fsync retains its thread and lock until this executes.
            if self._executor is not None:
                self._cleanup_future=self._executor.submit(self._cleanup_writer)
                self._cleanup_future.add_done_callback(self._cleanup_finished)
                self._executor.shutdown(wait=False,cancel_futures=False)
                self._executor=None
            if self.transport is not None:
                self._transport_close_task=asyncio.create_task(self.transport.close())
                self._transport_close_task.add_done_callback(self._observe_task)
            cleanup=asyncio.wrap_future(self._cleanup_future) if self._cleanup_future is not None else None
            if cleanup is not None:cleanup.add_done_callback(self._observe_task)
            for future in (stopped,self._transport_close_task,cleanup):
                if future is None:continue
                try:
                    if not await self._until(future,deadline):
                        self._invalidate(RuntimeError("local ingress cleanup deadline exceeded; cleanup remains tracked"),stage="shutdown",reason="cleanup_timeout")
                except BaseException as error:
                    self._invalidate(error,stage="shutdown",reason="cleanup_failed")
            # A deadline ends this await, not ownership of late cleanup. In
            # particular, do not cancel a close task before it gets its first
            # turn to release sockets. The tracked task may finish later.
        if self._error is not None:raise RuntimeError("local ingress closed with unconfirmed work") from self._error
