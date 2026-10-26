import json
import time
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from vibetrading.etf_journal import EtfJournal
from vibetrading.etf_trading import EtfTradingLoop
from vibetrading.kis_execution import KisCashBroker,KisOrderRejected,account_permission_rejection
from core.models import Side
from vibetrading.settings import RuntimeSettings


def settings(tmp_path):
    p=tmp_path/'.env'; p.write_text('',encoding='utf-8')
    env={}
    for scope in ['KIS','KIS_MOCK','KIS_MOCK2']:
        for key in ['APP_KEY','APP_SECRET','ACCOUNT_NUMBER']:
            env[scope+'_'+key]=scope+'-'+key
        env[scope+'_ACCOUNT_PRODUCT_CODE']='01'
    return replace(RuntimeSettings.from_env(p),data_root=tmp_path,state_dir=tmp_path/'state',env=env)


def test_mock2_credentials_and_tokens_are_isolated(tmp_path):
    s=settings(tmp_path); one=KisCashBroker(s,mode='mock'); two=KisCashBroker(s,mode='mock2')
    assert two.client.provider.values['KIS_APP_KEY']=='KIS_MOCK2-APP_KEY'
    assert two.client.endpoints.use_mock and two.tr('0012U')=='VTTC0012U'
    assert one.client._token_cache_path()!=two.client._token_cache_path()
    missing=replace(s,env={k:v for k,v in s.env.items() if not k.startswith('KIS_MOCK2_')})
    assert not KisCashBroker(missing,mode='mock2').client.has_credentials


class Broker:
    def __init__(self,deny=None,mode='mock'): self.calls=[]; self.deny=deny; self.mode=mode
    def account_params(self): return {'CANO':'001' if self.mode=='mock' else '002','ACNT_PRDT_CD':'01'}
    async def buying_power(self,*args,**kwargs): return {'max_buy_qty':'10000'}
    async def submit_order(self,intent):
        self.calls.append(('submit',intent.instrument.raw_symbol,intent.side))
        if self.deny: raise self.deny
        return {'ODNO':'same-number'}
    async def order_status(self,number,**kwargs):
        self.calls.append(('status',kwargs['symbol'],number))
        return {'tot_ccld_qty':'10','tot_ccld_amt':'1000','rmn_qty':'0'}


def loop(tmp_path,deny=None):
    x=EtfTradingLoop.__new__(EtfTradingLoop); x.settings=settings(tmp_path)
    x.journal=EtfJournal(tmp_path/'journal.sqlite3'); x.journal.initialize(Decimal(60000),'primary')
    x.broker=Broker(); x.brokers={'mock':x.broker,'mock2':Broker(deny,mode='mock2')}
    x._acquire_ownership(tmp_path/'authority')
    x.quotes={s:(Decimal(99),Decimal(100),time.time_ns()) for s in ['069500','122630','229200','251340']}
    x._day='2026-10-14'; x.phase='ready'; x.history=SimpleNamespace(day='')
    async def publish(intent): pass
    x.bus=SimpleNamespace(publish_order_intent=publish)
    return x


@pytest.fixture
def owned_loop(tmp_path):
    items = []
    def create(deny=None):
        item = loop(tmp_path,deny)
        items.append(item)
        return item
    yield create
    for item in items:
        item.journal.db.close()
        for lease in item._owner_leases.values(): lease.close()


@pytest.mark.asyncio
async def test_each_symbol_uses_correct_account_for_submit_and_fill_poll(tmp_path,owned_loop):
    x=owned_loop()
    for symbol in ['069500','122630','229200','251340']:
        await x.place('C3',symbol,Side.BUY,10,'decision:'+symbol)
    await x.reconcile()
    assert [c[1] for c in x.brokers['mock'].calls if c[0]=='submit']==['069500','229200']
    assert [c[1] for c in x.brokers['mock2'].calls if c[0]=='submit']==['122630','251340']
    assert [c[1] for c in x.brokers['mock2'].calls if c[0]=='status']==['122630','251340']
    assert x.journal.book('C3')['positions']=={s:10 for s in ['069500','122630','229200','251340']}


@pytest.mark.asyncio
async def test_permission_rejection_uses_captured_best_ask_without_real_fill(tmp_path,owned_loop):
    denied=KisOrderRejected('mock permission denial',code='40070000',business_message='모의투자 주문처리가 안되었습니다(매매불가 종목)')
    x=owned_loop(denied)
    await x.place('KP','122630',Side.BUY,10,'signal',direction=1)
    assert x.journal.book('KP')['positions']=={}
    assert x.journal.logical_book('KP')['positions']=={'122630':10}
    fill=x.journal.db.execute('SELECT * FROM assumed_fills').fetchone()
    assert Decimal(fill['price'])==100  # Captured ask, not limit ask+5.
    x.recover_assumptions(); assert x.journal.db.execute('SELECT COUNT(*) FROM assumed_fills').fetchone()[0]==1
    await x.exit('KP','stop')
    assert x.journal.assumed_positions()=={}
    assert len(x.brokers['mock2'].calls)==1  # No broker sell of imaginary shares.
    assert Decimal(x.journal.assumed_report(x._day)['net_pnl'])<0


@pytest.mark.asyncio
@pytest.mark.parametrize('error',[
    KisOrderRejected('market closed',code='40070000',business_message='주문 가능시간이 아닙니다'),
    KisOrderRejected('insufficient cash',code='40000000',business_message='증거금 부족'),
    RuntimeError('transport outcome unknown'),
])
async def test_market_or_uncertain_failures_never_create_assumed_fills(tmp_path,error,owned_loop):
    x=owned_loop(error)
    if isinstance(error,KisOrderRejected): await x.place('KP','122630',Side.BUY,10,'signal')
    else:
        with pytest.raises(RuntimeError): await x.place('KP','122630',Side.BUY,10,'signal')
    assert x.journal.assumed_positions()=={}


def test_assumed_sell_offsets_real_holding_without_corrupting_broker_book(tmp_path,owned_loop):
    x=owned_loop(); j=x.journal
    j.db.execute('INSERT INTO positions VALUES(?,?,?)',('KP','122630',10))
    j.assume_fill('denied-sell','KP','122630','sell',10,110,x._day,time.time_ns(),'permission denial')
    assert j.book('KP')['positions']=={'122630':10}
    assert j.logical_book('KP')['positions']=={}
    result=j.assumed_report(x._day,{'122630':100})
    assert Decimal(result['net_pnl'])==Decimal('99.837200')


@pytest.mark.asyncio
async def test_daily_report_sums_two_accounts_and_separates_counterfactual(tmp_path,monkeypatch,owned_loop):
    x=owned_loop(); x.today={}; x.history=SimpleNamespace(daily={})
    monkeypatch.setattr('vibetrading.etf_csv_sync.completed_session',lambda *args:True)
    async def one(): return {'summary':{'tot_evlu_amt':'20100','bfdy_tot_asst_evlu_amt':'20000','thdt_tlex_amt':'23'},'positions':[]}
    async def two(): return {'summary':{'tot_evlu_amt':'10050','bfdy_tot_asst_evlu_amt':'10000','thdt_tlex_amt':'7'},'positions':[]}
    x.brokers['mock'].account_state=one; x.brokers['mock2'].account_state=two
    x.journal.assume_fill('a','KP','122630','buy',10,100,x._day,1,'permission')
    x.journal.assume_fill('b','KP','122630','sell',10,110,x._day,2,'shadow exit')
    await x.report_daily(x._day,datetime(2026,10,14,15,35,tzinfo=ZoneInfo('Asia/Seoul')))
    data=json.loads(x.journal.db.execute("SELECT payload FROM notifications WHERE kind='mock_daily'").fetchone()[0])
    assert data['net_pnl']=='150' and data['fees']=='30'
    assert Decimal(data['assumed']['net_pnl'])==Decimal('99.689200')
    assert Decimal(data['adjusted_net_pnl'])==Decimal('249.689200')
    assert set(data['accounts'])=={'mock','mock2'}
