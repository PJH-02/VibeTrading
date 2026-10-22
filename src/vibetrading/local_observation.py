"""Bounded local observation log; trading journals remain authoritative.

Publish awaits local byte ownership only. ``wait_durable`` is the separate
producer-spool group-fsync receipt; it is never NATS/remote durability or a fill.
Startup/close ownership reuses the single writer lifecycle in local_pipeline.
"""
from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import math
import re
import time
import uuid

from .bus import dumps, subject_for_signal
from .feed_recovery import EngineCursor
from .local_pipeline import PipelineBudgets, SpoolingMarketPublisher


ObservationBudgets = PipelineBudgets
_SUBJECT = re.compile(r'(signal|order|system)\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\Z')


def encode_observation(subject: str, payload: bytes, msg_id: str | None = '', *, max_payload_bytes:int) -> bytes:
    """observation_v1: exact original body, subject and original message ID."""
    if (not isinstance(subject,str) or len(subject.encode('utf-8'))>256 or not _SUBJECT.fullmatch(subject)
            or type(max_payload_bytes) is not int or max_payload_bytes<=0
            or type(payload) is not bytes or not payload or len(payload)>max_payload_bytes
            or (msg_id is not None and (not isinstance(msg_id,str)
                or len(msg_id.encode('utf-8'))>256 or any(ord(char)<32 or ord(char)==127 for char in msg_id)))):
        raise ValueError('invalid observation subject, owned payload or message id')
    return json.dumps({'version':1,'subject':subject,'msg_id':msg_id,
        'payload_base64':base64.b64encode(payload).decode('ascii')},ensure_ascii=False,separators=(',',':')).encode('utf-8')


@dataclass(frozen=True, slots=True)
class ObservationAdmission:
    publisher_id: str
    cursor: EngineCursor
    digest: str
    status: str = 'ACCEPTED'


@dataclass(frozen=True, slots=True)
class ObservationDurableReceipt:
    cursor: EngineCursor
    digest: str
    status: str = 'DURABLE'


class _NoTransport:
    async def close(self):
        pass


class LocalObservationBus(SpoolingMarketPublisher):
    """One finite admission queue and one spool-owning thread, no NATS client.

    Existing sealed records may be appended after restart. A prior uncertain
    tail is rejected by MarketSpool and is never silently truncated here.
    Receipt fingerprints retain at most max_records entries; an older receipt
    fails explicitly rather than claiming content that is no longer retained.
    """

    def __init__(self, *, producer: str, incarnation: str, spool_factory,
                 budgets: ObservationBudgets, on_invalid):
        super().__init__(producer=producer,incarnation=incarnation,spool_factory=spool_factory,
            transport=_NoTransport(),budgets=budgets,env={},on_invalid=on_invalid)
        self._publisher_id=uuid.uuid4().hex
        self._baseline_sequence=0
        self._durable_cursor=EngineCursor(producer,incarnation,0)
        self._receipt_tail=OrderedDict()
        self._durability_changed=asyncio.Event()

    def _create_spool(self):
        self._spool=self.spool_factory()
        return self._spool.appended_cursor,self._spool.durable_cursor

    async def _connect(self):
        self._check()
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='observation-spool')
        self._factory_future=self._executor.submit(self._create_spool)
        factory=asyncio.wrap_future(self._factory_future)
        factory.add_done_callback(self._observe_task)
        appended,durable=await asyncio.shield(factory)
        self._check()
        if ((appended.producer,appended.incarnation)!=(self.producer,self.incarnation)
                or appended!=durable):
            raise RuntimeError('observation spool identity or durable tail is unverified')
        self._baseline_sequence=durable.sequence
        self._durable_cursor=EngineCursor(durable.producer,durable.incarnation,durable.sequence)
        self._ready=True
        self._workers=[asyncio.create_task(self._writer(),name='local-observation-writer')]

    def _invalidate(self, error, **kwargs):
        super()._invalidate(error, **kwargs)
        self._durability_changed.set()

    def enqueue_observation(self, subject: str, payload: bytes, msg_id: str | None = '') -> ObservationAdmission:
        envelope=encode_observation(subject,payload,msg_id,max_payload_bytes=self.budgets.max_frame_bytes)
        return self._admit_payload(envelope)

    def _admit_payload(self, payload: bytes) -> ObservationAdmission:
        self.accept_payload(payload)
        return ObservationAdmission(self._publisher_id,
            EngineCursor(self.producer,self.incarnation,self._baseline_sequence+self._accepted),
            hashlib.sha256(payload).hexdigest())

    async def publish_signal(self, signal):
        return self.enqueue_observation(subject_for_signal(signal),dumps(signal),signal.signal_id)

    async def publish_order_intent(self, intent):
        identity=str(getattr(intent,'intent_id','') or (intent.get('intent_id','') if isinstance(intent,dict) else ''))
        return self.enqueue_observation('order.intent',dumps(intent),identity)

    async def publish_system(self, kind: str, payload: dict | None = None):
        return self.enqueue_observation('system.'+kind,
            dumps({'event_type':'system','kind':kind,'ts_ns':time.time_ns(),**(payload or {})}),None)

    async def publish_market_event(self, event):
        raise RuntimeError('observation bus does not accept market events')

    async def publish_market_packet(self, packet):
        raise RuntimeError('observation bus does not accept market packets')

    async def _writer(self):
        while True:
            first=await self._queue.get()
            batch=[first]
            try:
                await asyncio.sleep(self.budgets.group_sync_ms/1000)
                while len(batch)<self.budgets.batch_records:
                    try:batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:break
                if self._error is not None:
                    raise RuntimeError('observation ingress invalid before persistence') from self._error
                receipts=await asyncio.get_running_loop().run_in_executor(self._executor,self._persist,batch)
                if len(receipts)!=len(batch):
                    raise RuntimeError('observation spool returned incomplete batch receipts')
                self._appended+=len(receipts)
                for item,receipt in zip(batch,receipts):
                    cursor=EngineCursor(receipt.cursor.producer,receipt.cursor.incarnation,receipt.cursor.sequence)
                    if ((cursor.producer,cursor.incarnation)!=(self.producer,self.incarnation)
                            or cursor.sequence!=self._durable_cursor.sequence+1):
                        raise RuntimeError('observation durable cursor identity/order mismatch')
                    digest=hashlib.sha256(item.payload).hexdigest()
                    self._durable_cursor=cursor
                    self._receipt_tail[cursor.sequence]=digest
                    while len(self._receipt_tail)>self.budgets.max_records:
                        self._receipt_tail.popitem(last=False)
                    self._durable+=1
                    self._release(item,failed=False)
                self._durability_changed.set()
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                self._invalidate(error)
                return
            finally:
                for item in batch:self._queue.task_done()

    async def wait_durable(self, admission: ObservationAdmission, *, timeout_seconds: float = 5) -> ObservationDurableReceipt:
        if (not isinstance(admission,ObservationAdmission) or admission.publisher_id!=self._publisher_id
                or (admission.cursor.producer,admission.cursor.incarnation)!=(self.producer,self.incarnation)):
            raise ValueError('observation admission does not belong to this publisher')
        if (isinstance(timeout_seconds,bool) or not isinstance(timeout_seconds,(int,float))
                or not math.isfinite(timeout_seconds) or timeout_seconds<=0):
            raise ValueError('durable receipt timeout must be positive and finite')
        deadline=asyncio.get_running_loop().time()+timeout_seconds
        while True:
            digest=self._receipt_tail.get(admission.cursor.sequence)
            if digest is not None:
                if digest!=admission.digest:
                    raise ValueError('observation admission content mismatch')
                return ObservationDurableReceipt(admission.cursor,digest)
            if admission.cursor.sequence<=self._durable_cursor.sequence:
                raise ValueError('observation durable receipt fingerprint expired')
            if self._error is not None:
                raise RuntimeError('observation durability is unconfirmed') from self._error
            if self._close_task is not None and self._close_task.done():
                raise RuntimeError('observation publisher closed before durable receipt')
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:
                raise asyncio.TimeoutError('observation durable receipt deadline exceeded')
            self._durability_changed.clear()
            await asyncio.wait_for(self._durability_changed.wait(),timeout=remaining)

    @property
    def durable_cursor(self):
        return self._durable_cursor

    @property
    def stats(self):
        values=super().stats
        values.pop('applied');values.pop('last_applied_sequence')
        return values|{'last_durable_sequence':self._durable_cursor.sequence,'receipt_tail':len(self._receipt_tail)}

    async def close(self, timeout_seconds=5):
        try:
            await super().close(timeout_seconds=timeout_seconds)
        finally:
            self._durability_changed.set()
