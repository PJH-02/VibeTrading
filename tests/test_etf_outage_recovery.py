import json
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from portfolio.etf_journal import EtfJournal
from vibetrading.etf_recovery import replay, start_books, apply_plan, digest, normalized_toss
from portfolio.etf_recovery_ledger import record_fill, filter_account_report
from core.models import OrderIntent, OrderDestination, InstrumentId, AssetClass, Venue, Side, EventType
from vibetrading.bus import allowed_supplement
from feeds.kis_index import normalize_index_minutes, index_bar
from feeds.providers.kis import kis_row_to_event
from vibetrading.trade_reporting import report_policy, order_key

DAY='2026-10-15'


def intent(identity='late', side=Side.BUY, qty=10):
    return OrderIntent(intent_id=identity,job_id='KD',signal_id='s',destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.KIS,asset_class=AssetClass.ETF,raw_symbol='251340',std_symbol='251340'),
        side=side,client_order_id=identity,quantity=Decimal(qty),limit_price=Decimal(60),
        metadata={'direction':-1,'account_scope':'mock2','recovery_cleanup':identity=='cleanup'})


def fill_actual(journal, identity, side=Side.BUY, qty=10, amount=600):
    journal.stage(intent(identity,side,qty),'20261015',-1,102.5)
    journal.accepted(identity,{'ODNO':identity,'KRX_FWDG_ORD_ORGNO':'org'})
    journal.apply_status(identity,{'tot_ccld_qty':str(qty),'tot_ccld_amt':str(amount),'rmn_qty':'0'})


def data():
    times=pd.date_range(DAY+' 09:01',DAY+' 09:32',freq='min')
    rows={}
    for symbol in ['069500','122630','229200','251340']:
        values=[]
        for stamp in times:
            price=50 if symbol=='251340' else 100
            if stamp.strftime('%H:%M')=='09:31':
                price=49 if symbol=='251340' else 103
            values.append({'timestamp':str(stamp),'date':DAY,'symbol':symbol,'source':'TOSS',
                'timestamp_semantics':'bar_end','trading_value':'','open':price,'high':price,
                'low':price,'close':price,'volume':100})
        rows[symbol]=values
    bundle={'day':DAY,'cutoff':DAY+'T09:32:00+09:00',
            'index':{'daily':{'output2':[{'stck_bsop_date':'20261015','bstp_nmix_oprc':'100'}]}}}
    def features(symbol,frame):
        return pd.DataFrame([{'timestamp':pd.Timestamp(DAY+' 09:30'),'time':'09:30',
            'usable':True,'price':100,'entry_signal':-1,'signal_strength':2,'vol14':.02,
            'upper_band':110,'lower_band':90,'vwap_used':100,'prior_median_range20':.1}])
    history=SimpleNamespace(c3=lambda *a:(0.,{'z':-2.,'mis':.005,'sigma':.3}),intraday_features=features)
    return bundle,rows,history


def test_causal_first_signal_and_next_minute_safety_stop(tmp_path):
    j=EtfJournal(tmp_path/'journal.sqlite');j.initialize(Decimal(30000),'mock')
    bundle,rows,h=data()
    plan=replay(bundle,rows,h,start_books(j,DAY))
    assert plan['decisions'][0]['payload']['action']=='no_entry'
    assert [(x['strategy'],x['side'],Decimal(x['price']))for x in plan['fills']]==[
        ('KD','buy',Decimal(50)),('KD','sell',Decimal(49))]
    assert pd.Timestamp(plan['fills'][0]['event_ns'],unit='ns',tz='UTC').tz_convert('Asia/Seoul').strftime('%H:%M')=='09:30'
    assert plan['books']['KD']['positions']=={} and plan['books']['KD']['entries']==1
    assert not any(x['strategy']=='KP'for x in plan['fills'])


def test_secondary_account_cash_caps_replay_even_with_larger_strategy_allocation(tmp_path):
    j=EtfJournal(tmp_path/'journal.sqlite');j.initialize(Decimal(30000),'mock')
    bundle,rows,h=data();bundle['account_cash']={'mock':'100000','mock2':'1000'}
    plan=replay(bundle,rows,h,start_books(j,DAY))
    assert plan['fills'][0]['quantity']==18  # account cap at conservative limit 55
    assert plan['opening_account_cash']['mock2']=='1000'


def test_newest_first_rest_input_is_sorted_before_day_open_and_features(tmp_path):
    j=EtfJournal(tmp_path/'journal.sqlite');j.initialize(Decimal(30000),'mock')
    b,r,h=data()
    for rows in r.values():
        rows[-1].update(open=80,high=80,low=80,close=80)
        rows.reverse()
    original=h.intraday_features
    def features(symbol,frame):
        assert frame.timestamp.is_monotonic_increasing
        assert frame.iloc[0]['open']==100
        return original(symbol,frame)
    h.intraday_features=features
    def c3(etf_open,index_open):
        assert etf_open==100
        return 0.,{'z':-2.,'mis':.005,'sigma':.3}
    h.c3=c3
    p=replay(b,r,h,start_books(j,DAY))
    assert p['fills'][0]['side']=='buy'and p['fills'][0]['price']=='50'


def test_delayed_order_excluded_without_double_positions_or_raw_evidence_loss(tmp_path):
    j=EtfJournal(tmp_path/'journal.sqlite');j.initialize(Decimal(30000),'mock')
    fill_actual(j,'late')
    bundle,rows,h=data();plan=replay(bundle,rows,h,start_books(j,DAY))
    assert apply_plan(j,plan) is True and apply_plan(j,plan) is False
    assert j.book('KD')['positions']=={'251340':10}
    assert j.logical_book('KD')['positions']=={}
    assert j.logical_book('KD')['cash']==Decimal(plan['books']['KD']['cash'])
    excluded,_=report_policy(j.db)
    assert order_key('mock2',DAY,'late')in excluded
    assert j.db.execute("SELECT filled FROM orders WHERE intent_id='late'").fetchone()[0]==10
    before=j.logical_book('KD')['cash']
    fill_actual(j,'cleanup',Side.SELL,10,490)
    assert j.book('KD')['positions']=={} and j.logical_book('KD')['positions']=={}
    assert j.logical_book('KD')['cash']==before
    assert j.db.execute('SELECT count(*) FROM reconstructed_orders').fetchone()[0]==2


def test_confirmed_partial_fills_mirror_once_and_keep_broker_ownership(tmp_path):
    j=EtfJournal(tmp_path/'journal.sqlite');j.initialize(Decimal(30000),'mock')
    j.db.execute('INSERT INTO recovery_runs VALUES(?,?,?,?)',
                 (DAY,'x',json.dumps({'strategies':['KD'],'plan_sha256':'x'}),0))
    j.stage(intent('live',qty=10),'20261015',-1,110);j.accepted('live',{'ODNO':'live'})
    first={'tot_ccld_qty':'4','tot_ccld_amt':'240','rmn_qty':'6'}
    j.apply_status('live',first);j.apply_status('live',first)
    assert j.book('KD')['positions']==j.logical_book('KD')['positions']=={'251340':4}
    j.apply_status('live',{'tot_ccld_qty':'10','tot_ccld_amt':'600','rmn_qty':'0'})
    assert j.book('KD')['positions']==j.logical_book('KD')['positions']=={'251340':10}
    assert j.db.execute('SELECT count(*) FROM reconstructed_orders').fetchone()[0]==2
    assert j.logical_book('KD')['cash']==Decimal('9399.91000')


def test_corrected_report_removes_entire_superseded_account_exactly(tmp_path):
    j=EtfJournal(tmp_path/'journal.sqlite');j.initialize(Decimal(30000),'mock')
    fill_actual(j,'late')
    b,r,h=data();p=replay(b,r,h,start_books(j,DAY));apply_plan(j,p)
    j.db.execute('INSERT INTO broker_trades VALUES(?,?,?,?,?,?,?)',('mock2',DAY,'late','251340','buy',10,'600'))
    assumed=j.assumed_report(DAY,{})
    payload={'day':DAY,'net_pnl':'-110','fees':'0','assumed':assumed,
             'accounts':{'mock2':{'net_pnl':'-110','fees':'0'}},'positions':{'mock2:251340':'10'}}
    result=filter_account_report(j,payload,{'251340':49})
    assert Decimal(result['net_pnl'])==0
    assert Decimal(result['adjusted_net_pnl'])==Decimal(assumed['net_pnl'])
    assert filter_account_report(j,result,{'251340':49})==result


def test_modified_plan_is_rejected_without_any_changes(tmp_path):
    j=EtfJournal(tmp_path/'journal.sqlite');j.initialize(Decimal(30000),'mock')
    b,r,h=data();p=replay(b,r,h,start_books(j,DAY));p['fills'][0]['quantity']+=1
    with pytest.raises(RuntimeError,match='checksum'):apply_plan(j,p)
    assert j.db.execute('SELECT count(*) FROM recovery_runs').fetchone()[0]==0


def test_kis_and_fallback_index_units_and_completed_end_labels():
    cutoff=pd.Timestamp(DAY+' 09:02',tz='Asia/Seoul').value
    raw=[{'cntr_tm':'20261015090000','open_pric':'88120','high_pric':'88200',
          'low_pric':'88000','cur_prc':'88190','trde_qty':'5'},
         {'cntr_tm':'20261015090200','open_pric':'88200','high_pric':'88200',
          'low_pric':'88200','cur_prc':'88200','trde_qty':'5'}]
    rows=normalize_index_minutes(raw,DAY,cutoff,provider='KIWOOM')
    assert len(rows)==1 and rows[0]['open']==881.2 and rows[0]['timestamp']==DAY+' 09:01:00'
    event=index_bar(rows[0])
    assert event.instrument.std_symbol=='KOSPI200' and allowed_supplement(event)
    assert not allowed_supplement(event.model_copy(update={'ingest_mode':'realtime'}))


def test_cash_index_quotes_use_index_schema_not_futures():
    r={'bstp_cls_code':'2001','bsop_hour':'110000','prpr_nmix':'864.52','oprc_nmix':'881.20',
       'nmix_hgpr':'894.74','nmix_lwpr':'857.55'}
    e=kis_row_to_event('H0UPCNT0',r)
    assert e.event_type==EventType.INDEX_QUOTE and e.value==Decimal('864.52')
    assert e.instrument.asset_class==AssetClass.INDEX and allowed_supplement(e)
    assert not allowed_supplement(e.model_copy(update={'provider':'other'}))


def test_toss_repair_never_includes_unclosed_or_previous_day_bars():
    raw=[{'timestamp':t,'openPrice':1,'highPrice':1,'lowPrice':1,'closePrice':1,'volume':0}
         for t in ['2026-10-14T15:00:00+09:00','2026-10-15T09:01:00+09:00','2026-10-15T09:02:00+09:00']]
    rows=normalized_toss(raw,'069500',DAY,pd.Timestamp(DAY+' 09:01',tz='Asia/Seoul'))
    assert len(rows)==1 and rows[0]['timestamp']==DAY+' 09:01:00'
