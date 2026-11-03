"""A public live gap changes factual book quality without interrupting delivery."""
import asyncio
from decimal import Decimal
import hashlib
import os

import pytest

from core.models import BookUpdateType, DataGapEvent, TradeEvent, Venue
from feeds.feed_recovery import EngineCursor, RecoveryFrame
from feeds.local_pipeline import DeliveryBudgets, PipelineBudgets, SpoolingMarketPublisher
from market.hot_path import HotPathStore
from market.local_engine import EngineBudgets, EngineError, LocalMarketEngine, _observation_noop
from runtime.local_transport import LocalTransportClient, LocalTransportServer, TransportBudgets
from storage.market_spool import MarketSpool, SpoolReader
from test_public_projection_budget import INSTRUMENT, STAMP, book, fields

PRODUCER, INC = 'public', 'live-gap'
EMPTY = hashlib.sha256(b'').hexdigest()
LIMITS = dict(max_disk_bytes=1_000_000, max_records=128, max_frame_bytes=8192,
    max_header_bytes=512, segment_max_bytes=65536, segment_max_records=64,
    segment_max_age_seconds=60)
READ = {k:v for k,v in LIMITS.items() if not k.startswith('segment_')}


def cursor(number): return EngineCursor(PRODUCER, INC, number)


def sequence():
    events = [book(1, 1, BookUpdateType.SNAPSHOT, None, 100),
        book(2, 1, BookUpdateType.DELTA, 100, 101).model_copy(update={'fast':False}),
        DataGapEvent(**fields(3),reason='crossed or empty native book'),
        DataGapEvent(**fields(4),reason='websocket disconnected; fresh snapshot required'),
        DataGapEvent(**fields(5),reason='repeated invalidation'),
        book(6, 1, BookUpdateType.DELTA, 101, 102).model_copy(update={'fast':False}),
        book(7, 2, BookUpdateType.SNAPSHOT, None, 200),
        book(8, 2, BookUpdateType.DELTA, 200, 201)]
    events += [TradeEvent(**fields(seq),price=Decimal('123.000000000000000003'),
        quantity=Decimal('0.000000000000000001')) for seq in range(9,33)]
    return events


def build(observer=True, clock=None, other=False):
    providers = {PRODUCER:'arcus-perp'}
    if other: providers['independent'] = 'binance-perp'
    return LocalMarketEngine(producer_providers=providers,
        budgets=EngineBudgets(2,4,8192,64,524288,10_000_000_000,128,1,5_000_000_000,.01),
        on_invalidate=_observation_noop,hot_path=HotPathStore(),observation_only=observer,
        trading_filter=None if observer else lambda _:False,
        **({} if clock is None else {'clock':clock}))


def start(local):
    local.begin_recovery(cursor(0),cursor(0),verified_checkpoint=True)
    assert local.commit_recovery(cursor(0),EMPTY,ready=True)


def test_public_live_gaps_preserve_prefix_generation_and_receipt_lease():
    now = [100]
    local = build(clock=lambda:now[0],other=True)
    start(local)
    initial_generation = local.stats['feeds'][PRODUCER]['generation']
    try:
        for number,event in enumerate(sequence(),1):
            now[0] += 4_000_000_000  # Every receipt, including gaps, must renew the five-second lease.
            frame = RecoveryFrame(cursor(number),event.model_dump_json().encode())
            receipt = local.on_frame(frame)
            assert receipt.cursor == cursor(number) and receipt.digest == frame.digest
            assert local.query_cursor(PRODUCER) == receipt
            feed = local.stats['feeds'][PRODUCER]
            assert feed['mode'] == 'LIVE' and feed['generation'] == initial_generation
            assert local.hot_path.apply_seq == number + (number > 5)
            if 3 <= number <= 6:
                snapshot = local.hot_path.snapshot(STAMP)
                assert snapshot.quality[INSTRUMENT.key] == 'INVALID'
                assert snapshot.latest_book(INSTRUMENT.key) is None
            if number == 5:
                # An unrelated provider progresses while this book remains invalid.
                other_zero = EngineCursor('independent','other',0)
                local.begin_recovery(other_zero,other_zero,verified_checkpoint=True)
                assert local.commit_recovery(other_zero,EMPTY,ready=True)
                event_other = sequence()[-1].model_copy(update={'provider':'binance-perp',
                    'venue':Venue.BINANCE,'event_id':'independent-trade',
                    'instrument':INSTRUMENT.model_copy(update={'venue':Venue.BINANCE})})
                target = EngineCursor('independent','other',1)
                local.on_frame(RecoveryFrame(target,event_other.model_dump_json().encode()))
                assert local.query_cursor('independent').cursor == target
            if number >= 7:
                assert local.hot_path.snapshot(STAMP).quality[INSTRUMENT.key] == 'VALID'
            # The unrelated fact adds one canonical apply to the global store cursor.
            if number >= 5:
                assert local.hot_path.apply_seq == number+1
            assert local.stats['queued_records'] == local.stats['queued_bytes'] == local.stats['delivered'] == 0
    finally: asyncio.run(local.close())


def test_default_live_gap_and_replay_guard_remain_strict():
    local,replay = build(False),build(False)
    events = sequence()
    try:
        start(local)
        for number,event in enumerate(events[:3],1):
            local.on_frame(RecoveryFrame(cursor(number),event.model_dump_json().encode()))
        assert local.query_cursor(PRODUCER).cursor == cursor(3)
        assert local.stats['feeds'][PRODUCER]['mode'] == 'INVALID'
        with pytest.raises(EngineError,match='live_input_before_verified_handoff'):
            local.on_frame(RecoveryFrame(cursor(4),events[3].model_dump_json().encode()))
        replay.begin_recovery(cursor(0),cursor(3),verified_checkpoint=True)
        for number,event in enumerate(events[:2],1):
            replay.apply_replay(RecoveryFrame(cursor(number),event.model_dump_json().encode()))
        with pytest.raises(EngineError,match='gap_inside_replay_range'):
            replay.apply_replay(RecoveryFrame(cursor(3),events[2].model_dump_json().encode()))
    finally:
        asyncio.run(local.close()); asyncio.run(replay.close())


@pytest.mark.asyncio
async def test_live_window32_real_uds_spool_preserves_all_gap_frames_without_reconnect(tmp_path):
    events = sequence()
    payloads = [event.model_dump_json().encode() for event in events]
    local = build(); start(local)
    cold = HotPathStore()
    for event in events: cold.apply_versioned(event)
    generation = local.stats['feeds'][PRODUCER]['generation']
    limits = TransportBudgets(8192,1024,64,524288,5,128,262144,2,5)
    endpoint = tmp_path/'uds'/'engine.sock'
    applied = []
    def on_frame(frame):
        receipt = local.on_frame(frame)
        applied.append((frame.cursor,frame.payload,receipt.digest))
        return receipt
    queries = []
    def query(high):
        queries.append(high)
        return local.begin_delivery_recovery(high)
    server = LocalTransportServer(endpoint,budgets=limits,allowed_uid=os.geteuid(),
        on_frame=on_frame,on_gap=local.on_gap,on_resume=local.request_resume,
        on_replay=local.apply_replay,on_recovery_query=query,
        on_recovery_commit=lambda h,d:local.commit_recovery(h,d,ready=True))
    await server.start()
    clients,acks,windows = [],[],[]
    class RecordingClient(LocalTransportClient):
        async def receive_ack(self):
            ack = await super().receive_ack()
            acks.append(ack)
            return ack
    def transport():
        client = RecordingClient(endpoint,producer=PRODUCER,incarnation=INC,budgets=limits,
            allowed_uid=os.geteuid(),on_gap=lambda *_:None)
        clients.append(client)
        return client
    class RecordingPublisher(SpoolingMarketPublisher):
        async def _wait_delivery_chunk(self,transport,frames,deadline,*,replay):
            windows.append((len(frames),replay))
            return await super()._wait_delivery_chunk(transport,frames,deadline,replay=replay)
    root = tmp_path/'spool'
    publisher = RecordingPublisher(producer=PRODUCER,incarnation=INC,
        spool_factory=lambda:MarketSpool(root,PRODUCER,INC,**LIMITS),transport=None,
        budgets=PipelineBudgets(64,524288,8192,32,1),env={},on_invalid=lambda _:None,
        resume_existing=True,delivery_budgets=DeliveryBudgets(32,262144,.001,.01,2,32),
        reader_factory=lambda c:SpoolReader(root,PRODUCER,INC,**READ),transport_factory=transport)
    async def until(predicate):
        async def wait():
            while not predicate(): await asyncio.sleep(.001)
        await asyncio.wait_for(wait(),3)
    try:
        await publisher.connect()
        await until(lambda:publisher.stats['delivery_state']=='LIVE')
        for payload in payloads: publisher.accept_payload(payload)
        await until(lambda:publisher.stats['last_applied_sequence']==32)
        assert windows == [(32,False)]
        assert len(clients)==1 and queries==[]
        assert local.stats['feeds'][PRODUCER]['generation']==generation
        assert local.stats['feeds'][PRODUCER]['mode']=='LIVE'
        assert publisher.stats['last_durable_sequence']==publisher.stats['last_applied_sequence']==32
        assert publisher.stats['failed']==0 and not publisher.stats['invalid']
        assert [p for _,p,_ in applied]==payloads
        assert [d for _,_,d in applied]==[hashlib.sha256(p).hexdigest() for p in payloads]
        for phase in ('RECEIVED','APPLIED'):
            assert [ack.cursor for ack in acks if ack.phase==phase]==[cursor(n) for n in range(1,33)]
        reader = SpoolReader(root,PRODUCER,INC,**READ)
        assert [row.payload for row in reader.read_batch(max_records=64,max_bytes=524288)]==payloads
        assert local.query_cursor(PRODUCER).cursor==cursor(32)
        assert local.query_cursor(PRODUCER).digest==hashlib.sha256(payloads[-1]).hexdigest()
        actual,expected = local.hot_path.snapshot(STAMP),cold.snapshot(STAMP)
        assert actual.books==expected.books and actual.quality==expected.quality
        assert local.hot_path.apply_seq==cold.apply_seq==32
        assert local.stats['queued_records']==local.stats['delivered']==0
    finally:
        await publisher.close(); await server.close(); await local.close()
