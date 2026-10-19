"""Guarded ETF submission uses fake adapters/HTTP and isolated journals only."""
import asyncio
import sqlite3
import time
from decimal import Decimal
from types import SimpleNamespace
from datetime import datetime

import pytest

from vibetrading.etf_journal import EtfJournal
from vibetrading.etf_trading import EtfDispatchBlocked, EtfTradingLoop
from vibetrading.kis_execution import KisCashBroker
from vibetrading.models import AssetClass, Bar1mEvent, BookUpdateType, DataGapEvent, InstrumentId, OrderBookEvent, OrderBookLevel, OrderDestination, OrderIntent, Side, TradeEvent, Venue
from vibetrading.providers.kiwoom import KST


class Broker:
    execution_permit_protocol = 1

    def __init__(self):
        self.before_permit = None
        self.after_handoff_error = None
        self.posts = []

    async def buying_power(self, *args, **kwargs):
        return {'max_buy_qty':'100000','psbl_qty_calc_unpr':'110'}

    async def submit_order(self, intent):
        raise AssertionError('guarded route cannot call the public legacy submit')

    async def submit_order_with_permit(self, intent, permit):
        await asyncio.sleep(0)
        if self.before_permit:
            self.before_permit()
        permit()
        self.posts.append(intent)
        if self.after_handoff_error:
            raise self.after_handoff_error
        return {'ODNO':str(len(self.posts))}


class Bus:
    def __init__(self):
        self.failure = None
        self.signals = []

    async def publish_order_intent(self, intent):
        if self.failure:
            raise self.failure

    async def publish_signal(self, signal):
        self.signals.append(signal)
        if self.failure:
            raise self.failure


@pytest.fixture
def loop(tmp_path):
    instance = EtfTradingLoop.__new__(EtfTradingLoop)
    state = tmp_path/'state'
    state.mkdir()
    with sqlite3.connect(state/'vibetrading.sqlite3') as db:
        db.execute('CREATE TABLE kill_switch(scope TEXT PRIMARY KEY,active INTEGER)')
    instance.settings = SimpleNamespace(state_dir=state,env={'ETF_EXECUTION_GUARDS':'true'})
    instance.journal = EtfJournal(state/'etf-mock/journal.sqlite3')
    instance.journal.initialize(Decimal(300000),'fixture-dual-mock')
    instance.brokers = {'mock':Broker(),'mock2':Broker()}
    instance.broker = instance.brokers['mock']
    instance.bus = Bus()
    instance.history = SimpleNamespace(day='',vehicle_reference=lambda symbol:{'symbol':symbol})
    instance._day, instance.phase = '2026-10-19','ready'
    instance.quotes = {symbol:(Decimal(100),Decimal(105),time.time_ns()) for symbol in ('069500','122630','229200','251340')}
    yield instance
    instance.journal.db.close()


def intent(identity='one', side=Side.BUY, *, expiry=None):
    return OrderIntent(intent_id=identity,job_id='C3',signal_id='signal',destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.KIS,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='069500'),
        side=side,client_order_id=identity,quantity=Decimal(2),limit_price=Decimal(110),
        metadata={'account_scope':'mock','valid_until_ts_ns':expiry or time.time_ns()+90_000_000_000})


def row(loop):
    return dict(loop.journal.db.execute('SELECT * FROM orders ORDER BY created_ns DESC LIMIT 1').fetchone())


@pytest.mark.asyncio
async def test_observational_bus_failure_still_submits_once_and_keeps_durable_evidence(loop):
    loop.bus.failure = RuntimeError('NATS offline')
    await loop.place('C3','069500',Side.BUY,2,'decision',direction=1)
    order = row(loop)
    assert order['status'] == 'pending'
    assert loop.journal.dispatch_state(order['intent_id']) == 'ACKED'
    evidence = loop.journal.db.execute('SELECT evidence,handoff_ns FROM dispatch_attempts').fetchone()
    assert '"valid_until_ts_ns":' in evidence[0] and evidence[1] is not None
    await loop.place('C3','069500',Side.BUY,2,'decision',direction=1)
    assert len(loop.broker.posts) == 1
    assert loop.journal.db.execute('SELECT COUNT(*) FROM dispatch_attempts').fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stop','global','mock2','ttl','quote'])
async def test_buy_rechecks_control_ttl_and_quote_after_adapter_await(loop, change):
    symbol = '122630' if change == 'mock2' else '069500'
    broker = loop.broker_for(symbol)

    def mutate():
        if change == 'stop':
            (loop.settings.state_dir/'etf-mock/STOP').touch()
        elif change in {'global','mock2'}:
            with sqlite3.connect(loop.settings.state_dir/'vibetrading.sqlite3') as db:
                db.execute('INSERT INTO kill_switch VALUES(?,1)',('global' if change == 'global' else 'account:kis-mock2',))
        elif change == 'ttl':
            # Preserve metadata as staged; advance the test clock instead.
            order = row(loop)
            deadline = OrderIntent.model_validate_json(order['payload']).metadata['valid_until_ts_ns']
            broker.test_clock.setattr(time,'time_ns',lambda:deadline)
        else:
            loop.quotes[symbol] = (Decimal(100),Decimal(105),time.time_ns()-11_000_000_000)

    broker.before_permit = mutate
    with pytest.MonkeyPatch.context() as clock:
        broker.test_clock = clock
        with pytest.raises(EtfDispatchBlocked):
            await loop.place('C3',symbol,Side.BUY,2,'decision',direction=1)
    order = row(loop)
    assert order['status'] == 'blocked' and Decimal(order['reserve']) == 0
    assert loop.journal.dispatch_state(order['intent_id']) == 'NOT_SENT'
    assert broker.posts == []


@pytest.mark.asyncio
async def test_full_commit_delay_cannot_extend_dispatch_ttl(loop, monkeypatch):
    original = loop.journal.dispatch_handoff

    def slow_commit(identity):
        original(identity)
        deadline = OrderIntent.model_validate_json(row(loop)['payload']).metadata['valid_until_ts_ns']
        monkeypatch.setattr(time,'time_ns',lambda:deadline)

    monkeypatch.setattr(loop.journal,'dispatch_handoff',slow_commit)
    with pytest.raises(EtfDispatchBlocked,match='TTL'):
        await loop.place('C3','069500',Side.BUY,2,'decision',direction=1)
    assert loop.broker.posts == []
    assert loop.journal.dispatch_state(row(loop)['intent_id']) == 'NOT_SENT'


@pytest.mark.asyncio
async def test_sell_is_reduce_only_but_allowed_while_halted(loop):
    loop.journal.db.execute('INSERT INTO positions VALUES(?,?,?)',('C3','069500',3))
    (loop.settings.state_dir/'etf-mock/STOP').touch()
    await loop.place('C3','069500',Side.SELL,2,'exit')
    assert len(loop.broker.posts) == 1
    with pytest.raises(EtfDispatchBlocked,match='unreserved owned'):
        await loop.place('C3','069500',Side.SELL,2,'second-exit')
    assert len(loop.broker.posts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['legacy','inherited'])
async def test_adapter_without_concrete_paired_capability_is_blocked(loop, kind):
    class Legacy:
        async def buying_power(self,*args,**kwargs):
            return {'max_buy_qty':'100'}

        async def submit_order(self,*args):
            raise AssertionError('unsupported adapter must never submit')

    class Inherited(Broker):
        pass

    loop.broker = Legacy() if kind == 'legacy' else Inherited()
    loop.brokers['mock'] = loop.broker
    with pytest.raises(EtfDispatchBlocked,match='paired dispatch permit'):
        await loop.place('C3','069500',Side.BUY,2,'decision')
    assert loop.journal.dispatch_state(row(loop)['intent_id']) == 'NOT_SENT'


@pytest.mark.asyncio
async def test_transport_loss_after_handoff_stays_unknown_across_restart(loop):
    loop.broker.after_handoff_error = asyncio.TimeoutError('ACK lost')
    with pytest.raises(asyncio.TimeoutError):
        await loop.place('C3','069500',Side.BUY,2,'decision')
    order = row(loop)
    assert order['status'] == 'unknown' and Decimal(order['reserve']) > 0
    assert loop.journal.dispatch_state(order['intent_id']) == 'UNKNOWN'
    path = loop.settings.state_dir/'etf-mock/journal.sqlite3'
    loop.journal.db.close()
    loop.journal = EtfJournal(path)
    loop.journal.recover_dispatch_attempts()
    await loop.place('C3','069500',Side.BUY,2,'decision')
    assert len(loop.broker.posts) == 1
    assert row(loop)['status'] == 'unknown'


@pytest.mark.parametrize('handoff', [False,True])
def test_restart_distinguishes_committed_prepared_from_handoff_without_resubmit(loop, handoff):
    value = intent()
    loop.journal.stage(value,'20261019',1,None,dispatch_evidence=loop._dispatch_evidence(value))
    if handoff:
        loop.journal.dispatch_handoff(value.intent_id)
    loop.journal.recover_dispatch_attempts()
    order = row(loop)
    assert loop.journal.dispatch_state(value.intent_id) == ('UNKNOWN' if handoff else 'NOT_SENT')
    assert order['status'] == ('unknown' if handoff else 'blocked')
    assert (Decimal(order['reserve']) > 0) is handoff
    assert not loop.journal.stage(value,'20261019',1,None,dispatch_evidence=loop._dispatch_evidence(value))


def test_dispatch_evidence_failure_rolls_back_stage_and_reservation(loop):
    loop.journal.db.execute("CREATE TRIGGER reject_evidence BEFORE INSERT ON dispatch_attempts BEGIN SELECT RAISE(ABORT,'injected'); END")
    value = intent()
    with pytest.raises(sqlite3.IntegrityError,match='injected'):
        loop.journal.stage(value,'20261019',1,None,dispatch_evidence=loop._dispatch_evidence(value))
    assert loop.journal.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0
    assert loop.journal.book('C3')['reserved'] == 0
    assert loop.journal.book('C3')['entries'] == 0


@pytest.mark.asyncio
async def test_original_signal_ttl_is_propagated_without_extension(loop):
    loop.bus.failure = RuntimeError('observations unavailable')
    await loop.enter('C3',1,1.0,'decision')
    signal = loop.bus.signals[0]
    assert loop.broker.posts
    assert all(value.metadata['valid_until_ts_ns'] == signal.valid_until_ts_ns for value in loop.broker.posts)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['halt','expired'])
async def test_market_replacement_reuses_expiry_and_checks_final_buy_gate(loop, fault):
    value = intent(expiry=time.time_ns()-1 if fault == 'expired' else None)
    loop.journal.stage(value,'20261019',1,None,dispatch_evidence=loop._dispatch_evidence(value))
    loop.journal.accepted(value.intent_id,{'ODNO':'original'})
    loop.journal.apply_status(value.intent_id,{'tot_ccld_qty':'1','tot_ccld_amt':'100','rmn_qty':'0','cncl_yn':'Y'})
    loop.journal.db.execute('UPDATE orders SET fallback_requested=1 WHERE intent_id=?',(value.intent_id,))
    original = dict(loop.journal.db.execute('SELECT * FROM orders WHERE intent_id=?',(value.intent_id,)).fetchone())
    if fault == 'halt':
        loop.broker.before_permit = lambda:(loop.settings.state_dir/'etf-mock/STOP').touch()
    with pytest.raises(EtfDispatchBlocked):
        await loop.replace_with_market(original)
    replacement = row(loop)
    assert replacement['intent_id'] != original['intent_id']
    assert loop.journal.dispatch_state(replacement['intent_id']) == 'NOT_SENT'
    assert OrderIntent.model_validate_json(replacement['payload']).metadata['valid_until_ts_ns'] == value.metadata['valid_until_ts_ns']
    assert loop.broker.posts == []
    await loop.replace_with_market(original)
    assert loop.broker.posts == []


@pytest.mark.asyncio
@pytest.mark.parametrize('expire', [False,True])
async def test_kis_permit_runs_after_token_and_session_enter_before_post(loop, monkeypatch, expire):
    trace = []
    value = intent()
    loop.journal.stage(value,'20261019',1,None,dispatch_evidence=loop._dispatch_evidence(value))

    async def token():
        trace.append('token')
        if expire:
            monkeypatch.setattr(time,'time_ns',lambda:value.metadata['valid_until_ts_ns'])
        return 'fixture-token',None

    async def safe_json(response):
        return {'rt_cd':'0','output':{'ODNO':'fixture'}}

    class Response:
        status, headers = 200, {}

        async def __aenter__(self):
            return self

        async def __aexit__(self,*args):
            pass

    class Session:
        def __init__(self,*args,**kwargs):
            pass

        async def __aenter__(self):
            trace.append('session')
            return self

        async def __aexit__(self,*args):
            pass

        def post(self,*args,**kwargs):
            assert loop.journal.dispatch_state(value.intent_id) == 'HANDOFF'
            trace.append('post')
            return Response()

    broker = KisCashBroker.__new__(KisCashBroker)
    broker.mode, broker._lock, broker._next_request_at = 'mock',asyncio.Lock(),0
    broker.client = SimpleNamespace(provider=SimpleNamespace(values={'KIS_ACCOUNT_NUMBER':'fixture','KIS_ACCOUNT_PRODUCT_CODE':'01'}),
        issue_access_token=token,_base_headers=lambda:{},_app_key=lambda:'fake',_app_secret=lambda:'fake',
        endpoints=SimpleNamespace(rest_base_url='https://invalid.example'),_safe_json=safe_json)
    monkeypatch.setattr('vibetrading.kis_execution.aiohttp.ClientSession',Session)
    if expire:
        with pytest.raises(EtfDispatchBlocked,match='TTL'):
            await loop._submit_staged(broker,value)
        assert trace == ['token','session']
        assert loop.journal.dispatch_state(value.intent_id) == 'NOT_SENT'
    else:
        assert (await loop._submit_staged(broker,value))['ODNO'] == 'fixture'
        assert trace == ['token','session','post']


@pytest.mark.asyncio
async def test_gap_requires_explicit_causal_coverage_after_snapshot_and_prior_history(loop):
    loop._day = datetime.now(KST).strftime('%Y-%m-%d')
    instrument = InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='KIWOOM:KRX:069500')
    loop.features = {'069500':{'usable':True}}
    loop.today = {'069500':{1:{'old':'pre-gap'}}}
    gap = DataGapEvent(instrument=instrument,venue=Venue.KIWOOM,provider='local-engine',reason='bounded fanout overflow')
    await loop.event(gap.model_dump(mode='json'))
    assert '069500' not in loop.quotes and loop.features == {}
    assert loop.today['069500'] == {}
    with pytest.raises(EtfDispatchBlocked,match='snapshot and history'):
        await loop.place('C3','122630',Side.BUY,1,'decision')
    book = OrderBookEvent(instrument=instrument,venue=Venue.KIWOOM,provider='kiwoom',depth=1,
        trading_date=loop._day,bids=[OrderBookLevel(price=Decimal(100),quantity=Decimal(1))],
        asks=[OrderBookLevel(price=Decimal(105),quantity=Decimal(1))],fast=True)
    delta = book.model_copy(update={'update_type':BookUpdateType.DELTA})
    await loop.event(delta.model_dump(mode='json'))
    assert '069500' not in loop.quotes
    await loop.event(book.model_copy(update={'fast':False}).model_dump(mode='json'))
    assert '069500' not in loop.quotes
    await loop.event(book.model_dump(mode='json'))
    assert loop._feed_entry_blocked('C3')
    assert not loop._feed_entry_blocked('KD')

    async def prepare(day):
        loop.history.day = day

    loop.history.prepare = prepare
    await loop.prepare(loop._day)
    assert loop._feed_entry_blocked('C3')
    assert loop.phase == 'feed_recovery_required'
    verifications = []

    def verified(symbol,generation):
        verifications.append((symbol,generation))
        return True

    loop._gap_recovery_verifier = verified
    loop._restore_feed_readiness()
    assert verifications == [('069500',loop._feed_gap_generation)]
    assert not loop._feed_entry_blocked('C3')
    assert loop.phase == 'ready'


@pytest.mark.asyncio
@pytest.mark.parametrize('initialized',[False,True])
async def test_injected_source_never_connects_nats_and_failure_invalidates_quotes(loop, monkeypatch, initialized):
    processed = asyncio.Event()
    calls = []
    initializations = []

    async def source():
        yield {'fixture':'event'}
        await processed.wait()
        raise RuntimeError('source failed')

    async def initialize():
        initializations.append(True)

    async def timer():
        await asyncio.Event().wait()

    async def event(data):
        calls.append(data)
        processed.set()

    async def close():
        calls.append('bus closed')

    async def forbidden(*args,**kwargs):
        raise AssertionError('injected source must not connect NATS')

    def queue_forbidden(*args,**kwargs):
        raise AssertionError('injected source cannot allocate another market queue')

    loop._event_source = source()
    loop.initialize, loop.timer, loop.event = initialize,timer,event
    loop.bus.close = close
    monkeypatch.setattr('vibetrading.etf_trading.nats.connect',forbidden)
    monkeypatch.setattr('vibetrading.etf_trading.asyncio.Queue',queue_forbidden)
    with pytest.raises(RuntimeError,match='source failed'):
        await asyncio.wait_for(loop.run(initialized=initialized),timeout=3)
    assert len(initializations) == (0 if initialized else 1)
    assert calls == [{'fixture':'event'},'bus closed']
    assert loop.quotes == {}
    assert loop._feed_entry_blocked('C3') and loop._feed_entry_blocked('KD')


@pytest.mark.asyncio
async def test_injected_source_quiet_timeouts_preserve_one_read_and_cancel_closes_source(loop):
    gate, started, received, closed = (asyncio.Event() for _ in range(4))

    async def source():
        try:
            started.set()
            await gate.wait()
            yield {'fixture':'after quiet'}
            await asyncio.Event().wait()
        finally:
            closed.set()

    async def initialize():
        raise AssertionError('initialized=True cannot initialize again')

    async def timer():
        await asyncio.Event().wait()

    async def event(data):
        assert data == {'fixture':'after quiet'}
        received.set()

    async def close():
        pass

    loop._event_source = source()
    loop.initialize, loop.timer, loop.event = initialize,timer,event
    loop.bus.close = close
    task = asyncio.create_task(loop.run(initialized=True))
    try:
        await asyncio.wait_for(started.wait(),timeout=1)
        # Cross two production quiet intervals. Cancelling each anext on a
        # timeout would already have closed the async generator here.
        await asyncio.sleep(2.1)
        assert not closed.is_set() and not task.done()
        gate.set()
        await asyncio.wait_for(received.wait(),timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert closed.is_set() and loop.quotes == {}
    assert loop._feed_entry_blocked('C3') and loop._feed_entry_blocked('KD')


@pytest.mark.asyncio
async def test_state_only_replay_restores_intraday_bars_without_live_or_order_side_effects():
    # Deliberately no journal, bus, lock, history or settings. Any strategy or
    # order path access during replay fails instead of being hidden by fakes.
    replay = EtfTradingLoop.__new__(EtfTradingLoop)
    replay.today = {'069500':{},'122630':{}}
    replay.quotes = {'069500':('unchanged',)}
    replay.features = {'069500':{'usable':True}}
    now = datetime.now(KST)
    close_ns = int(now.replace(hour=9,minute=30,second=0,microsecond=0).timestamp()*1e9)
    instrument = InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='KIWOOM:KRX:069500')
    bar = Bar1mEvent(instrument=instrument,venue=Venue.KIWOOM,provider='kiwoom',trading_date=now.strftime('%Y-%m-%d'),
        open_ts_ns=close_ns-60_000_000_000,close_ts_ns=close_ns,
        open=Decimal(100),high=Decimal(105),low=Decimal(99),close=Decimal(103),volume=Decimal(10))
    await replay.event(bar.model_dump(mode='json'),state_only=True)
    result = replay.today['069500'][bar.open_ts_ns]
    assert {key:result[key] for key in ('open','high','low','close','volume')} == {'open':100.,'high':105.,'low':99.,'close':103.,'volume':10.}
    assert result['timestamp'].strftime('%H:%M') == '09:30' and result['source'] == 'KIWOOM'
    vehicle = bar.model_copy(update={'instrument':instrument.model_copy(update={'raw_symbol':'122630','std_symbol':'KIWOOM:KRX:122630'})})
    await replay.event(vehicle.model_dump(mode='json'),state_only=True)
    assert len(replay.today['122630']) == 1
    for event in (
        TradeEvent(instrument=instrument,venue=Venue.KIWOOM,provider='kiwoom',price=Decimal(100),quantity=Decimal(1)),
        OrderBookEvent(instrument=instrument,venue=Venue.KIWOOM,provider='kiwoom',depth=1,
            bids=[OrderBookLevel(price=Decimal(100),quantity=Decimal(1))],asks=[OrderBookLevel(price=Decimal(105),quantity=Decimal(1))]),
        DataGapEvent(instrument=instrument,venue=Venue.KIWOOM,provider='local-engine',reason='historical gap'),
    ):
        await replay.event(event.model_dump(mode='json'),state_only=True)
    assert replay.quotes == {'069500':('unchanged',)}
    assert replay.features == {'069500':{'usable':True}}
    assert len(replay.today['069500']) == 1 and not hasattr(replay,'_feed_invalid')
    invalid = bar.model_copy(update={'provider':'unapproved','open_ts_ns':bar.open_ts_ns+60_000_000_000,'close_ts_ns':bar.close_ts_ns+60_000_000_000})
    await replay.event(invalid.model_dump(mode='json'),state_only=True)
    assert len(replay.today['069500']) == 1
