"""Finite shutdown and ownership receipts, with fake local transport only."""
import asyncio
import hashlib
import threading
from types import SimpleNamespace

import pytest

from vibetrading.feed_recovery import EngineCursor
from vibetrading.local_pipeline import PipelineBudgets, SpoolingMarketPublisher
from vibetrading.local_transport import TransportAck
from vibetrading.market_spool import MarketSpool, read_spool_records


def budgets(**updates):
    values = dict(max_records=4,max_bytes=2048,max_frame_bytes=512,batch_records=4,group_sync_ms=1)
    return PipelineBudgets(**(values|updates))


class Transport:
    def __init__(self, *, auto_ack=True):
        self.acks = asyncio.Queue()
        self.auto_ack = auto_ack
        self.sent = []
        self.sent_event = asyncio.Event()
        self.connected_event = asyncio.Event()
        self.connect_gate = None
        self.close_gate = None
        self.closed = False
        self.resume_calls = 0

    async def connect(self):
        self.connected_event.set()
        if self.connect_gate is not None:
            await self.connect_gate.wait()

    async def request_resume(self, cursor):
        self.resume_calls += 1
        return TransportAck('RESUMED',cursor,hashlib.sha256(b'').hexdigest())

    def try_send(self, frame):
        self.sent.append(frame)
        self.sent_event.set()
        if self.auto_ack:
            self.ack(frame)
        return True

    def ack(self, frame, *, phase='APPLIED', digest=None):
        self.acks.put_nowait(TransportAck(phase,frame.cursor,digest or frame.digest))

    async def receive_ack(self):
        return await self.acks.get()

    async def close(self):
        if self.close_gate is not None:
            await self.close_gate.wait()
        self.closed = True


class Spool:
    def __init__(self, *, flush_gate=None, flush_started=None, loop=None):
        self.owner = threading.get_ident()
        self.operations = [('factory',self.owner)]
        self.appended_cursor = EngineCursor('collector','run',0)
        self.flush_gate, self.flush_started, self.loop = flush_gate,flush_started,loop
        self.closed = threading.Event()

    def _check(self, operation):
        assert threading.get_ident() == self.owner
        self.operations.append((operation,threading.get_ident()))

    def append(self, payload):
        self._check('append')
        self.appended_cursor = EngineCursor('collector','run',self.appended_cursor.sequence+1)
        return SimpleNamespace(cursor=self.appended_cursor)

    def flush(self):
        self._check('flush')
        if self.flush_started is not None:
            self.loop.call_soon_threadsafe(self.flush_started.set)
        if self.flush_gate is not None and not self.flush_gate.wait(3):
            raise RuntimeError('test flush gate was not released')
        return self.appended_cursor

    def close(self):
        self._check('close')
        self.closed.set()


def publisher(factory, transport=None, **updates):
    return SpoolingMarketPublisher(producer='collector',incarnation='run',spool_factory=factory,
        transport=transport or Transport(),budgets=budgets(**updates),env={},on_invalid=lambda reason:None)


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(wait(),timeout=2)


@pytest.mark.asyncio
async def test_existing_spool_needs_explicit_engine_checkpoint_handoff():
    def factory():
        spool=Spool();spool.appended_cursor=EngineCursor('collector','run',7)
        return spool
    transport=Transport()
    checkpoints=[]
    async def resume(cursor):
        checkpoints.append(cursor)
        return TransportAck('RESUMED',cursor,hashlib.sha256(b'').hexdigest())
    transport.request_resume=resume
    bus=SpoolingMarketPublisher(producer='collector',incarnation='run',spool_factory=factory,
        transport=transport,budgets=budgets(),env={},on_invalid=lambda reason:None,resume_existing=True)
    await bus.connect()
    bus.accept_payload(b'next')
    await until(lambda:bus.stats['applied']==1)
    await bus.close()
    assert checkpoints==[EngineCursor('collector','run',7)]
    assert [frame.cursor.sequence for frame in transport.sent]==[8]
    assert bus.stats['last_applied_sequence']==8


@pytest.mark.asyncio
async def test_existing_spool_rejected_checkpoint_does_not_emit_old_or_new_frames():
    def factory():
        spool=Spool();spool.appended_cursor=EngineCursor('collector','run',7)
        return spool
    transport=Transport()
    async def resume(cursor):return TransportAck('REJECTED',cursor,hashlib.sha256(b'').hexdigest())
    transport.request_resume=resume
    bus=SpoolingMarketPublisher(producer='collector',incarnation='run',spool_factory=factory,
        transport=transport,budgets=budgets(),env={},on_invalid=lambda reason:None,resume_existing=True)
    with pytest.raises(RuntimeError,match='rejected'):
        await bus.connect()
    assert transport.sent==[] and bus.stats['accepted']==0


@pytest.mark.asyncio
async def test_real_spool_close_drains_accepted_raw_bytes_on_one_thread_without_nats(tmp_path, monkeypatch):
    nats_calls = []

    async def unavailable(*args,**kwargs):
        nats_calls.append(True)
        raise OSError('NATS down')

    monkeypatch.setattr('nats.connect',unavailable)
    limits = dict(max_disk_bytes=65536,max_records=32,max_frame_bytes=2048,max_header_bytes=512,
        segment_max_bytes=4096,segment_max_records=2,segment_max_age_seconds=60)
    thread_ids = []

    class TracedSpool(MarketSpool):
        def __init__(self):
            thread_ids.append(threading.get_ident())
            super().__init__(tmp_path/'spool','collector','run',**limits)

        def append(self, payload):
            thread_ids.append(threading.get_ident())
            return super().append(payload)

        def flush(self):
            thread_ids.append(threading.get_ident())
            return super().flush()

        def close(self):
            thread_ids.append(threading.get_ident())
            return super().close()

    local = publisher(TracedSpool)
    await local.connect()
    payloads = [b'{"price":"0.123456789012345678","source_ts_ns":null}',
        '{"raw_payload":"원문/交易","quantity":"0"}'.encode(),b'{"third":3}',b'{"fourth":4}']
    for payload in payloads:
        local.accept_payload(payload)
    await local.close(timeout_seconds=2)
    assert local.stats['accepted'] == local.stats['applied'] == 4
    assert local.stats['failed'] == local.stats['pending'] == local.stats['queued_bytes'] == 0
    assert not local.stats['cleanup_pending']
    assert len(set(thread_ids)) == 1 and thread_ids[0] != threading.get_ident()
    read_limits = {key:limits[key] for key in ('max_disk_bytes','max_records','max_frame_bytes','max_header_bytes')}
    assert [row.payload for row in read_spool_records(tmp_path/'spool',producer_id='collector',incarnation='run',**read_limits)] == payloads
    assert nats_calls == []


@pytest.mark.asyncio
async def test_received_ack_keeps_payload_owned_until_exact_applied_ack():
    transport = Transport(auto_ack=False)
    local = publisher(Spool,transport)
    await local.connect()
    local.accept_payload(b'raw')
    await transport.sent_event.wait()
    assert local.stats['durable'] == 1
    assert local.stats['pending'] == 1 and local.stats['queued_bytes'] == 3
    transport.ack(transport.sent[0],phase='RECEIVED')
    await asyncio.sleep(0)
    assert local.stats['queued_bytes'] == 3
    transport.ack(transport.sent[0])
    await local.close(timeout_seconds=1)
    assert local.stats['applied'] == 1 and local.stats['queued_bytes'] == 0


@pytest.mark.asyncio
async def test_bad_applied_digest_invalidates_without_false_release():
    transport = Transport(auto_ack=False)
    local = publisher(Spool,transport)
    await local.connect()
    local.accept_payload(b'raw')
    await transport.sent_event.wait()
    transport.ack(transport.sent[0],digest='0'*64)
    await until(lambda:local.stats['invalid'])
    assert local.stats['applied'] == 0 and local.stats['queued_bytes'] == 3
    with pytest.raises(RuntimeError,match='unconfirmed'):
        await local.close(timeout_seconds=1)
    assert local.stats['failed'] == 1 and local.stats['pending'] == local.stats['queued_bytes'] == 0


@pytest.mark.asyncio
async def test_stalled_flush_close_returns_on_deadline_and_late_cleanup_keeps_thread_affinity():
    gate, started = threading.Event(),asyncio.Event()
    loop = asyncio.get_running_loop()
    holder = []

    def factory():
        value = Spool(flush_gate=gate,flush_started=started,loop=loop)
        holder.append(value)
        return value

    local = publisher(factory)
    await local.connect()
    local.accept_payload(b'raw')
    await asyncio.wait_for(started.wait(),timeout=1)
    ticks = []

    async def heartbeat():
        for _ in range(10):
            ticks.append(True)
            await asyncio.sleep(.005)

    beat = asyncio.create_task(heartbeat())
    before = loop.time()
    try:
        with pytest.raises(RuntimeError,match='unconfirmed'):
            await asyncio.wait_for(local.close(timeout_seconds=.03),timeout=.5)
        assert loop.time()-before < .4
        assert ticks and local.stats['cleanup_pending']
        assert local.stats['cleanup_retained_bytes'] == 3
        assert local.stats['accepted'] == local.stats['failed'] == 1
        assert local.stats['pending'] == local.stats['queued_bytes'] == 0
        with pytest.raises(RuntimeError):
            local.accept_payload(b'new')
    finally:
        gate.set()
        await beat
        await until(lambda:holder[0].closed.is_set())
    await until(lambda:not local.stats['cleanup_pending'])
    assert local.stats['cleanup_retained_bytes'] == 0
    assert len({identity for _,identity in holder[0].operations}) == 1


@pytest.mark.asyncio
async def test_close_during_blocked_factory_closes_late_spool_without_transport_reconnect():
    gate, started = threading.Event(),asyncio.Event()
    loop = asyncio.get_running_loop()
    holder = []

    def factory():
        loop.call_soon_threadsafe(started.set)
        if not gate.wait(3):
            raise RuntimeError('test factory gate was not released')
        value = Spool()
        holder.append(value)
        return value

    transport = Transport()
    local = publisher(factory,transport)
    connecting = asyncio.create_task(local.connect())
    await asyncio.wait_for(started.wait(),timeout=1)
    try:
        with pytest.raises(RuntimeError,match='unconfirmed'):
            await asyncio.wait_for(local.close(timeout_seconds=.03),timeout=.5)
        assert local.stats['cleanup_pending']
        assert not transport.connected_event.is_set() and transport.resume_calls == 0
    finally:
        gate.set()
        await asyncio.gather(connecting,return_exceptions=True)
        await until(lambda:bool(holder) and holder[0].closed.is_set())
    assert len({identity for _,identity in holder[0].operations}) == 1
    assert not transport.connected_event.is_set()


@pytest.mark.asyncio
async def test_close_cancels_pending_transport_startup_and_never_resumes():
    transport = Transport()
    transport.connect_gate = asyncio.Event()
    local = publisher(Spool,transport)
    connecting = asyncio.create_task(local.connect())
    await transport.connected_event.wait()
    await local.close(timeout_seconds=1)
    result = await asyncio.gather(connecting,return_exceptions=True)
    assert isinstance(result[0],asyncio.CancelledError)
    assert transport.resume_calls == 0 and transport.closed
    assert local._spool.closed.is_set() and not local._ready


@pytest.mark.asyncio
async def test_cancelled_close_caller_does_not_cancel_accepted_drain():
    transport = Transport(auto_ack=False)
    local = publisher(Spool,transport)
    await local.connect()
    local.accept_payload(b'raw')
    await transport.sent_event.wait()
    closing = asyncio.create_task(local.close(timeout_seconds=1))
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    with pytest.raises(RuntimeError):
        local.accept_payload(b'new')
    transport.ack(transport.sent[0])
    await local.close(timeout_seconds=1)
    assert local.stats['applied'] == 1 and local.stats['failed'] == 0
    assert local._spool.closed.is_set()


@pytest.mark.asyncio
async def test_stalled_transport_close_uses_total_shutdown_deadline():
    transport = Transport()
    transport.close_gate = asyncio.Event()
    local = publisher(Spool,transport)
    await local.connect()
    before = asyncio.get_running_loop().time()
    try:
        with pytest.raises(RuntimeError,match='unconfirmed'):
            await asyncio.wait_for(local.close(timeout_seconds=.03),timeout=.5)
        assert asyncio.get_running_loop().time()-before < .4
        assert local._spool.closed.is_set()
        assert local.stats['transport_cleanup_pending']
    finally:
        transport.close_gate.set()
    await until(lambda:local._transport_close_task.done())


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [b'',bytearray(b'raw'),b'x'*513])
async def test_invalid_admission_never_increases_owned_bytes(payload):
    local = publisher(Spool)
    await local.connect()
    with pytest.raises(RuntimeError,match='budget'):
        local.accept_payload(payload)
    assert local.stats['accepted'] == local.stats['queued_bytes'] == 0
    with pytest.raises(RuntimeError):
        await local.close(timeout_seconds=1)


@pytest.mark.asyncio
async def test_count_budget_includes_inflight_unacknowledged_records():
    transport = Transport(auto_ack=False)
    local = publisher(Spool,transport,max_records=1,batch_records=1)
    await local.connect()
    local.accept_payload(b'raw')
    await transport.sent_event.wait()
    with pytest.raises(RuntimeError,match='budget'):
        local.accept_payload(b'next')
    assert local.stats['accepted'] == 1 and local.stats['queued_bytes'] == 3
    with pytest.raises(RuntimeError):
        await local.close(timeout_seconds=1)
    assert local.stats['failed'] == 1


@pytest.mark.parametrize('field', ['max_records','max_bytes','max_frame_bytes','batch_records','group_sync_ms'])
@pytest.mark.parametrize('value', [True,0,-1,1.0,float('inf')])
def test_pipeline_budgets_are_positive_explicit_integers(field, value):
    with pytest.raises(ValueError):
        budgets(**{field:value})


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [True,0,-1,float('inf'),float('nan')])
async def test_invalid_close_deadline_does_not_start_cleanup(value):
    local = publisher(Spool)
    with pytest.raises(ValueError):
        await local.close(timeout_seconds=value)
    assert local._close_task is None and not local._closing
    await local.close(timeout_seconds=1)
