import json,sqlite3
import pyarrow.parquet as pq
from pathlib import Path
c=sqlite3.connect('file:/data/crypto_state/vibetrading.sqlite3?mode=ro',uri=True); c.row_factory=sqlite3.Row
for typ in ('order_book','trade'):
 for v in ('binance','bybit','bitget','arcus','grvt'):
  r=c.execute("SELECT path FROM parquet_manifest WHERE lifecycle_state='active' AND event_type=? AND venue=? AND local_present=1 ORDER BY min_ts_ns LIMIT 1",(typ,v)).fetchone()
  if r:
   p=pq.ParquetFile(r['path']); b=next(p.iter_batches(batch_size=1)).to_pylist()[0]
   print(json.dumps({'venue':v,'type':typ,'schema':p.schema_arrow.names,'row':b},default=str))
