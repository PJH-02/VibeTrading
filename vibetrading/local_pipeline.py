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

from .bus import MarketEventPacket, prepare_market_event, resolve_market_publication
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


@dataclass
class _Admission:
    identity: int
    payload: bytes
    size: int
    accepted_ns: int
    cursor: EngineCursor | None = None
    done: bool = False


class SpoolingMarketPublisher:
    def __init__(self, *, producer: str, incarnation: str, spool_factory: Callable,
                 transport, budgets: PipelineBudgets, env: dict[str,str],
                 on_invalid: Callable[[str],None], resume_existing: bool=False) -> None:
        EngineCursor(producer,incarnation,0)
        if inspect.iscoroutinefunction(spool_factory) or inspect.iscoroutinefunction(on_invalid):
            raise TypeError("spool factory and invalidation callback must be synchronous")
        self.producer,self.incarnation=producer,incarnation
        self.spool_factory,self.transport,self.budgets=spool_factory,transport,budgets
        self.env,self.on_invalid=dict(env),on_invalid
        if type(resume_existing) is not bool:raise TypeError("resume_existing must be boolean")
        self.resume_existing=resume_existing
        self._queue=asyncio.Queue(maxsize=budgets.max_records)
        self._owned: dict[int,_Admission]={}
        self._outstanding: dict[int,_Admission]={}
        self._bytes=0
        self._accepted=self._appended=self._durable=self._applied=self._failed=self._filtered=0
        self._last_applied=0
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

    def _invalidate(self, error: BaseException):
        if self._error is not None:
            return
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
            self._startup_task=asyncio.create_task(self._connect(),name="local-ingress-startup")
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
        self._ready=True
        self._workers=[asyncio.create_task(self._writer(),name="local-spool-writer"),
                       asyncio.create_task(self._acks(),name="local-engine-acks")]

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
            self._invalidate(error)
            raise error
        item=_Admission(self._accepted+1,payload,size,time.monotonic_ns())
        # No suspension between checks and ownership transfer.
        self._queue.put_nowait(item)
        self._owned[item.identity]=item;self._bytes+=size;self._accepted+=1
        self._settled.clear()

    def _persist(self, batch):
        from .market_spool import SpoolRotateRequired
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
            try:
                # One group timer per writer, never one task per event.
                await asyncio.sleep(self.budgets.group_sync_ms/1000)
                while len(batch)<self.budgets.batch_records:
                    try:batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:break
                if self._error is not None:
                    raise RuntimeError("local ingress invalid before persistence") from self._error
                receipts=await asyncio.get_running_loop().run_in_executor(self._executor,self._persist,batch)
                self._appended+=len(receipts);self._durable+=len(receipts)
                for item,receipt in zip(batch,receipts):
                    cursor=EngineCursor(receipt.cursor.producer,receipt.cursor.incarnation,receipt.cursor.sequence)
                    if (cursor.producer,cursor.incarnation)!=(self.producer,self.incarnation):
                        raise RuntimeError("spool receipt identity mismatch")
                    item.cursor=cursor;self._outstanding[cursor.sequence]=item
                    if not self.transport.try_send(RecoveryFrame(cursor,item.payload)):
                        raise RuntimeError("local transport rejected durable ingress; replay required")
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                self._invalidate(error)
                return
            finally:
                for item in batch:self._queue.task_done()

    async def _acks(self):
        try:
            while True:
                receipt=await self.transport.receive_ack()
                if receipt.phase in {"GAP","REJECTED"}:
                    raise RuntimeError("local engine rejected or invalidated input")
                if receipt.phase != "APPLIED":continue
                cursor=receipt.cursor
                if (cursor.producer,cursor.incarnation)!=(self.producer,self.incarnation):
                    raise RuntimeError("local engine ACK identity mismatch")
                item=self._outstanding.get(cursor.sequence)
                if item is None:
                    raise RuntimeError("unknown or duplicate local engine ACK")
                if receipt.digest != hashlib.sha256(item.payload).hexdigest() or cursor.sequence!=self._last_applied+1:
                    raise RuntimeError("local engine ACK content/order mismatch")
                self._last_applied=cursor.sequence;self._applied+=1
                self._outstanding.pop(cursor.sequence)
                self._release(item,failed=False)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._invalidate(error)

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
                if not done:self._invalidate(RuntimeError("local ingress drain timed out"))
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
            self._transport_close_task=asyncio.create_task(self.transport.close())
            self._transport_close_task.add_done_callback(self._observe_task)
            cleanup=asyncio.wrap_future(self._cleanup_future) if self._cleanup_future is not None else None
            if cleanup is not None:cleanup.add_done_callback(self._observe_task)
            for future in (stopped,self._transport_close_task,cleanup):
                if future is None:continue
                try:
                    if not await self._until(future,deadline):
                        self._invalidate(RuntimeError("local ingress cleanup deadline exceeded; cleanup remains tracked"))
                except BaseException as error:
                    self._invalidate(error)
            # A deadline ends this await, not ownership of late cleanup. In
            # particular, do not cancel a close task before it gets its first
            # turn to release sockets. The tracked task may finish later.
        if self._error is not None:raise RuntimeError("local ingress closed with unconfirmed work") from self._error
