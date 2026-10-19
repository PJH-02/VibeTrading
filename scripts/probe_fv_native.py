import json
from pathlib import Path
import pyarrow.parquet as pq
root=Path('/research');cat=json.loads((root/'catalogue.json').read_text())
for v in ('binance','bybit','bitget','arcus','grvt'):
 items=[r for r in cat if r['venue']==v and r['event_type']=='order_book']
 r=min(items,key=lambda r:r['min_ts_ns']);p=root/(v+'_native')/(r['file_id']+'.parquet')
 if not p.exists():continue
 x=pq.ParquetFile(p).read().to_pylist()
 print(json.dumps({'venue':v,'rows':len(x),'first_ns':x[0]['receive_ts_ns'],'last_ns':x[-1]['receive_ts_ns'],'types':list({a['update_type'] for a in x}),'null_raw':sum(not a['raw_payload'] for a in x),'first':{k:x[0].get(k) for k in ('sync_generation','final_update_id','previous_update_id')},'raw':(x[0].get('raw_payload') or '')[:180]}))
