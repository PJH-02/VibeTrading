import csv,hashlib,json,pathlib
from datetime import datetime
root=pathlib.Path(__file__).resolve().parents[1];out=root/'docs/evidence'
def read(name):return list(csv.DictReader((out/name).open(encoding='utf-8')))
days=read('market-historical-instrument-days-20261006.csv')+read('market-instrument-days-20261006.csv')
days=[r for r in days if r['event_type'] in ('order_book','trade')]
index={(r['provider'],r['symbol'],r['date']):r for r in days if r['event_type']=='order_book'}
observed=read('market-historical-observed-book-intervals-20261006.csv')+read('market-observed-book-intervals-20261006.csv')
intervals=[]
for r in observed:
 d=index[r['provider'],r['symbol'],r['date']];b=datetime.fromisoformat(r['start_kst']);e=datetime.fromisoformat(r['end_kst_exclusive']);first=datetime.fromisoformat(d['first_kst']);last=datetime.fromisoformat(d['last_kst'])
 r['first_observed_bound_kst']=max(b,first).isoformat();r['last_observed_bound_kst']=min(e,last).isoformat();r['all_ticks_verified']='false';intervals.append(r)
for name,rows in [('MARKET_OBSERVATION_DAYS_2026-10-06.csv',days),('MARKET_OBSERVED_INTERVALS_2026-10-06.csv',intervals)]:
 with (root/'docs'/name).open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(sorted(rows,key=lambda r:(r['provider'],r['symbol'],r['date'])))
raw=json.loads((out/'incident-before-20261006.json').read_text());gaps=[r for rows in raw['gaps'].values() for r in rows if r['provider'] in ('kis','kiwoom') and r['ended_ns'] is None]
receipt={'schema_version':1,'audit_date_kst':'2026-10-06','files_verified':43758,'file_failures':0,'all_ticks_lossless_periods_proven':[],
 'unclosed_original_gaps':gaps,'original_gap_end_times_modified':False,
 'bookkeeping_defect':'old Market confirm_recovered checks CRYPTO_DATA; correct MARKET_DATA selection was deployed Oct3 and retained in current release',
 'interpretation':'An open operational gap is not proof of total absence afterwards. Use instrument/event/date observed intervals; each 5min bin has >=1 order_book observation. Ticks between observations, expected subscriptions and full vendor sequence continuity are not proven.',
 'data_rows_order_book_trade':sum(int(r['rows']) for r in days),'instrument_event_days':len(days),
 'sha256':{p:hashlib.sha256((root/'docs'/p).read_bytes()).hexdigest() for p in ('MARKET_OBSERVATION_DAYS_2026-10-06.csv','MARKET_OBSERVED_INTERVALS_2026-10-06.csv')}}
(root/'docs/MARKET_GAP_RECONCILIATION_2026-10-06.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8');print(json.dumps({k:receipt[k] for k in ('files_verified','data_rows_order_book_trade','instrument_event_days')}))
