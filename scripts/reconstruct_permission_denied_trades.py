"""Read-only reconstruction from recorded quotes/bars; never invent old prices."""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from decimal import Decimal
sys.path.insert(0,'/app')
import pandas as pd
import pyarrow.dataset as ds
from strategies.kr.etf_history import EtfHistory
from runtime.etf_trading import intraday_decision
from feeds.providers.kiwoom import KiwoomAuthClient
from runtime.settings import get_settings

async def main():
    settings=get_settings(); day='2026-10-14'
    root=settings.state_dir/'etf-mock'
    db=sqlite3.connect('file:'+str(root/'journal.sqlite3')+'?mode=ro',uri=True); db.row_factory=sqlite3.Row
    orders=[dict(r) for r in db.execute("SELECT * FROM orders WHERE day='20261014' AND status='rejected'")]
    history=EtfHistory(KiwoomAuthClient(settings),root/'warmup')
    assert json.loads((root/'warmup/warmup.json').read_text())['day']==day
    history.day=day
    for base in ['069500','229200']:
        history.daily[base]=pd.read_json(root/'warmup'/(base+'-daily.json'),orient='table')
        history.minutes[base]=pd.read_json(root/'warmup'/(base+'-minute.json'),orient='table')
    path=settings.data_root/'market_data'
    catalogue=sqlite3.connect('file:'+str(settings.state_dir/'vibetrading.sqlite3')+'?mode=ro',uri=True)
    def dataset(kind,lo,hi):
        files=[row[0] for row in catalogue.execute("SELECT path FROM parquet_manifest WHERE event_type=? AND provider='kiwoom' AND trading_date=? AND local_present=1 AND lifecycle_state='active' AND max_ts_ns>=? AND min_ts_ns<=?",
            (kind,day,lo,hi)) if Path(row[0]).exists()]
        if not files: raise RuntimeError('No locally recorded evidence for '+kind)
        return ds.dataset(files,format='parquet')
    def quote(symbol,ts):
        table=dataset('order_book',ts-30_000_000_000,ts+30_000_000_000).scanner(columns=['receive_ts_ns','bids','asks','event_id'],filter=
            (ds.field('instrument_raw_symbol')==symbol)&(ds.field('receive_ts_ns')<=ts)&(ds.field('receive_ts_ns')>=ts-10_000_000_000),
            use_threads=False,batch_size=512,batch_readahead=1,fragment_readahead=1).to_table()
        if not table.num_rows: raise RuntimeError(f'No recorded fresh quote for {symbol} at {ts}')
        row=max(table.to_pylist(),key=lambda r:r['receive_ts_ns'])
        return {'bid':str(max(r['price'] for r in row['bids'])),'ask':str(min(r['price'] for r in row['asks'])),
            'received_ns':row['receive_ts_ns'],'intent_ns':ts,'event_id':row['event_id'],'source':'recorded_parquet_order_book'}
    output=[]
    for order in orders:
        strategy,symbol=order['strategy'],order['symbol']
        assert (strategy,symbol,order['quantity']) in [('KP','122630',385),('KD','251340',10863)],'Unexpected historical rejection: review first'
        intent=json.loads(order['payload']); metadata=intent['metadata']; created=int(intent['created_ts_ns'])
        entry=quote(symbol,created)
        base='069500' if strategy=='KP' else '229200'
        session=settings.state_dir/'etf-csv-sync/sessions'/(day+'-'+base+'.jsonl')
        frame=pd.DataFrame([json.loads(line) for line in session.read_text().splitlines()])
        frame['timestamp']=pd.to_datetime(frame.timestamp); frame['trading_value']=float('nan')
        frame=frame.drop_duplicates('timestamp',keep='last').sort_values('timestamp')
        features=history.intraday_features(base,frame)
        book={'positions':{symbol:order['quantity']},'direction':metadata['direction'],'entries':1}
        exit_data=None
        for row in frame.to_dict('records'):
            close_ns=int(pd.Timestamp(row['timestamp']).tz_localize('Asia/Seoul').tz_convert('UTC').value)
            if close_ns<=created: continue
            action=None; stop=metadata.get('safety_stop')
            if strategy=='KD' and stop is not None:
                breached=row['close']<=stop if book['direction']==1 else row['close']>=stop
                if breached: action='safety_stop'
            if not action and row['timestamp'].minute in {0,30}:
                selected=features[features.timestamp==row['timestamp']]
                if len(selected) and intraday_decision(strategy,selected.iloc[0].to_dict(),book)[2]=='paper_stop': action='paper_stop'
            if not action: continue
            decision=db.execute('SELECT MIN(created_ns) FROM decisions WHERE id LIKE ?',
                (f'{strategy}:{close_ns}:%',)).fetchone()[0]
            if decision is None:
                table=dataset('bar_1m',close_ns-60_000_000_000,close_ns+120_000_000_000).to_table(columns=['receive_ts_ns'],filter=(ds.field('instrument_raw_symbol')==base)&
                    (ds.field('close_ts_ns')==close_ns)&(ds.field('receive_ts_ns')>=close_ns)&
                    (ds.field('receive_ts_ns')<=close_ns+120_000_000_000),use_threads=False)
                if not table.num_rows: raise RuntimeError('No recorded signal processing time')
                decision=min(table.column('receive_ts_ns').to_pylist())
            exit_data={'action':action,'signal_close_ns':close_ns,'quote':quote(symbol,decision)}
            break
        output.append({'intent_id':order['intent_id'],'strategy':strategy,'symbol':symbol,'quantity':order['quantity'],
            'day':day,'entry_quote':entry,'exit':exit_data,'reason':'Historical verified 40070000: 매매불가 종목'})
    destination=root/'permission-reconstruction.json'; destination.write_text(json.dumps(output,indent=2))
    print(json.dumps(output))

asyncio.run(main())
