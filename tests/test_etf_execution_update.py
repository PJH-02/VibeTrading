"""Five-second partial-fill replacement and immutable CSV history boundaries."""
import hashlib
import json
import time
from decimal import Decimal

import pandas as pd
import pytest

from storage.etf_csv_history import CsvStrategyHistory, ETF_HISTORY_ROLES, CSV_FILES
from vibetrading.etf_journal import EtfJournal
from vibetrading.etf_trading import EtfTradingLoop
from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue


class AccountBroker:
    mode = 'mock'

    def account_params(self):
        return {'CANO':'001' if self.mode == 'mock' else '002','ACNT_PRDT_CD':'01'}


@pytest.fixture
def acquire_owner(tmp_path):
    owners = []
    def acquire(loop):
        secondary = type(loop.broker)()
        secondary.mode = 'mock2'
        loop.brokers = {'mock':loop.broker,'mock2':secondary}
        loop._acquire_ownership(tmp_path/'authority')
        owners.append(loop)
    yield acquire
    for loop in owners:
        for lease in loop._owner_leases.values(): lease.close()


def order(side=Side.BUY):
    return OrderIntent(intent_id='limit-1',job_id='C3',signal_id='s',destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.KIS,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='069500'),
        side=side,client_order_id='limit-1',quantity=Decimal(10),limit_price=Decimal(100),
        metadata={'direction':1,'safety_stop':90})


class Bus:
    async def publish_order_intent(self,intent):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize('side',[Side.BUY,Side.SELL])
async def test_cancel_fill_race_replaces_only_confirmed_remaining_once(tmp_path,side,acquire_owner):
    journal = EtfJournal(tmp_path/'journal.sqlite3')
    journal.initialize(Decimal(6000),'mock')
    if side==Side.SELL:
        seed = order().model_copy(update={'intent_id':'seed-buy','client_order_id':'seed-buy'})
        journal.stage(seed,'20261013',1,90)
        journal.accepted('seed-buy',{'ODNO':'SEED'})
        journal.apply_status('seed-buy',{'tot_ccld_qty':'10','tot_ccld_amt':'1000','rmn_qty':'0'})
    journal.stage(order(side),'20261013',1,90)
    journal.accepted('limit-1',{'ODNO':'L1','KRX_FWDG_ORD_ORGNO':'org'})
    journal.apply_status('limit-1',{'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6'})
    aged = time.time_ns()-6_000_000_000
    journal.db.execute('UPDATE orders SET created_ns=?,accepted_ns=?',(aged,aged))
    events = []
    class Broker(AccountBroker):
        calls = 1
        async def order_status(self,number,**kwargs):
            if number=='M1':
                return {'tot_ccld_qty':'5','tot_ccld_amt':'505','rmn_qty':'0'}
            self.calls += 1
            events.append('status')
            return {'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6'} if self.calls==1 else {'tot_ccld_qty':'5','tot_ccld_amt':'495','rmn_qty':'0','cncl_yn':'Y'}
        async def cancel_order(self,*args): events.append('cancel')
        async def buying_power(self,symbol,price,**kwargs):
            assert kwargs['market'] is True
            return {'max_buy_qty':'100','psbl_qty_calc_unpr':'130'}
        async def submit_order(self,intent):
            events.append('market')
            assert intent.limit_price is None and intent.quantity==5
            return {'ODNO':'M1'}
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.journal,loop.broker,loop.bus,loop.phase = journal,Broker(),Bus(),'ready'
    acquire_owner(loop)
    await loop.reconcile()
    assert events==['cancel','status','market']
    assert journal.book('C3')['positions']=={'069500':5}
    assert journal.book('KP')['cash']==2000
    assert journal.pending()[0]['order_type']=='market'
    await loop.reconcile()
    await loop.reconcile()
    assert events.count('market')==1
    assert journal.book('C3')['positions']==({'069500':10} if side==Side.BUY else {})
    assert journal.book('C3')['cash']==(Decimal('999.85000') if side==Side.BUY else Decimal('1999.70000'))
    assert journal.pending()==[]
    journal.db.close()


@pytest.mark.asyncio
async def test_no_market_before_five_seconds_or_without_cancel_confirmation(tmp_path,acquire_owner):
    journal = EtfJournal(tmp_path/'journal.sqlite3')
    journal.initialize(Decimal(6000),'mock')
    journal.stage(order(),'20261013',1,90)
    journal.accepted('limit-1',{'ODNO':'L1','KRX_FWDG_ORD_ORGNO':'org'})
    called = []
    class Broker(AccountBroker):
        async def order_status(self,*args,**kwargs):
            return {'tot_ccld_qty':'0','tot_ccld_amt':'0','rmn_qty':'10'}
        async def cancel_order(self,*args): called.append('cancel')
        async def submit_order(self,*args): raise AssertionError('unconfirmed cancellation cannot create market order')
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.journal,loop.broker,loop.bus,loop.phase = journal,Broker(),Bus(),'ready'
    acquire_owner(loop)
    await loop.reconcile()
    assert called==[]
    aged = time.time_ns()-6_000_000_000
    journal.db.execute('UPDATE orders SET created_ns=?,accepted_ns=?',(aged,aged))
    await loop.reconcile()
    assert called==['cancel']
    assert journal.pending()[0]['order_type']=='limit'
    assert journal.book('C3')['reserved']==1005
    journal.db.close()


def make_references(root):
    roles = list(CSV_FILES)
    manifest = {'files':{}}
    root.mkdir()
    (root/'raw').mkdir()
    for role in roles:
        symbol = next((s for s,pair in ETF_HISTORY_ROLES.items() if role in pair),'069500')
        price = {'122630':200,'251340':50}.get(symbol,100)
        data = f'timestamp,date,open,high,low,close,volume,symbol,source,timestamp_semantics\n2026-09-29 09:01:00,2026-09-29,{price},{price+1},{price-1},{price},1,{symbol},TOSS,bar_end\n'
        path = root/CSV_FILES[role]
        path.write_text(data,encoding='utf-8')
        manifest['files'][role] = {'path':CSV_FILES[role],'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'provider':'TOSS','last_date':'2026-09-29','rows':1,'bytes':path.stat().st_size}
    (root/'manifest.json').write_text(json.dumps(manifest))
    return CsvStrategyHistory(root)


def test_reference_csv_prices_and_clocks_are_not_rewritten_by_live_refresh(tmp_path):
    reference = make_references(tmp_path/'history')
    frame = reference.minute('069500_minute',{'2026-09-29'})
    assert frame.timestamp.iloc[0]==pd.Timestamp('2026-09-29 09:01')
    newer = pd.concat([frame.assign(close=999),frame.assign(date='2026-09-30',timestamp=pd.Timestamp('2026-09-30 09:01'),source='KIWOOM')])
    merged = reference.merge_minutes(frame,newer)
    assert merged.iloc[0]['close']==100 and len(merged)==2
    day = reference.daily('069500_daily','069500')
    refreshed = pd.concat([day.assign(close=999),day.assign(timestamp=pd.Timestamp('2026-09-30'),date='2026-09-30',close=102,source='KIWOOM')])
    result = reference.merge_daily(day,refreshed)
    assert list(result.close)==[100,102]


def test_changed_reference_csv_fails_checksum_guard(tmp_path):
    root = tmp_path/'history'
    make_references(root)
    (root/'raw/069500_daily.csv').write_text('changed',encoding='utf-8')
    with pytest.raises(RuntimeError,match='checksum'):
        CsvStrategyHistory(root)


def test_missing_leverage_or_inverse_history_is_not_a_complete_reference_bundle(tmp_path):
    root = tmp_path/'history'
    make_references(root)
    manifest = json.loads((root/'manifest.json').read_text())
    del manifest['files']['251340_minute']
    (root/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError,match='incomplete'):
        CsvStrategyHistory(root)


def test_execution_vehicles_have_independent_prior_prices_and_cannot_drive_signals(tmp_path):
    from vibetrading.etf_history import EtfHistory
    reference = make_references(tmp_path/'history')
    history = EtfHistory(None,tmp_path/'warmup')
    history.day = '2026-09-30'
    for symbol,(daily_role,minute_role) in ETF_HISTORY_ROLES.items():
        history.daily[symbol] = reference.daily(daily_role,symbol)
        history.minutes[symbol] = reference.minute(minute_role,{'2026-09-29'})
    assert history.vehicle_reference('122630')['minute_close']==200
    assert history.vehicle_reference('251340')['minute_close']==50
    assert history.vehicle_reference('069500')['minute_close']==100
    with pytest.raises(ValueError,match='unleveraged'):
        history.intraday_features('251340',history.minutes['251340'])
    # Today's close must never overwrite the order's historical reference.
    history.daily['122630'] = pd.concat([history.daily['122630'],history.daily['122630'].assign(
        date=history.day,timestamp=pd.Timestamp(history.day),close=999999)])
    assert history.vehicle_reference('122630')['previous_close']==200


@pytest.mark.asyncio
@pytest.mark.parametrize('strategy,direction,leverage,expected',[
    ('KP',1,.8,{'069500'}),('KP',1,1.5,{'069500','122630'}),('KP',1,2.,{'122630'}),
    ('KD',1,1.,{'229200'}),('KD',-1,1.,{'251340'}),
])
async def test_kp_leverage_and_kd_short_route_to_actual_vehicles(tmp_path,strategy,direction,leverage,expected,acquire_owner):
    from vibetrading.etf_history import EtfHistory
    reference = make_references(tmp_path/'history')
    history = EtfHistory(None,tmp_path/'warmup')
    history.day = '2026-09-30'
    for symbol,(daily_role,minute_role) in ETF_HISTORY_ROLES.items():
        history.daily[symbol] = reference.daily(daily_role,symbol)
        history.minutes[symbol] = reference.minute(minute_role,{'2026-09-29'})
    journal = EtfJournal(tmp_path/'journal.sqlite3')
    journal.initialize(Decimal(300000),'mock')
    intents = []
    class Broker(AccountBroker):
        async def buying_power(self,*args,**kwargs): return {'max_buy_qty':'100000'}
        async def submit_order(self,intent):
            intents.append(intent)
            return {'ODNO':str(len(intents)),'KRX_FWDG_ORD_ORGNO':'org'}
    class SignalBus(Bus):
        async def publish_signal(self,signal):
            assert signal.metadata['weights'].keys() >= expected
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.journal,loop.broker,loop.bus,loop.history = journal,Broker(),SignalBus(),history
    acquire_owner(loop)
    loop._day,loop.phase = history.day,'ready'
    loop.entry_blocked = lambda strategy:False
    loop.quotes = {s:(Decimal(100),Decimal(105),time.time_ns()) for s in ETF_HISTORY_ROLES}
    await loop.enter(strategy,direction,leverage,'signal',90 if strategy=='KD' else None)
    assert {i.instrument.raw_symbol for i in intents}==expected
    assert all(i.side==Side.BUY and i.metadata['direction']==direction for i in intents)
    assert all(i.metadata['vehicle_history']['symbol']==i.instrument.raw_symbol for i in intents)
    assert journal.book(strategy)['reserved']<=100000
    assert sum(r['status']=='pending' for r in journal.pending())==len(expected)
    journal.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('symbol',['122630','251340'])
async def test_live_vehicle_minutes_are_kept_without_changing_base_signals(symbol):
    from core.models import Bar1mEvent
    from feeds.providers.kiwoom import KST
    from datetime import datetime
    now = datetime.now(KST)
    close = now.replace(hour=9,minute=30,second=0,microsecond=0)
    bar = Bar1mEvent(instrument=InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,
        raw_symbol=symbol,std_symbol='KIWOOM:KRX:'+symbol),venue=Venue.KIWOOM,provider='kiwoom',
        trading_date=now.strftime('%Y-%m-%d'),source_ts_ns=int(close.timestamp()*1e9),
        open_ts_ns=int(close.timestamp()*1e9)-60_000_000_000,close_ts_ns=int(close.timestamp()*1e9),
        open=Decimal(200),high=Decimal(201),low=Decimal(199),close=Decimal(200),volume=Decimal(1))
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.today = {s:{} for s in ETF_HISTORY_ROLES}
    # No history/journal: touching the signal path on a vehicle bar would fail.
    await loop.event(bar.model_dump(mode='json'))
    assert len(loop.today[symbol])==1 and loop.today['069500']=={} and loop.today['229200']=={}


@pytest.mark.asyncio
async def test_c3_uses_native_day_opens_without_waiting_for_a_minute_close(tmp_path):
    from types import SimpleNamespace
    journal = EtfJournal(tmp_path/'journal.sqlite3')
    journal.initialize(Decimal(6000),'mock')
    seen = []
    class Client:
        async def post_api(self,path,api_id,body):
            return {'open_pric':'100' if api_id=='ka10001' else '200'}
    class History:
        def c3(self,etf_open,index_open):
            assert etf_open==100 and index_open==200
            return 1.,{'z':2.,'mis':-.1,'sigma':.3}
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.journal,loop.kiwoom,loop.history = journal,Client(),History()
    loop._day,loop._c3_attempt_ns = '2026-10-13',0
    loop.settings=SimpleNamespace(state_dir=tmp_path)
    (tmp_path/'kis-index').mkdir()
    (tmp_path/'kis-index/snapshot.json').write_text(
        json.dumps({'day':loop._day,'bstp_nmix_oprc':'200'}),encoding='utf-8')
    loop.quotes = {'069500':(Decimal(100),Decimal(105),time.time_ns())}
    async def enter(*args): seen.append(args)
    loop.enter = enter
    await loop.evaluate_c3()
    await loop.evaluate_c3()
    assert len(seen)==1 and seen[0][0]=='C3'
    journal.db.close()
