"""Finite shutdown and ownership receipts, with fake local transport only."""
import asyncio
import hashlib
import threading
from types import SimpleNamespace

import pytest

from feeds.feed_recovery import EngineCursor
from feeds.local_pipeline import DeliveryBudgets, PipelineBudgets, SpoolingMarketPublisher
from runtime.local_transport import AppliedReceipt, LocalTransportClient, LocalTransportServer, TransportAck, TransportBudgets
from storage.market_spool import MarketSpool, SpoolCursor, SpoolReader, read_spool_records


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


def capture_group_deadlines(monkeypatch):
    wait_for = asyncio.wait_for
    deadlines = []

    async def capture(awaitable, timeout):
        if getattr(getattr(awaitable,'cr_code',None),'co_name',None) != 'gather_group':
            return await wait_for(awaitable,timeout)
        group = SimpleNamespace(timeout=timeout,task=asyncio.create_task(awaitable),expired=False)
        deadlines.append(group)
        try:
            return await wait_for(group.task,timeout)
        except asyncio.CancelledError:
            # A controlled expiry cancels and settles the gather child just
            # as wait_for does, then reports its timeout to the writer.
            if group.expired:raise asyncio.TimeoutError
            raise

    monkeypatch.setattr("feeds.local_pipeline.asyncio.wait_for",capture)
    return deadlines


@pytest.mark.asyncio
@pytest.mark.parametrize('staggered', [False,True])
@pytest.mark.parametrize(('updates','payloads'), [
    ({'batch_records':1}, [b'one']),
    ({}, [b'one',b'two',b'three',b'four']),
    ({'max_bytes':6,'max_frame_bytes':6}, [b'ab',b'cdef']),
])
async def test_full_group_flushes_without_wait_and_sends_only_after_flush(updates, payloads, staggered):
    gate, started = threading.Event(),asyncio.Event()
    loop = asyncio.get_running_loop()
    transport = Transport()
    local = publisher(lambda:Spool(flush_gate=gate,flush_started=started,loop=loop),
                      transport,group_sync_ms=60_000,**updates)
    await local.connect()
    try:
        local.accept_payload(payloads[0])
        if staggered:await until(local._queue.empty)
        for payload in payloads[1:]:local.accept_payload(payload)
        # A full group must reach fsync without waiting out the 60s deadline.
        await asyncio.wait_for(started.wait(),timeout=1)
        assert transport.sent == []
        assert local.stats['durable'] == 0
        assert local.stats['pending'] == len(payloads)
        assert local.stats['queued_bytes'] == sum(map(len,payloads))
    finally:
        gate.set()
        await local.close(timeout_seconds=1)
    assert [frame.payload for frame in transport.sent] == payloads
    assert [frame.cursor.sequence for frame in transport.sent] == list(range(1,len(payloads)+1))
    assert [operation for operation,_ in local._spool.operations] == [
        'factory',*(['append']*len(payloads)),'flush','close']
    assert local.stats['accepted'] == local.stats['durable'] == local.stats['applied'] == len(payloads)
    assert local.stats['pending'] == local.stats['queued_bytes'] == local.stats['failed'] == 0


@pytest.mark.asyncio
async def test_partial_group_uses_one_deadline_despite_arrivals_and_close_drains(monkeypatch):
    deadlines = capture_group_deadlines(monkeypatch)
    transport = Transport()
    local = publisher(Spool,transport,group_sync_ms=60_000)
    await local.connect()
    payloads = [b'first',b'second',b'third']
    closing = None
    try:
        local.accept_payload(payloads[0])
        await until(lambda:len(deadlines)==1 and local._queue.empty())
        first_deadline = deadlines[0].timeout
        assert 0 < first_deadline <= 60
        for payload in payloads[1:]:
            local.accept_payload(payload)
            await until(local._queue.empty)
            assert len(deadlines) == 1 and deadlines[0].timeout == first_deadline
        assert transport.sent == [] and local.stats['appended'] == 0
        closing = asyncio.create_task(local.close(timeout_seconds=1))
        await asyncio.sleep(0)
        assert not closing.done()
        # Expire the gather without a wall-clock timing race.
        deadlines[0].expired = True
        deadlines[0].task.cancel()
        await closing
        assert deadlines[0].expired and deadlines[0].task.done()
        assert [frame.payload for frame in transport.sent] == payloads
        assert [frame.cursor.sequence for frame in transport.sent] == [1,2,3]
        assert [operation for operation,_ in local._spool.operations].count('flush') == 1
        assert local.stats['accepted'] == local.stats['durable'] == local.stats['applied'] == 3
        assert local.stats['pending'] == local.stats['queued_bytes'] == local.stats['failed'] == 0
        await asyncio.wait_for(local._queue.join(),timeout=1)
    finally:
        if not local._closing:
            for timer in deadlines:
                if not timer.task.done():
                    timer.expired = True
                    timer.task.cancel()
            await local.close(timeout_seconds=1)
        if closing is not None:await closing


@pytest.mark.asyncio
async def test_close_timeout_during_gather_accounts_for_every_accepted_item(monkeypatch):
    deadlines = capture_group_deadlines(monkeypatch)
    local = publisher(Spool,group_sync_ms=60_000)
    await local.connect()
    for payload in (b'first',b'second'):local.accept_payload(payload)
    await until(lambda:len(deadlines)==1 and local._queue.empty())
    with pytest.raises(RuntimeError,match='unconfirmed'):
        await local.close(timeout_seconds=.03)
    assert local.stats['accepted'] == local.stats['failed'] == 2
    assert local.stats['appended'] == local.stats['durable'] == local.stats['applied'] == 0
    assert local.stats['pending'] == local.stats['queued_bytes'] == 0
    assert local.stats['fault_stage'] == 'shutdown' and local.stats['fault_reason'] == 'drain_timeout'
    assert local.transport.sent == []
    await asyncio.wait_for(local._queue.join(),timeout=1)


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
    assert bus.stats['last_durable_sequence']==bus.stats['last_applied_sequence']==7
    assert bus.stats['durable_applied_lag_records']==0
    bus.accept_payload(b'next')
    await until(lambda:bus.stats['applied']==1)
    await bus.close()
    assert checkpoints==[EngineCursor('collector','run',7)]
    assert [frame.cursor.sequence for frame in transport.sent]==[8]
    assert bus.stats['last_applied_sequence']==8
    assert bus.stats['last_durable_sequence']==8 and bus.stats['durable_applied_lag_records']==0


@pytest.mark.asyncio
async def test_delivery_recovers_fixed_durable_high_water_after_resume_rejection(tmp_path):
    from feeds.feed_recovery import RecoveryFrame

    producer,incarnation='collector','run'
    spool_args=dict(max_disk_bytes=8192,max_records=64,max_frame_bytes=512,max_header_bytes=128,
                    segment_max_bytes=4096,segment_max_records=32,segment_max_age_seconds=60)
    def spool_factory():return MarketSpool(tmp_path,producer,incarnation,**spool_args)
    def reader_factory(cursor):
        return SpoolReader(tmp_path,producer,incarnation,after=SpoolCursor(
            cursor.producer,cursor.incarnation,cursor.sequence),
            max_disk_bytes=spool_args['max_disk_bytes'],max_records=spool_args['max_records'],
            max_frame_bytes=spool_args['max_frame_bytes'],max_header_bytes=spool_args['max_header_bytes'])

    gate=asyncio.Event()
    class RecoveryTransport:
        def __init__(self):
            self.replayed=[];self.commit_calls=0;self.queried=[];self.commits=[]
        async def connect(self):await gate.wait()
        async def request_resume(self,target):
            return TransportAck('REJECTED',target,hashlib.sha256(b'').hexdigest())
        async def query_cursor(self,target):
            self.queried.append(target)
            return TransportAck('QUERY',EngineCursor(producer,incarnation,0),hashlib.sha256(b'').hexdigest())
        def try_send(self,item,*,replay=False):
            assert replay
            self.replayed.append(item)
            return True
        async def receive_ack(self):
            item=self.replayed[-1]
            return TransportAck('APPLIED',item.cursor,item.digest)
        async def commit_recovery(self,target,digest):
            self.commit_calls+=1
            self.commits.append((target,digest))
            return self.commit_calls>=2
        async def close(self):pass

    client=RecoveryTransport()
    publisher=SpoolingMarketPublisher(producer=producer,incarnation=incarnation,
        spool_factory=spool_factory,transport=None,budgets=budgets(),env={},on_invalid=lambda reason:None,
        resume_existing=True,delivery_budgets=DeliveryBudgets(2,512,.001,.01,.2),
        reader_factory=reader_factory,transport_factory=lambda:client)
    await publisher.connect()
    try:
        publisher.accept_payload(b'durable-before-connect')
        await until(lambda:publisher.stats['durable']==1 and publisher.stats['pending']==0)
        high=EngineCursor(producer,incarnation,1)
        gate.set()
        await until(lambda:client.commit_calls>=2)
        assert [(item.cursor,item.payload) for item in client.replayed]==[(high,b'durable-before-connect')]
        assert client.queried==[high]
        assert client.commits==[(high,hashlib.sha256(b'durable-before-connect').hexdigest())]*2
        assert client.commit_calls>=2
        assert publisher.stats['last_durable_sequence']==1
    finally:
        gate.set()
        await publisher.close()


@pytest.mark.asyncio
async def test_delivery_waits_through_received_for_two_live_applied_receipts(tmp_path):
    producer,incarnation='collector','run'
    spool_args=dict(max_disk_bytes=8192,max_records=64,max_frame_bytes=512,max_header_bytes=128,
                    segment_max_bytes=4096,segment_max_records=32,segment_max_age_seconds=60)
    def spool_factory():return MarketSpool(tmp_path,producer,incarnation,**spool_args)
    def reader_factory(cursor):
        return SpoolReader(tmp_path,producer,incarnation,after=SpoolCursor(
            cursor.producer,cursor.incarnation,cursor.sequence),
            max_disk_bytes=spool_args['max_disk_bytes'],max_records=spool_args['max_records'],
            max_frame_bytes=spool_args['max_frame_bytes'],max_header_bytes=spool_args['max_header_bytes'])

    limits=TransportBudgets(1024,1024,4,8192,2.0,32,65536,2,2.0)
    applied=[]
    transports=[]
    server=LocalTransportServer(('127.0.0.1',0),budgets=limits,
        on_frame=lambda item:(applied.append(item) or AppliedReceipt(item.cursor,item.digest)),
        on_gap=lambda *args:None,on_resume=lambda target:True,profile='offline-loopback')
    await server.start()
    def transport_factory():
        client=LocalTransportClient(server.addresses,producer=producer,incarnation=incarnation,
            budgets=limits,on_gap=lambda *args:None,profile='offline-loopback')
        transports.append(client)
        return client
    publisher=SpoolingMarketPublisher(producer=producer,incarnation=incarnation,
        spool_factory=spool_factory,transport=None,budgets=budgets(),env={},on_invalid=lambda reason:None,
        resume_existing=True,delivery_budgets=DeliveryBudgets(2,512,.001,.01,.5),
        reader_factory=reader_factory,transport_factory=transport_factory)
    try:
        await publisher.connect()
        await until(lambda:publisher.stats['delivery_state']=='LIVE' or publisher.stats['invalid'])
        assert publisher.stats['delivery_state']=='LIVE', f"publisher={publisher.stats}; transports={[client.stats for client in transports]}"
        publisher.accept_payload(b'live-one')
        publisher.accept_payload(b'live-two')
        try:
            await until(lambda:publisher.stats['applied']==2 or publisher.stats['invalid'])
        except asyncio.TimeoutError as error:
            raise AssertionError(f"live delivery stalled; publisher={publisher.stats}; "
                                 f"transports={[client.stats for client in transports]}; "
                                 f"applied={[(item.cursor.sequence,item.digest) for item in applied]}") from error
        assert [item.payload for item in applied]==[b'live-one',b'live-two']
        assert publisher.stats['last_applied_sequence']==2
        assert not publisher.stats['invalid']
    finally:
        await publisher.close()
        await server.close()


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
async def test_bad_applied_digest_invalidates_without_false_release(monkeypatch):
    now = [1_000]
    monkeypatch.setattr("feeds.local_pipeline.time.monotonic_ns",lambda:now[0])
    transport = Transport(auto_ack=False)
    local = publisher(Spool,transport)
    await local.connect()
    local.accept_payload(b'raw')
    await transport.sent_event.wait()
    now[0] = 6_000
    transport.ack(transport.sent[0],digest='0'*64)
    await until(lambda:local.stats['invalid'])
    assert local.stats['applied'] == 0 and local.stats['queued_bytes'] == 3
    fault = local.stats
    assert (fault['fault_stage'],fault['fault_reason'],fault['fault_cursor_sequence']) == (
        'ack','ack_content_or_order_mismatch',1)
    assert fault['fault_oldest_pending_age_ns'] == 5_000
    with pytest.raises(RuntimeError,match='unconfirmed'):
        await local.close(timeout_seconds=1)
    assert local.stats['failed'] == 1 and local.stats['pending'] == local.stats['queued_bytes'] == 0
    assert local.stats['oldest_pending_age_ns'] == 0
    assert {key:value for key,value in local.stats.items() if key.startswith('fault_')} == {
        key:value for key,value in fault.items() if key.startswith('fault_')}


@pytest.mark.asyncio
async def test_latency_evidence_uses_monotonic_age_and_exact_applied_ack(monkeypatch):
    now = [1_000]
    monkeypatch.setattr("feeds.local_pipeline.time.monotonic_ns",lambda:now[0])
    transport = Transport(auto_ack=False)
    local = publisher(Spool,transport)
    await local.connect()
    payloads = [b'first',b'second']
    for payload in payloads:local.accept_payload(payload)
    now[0] = 3_000
    await transport.sent_event.wait()
    stats = local.stats
    assert [frame.payload for frame in transport.sent] == payloads
    assert stats['last_durable_sequence'] == stats['durable_applied_lag_records'] == 2
    assert stats['oldest_pending_age_ns'] == 2_000
    assert stats['last_durable_latency_ns'] == stats['max_durable_latency_ns'] == 2_000
    assert stats['last_applied_latency_ns'] == stats['max_applied_latency_ns'] == 0
    transport.ack(transport.sent[0],phase='RECEIVED')
    await asyncio.sleep(0)
    assert local.stats['durable_applied_lag_records'] == 2 and local.stats['pending'] == 2
    now[0] = 6_000
    transport.ack(transport.sent[0])
    await until(lambda:local.stats['applied']==1)
    assert local.stats['last_applied_latency_ns'] == local.stats['max_applied_latency_ns'] == 5_000
    assert local.stats['durable_applied_lag_records'] == 1
    now[0] = 9_000
    transport.ack(transport.sent[1])
    await local.close(timeout_seconds=1)
    assert local.stats['last_applied_latency_ns'] == local.stats['max_applied_latency_ns'] == 8_000
    assert local.stats['oldest_pending_age_ns'] == local.stats['durable_applied_lag_records'] == 0
    assert local.stats['fault_stage'] is local.stats['fault_reason'] is local.stats['fault_cursor_sequence'] is None
    assert all(type(value) is int and value>=0 for key,value in local.stats.items() if key.endswith('_ns'))


@pytest.mark.asyncio
async def test_persistence_failure_has_bounded_evidence_without_false_durability(monkeypatch):
    now = [1_000]
    monkeypatch.setattr("feeds.local_pipeline.time.monotonic_ns",lambda:now[0])
    class FailingSpool(Spool):
        def flush(self):
            now[0] = 6_000
            raise OSError('private-error-text-'*1_000)
    local = publisher(FailingSpool)
    await local.connect()
    local.accept_payload(b'first');local.accept_payload(b'second')
    await until(lambda:local.stats['invalid'])
    stats = local.stats
    assert (stats['fault_stage'],stats['fault_reason'],stats['fault_cursor_sequence']) == (
        'persist','persistence_failed',None)
    assert stats['fault_oldest_pending_age_ns'] == 5_000
    assert stats['durable'] == stats['last_durable_sequence'] == stats['durable_applied_lag_records'] == 0
    assert stats['pending'] == 2 and stats['queued_bytes'] == 11
    assert 'private-error-text' not in str(stats)
    with pytest.raises(RuntimeError,match='unconfirmed'):await local.close(timeout_seconds=1)
    assert local.stats['failed'] == 2 and local.stats['pending'] == local.stats['queued_bytes'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('raises', [False,True])
async def test_send_failure_retains_entire_flushed_batch_lag_and_first_failed_cursor(raises):
    transport = Transport(auto_ack=False)
    def reject(frame):
        transport.sent.append(frame)
        if raises:raise OSError('transport failed')
        return False
    transport.try_send = reject
    local = publisher(Spool,transport)
    await local.connect()
    payloads = [b'first',b'second',b'third']
    for payload in payloads:local.accept_payload(payload)
    await until(lambda:local.stats['invalid'])
    stats = local.stats
    assert (stats['fault_stage'],stats['fault_reason'],stats['fault_cursor_sequence']) == (
        'transport','durable_send_failed',1)
    assert stats['durable'] == stats['last_durable_sequence'] == stats['durable_applied_lag_records'] == 3
    assert [frame.payload for frame in transport.sent] == [payloads[0]]
    assert stats['pending'] == 3 and stats['queued_bytes'] == sum(map(len,payloads))
    with pytest.raises(RuntimeError,match='unconfirmed'):await local.close(timeout_seconds=1)
    assert local.stats['failed'] == 3 and local.stats['pending'] == local.stats['queued_bytes'] == 0
    assert local.stats['durable_applied_lag_records'] == 3


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
        assert local.stats['fault_stage'] == 'shutdown' and local.stats['fault_reason'] == 'drain_timeout'
        assert local.stats['fault_oldest_pending_age_ns'] > 0
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
    assert local.stats['fault_stage'] == 'admission'
    assert local.stats['fault_reason'] == 'invalid_frame_or_ownership_budget'
    assert local.stats['fault_cursor_sequence'] is None
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
