"""Read-only operational facts with Telegram-owned durable ACK checkpoints.

Market ticks/books are never scanned. HTTP and trading journals are outside
this adapter; an ACK means its caller already committed the local outbox.
"""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import time

from .bus import BusMessage
from .process_lease import ProcessLease


@dataclass(frozen=True)
class FactsEventBudgets:
    """Explicit bounds; max_checkpoint_bytes limits main SQLite pages.

    checkpoint_storage_reserve derives the additional one-transaction WAL,
    bounded SHM and filesystem margin; no retention/deletion is implied.
    """
    max_records: int
    max_bytes: int
    max_frame_bytes: int
    max_consumers: int
    poll_seconds: float
    query_timeout_seconds: float
    max_nak_seconds: float
    max_checkpoint_bytes: int

    def __post_init__(self):
        for value in (self.max_records,self.max_bytes,self.max_frame_bytes,self.max_consumers,self.max_checkpoint_bytes):
            if type(value) is not int or value<=0:
                raise ValueError('facts budgets require positive explicit integers')
        if self.max_frame_bytes>self.max_bytes or self.max_checkpoint_bytes<131072:
            raise ValueError('facts frame/checkpoint budgets are inconsistent')
        for value in (self.poll_seconds,self.query_timeout_seconds,self.max_nak_seconds):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise ValueError('facts time budgets must be positive and finite')


def checkpoint_storage_reserve(maximum: int, page_size: int = 512) -> int:
    """Main + one non-spilling transaction's WAL/SHM + 4096-byte margin.

    512-byte pages conservatively reserve space before an existing database
    is opened. The owning session recomputes bounds for its actual page size.
    """
    if (type(maximum) is not int or maximum < 131072 or type(page_size) is not int
            or page_size < 512 or page_size > 65536 or page_size & (page_size-1)):
        raise ValueError('invalid facts checkpoint storage bounds')
    pages=maximum//page_size
    return maximum+32+pages*(page_size+24)+32768*(2+(pages+3999)//4000)+4096


def _subject(subject):
    if (not isinstance(subject,str) or len(subject.encode())>256
            or not any(subject.startswith(prefix) for prefix in ('system.','signal.','order.','md.data_gap.','crypto.data_gap.'))
            or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:>)?',subject)
            or ('>' in subject and not subject.endswith('.>'))):
        raise ValueError('only operational facts subjects are allowed')
    return subject[:-1] if subject.endswith('>') else None


class _FactsSession:
    def __init__(self, facts_path, checkpoint_path, budgets):
        self.facts_path,self.checkpoint_path,self.budgets=facts_path,checkpoint_path,budgets
        self.source=self.checkpoints=self.lease=None
        self._page_size=512  # Conservative file checks before SQLite opens.
        try:
            if not facts_path.is_file():raise RuntimeError('configured local facts database is missing')
            self.source=sqlite3.connect(facts_path.as_uri()+'?mode=ro',uri=True,timeout=budgets.query_timeout_seconds)
            self.source.row_factory=sqlite3.Row
            self.source.execute('PRAGMA query_only=ON')
            if self.source.execute('PRAGMA user_version').fetchone()[0]!=1:
                raise RuntimeError('unsupported local facts schema')
            generation=self.source.execute("SELECT value FROM facts_metadata WHERE key='generation'").fetchone()
            if generation is None or re.fullmatch(r'[0-9a-f]{32}',generation[0]) is None:
                raise RuntimeError('local facts generation is unverified')
            self.generation=generation[0]
            stat=facts_path.stat();self.file_identity=(stat.st_dev,stat.st_ino)
            checkpoint_path.parent.mkdir(parents=True,exist_ok=True)
            self.lease=ProcessLease(checkpoint_path.with_suffix(checkpoint_path.suffix+'.lock'))
            self.lease.acquire()
            self._check_checkpoint_files()
            self._reserve_checkpoint_space()
            self.checkpoints=sqlite3.connect(checkpoint_path,timeout=budgets.query_timeout_seconds,isolation_level=None)
            self.checkpoints.row_factory=sqlite3.Row
            self.checkpoints.execute('PRAGMA journal_mode=WAL')
            self.checkpoints.execute('PRAGMA synchronous=FULL')
            self.checkpoints.execute('PRAGMA wal_autocheckpoint=0')
            self.checkpoints.execute('PRAGMA cache_spill=OFF')
            page_size=self.checkpoints.execute('PRAGMA page_size').fetchone()[0]
            self._page_size=page_size
            maximum=budgets.max_checkpoint_bytes//page_size
            self._checkpoint_gate()
            actual=self.checkpoints.execute('PRAGMA max_page_count='+str(maximum)).fetchone()[0]
            if actual>maximum or self.checkpoints.execute('PRAGMA page_count').fetchone()[0]>maximum:
                raise RuntimeError('existing facts checkpoint exceeds configured page capacity')
            version=self.checkpoints.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1):raise RuntimeError('unsupported facts checkpoint schema')
            self._checkpoint_gate()
            self.checkpoints.execute('BEGIN IMMEDIATE')
            try:
                self.checkpoints.execute('CREATE TABLE IF NOT EXISTS consumer(durable TEXT PRIMARY KEY,subject TEXT NOT NULL,generation TEXT NOT NULL,cursor INTEGER NOT NULL CHECK(cursor>=0))')
                if version==0:self.checkpoints.execute('PRAGMA user_version=1')
                self.checkpoints.execute('COMMIT')
            except BaseException:
                if self.checkpoints.in_transaction:self.checkpoints.execute('ROLLBACK')
                raise
            self._checkpoint_gate()
            if self.checkpoints.execute('SELECT COUNT(*) FROM consumer').fetchone()[0]>budgets.max_consumers:
                raise RuntimeError('existing facts consumers exceed configured capacity')
            if self.checkpoints.execute('PRAGMA synchronous').fetchone()[0]!=2:
                raise RuntimeError('facts checkpoint FULL durability unavailable')
            if str(self.checkpoints.execute('PRAGMA journal_mode').fetchone()[0]).lower()!='wal':
                raise RuntimeError('facts checkpoint WAL durability unavailable')
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self.source is not None:self.source.close();self.source=None
        finally:
            try:
                if self.checkpoints is not None:self.checkpoints.close();self.checkpoints=None
            finally:
                if self.lease is not None:self.lease.close();self.lease=None

    def _checkpoint_size(self,suffix=''):
        path=self.checkpoint_path.with_name(self.checkpoint_path.name+suffix)
        return path.stat().st_size if path.exists() else 0

    def _check_checkpoint_files(self):
        maximum=self.budgets.max_checkpoint_bytes
        pages=maximum//self._page_size
        limits=(('',maximum),('-wal',32+pages*(self._page_size+24)),
                ('-shm',32768*(2+(pages+3999)//4000)))
        for suffix,limit in limits:
            if self._checkpoint_size(suffix)>limit:
                raise RuntimeError('facts checkpoint '+(suffix or 'main')+' exceeds configured file capacity')

    def _reserve_checkpoint_space(self):
        # WAL is empty at mutation gates. Reserve remaining main growth and
        # the whole next WAL/SHM even if an existing SHM already occupies space.
        required=checkpoint_storage_reserve(self.budgets.max_checkpoint_bytes,self._page_size)-self._checkpoint_size()
        if shutil.disk_usage(self.checkpoint_path.parent).free<required:
            raise RuntimeError('insufficient free space for bounded facts checkpoint transaction')

    def _checkpoint_gate(self):
        self._check_checkpoint_files()
        result=self.checkpoints.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if int(result[0])!=0 or self._checkpoint_size('-wal')!=0:
            raise RuntimeError('facts checkpoint WAL pinned; next transaction is forbidden')
        self._check_checkpoint_files()
        self._reserve_checkpoint_space()

    def _consumer(self,durable,subject):
        row=self.checkpoints.execute('SELECT * FROM consumer WHERE durable=?',(durable,)).fetchone()
        if row is None:
            if self.checkpoints.execute('SELECT COUNT(*) FROM consumer').fetchone()[0]>=self.budgets.max_consumers:
                raise RuntimeError('facts consumer capacity exceeded')
            self._checkpoint_gate()
            self.checkpoints.execute('BEGIN IMMEDIATE')
            try:
                self.checkpoints.execute('INSERT INTO consumer VALUES(?,?,?,0)',(durable,subject,self.generation))
                self.checkpoints.execute('COMMIT')
            except BaseException:
                if self.checkpoints.in_transaction:self.checkpoints.execute('ROLLBACK')
                raise
            self._checkpoint_gate()
            return 0
        if row['subject']!=subject or row['generation']!=self.generation:
            raise RuntimeError('facts checkpoint subject/generation changed; explicit recovery required')
        return row['cursor']

    def _advance(self,durable,subject,cursor):
        previous=self._consumer(durable,subject)
        if cursor<=previous:return
        self._checkpoint_gate()
        self.checkpoints.execute('BEGIN IMMEDIATE')
        try:
            self.checkpoints.execute('UPDATE consumer SET cursor=MAX(cursor,?) WHERE durable=?',(cursor,durable))
            self.checkpoints.execute('COMMIT')
        except BaseException:
            if self.checkpoints.in_transaction:self.checkpoints.execute('ROLLBACK')
            raise
        # A post-COMMIT pin/fault returns an unknown ACK result. The durable
        # cursor is retained and replay uses the same committed outbox ID.
        self._checkpoint_gate()

    def fetch(self,durable,subject,batch):
        stat=self.facts_path.stat()
        if (stat.st_dev,stat.st_ino)!=self.file_identity:
            raise RuntimeError('local facts file identity changed')
        cursor=self._consumer(durable,subject)
        deadline=time.monotonic()+self.budgets.query_timeout_seconds
        self.source.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        self.source.execute('BEGIN')
        try:
            generation=self.source.execute("SELECT value FROM facts_metadata WHERE key='generation'").fetchone()
            if generation is None or generation[0]!=self.generation:
                raise RuntimeError('local facts generation changed')
            high_water=self.source.execute('SELECT COALESCE(MAX(fact_id),0) FROM local_fact').fetchone()[0]
            if high_water<cursor:raise RuntimeError('local facts cursor regressed')
            prefix=_subject(subject)
            condition='subject>=? AND subject<?' if prefix is not None else 'subject=?'
            params=(prefix,prefix+'\U0010ffff') if prefix is not None else (subject,)
            metadata=self.source.execute('SELECT fact_id,length(raw_payload) raw_size,length(decoded_payload) decoded_size FROM local_fact WHERE fact_id>? AND fact_id<=? AND '+condition+' ORDER BY fact_id LIMIT ?',
                (cursor,high_water,*params,batch)).fetchall()
            rows=[];retained=0
            for entry in metadata:
                size=entry['raw_size']+(entry['decoded_size'] or 0)
                if (not entry['raw_size'] or entry['raw_size']>self.budgets.max_frame_bytes
                        or (entry['decoded_size'] or 0)>self.budgets.max_frame_bytes or size>self.budgets.max_bytes):
                    raise RuntimeError('local fact exceeds configured frame/byte capacity')
                if retained+size>self.budgets.max_bytes:break
                row=dict(self.source.execute('SELECT * FROM local_fact WHERE fact_id=?',(entry['fact_id'],)).fetchone())
                retained+=size;rows.append(row)
            self.source.execute('COMMIT')
        except BaseException:
            self.source.execute('ROLLBACK')
            raise
        finally:
            self.source.set_progress_handler(None,0)
        if not rows:
            self._advance(durable,subject,high_water)
        return rows

    def ack(self,durable,subject,fact_id):
        self._advance(durable,subject,fact_id)


def _body(row):
    raw=row['raw_payload']
    if type(raw) is not bytes or hashlib.sha256(raw).hexdigest()!=row['payload_sha256']:
        raise RuntimeError('local fact checksum mismatch')
    body=row['decoded_payload']
    if body is not None:
        envelope=json.loads(raw)
        if (not isinstance(envelope,dict) or type(envelope.get('version')) is not int or envelope['version']!=1
                or envelope.get('subject')!=row['subject'] or envelope.get('msg_id')!=row['msg_id']
                or base64.b64decode(envelope['payload_base64'],validate=True)!=body):
            raise RuntimeError('local fact observation body does not match its envelope')
    else:body=raw
    document=json.loads(body)
    if not isinstance(document,dict):raise RuntimeError('local fact body must be a JSON object')
    return document


class _Delivery:
    def __init__(self,bus,epoch,durable,subject,fact_id):
        self.bus,self.epoch,self.durable,self.subject,self.fact_id=bus,epoch,durable,subject,fact_id
        self.done=asyncio.Event();self.outcome=None;self.delay=0

    async def ack(self):
        if self.outcome=='ACK':return
        if self.outcome is not None:raise RuntimeError('local fact delivery already settled')
        await self.bus._call('ack',self.durable,self.subject,self.fact_id,epoch=self.epoch)
        self.outcome='ACK';self.done.set()

    async def nak(self,delay=5):
        if self.outcome is not None:return
        if isinstance(delay,bool) or not isinstance(delay,(int,float)) or not math.isfinite(delay) or delay<0:
            raise ValueError('invalid local fact retry delay')
        self.delay=min(delay,self.bus.budgets.max_nak_seconds)
        self.outcome='NAK';self.done.set()


class LocalFactsEventBus:
    def __init__(self, facts_path:Path, checkpoint_path:Path, *, budgets:FactsEventBudgets):
        facts_path,checkpoint_path=Path(facts_path),Path(checkpoint_path)
        if not facts_path.is_absolute() or not checkpoint_path.is_absolute() or facts_path.resolve()==checkpoint_path.resolve():
            raise ValueError('facts and checkpoint require distinct explicit absolute paths')
        self.facts_path,self.checkpoint_path,self.budgets=facts_path,checkpoint_path,budgets
        self._executor=self._session=self._startup=self._close_task=None
        self._cleanup_future=None;self._cleanup_error=None
        self._epoch=0;self._closing=False;self._ready=False
        self._subscribers={};self._deliveries=set()
        self._requests={};self._request_keys={}

    @staticmethod
    def _observe(future):
        if not future.cancelled():future.exception()

    def _open(self):
        self._session=_FactsSession(self.facts_path,self.checkpoint_path,self.budgets)

    async def connect(self, *, initialize=False):
        del initialize
        if self._ready:return
        if self._closing:
            if self._close_task is None or not self._close_task.done() or (self._cleanup_future is not None and not self._cleanup_future.done()):
                raise RuntimeError('local facts cleanup is still pending')
            if self._cleanup_error is not None:
                raise RuntimeError('previous local facts cleanup failed')
            self._closing=False;self._startup=self._close_task=None
        if self._startup is None:
            self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='telegram-facts')
            self._startup=asyncio.create_task(self._connect())
        try:
            await asyncio.shield(self._startup)
        except BaseException:
            try:await self.close()
            except BaseException:pass
            raise

    async def _connect(self):
        future=asyncio.wrap_future(self._executor.submit(self._open));future.add_done_callback(self._observe)
        await asyncio.shield(future)
        if self._closing:raise RuntimeError('local facts closed during startup')
        self._epoch+=1
        # Old generators may still be owned by their callers. Epoch/token
        # checks keep their eventual finalizers from touching new consumers.
        self._subscribers={};self._deliveries=set()
        self._ready=True

    def _request_finished(self,token,key,future):
        if self._requests.get(token) is future:
            self._requests.pop(token,None)
        if self._request_keys.get(key) is token:
            self._request_keys.pop(key,None)

    async def _call(self,method,*args,epoch):
        if not self._ready or self._closing or epoch!=self._epoch:
            raise RuntimeError('local facts bus is closed or delivery epoch changed')
        # A cancelled waiter does not cancel SQLite or relinquish its slot.
        # Retain ownership until the actual worker future completes, including
        # reconnect attempts with the same durable after anext timeout.
        for token,future in tuple(self._requests.items()):
            if future.done():
                for key,value in tuple(self._request_keys.items()):
                    if value is token:self._request_finished(token,key,future)
        key=(epoch,args[0])
        if key in self._request_keys or len(self._requests)>=self.budgets.max_consumers:
            raise RuntimeError('local facts request capacity occupied until worker completion')
        token=object()
        raw=self._executor.submit(getattr(self._session,method),*args)
        self._requests[token]=raw;self._request_keys[key]=token
        loop=asyncio.get_running_loop()
        raw.add_done_callback(lambda done:loop.call_soon_threadsafe(self._request_finished,token,key,done))
        future=asyncio.wrap_future(raw)
        future.add_done_callback(self._observe)
        result=await asyncio.shield(future)
        if not self._ready or self._closing or epoch!=self._epoch:
            raise RuntimeError('local facts request completed in a stale delivery epoch')
        return result

    async def subscribe(self,subject,durable,batch=100):
        _subject(subject)
        if not isinstance(durable,str) or not durable or len(durable.encode())>128 or type(batch) is not int or batch<=0:
            raise ValueError('invalid local facts durable/batch')
        if not self._ready or self._closing:raise RuntimeError('connect local facts bus first')
        if durable in self._subscribers or len(self._subscribers)>=self.budgets.max_consumers:
            raise RuntimeError('local facts consumer already active or capacity exceeded')
        epoch=self._epoch;subscription=object()
        self._subscribers[durable]=(epoch,subject,subscription)
        try:
            while self._ready and not self._closing and epoch==self._epoch:
                rows=await self._call('fetch',durable,subject,min(batch,self.budgets.max_records),epoch=epoch)
                if not rows:
                    await asyncio.sleep(self.budgets.poll_seconds)
                    continue
                for row in rows:
                    if self._closing or epoch!=self._epoch:return
                    delivery=_Delivery(self,epoch,durable,subject,row['fact_id'])
                    self._deliveries.add(delivery)
                    try:
                        body=_body(row)
                        identity=str(row['msg_id'] or body.get('event_id') or body.get('signal_id') or body.get('intent_id') or '')
                        yield BusMessage(body,row['subject'],identity,stream_name='LOCAL_FACTS',
                            stream_generation=self._session.generation,stream_sequence=row['fact_id'],_raw=delivery)
                        await delivery.done.wait()
                        if delivery.outcome=='NAK':
                            await asyncio.sleep(delivery.delay)
                            break
                        if delivery.outcome!='ACK':return
                    finally:
                        self._deliveries.discard(delivery)
        finally:
            if self._subscribers.get(durable)==(epoch,subject,subscription):
                self._subscribers.pop(durable,None)

    def _close_session(self):
        if self._session is not None:self._session.close();self._session=None

    def _cleanup_finished(self,future):
        try:
            error=future.exception()
            if error is not None:self._cleanup_error=type(error).__name__
        except BaseException as error:self._cleanup_error=type(error).__name__

    async def close(self,timeout_seconds=5):
        if isinstance(timeout_seconds,bool) or not isinstance(timeout_seconds,(int,float)) or not math.isfinite(timeout_seconds) or timeout_seconds<=0:
            raise ValueError('local facts close deadline must be positive and finite')
        if self._close_task is None:
            self._closing=True;self._ready=False
            for delivery in tuple(self._deliveries):delivery.done.set()
            self._deliveries.clear()
            self._close_task=asyncio.create_task(self._close(timeout_seconds))
        await asyncio.shield(self._close_task)

    async def _close(self,timeout):
        deadline=asyncio.get_running_loop().time()+timeout
        if self._startup is not None and not self._startup.done():self._startup.cancel()
        if self._executor is not None:
            self._cleanup_future=self._executor.submit(self._close_session)
            self._cleanup_future.add_done_callback(self._cleanup_finished)
            self._executor.shutdown(wait=False,cancel_futures=False);self._executor=None
            cleanup=asyncio.wrap_future(self._cleanup_future);cleanup.add_done_callback(self._observe)
            done,_=await asyncio.wait((cleanup,),timeout=max(0,deadline-asyncio.get_running_loop().time()))
            if not done:raise RuntimeError('local facts cleanup remains pending')
            try:cleanup.result()
            except BaseException as exc:
                self._cleanup_error=type(exc).__name__
                raise

    @property
    def stats(self):
        return {'active_consumers':len(self._subscribers),'inflight':len(self._deliveries),
            'pending_requests':len(self._requests),'max_pending_requests':self.budgets.max_consumers,
            'cleanup_pending':self._cleanup_future is not None and not self._cleanup_future.done(),
            'cleanup_error':self._cleanup_error,'ready':self._ready}
