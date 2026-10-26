"""Scope guards, broker account isolation and durable partial-fill accounting."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from vibetrading.etf_journal import EtfJournal
from vibetrading.etf_trading import intraday_decision
from vibetrading.kis_execution import KisCashBroker
from core.models import AssetClass, EtfNavEvent, InstrumentId, OrderDestination, OrderIntent, Side, Venue, event_from_dict
from storage.parquet_store import _schema, flatten_event, ParquetBatchWriter
from storage.sqlite_store import RuntimeStore
from feeds.providers.kiwoom import KST, KiwoomCollector, parse_kiwoom_symbols
from feeds.providers.kis_client import KisAuthClient
from vibetrading.settings import RuntimeSettings
from feeds.universe_config import configured_symbols


def settings(tmp_path):
    path = tmp_path/'.env'
    path.write_text(f'VIBETRADING_DATA_ROOT={tmp_path.as_posix()}\nKIS_APP_KEY=real\nKIS_APP_SECRET=real-secret\nKIS_ACCOUNT_NUMBER=real-account\nKIS_ACCOUNT_PRODUCT_CODE=01\nKIS_MOCK_APP_KEY=mock\nKIS_MOCK_APP_SECRET=mock-secret\nKIS_MOCK_ACCOUNT_NUMBER=mock-account\nKIS_MOCK_ACCOUNT_PRODUCT_CODE=01\n')
    return RuntimeSettings.from_env(path)


def intent(identity='one',side=Side.BUY,quantity=10,strategy='C3'):
    return OrderIntent(intent_id=identity,job_id=strategy,signal_id=identity,destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.KIS,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='069500'),
        side=side,client_order_id=identity,quantity=Decimal(quantity),limit_price=Decimal(100))


def test_mock_never_uses_real_credentials_and_caches(tmp_path):
    real = KisAuthClient(settings(tmp_path),mode='real')
    mock = KisAuthClient(settings(tmp_path),mode='mock')
    assert real._app_key() == 'real' and mock._app_key() == 'mock'
    assert real._token_cache_path() != mock._token_cache_path()
    assert 'openapivts' in mock.endpoints.rest_base_url
    missing = tmp_path/'missing.env'
    missing.write_text('KIS_APP_KEY=real\nKIS_APP_SECRET=secret\n')
    no_mock = KisAuthClient(RuntimeSettings.from_env(missing),mode='mock')
    assert not no_mock.has_credentials


def test_kis_cache_expiry_uses_kst_even_on_utc_hosts(tmp_path):
    client = KisAuthClient(settings(tmp_path),mode='mock')
    client._write_cached_access_token('expired',(datetime.now(timezone.utc)+timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S'))
    assert client._read_cached_access_token() is None
    client._write_cached_access_token('valid',(datetime.now(timezone.utc)+timedelta(hours=10)).strftime('%Y-%m-%d %H:%M:%S'))
    assert client._read_cached_access_token()[0]=='valid'


@pytest.mark.asyncio
async def test_mock_and_real_order_tr_ids_and_integer_validation(tmp_path):
    for mode,prefix in [('mock','VTTC'),('real','TTTC')]:
        broker = KisCashBroker(settings(tmp_path),mode=mode)
        called = []
        async def request(method,path,tr_id,data):
            called.append((method,path,tr_id,data))
            return {'body':{'output':{'ODNO':'1'}}}
        broker.request = request
        await broker.submit_order(intent())
        await broker.submit_order(intent('sell',Side.SELL))
        assert called[0][2] == prefix+'0012U'
        assert called[1][2] == prefix+'0011U'
        assert called[0][3]['CANO'] == mode+'-account'
        with pytest.raises(ValueError):
            await broker.submit_order(intent(quantity=Decimal('0.5')))


def test_partial_fills_are_idempotent_and_budget_is_independent(tmp_path):
    journal = EtfJournal(tmp_path/'book.sqlite3')
    journal.initialize(Decimal(6000),'mock-account')
    assert all(b['cash']==2000 for b in journal.summary().values())
    original = intent()
    assert journal.stage(original,'20261013',1,None)
    assert not journal.stage(original,'20261013',1,None)
    assert journal.book('C3')['positions'] == {}
    assert journal.book('C3')['reserved'] == 1005
    journal.accepted('one',{'ODNO':'1'})
    fill = {'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6'}
    journal.apply_status('one',fill)
    journal.apply_status('one',fill)
    assert journal.book('C3')['positions']=={'069500':4}
    assert journal.book('KP')['cash']==2000
    assert journal.book('C3')['cash']==Decimal('1603.94060')
    journal.apply_status('one',fill|{'cncl_yn':'Y','rmn_qty':'0'})
    assert journal.book('C3')['reserved']==0
    with pytest.raises(RuntimeError,match='exceeds'):
        journal.stage(intent('too-big',quantity=50),'20261013',1,None)
    with pytest.raises(RuntimeError,match='owned'):
        journal.stage(intent('oversell',Side.SELL,5),'20261013',0,None)
    journal.db.close()


def test_uncertain_submit_survives_restart_with_cash_reserved(tmp_path):
    path = tmp_path/'book.sqlite3'
    journal = EtfJournal(path)
    journal.initialize(Decimal(6000),'mock-account')
    journal.stage(intent(),'20261013',1,None)
    journal.ambiguous('one')
    journal.db.close()
    journal = EtfJournal(path)
    assert journal.pending()[0]['status']=='unknown'
    assert journal.book('C3')['reserved']==1005
    with pytest.raises(RuntimeError,match='another'):
        journal.initialize(Decimal(6000),'other-account')
    journal.db.close()


def test_etf_nav_is_native_typed_and_roundtrips_to_parquet():
    collector = KiwoomCollector(parse_kiwoom_symbols('069500,005930'),publish_rest_snapshot=False)
    registration = collector.realtime_registration()
    assert registration[1]=={'item':['069500'],'type':['0G']}
    event = collector.parse_ws_frame(json.dumps({'trnm':'REAL','data':[{'item':'069500','type':'0G','values':{'36':'+7488.27','20':'100000','265':'+10.04','266':'-0.04'}}]}))[0]
    assert isinstance(event,EtfNavEvent) and event.nav==Decimal('7488.27')
    assert event.etf_disparity_pct==Decimal('-.04')
    assert event.instrument.asset_class==AssetClass.ETF
    assert isinstance(event_from_dict(event.model_dump(mode='json')),EtfNavEvent)
    assert 'nav' in _schema('etf_nav').names and Decimal(flatten_event(event)['nav'])==Decimal('7488.27')


def test_minute_start_labels_and_unfinished_bar_rejection():
    collector = KiwoomCollector(parse_kiwoom_symbols('069500'))
    row = {'cntr_tm':'20261013090000','open_pric':'-100','high_pric':'-105','low_pric':'-99','cur_prc':'-102','trde_qty':'10'}
    clock = int(datetime(2026,10,13,9,1,tzinfo=KST).timestamp()*1e9)
    event = collector.bar_event_from_rest(collector.subscriptions[0],row,now_ns=clock)
    assert event.close_ts_ns-event.open_ts_ns==60_000_000_000
    assert event.close==102 and event.volume==10
    assert collector.bar_event_from_rest(collector.subscriptions[0],row,now_ns=clock-1) is None


def test_frozen_kp_kd_sizing_and_stops():
    book = {'direction':0,'positions':{},'entries':0}
    feature = {'usable':True,'entry_signal':-1,'vol14':.01,'signal_strength':1.2,'upper_band':102,'lower_band':98,'vwap_used':99,'price':97}
    assert intraday_decision('KP',feature,book)[2]=='no_entry'
    assert intraday_decision('KD',feature,book)[:2]==(-1,1.)
    assert intraday_decision('KP',feature|{'entry_signal':1},book)[:2]==(1,2.)
    assert intraday_decision('KD',feature|{'price':99},book|{'direction':-1,'positions':{'251340':1}})[2]=='paper_stop'
    assert intraday_decision('KD',feature,book|{'entries':1})[2]=='no_entry'


def test_collection_scope_contains_all_seven_etfs_and_no_retired_symbols():
    assert set(configured_symbols('kiwoom')) >= {'069500','122630','114800','252670','229200','233740','251340'}
    assert configured_symbols('binance') == []
    assert configured_symbols('hyperliquid') == []


@pytest.mark.asyncio
async def test_kosdaq_master_alias_uses_index_future_trs_not_night_options(tmp_path,monkeypatch):
    from feeds.providers.kis import KisCollector
    from feeds.providers.kis_master import KisMasterDownloader
    from feeds.providers.universe import ListedContract, InstrumentUniverseResolver
    from feeds.providers import universe
    contracts = [ListedContract('F'+month,'F'+month,AssetClass.FUTURE,Venue.KIS,
        month[:4]+'-'+month[4:],month,underlying='KSQ150') for month in ('209912','210003')]
    contracts.append(ListedContract('OPTION','OPTION',AssetClass.OPTION,Venue.KIS,
        '2099-12','209912',strike=Decimal(100),option_right='CALL',underlying='KSQ150'))
    async def master(self): return contracts
    monkeypatch.setattr(KisMasterDownloader,'index_derivatives',master)
    monkeypatch.setattr(universe,'get_settings',lambda:settings(tmp_path))
    resolved = await InstrumentUniverseResolver().resolve_kis_index_derivatives(underlying='KOSDAQ150')
    assert len(resolved['futures'])==2 and resolved['options']==[]
    collector = KisCollector(auto_index_derivatives=True,index_underlyings=('KOSDAQ150',))
    subscriptions = await collector._resolve_index_derivative_subscriptions()
    assert {s.tr_id for s in subscriptions}=={'H0IFCNT0','H0IFASP0'}
    assert len(subscriptions)==4


def test_nav_is_committed_as_decimal_parquet(tmp_path):
    import pyarrow.parquet as pq
    runtime = settings(tmp_path)
    store = RuntimeStore(runtime,tmp_path/'manifest.sqlite3')
    writer = ParquetBatchWriter(runtime,store)
    event = EtfNavEvent(instrument=InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='KIWOOM:KRX:069500'),
        venue=Venue.KIWOOM,provider='kiwoom',source_ts_ns=1,nav=Decimal('12345.67'),etf_disparity_pct=Decimal('-.23'))
    writer.add(event)
    paths = writer.flush_all()
    rows = pq.ParquetFile(paths[0]).read().to_pylist()
    assert rows[0]['nav']==Decimal('12345.67')
    assert rows[0]['etf_disparity_pct']==Decimal('-.23')
    assert store.list_manifest()[0]['event_type']=='etf_nav'
    store.close()


def test_native_history_features_exclude_future_prices_and_incomplete_windows(tmp_path):
    import pandas as pd
    from vibetrading.etf_history import EtfHistory
    history = EtfHistory(None,tmp_path/'history')
    dates = pd.bdate_range('2026-08-01',periods=25)
    history.day = str(dates[-1].date())
    daily = pd.DataFrame([{'date':str(d.date()),'timestamp':d,'symbol':'069500','source':'KIWOOM','open':100+i,'high':103+i,'low':99+i,'close':101+i,'volume':1000} for i,d in enumerate(dates)])
    minute = []
    for i,day in enumerate(dates):
        for index,timestamp in enumerate(pd.date_range(day+pd.Timedelta(hours=9,minutes=1),periods=30,freq='min')):
            minute.append({'date':str(day.date()),'timestamp':timestamp,'symbol':'069500','source':'KIWOOM','open':100+i,'high':103+i,'low':99+i,'close':102+i,'volume':1,'trading_value':float('nan')})
    all_minutes = pd.DataFrame(minute)
    history.daily['069500']=daily
    history.minutes['069500']=all_minutes[all_minutes.date<history.day]
    today = all_minutes[all_minutes.date==history.day]
    first = history.intraday_features('069500',today)
    assert first.usable.all() and len(first)==1
    history.daily['069500'].loc[history.daily['069500'].date==history.day,'close']=999999
    second = history.intraday_features('069500',today)
    assert first.noise.iloc[0]==second.noise.iloc[0] and first.vol14.iloc[0]==second.vol14.iloc[0]
    assert history.intraday_features('069500',today.iloc[:-1]).empty
    empty = today.copy()
    empty['volume']=0
    assert history.intraday_features('069500',empty).empty


@pytest.mark.asyncio
async def test_bus_blocks_retired_events_and_publishes_etf_live_copy(tmp_path):
    from vibetrading.bus import NatsJetStreamBus
    from core.models import TradeEvent, utc_now_ns
    bus = NatsJetStreamBus(settings(tmp_path))
    published = []
    live = []
    async def publish(subject,payload,message_id=None): published.append(subject)
    class Connection:
        async def publish(self,subject,payload): live.append(subject)
    bus._publish=publish
    bus._nc=Connection()
    event = TradeEvent(instrument=InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.ETF,raw_symbol='069500',std_symbol='KIWOOM:KRX:069500'),
        venue=Venue.KIWOOM,provider='kiwoom',source_ts_ns=utc_now_ns(),price=Decimal(100),quantity=Decimal(1))
    await bus.publish_market_event(event)
    assert len(published)==1 and live==['live.'+published[0]]
    retired = event.model_copy(update={'provider':'bybit-perp'})
    await bus.publish_market_event(retired)
    option = event.model_copy(update={'venue':Venue.KIS,'provider':'kis','instrument':event.instrument.model_copy(update={'venue':Venue.KIS,'asset_class':AssetClass.OPTION})})
    await bus.publish_market_event(option)
    assert len(published)==1
