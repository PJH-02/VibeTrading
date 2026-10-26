"""Bounded intraday data repair and causal, explicitly assumed executions."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import math
import time
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

import pandas as pd
import numpy as np

from core.models import AssetClass, InstrumentId, Venue, Bar1mEvent
from feeds.providers.toss import TossAuthClient
from feeds.providers.kiwoom import KiwoomAuthClient, KST
from feeds.providers.kis_client import KisAuthClient
from feeds.kis_index import index_snapshot, normalize_index_minutes, index_bar
from .etf_csv_history import CsvStrategyHistory, ETF_HISTORY_ROLES, atomic_json
from .etf_history import EtfHistory
from .etf_reference import weights
from .etf_intraday_store import merge_session_rows
from .etf_recovery_ledger import exclude, record_fill
from .settings import get_settings

ETFS = ('069500','122630','229200','251340','114800','252670','233740')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,default=str).encode()).hexdigest()


def normalized_toss(rows, symbol, day, cutoff):
    output = {}
    for row in rows:
        stamp = pd.Timestamp(row['timestamp'])
        stamp = stamp.tz_localize('Asia/Seoul') if stamp.tzinfo is None else stamp.tz_convert('Asia/Seoul')
        if str(stamp.date()) != day or stamp > cutoff or not '09:01'<=stamp.strftime('%H:%M')<='15:20':
            continue
        if stamp.second or stamp.microsecond:
            raise ValueError('Non-aligned Toss minute')
        values = {key:float(row[native]) for key,native in
            [('open','openPrice'),('high','highPrice'),('low','lowPrice'),('close','closePrice'),('volume','volume')]}
        prices = [values[k] for k in ('open','high','low','close')]
        if (not all(math.isfinite(v)for v in values.values()) or min(prices)<=0 or values['volume']<0
                or values['high']<max(prices) or values['low']>min(prices)):
            raise ValueError('Invalid recovered OHLCV')
        output[str(stamp.tz_localize(None))] = {
            'timestamp':str(stamp.tz_localize(None)),'date':day,'symbol':symbol,'source':'TOSS',
            'timestamp_semantics':'bar_end','trading_value':row.get('tradingValue',''),**values}
    return sorted(output.values(),key=lambda r:r['timestamp'])


async def collect(settings, day, *, max_pages=3):
    now = datetime.now(KST)
    if day != str(now.date()) or not 1<=max_pages<=4:
        raise ValueError('Recovery is limited to today and at most four pages per ETF')
    cutoff = pd.Timestamp(now.replace(second=0,microsecond=0))
    cutoff = min(cutoff,pd.Timestamp(day+' 15:20',tz='Asia/Seoul'))
    out = {'day':day,'cutoff':cutoff.isoformat(),'collected_ns':time.time_ns(),'etfs':{},'index':{}}
    toss = TossAuthClient(settings)
    token = toss._read_token_cache()
    if not token:
        raise RuntimeError('A valid existing Toss token is required; live token cache is not replaced')
    for symbol in ETFS:
        before = cutoff.isoformat(); rows = []
        for page in range(max_pages):
            data = await toss._request_once('GET','/api/v1/candles',token=token,
                params={'symbol':symbol,'interval':'1m','count':200,'adjusted':'false','before':before},
                json_body=None,account_seq=None)
            result = data.get('result',data); part = result.get('candles',[]); rows.extend(part)
            stamps = [pd.Timestamp(r['timestamp']) for r in part]
            if not stamps or min(stamps)<=pd.Timestamp(day+' 09:01',tz='Asia/Seoul') or not result.get('nextBefore'):
                break
            if before == result['nextBefore']:
                raise RuntimeError('Recovery pagination stalled')
            before = result['nextBefore']; await asyncio.sleep(.4)
        out['etfs'][symbol] = {'rows':rows,'pages':page+1}
        await asyncio.sleep(.4)
    kis = KisAuthClient(settings,mode='real')
    out['index']['minute'] = await index_snapshot(kis)
    await asyncio.sleep(.4)
    out['index']['daily'] = (await kis.get(
        '/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice',tr_id='FHKUP03500100',
        params={'FID_COND_MRKT_DIV_CODE':'U','FID_INPUT_ISCD':'2001',
                'FID_INPUT_DATE_1':day.replace('-',''),'FID_INPUT_DATE_2':day.replace('-',''),
                'FID_PERIOD_DIV_CODE':'D'}))['body']
    # KIS native minute endpoint retains only 102 recent rows. Preserve
    # provenance while filling its earlier same-day window from Kiwoom.
    kiwoom = KiwoomAuthClient(settings)
    out['index']['kiwoom'] = await kiwoom.post_api('/api/dostk/chart','ka20005',{'inds_cd':'201','tic_scope':'1'})
    from .kis_execution import KisCashBroker
    out['account_cash']={}
    for scope in ('mock','mock2'):
        state=await KisCashBroker(settings,mode=scope).account_state()
        opening=Decimal(str(state['summary']['bfdy_tot_asst_evlu_amt']))
        if opening<=0:raise RuntimeError('Opening mock account capital unavailable')
        out['account_cash'][scope]=str(opening)
        await asyncio.sleep(.4)
    return out


def datasets(bundle):
    day,cutoff = bundle['day'],pd.Timestamp(bundle['cutoff'])
    expected = pd.date_range(day+' 09:01',cutoff.tz_localize(None),freq='min')
    result = {}
    for symbol in ETFS:
        rows = normalized_toss(bundle['etfs'][symbol]['rows'],symbol,day,cutoff)
        actual = pd.DatetimeIndex([pd.Timestamp(r['timestamp'])for r in rows])
        missing = expected.difference(actual)
        if len(missing):
            raise RuntimeError(symbol+': missing recovered minutes '+','.join(map(str,missing)))
        result[symbol] = rows
    native = bundle['index'].get('kiwoom',{}).get('inds_min_pole_qry',[])
    index = {r['timestamp']:r for r in normalize_index_minutes(native,day,cutoff.value,provider='KIWOOM')}
    index.update({r['timestamp']:r for r in normalize_index_minutes(
        bundle['index']['minute'].get('output2',[]),day,cutoff.value)})
    missing = expected.difference(pd.DatetimeIndex([pd.Timestamp(t)for t in index]))
    if len(missing):
        raise RuntimeError('KOSPI200 recovery incomplete: '+','.join(map(str,missing)))
    result['KOSPI200'] = sorted(index.values(),key=lambda r:r['timestamp'])
    return result


def load_history(root, day):
    ref = CsvStrategyHistory(root)
    h = EtfHistory.__new__(EtfHistory)
    h.day,h.daily,h.minutes = day,{},{}
    for symbol,(daily_key,minute_key) in ETF_HISTORY_ROLES.items():
        h.daily[symbol] = ref.daily(daily_key,symbol)
        prior = h.daily[symbol][h.daily[symbol].date<day]
        h.minutes[symbol] = ref.minute(minute_key,set(prior.date.tail(15)))
        if prior.shape[0]<22 or h.minutes[symbol].date.nunique()<14:
            raise RuntimeError('Insufficient strategy warmup')
    h.index = ref.daily('KOSPI200_daily','KOSPI200')
    h.us = ref.daily('SP500_daily','SP500').set_index('timestamp').close.dropna().sort_index()
    return h


def start_books(journal, day):
    books = {}
    for strategy in ('C3','KP','KD'):
        book = journal.logical_book(strategy)
        for row in journal.db.execute("SELECT * FROM orders WHERE strategy=? AND replace(day,'-','')=? AND filled>0",
                                      (strategy,day.replace('-',''))):
            sign = 1 if row['side']=='buy' else -1
            book['cash'] += sign*Decimal(row['notional'])+Decimal(row['notional'])*Decimal('.00015')
            s=row['symbol'];book['positions'][s]=book['positions'].get(s,0)-sign*row['filled']
        for row in journal.db.execute('SELECT * FROM assumed_fills WHERE strategy=? AND day=?',(strategy,day)):
            sign=1 if row['side']=='buy' else -1;cost=Decimal(row['price'])*row['quantity']
            book['cash']+=sign*cost+Decimal(row['fee'])
            s=row['symbol'];book['positions'][s]=book['positions'].get(s,0)-sign*row['quantity']
        book['positions']={s:q for s,q in book['positions'].items()if q}
        if book['positions']:
            raise RuntimeError('Recovery requires verified flat opening positions for '+strategy)
        books[strategy]={'cash':book['cash'],'positions':{},'entries':0,'direction':0,'safety_stop':None}
    return books


def replay(bundle, rows, history, initial, *, fee_rate=Decimal('.000148')):
    from .etf_trading import intraday_decision
    day = bundle['day']; books = {s:{**b,'positions':dict(b['positions'])}for s,b in initial.items()}
    frames = {s:pd.DataFrame(sorted(v,key=lambda r:r['timestamp']))for s,v in rows.items()}
    for frame in frames.values():
        frame['timestamp']=pd.to_datetime(frame.timestamp)
        frame['trading_value']=pd.to_numeric(frame.trading_value,errors='coerce')
    prices = {s:f.set_index('timestamp')for s,f in frames.items()}
    decisions,fills = [],[]
    account_cash={s:Decimal(v)for s,v in bundle.get('account_cash',{}).items()}
    def ns(t):return int(pd.Timestamp(t).tz_localize('Asia/Seoul').value)
    def fill(strategy,symbol,side,quantity,price,stamp,basis):
        price=Decimal(str(price));quantity=int(quantity)
        if quantity<=0:return
        book=books[strategy];sign=1 if side=='buy'else-1;fee=price*quantity*fee_rate
        if side=='sell'and quantity>book['positions'].get(symbol,0):raise RuntimeError('Replay oversell')
        if side=='buy'and price*quantity+fee>book['cash']:raise RuntimeError('Replay over allocation')
        book['cash']-=sign*price*quantity+fee
        book['positions'][symbol]=book['positions'].get(symbol,0)+sign*quantity
        book['positions']={s:q for s,q in book['positions'].items()if q}
        scope='mock2'if symbol in {'122630','251340'}else'mock'
        if scope in account_cash:
            account_cash[scope]-=sign*price*quantity+fee
            if account_cash[scope]<0:raise RuntimeError('Replay exceeded mock account buying capital')
        identity=hashlib.sha256(f"recovery:{day}:{strategy}:{stamp}:{symbol}:{side}".encode()).hexdigest()
        fills.append({'id':identity,'strategy':strategy,'symbol':symbol,'side':side,'quantity':quantity,
                      'price':str(price),'fee':str(fee),'event_ns':ns(stamp),'price_basis':basis})
    def enter(strategy,direction,leverage,stamp,opening=False,stop=None):
        book=books[strategy];capital=book['cash']
        for symbol,weight in weights(direction,leverage,'KOSPI200'if strategy!='KD'else'KOSDAQ150').items():
            if weight<=0:continue
            price=frames[symbol].iloc[0]['open']if opening else prices[symbol].loc[stamp,'close']
            conservative=((Decimal(str(price))+5)/5).to_integral_value(rounding=ROUND_CEILING)*5
            qty=int(capital*Decimal(str(weight))/(conservative*Decimal('1.005')))
            scope='mock2'if symbol in {'122630','251340'}else'mock'
            if scope in account_cash:
                unit=max(conservative,Decimal(str(price))*(1+fee_rate))
                qty=min(qty,int(account_cash[scope]/unit))
            fill(strategy,symbol,'buy',qty,price,stamp,
                 'official opening price; immediate fill assumption'if opening else'completed signal-minute close; immediate fill assumption')
        if book['positions']:
            book.update(entries=1,direction=direction,safety_stop=stop)
    def exit_book(strategy,stamp,reason):
        book=books[strategy]
        for symbol,qty in list(book['positions'].items()):
            fill(strategy,symbol,'sell',qty,prices[symbol].loc[stamp,'close'],stamp,
                 reason+'; completed signal-minute close; immediate fill assumption')
        book.update(direction=0,safety_stop=None)
    native_daily=[r for r in bundle['index']['daily'].get('output2',[])if r.get('stck_bsop_date')==day.replace('-','')]
    if len(native_daily)!=1:raise RuntimeError('KIS current index opening snapshot unavailable')
    index_open=float(native_daily[0]['bstp_nmix_oprc'])
    leverage,c3=history.c3(float(frames['069500'].iloc[0]['open']),index_open)
    open_stamp=pd.Timestamp(day+' 09:00')
    decisions.append({'id':'C3:'+day+':open','strategy':'C3','event_ns':ns(open_stamp),
                      'payload':{**c3,'leverage':leverage,'action':'entry'if leverage else'no_entry','index_open':index_open}})
    if leverage>0:enter('C3',1,leverage,open_stamp,opening=True)
    features={}
    for symbol,strategy in [('069500','KP'),('229200','KD')]:
        f=history.intraday_features(symbol,frames[symbol])
        features[strategy]={pd.Timestamp(r['timestamp']):r for r in f.to_dict('records')}
    for stamp in sorted(set(frames['229200'].timestamp)):
        for strategy,symbol in [('KP','069500'),('KD','229200')]:
            book=books[strategy]
            if strategy=='KD'and book['positions']and book['safety_stop']is not None:
                close=float(prices[symbol].loc[stamp,'close'])
                if (close<=book['safety_stop']if book['direction']==1 else close>=book['safety_stop']):
                    exit_book(strategy,stamp,'KD safety stop')
                    decisions.append({'id':f'{strategy}:{ns(stamp)}:safety_stop','strategy':strategy,
                                      'event_ns':ns(stamp),'payload':{'action':'safety_stop'}})
                    continue
            if stamp.strftime('%H:%M')>='15:20':
                if book['positions']:exit_book(strategy,stamp,'session exit')
                continue
            row=features[strategy].get(stamp)
            if row is None or not row['usable']:continue
            direction,lev,action=intraday_decision(strategy,row,book)
            decisions.append({'id':f'{strategy}:{ns(stamp)}:{action}','strategy':strategy,'event_ns':ns(stamp),
                              'payload':{'action':action,'direction':direction,'leverage':lev}})
            if action=='entry':
                stop=float(row['price'])*(1-direction*.25*row['prior_median_range20'])if strategy=='KD'else None
                enter(strategy,direction,lev,stamp,stop=stop)
            elif action=='paper_stop':exit_book(strategy,stamp,'30-minute strategy stop')
    if max(frames['069500'].timestamp).strftime('%H:%M')>='15:20'and books['C3']['positions']:
        exit_book('C3',pd.Timestamp(day+' 15:20'),'C3 session exit')
    plan={'day':day,'cutoff':bundle['cutoff'],'input_sha256':digest(bundle),'strategies':['C3','KP','KD'],
          'initial_cash':{s:str(b['cash'])for s,b in initial.items()},'decisions':decisions,'fills':fills,
          'opening_account_cash':bundle.get('account_cash',{}),
          'books':{s:{**b,'cash':str(b['cash'])}for s,b in books.items()},
          'fee_rate':str(fee_rate),'assumption':'Immediate fill at signal-minute close; C3 at official open. Never next-minute close.'}
    plan['plan_sha256']=digest(plan)
    return plan


def apply_plan(journal,plan):
    if digest({k:v for k,v in plan.items()if k!='plan_sha256'})!=plan['plan_sha256']:
        raise RuntimeError('Recovery plan checksum changed')
    old=journal.db.execute('SELECT plan_sha256 FROM recovery_runs WHERE day=?',(plan['day'],)).fetchone()
    if old:
        if old[0]!=plan['plan_sha256']:raise RuntimeError('A different recovery is already applied')
        return False
    if journal.pending():raise RuntimeError('Pending orders block recovery')
    with journal.transaction():
        # Only the explicitly superseded intraday decisions' physical path is neutralized.
        orders=list(journal.db.execute("SELECT intent_id FROM orders WHERE replace(day,'-','')=?",
                                      (plan['day'].replace('-',''),)))
        for row in orders:exclude(journal,row['intent_id'],'User replaced delayed outage-day execution with first-signal reconstruction')
        journal.db.execute('INSERT INTO recovery_runs VALUES(?,?,?,?)',
            (plan['day'],plan['plan_sha256'],json.dumps(plan),time.time_ns()))
        for decision in plan['decisions']:
            journal.decision(decision['strategy'],decision['id'],
                             {**decision['payload'],'reconstructed':True,'event_ns':decision['event_ns']})
        for fill in plan['fills']:
            record_fill(journal,fill['id'],fill['strategy'],fill['symbol'],fill['side'],fill['quantity'],
                fill['price'],plan['day'],fill['event_ns'],basis=fill['price_basis'],source='TOSS_recovery',
                fee_rate=Decimal(plan['fee_rate']))
        for strategy,book in plan['books'].items():
            journal.db.execute('UPDATE books SET entries=?,direction=?,safety_stop=? WHERE strategy=?',
                               (book['entries'],book['direction'],book['safety_stop'],strategy))
            actual=journal.logical_book(strategy)
            if actual['positions']!=book['positions']or abs(actual['cash']-Decimal(book['cash']))>Decimal('.000001'):
                raise RuntimeError('Reconstructed logical book did not reconcile for '+strategy)
    return True


async def ingest(settings,bundle,rows):
    from .bus import NatsJetStreamBus
    bus=NatsJetStreamBus(settings);await bus.connect()
    counts={}
    try:
        for symbol,data in rows.items():
            added=await asyncio.to_thread(merge_session_rows,settings.state_dir/'etf-csv-sync'/'sessions',
                                          bundle['day'],symbol,data,replace=True)
            counts[symbol]=len(added)
            for row in added:
                if symbol=='KOSPI200':event=index_bar(row)
                else:
                    end=pd.Timestamp(row['timestamp']).tz_localize('Asia/Seoul').value
                    event=Bar1mEvent(instrument=InstrumentId(venue=Venue.TOSS,asset_class=AssetClass.ETF,
                        raw_symbol=symbol,std_symbol='TOSS:KR:'+symbol,currency='KRW',exchange='KRX'),
                        venue=Venue.TOSS,provider='toss',source_ts_ns=end,receive_ts_ns=time.time_ns(),
                        session='kr_equity_etf',open_ts_ns=end-60_000_000_000,close_ts_ns=end,
                        sequence='recovery:'+str(end),ingest_mode='backfill',
                        **{k:Decimal(str(row[k]))for k in ('open','high','low','close','volume')})
                await bus.publish_market_event(event)
            await asyncio.sleep(.1)
    finally:await bus.close()
    return counts


async def main_async(args):
    from .etf_journal import EtfJournal
    settings=get_settings();root=settings.state_dir/'etf-recovery'/args.day;root.mkdir(parents=True,exist_ok=True)
    journal=EtfJournal(settings.state_dir/'etf-mock/journal.sqlite3')
    old=journal.db.execute('SELECT plan_sha256 FROM recovery_runs WHERE day=?',(args.day,)).fetchone()
    if old:
        print(json.dumps({'already_applied':True,'plan_sha256':old[0]}));journal.db.close();return
    marker=settings.state_dir/'etf-mock'/'RECOVERY_IN_PROGRESS'
    if args.apply:
        marker.write_text(json.dumps({'day':args.day,'started_ns':time.time_ns()}))
    bundle=await collect(settings,args.day)
    atomic_json(root/'input.json',bundle)
    rows=datasets(bundle)
    history=await asyncio.to_thread(load_history,settings.state_dir/'etf-mock/history',args.day)
    try:
        plan=replay(bundle,rows,history,start_books(journal,args.day))
        atomic_json(root/'plan.json',plan)
        counts=await ingest(settings,bundle,rows)
        if args.apply:
            apply_plan(journal,plan)
            marker.unlink()
        result={'plan_sha256':plan['plan_sha256'],'ingested_rows':counts,'fills':plan['fills'],
                'books':plan['books'],'c3':plan['decisions'][0],'applied':args.apply}
        atomic_json(root/'result.json',result);print(json.dumps(result))
    finally:journal.db.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--day',default=str(datetime.now(KST).date()))
    p.add_argument('--apply',action='store_true')
    asyncio.run(main_async(p.parse_args()))


if __name__=='__main__':main()
