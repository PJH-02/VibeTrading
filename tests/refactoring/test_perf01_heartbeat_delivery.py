"""PERF-01 regressions for heartbeat PONG isolation from market receipts."""
import asyncio
from decimal import Decimal
import hashlib
import os
import threading

import pytest

from core.models import AssetClass, InstrumentId, TradeEvent, Venue
from feeds.feed_recovery import EngineCursor
from feeds.local_pipeline import DeliveryBudgets, PipelineBudgets, SpoolingMarketPublisher
from market.hot_path import HotPathStore
from market.local_engine import EngineBudgets, LocalMarketEngine
from runtime.local_transport import LocalTransportClient, LocalTransportServer, TransportAck, TransportBudgets
from storage.market_spool import MarketSpool, SpoolCursor, SpoolReader


def _event_payload(sequence=1):
    instrument=InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,
        raw_symbol="069500",std_symbol="069500")
    event=TradeEvent(instrument=instrument,venue=Venue.KIWOOM,provider="kiwoom",
        source_ts_ns=1_800_000_000_000_000_000+sequence,
        receive_ts_ns=1_800_000_000_000_000_000+sequence,
        wallclock_ts_ns=1_800_000_000_000_000_000+sequence,
        price=Decimal("123.45"),quantity=Decimal("2"),event_id=f"heartbeat-{sequence}",sequence=sequence)
    return event.model_dump_json().encode()


async def _until(predicate, timeout=2):
    async def poll():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(poll(),timeout)


def _pipeline(tmp_path, endpoint, clients, applied, callbacks, flush_gate, flush_started):
    producer,incarnation="heartbeat-feed","heartbeat-boot"
    spool_options=dict(max_disk_bytes=8192,max_records=16,max_frame_bytes=2048,max_header_bytes=512,
        segment_max_bytes=4096,segment_max_records=8,segment_max_age_seconds=60)
    def spool_factory():
        spool=MarketSpool(tmp_path/"spool",producer,incarnation,**spool_options)
        original_flush=spool.flush
        def gated_flush():
            flush_started.set()
            if not flush_gate.wait(2):raise TimeoutError("test flush gate timed out")
            return original_flush()
        spool.flush=gated_flush
        return spool
    def reader_factory(cursor):
        return SpoolReader(tmp_path/"spool",producer,incarnation,
            after=SpoolCursor(cursor.producer,cursor.incarnation,cursor.sequence),
            **{key:spool_options[key] for key in ("max_disk_bytes","max_records","max_frame_bytes","max_header_bytes")})
    limits=TransportBudgets(2048,1024,2,8192,2.0,4,8192,2,2.0)
    engine=LocalMarketEngine(producer_providers={producer:"kiwoom"},
        budgets=EngineBudgets(4,8,8192,8,65536,10_000_000_000,8,4,20_000_000_000,.01),
        on_invalidate=lambda *_:None,hot_path=HotPathStore(),
        post_apply_callbacks=(lambda result,snapshot:(callbacks.append((result,snapshot))),),
        snapshot_clock=lambda:1_800_000_000_000_000_000,
        fresh_starts={producer:incarnation},verified_fresh_start=True)
    def on_frame(frame):
        receipt=engine.on_frame(frame)
        applied.append(frame)
        return receipt
    server=LocalTransportServer(endpoint,budgets=limits,on_frame=on_frame,
        on_gap=engine.on_gap,on_resume=engine.resume_receipt,on_replay=engine.apply_replay,
        on_recovery_query=engine.begin_delivery_recovery,
        on_recovery_commit=lambda cursor,digest:engine.commit_recovery(cursor,digest,ready=True),
        allowed_uid=os.getuid())
    def transport_factory():
        client=LocalTransportClient(endpoint,producer=producer,incarnation=incarnation,
            budgets=limits,on_gap=lambda *_:None,allowed_uid=os.getuid())
        clients.append(client)
        return client
    publisher=SpoolingMarketPublisher(producer=producer,incarnation=incarnation,
        spool_factory=spool_factory,transport=None,
        budgets=PipelineBudgets(4,8192,2048,2,1),env={},on_invalid=lambda *_:None,
        delivery_budgets=DeliveryBudgets(2,4096,.2,.02,.25),
        reader_factory=reader_factory,transport_factory=transport_factory)
    return limits,engine,server,publisher


@pytest.mark.asyncio
async def test_real_uds_heartbeat_before_first_durable_frame_does_not_force_recovery(tmp_path):
    endpoint=str(tmp_path/"market.sock")
    clients,applied,callbacks=[],[],[]
    flush_gate,flush_started=threading.Event(),threading.Event()
    _,engine,server,publisher=_pipeline(tmp_path,endpoint,clients,applied,callbacks,flush_gate,flush_started)
    try:
        await server.start()
        await publisher.connect()
        await _until(lambda:publisher.stats["delivery_state"]=="LIVE" and bool(clients))
        await asyncio.sleep(.05)  # let the idle poll enter its bounded wait
        client=clients[0]
        publisher.accept_payload(_event_payload())
        assert await asyncio.to_thread(flush_started.wait,1)
        assert await client.heartbeat()
        await _until(lambda:client.stats["ack_records"]==1)
        assert client.stats["last_ack_phase"]=="PONG"
        flush_gate.set()
        await _until(lambda:publisher.stats["applied"]==1 or publisher.stats["invalid"]
            or publisher.stats["last_delivery_error_type"],timeout=3)
        assert publisher.stats["accepted"]==publisher.stats["durable"]==publisher.stats["applied"]==1
        assert publisher.stats["last_applied_sequence"]==1
        assert engine.stats["applied"]==1 and len(applied)==len(callbacks)==1
        assert len(clients)==1 and client.stats["ack_records"]==0

        # More heartbeat responses than the finite control queue capacity must
        # drain during idle polling, and the next actual frame still applies once.
        for _ in range(8):
            assert await client.heartbeat()
            await asyncio.sleep(.21)
        await _until(lambda:client.stats["ack_records"]==0)
        with pytest.raises(asyncio.QueueEmpty):client.receive_ack_nowait()
        publisher.accept_payload(_event_payload(2))
        await _until(lambda:publisher.stats["applied"]==2 or publisher.stats["invalid"]
            or publisher.stats["last_delivery_error_type"],timeout=3)
        assert publisher.stats["accepted"]==publisher.stats["durable"]==publisher.stats["applied"]==2
        assert len(applied)==len(callbacks)==2 and len(clients)==1
        assert client.stats["ack_records"]==0
    finally:
        flush_gate.set()
        await publisher.close(timeout_seconds=2)
        await server.close()
        await engine.close()


@pytest.mark.asyncio
async def test_ack_wait_ignores_only_pong_without_renewing_absolute_deadline():
    class HeartbeatOnly:
        async def receive_ack(self):
            await asyncio.sleep(.004)
            return TransportAck("PONG",EngineCursor("p","i",0),hashlib.sha256(b"").hexdigest())
    publisher=object.__new__(SpoolingMarketPublisher)
    started=asyncio.get_running_loop().time()
    with pytest.raises(asyncio.TimeoutError):
        await publisher._receive_delivery_ack(HeartbeatOnly(),started+.04)
    assert asyncio.get_running_loop().time()-started < .12


@pytest.mark.asyncio
async def test_ack_wait_returns_non_pong_for_strict_correlation_validation():
    class Receipts:
        def __init__(self):self.values=iter((
            TransportAck("PONG",EngineCursor("p","i",0),hashlib.sha256(b"").hexdigest()),
            TransportAck("RECEIVED",EngineCursor("p","i",1),"wrong-digest")))
        async def receive_ack(self):return next(self.values)
    publisher=object.__new__(SpoolingMarketPublisher)
    deadline=asyncio.get_running_loop().time()+1
    receipts=Receipts()
    data=await publisher._receive_delivery_ack(receipts,deadline)
    assert data.phase=="RECEIVED" and data.digest=="wrong-digest"
    assert data.cursor==EngineCursor("p","i",1)


@pytest.mark.asyncio
async def test_live_and_replay_ack_order_survives_interleaved_pongs(tmp_path):
    producer,incarnation="ack-order","ack-boot"
    options=dict(max_disk_bytes=8192,max_records=16,max_frame_bytes=2048,max_header_bytes=512,
        segment_max_bytes=4096,segment_max_records=8,segment_max_age_seconds=60)
    spool_root=tmp_path/"spool"
    def spool_factory():return MarketSpool(spool_root,producer,incarnation,**options)
    def reader_factory(cursor):
        return SpoolReader(spool_root,producer,incarnation,
            after=SpoolCursor(cursor.producer,cursor.incarnation,cursor.sequence),
            **{key:options[key] for key in ("max_disk_bytes","max_records","max_frame_bytes","max_header_bytes")})
    class InterleavedTransport:
        def __init__(self,replay):self.replay=replay;self.acks=asyncio.Queue();self.sent=[]
        async def connect(self):pass
        async def request_resume(self,cursor):
            phase="REJECTED" if self.replay else "RESUMED"
            return TransportAck(phase,cursor,hashlib.sha256(b"").hexdigest())
        async def query_cursor(self,target):
            return TransportAck("QUERY",EngineCursor(producer,incarnation,0),hashlib.sha256(b"").hexdigest())
        def try_send(self,frame,*,replay=False):
            self.sent.append((frame,replay))
            self.acks.put_nowait(TransportAck("PONG",EngineCursor(producer,incarnation,0),hashlib.sha256(b"").hexdigest()))
            if not replay:
                self.acks.put_nowait(TransportAck("RECEIVED",frame.cursor,frame.digest))
                self.acks.put_nowait(TransportAck("PONG",EngineCursor(producer,incarnation,0),hashlib.sha256(b"").hexdigest()))
            self.acks.put_nowait(TransportAck("APPLIED",frame.cursor,frame.digest))
            return True
        async def receive_ack(self):return await self.acks.get()
        async def commit_recovery(self,cursor,digest):return True
        async def close(self):pass

    # Make a real durable prefix so the second publisher must use replay APPLIED.
    seed=spool_factory()
    seed.append(b"record-one");seed.flush();seed.close()
    replay=InterleavedTransport(True)
    def transport_factory():return replay
    publisher=SpoolingMarketPublisher(producer=producer,incarnation=incarnation,
        spool_factory=spool_factory,transport=None,
        budgets=PipelineBudgets(4,8192,2048,2,1),env={},on_invalid=lambda *_:None,
        resume_existing=True,delivery_budgets=DeliveryBudgets(2,4096,.005,.02,.2),
        reader_factory=reader_factory,transport_factory=transport_factory)
    try:
        await publisher.connect()
        await _until(lambda:len(replay.sent)==1 and publisher.stats["delivery_state"]=="LIVE")
        assert replay.sent[0][1] is True
        assert publisher.stats["last_applied_sequence"]==1
        publisher.accept_payload(b"record-two")
        await _until(lambda:publisher.stats["applied"]==1)
        assert replay.sent[1][1] is False
        assert replay.acks.empty()
        assert publisher.stats["last_applied_sequence"]==2
        assert not publisher.stats["invalid"]
    finally:
        await publisher.close(timeout_seconds=2)
