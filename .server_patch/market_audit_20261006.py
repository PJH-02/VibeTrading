"""Read-only SHA-verified KIS/Kiwoom observation audit; no inferred gap closure."""
import concurrent.futures,csv,hashlib,json,os,pathlib,sqlite3,time,threading
from collections import defaultdict
from datetime import datetime,timedelta,timezone
import pyarrow.parquet as pq
from vibetrading.settings import get_settings
from vibetrading.storage_backend import archive_backend
ROOT=pathlib.Path('/audit'); KST=timezone(timedelta(hours=9)); BIN=300_000_000_000
THREAD=threading.local()
def label(ns):return datetime.fromtimestamp(ns/1e9,KST).isoformat()
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  while b:=f.read(1024*1024):h.update(b)
 return h.hexdigest()
def scan(m):
 p=pathlib.Path(m['path']);downloaded=False
 if not p.is_file():
  assert m['upload_status']=='remote_verified' and m['remote_key']
  p=ROOT/'temp'/str(m['file_id'])
  if not hasattr(THREAD,'backend'):THREAD.backend=archive_backend(get_settings())
  THREAD.backend.download_file(m['remote_key'],p);downloaded=True
 try:
  assert p.stat().st_size==m['byte_size'] and sha(p)==m['file_sha256'],'SHA/size mismatch'
  pf=pq.ParquetFile(p);assert pf.metadata.num_rows==m['rows'],'row count mismatch'
  names=set(pf.schema_arrow.names);wanted=['instrument_std_symbol','instrument_raw_symbol','instrument_exchange','instrument_asset_class','source_ts_ns','wallclock_ts_ns','receive_ts_ns','sampled_at_ns']
  agg={}
  for batch in pf.iter_batches(batch_size=8192,columns=[c for c in wanted if c in names]):
   v=batch.to_pydict()
   symbols=v.get('instrument_std_symbol') or v.get('instrument_raw_symbol') or ['unknown']*batch.num_rows
   asset_classes=v.get('instrument_asset_class') or ['unknown']*batch.num_rows
   for i in range(batch.num_rows):
    symbol=str(symbols[i])
    ts=next((int(v[c][i]) for c in ('source_ts_ns','sampled_at_ns','wallclock_ts_ns','receive_ts_ns') if c in v and v[c][i] and 1735689600000000000<int(v[c][i])<1830297600000000000),None)
    if ts is None:continue
    key=(m['provider'],m['event_type'],symbol,str(asset_classes[i]),label(ts)[:10])
    a=agg.setdefault(key,{'first':ts,'last':ts,'rows':0,'bins':set()});a['first']=min(a['first'],ts);a['last']=max(a['last'],ts);a['rows']+=1;a['bins'].add(ts//BIN)
  return m['file_id'],agg,None
 except Exception as e:return m['file_id'],{},type(e).__name__+': '+str(e)[:250]
 finally:
  if downloaded:p.unlink(missing_ok=True)
def main():
 (ROOT/'temp').mkdir(exist_ok=True);metas=json.loads((ROOT/'manifest.json').read_text());agg={};failures=[];done=0
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  futures={pool.submit(scan,m):m for m in metas}
  for f in concurrent.futures.as_completed(futures):
   m=futures[f]
   try:fid,assets,error=f.result()
   except Exception as e:fid,assets,error=m['file_id'],{},type(e).__name__
   if error:failures.append({'file_id':fid,'error':error})
   for key,s in assets.items():
    a=agg.setdefault(key,{'first':s['first'],'last':s['last'],'rows':0,'bins':set()});a['first']=min(a['first'],s['first']);a['last']=max(a['last'],s['last']);a['rows']+=s['rows'];a['bins'].update(s['bins'])
   done+=1
   if done%500==0:(ROOT/'progress.json').write_text(json.dumps({'completed':done,'total':len(metas),'failures':len(failures),'ts_ns':time.time_ns()}));print(f'{done}/{len(metas)} failures={len(failures)}',flush=True)
 rows=[];intervals=[]
 for key,a in sorted(agg.items()):
  provider,event_type,symbol,asset,day=key;bins=sorted(a['bins']);holes=[];runs=[];begin=prev=bins[0]
  for b in bins[1:]:
   if b>prev+1:holes.append((prev+1,b));runs.append((begin,prev+1));begin=b
   prev=b
  runs.append((begin,prev+1))
  row=dict(provider=provider,event_type=event_type,symbol=symbol,asset_class=asset,date=day,first_kst=label(a['first']),last_kst=label(a['last']),rows=a['rows'],observed_5min_bins=len(bins),empty_5min_bins_between_first_last=sum(e-s for s,e in holes),longest_empty_minutes=max([5*(e-s) for s,e in holes] or [0]));rows.append(row)
  if event_type=='order_book':
   for s,e in runs:intervals.append({'provider':provider,'symbol':symbol,'asset_class':asset,'date':day,'start_kst':label(s*BIN),'end_kst_exclusive':label(e*BIN),'interpretation':'each 5min bin contains an observation; tick completeness not established'})
 for name,items in [('instrument-days.csv',rows),('observed-book-intervals.csv',intervals)]:
  with (ROOT/name).open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=list(items[0]) if items else ['provider']);w.writeheader();w.writerows(items)
 result={'ts_ns':time.time_ns(),'files_total':len(metas),'files_sha_verified':done-len(failures),'failures':failures,'instrument_days':len(rows),'rows':sum(r['rows'] for r in rows),'limitations':['No exchange sequence baseline: no all-tick losslessness claim','No guessed end timestamp is written to original gap ledger','Empty bins are observations absent, not proven exchange event loss']}
 (ROOT/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
if __name__=='__main__':main()
