"""Read bounded, already verified archive files without changing stored data."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

import pyarrow.parquet as pq
from vibetrading.settings import get_settings
from vibetrading.storage_backend import archive_backend

cutoff=CUTOFF
counts=Counter();channels=Counter();checked=[]
db=sqlite3.connect('file:/data/crypto_state/vibetrading.sqlite3?mode=ro',uri=True,timeout=3)
db.execute('PRAGMA query_only=ON')
files=[]
for kind in ('trade','order_book','funding_rate','open_interest'):
    files.extend(db.execute("SELECT remote_key,file_sha256,byte_size,event_type FROM parquet_manifest WHERE provider='binance-perp' AND upload_status='remote_verified' AND created_ts_ns>=? AND event_type=? ORDER BY created_ts_ns DESC LIMIT 2",(cutoff,kind)).fetchall())
db.close()
backend=archive_backend(get_settings())
try:
    with tempfile.TemporaryDirectory(prefix='binance-verify-') as temp:
        for i,(key,sha,size,kind) in enumerate(files):
            destination=Path(temp)/str(i)
            backend.download_file(key,destination)
            actual=hashlib.sha256(destination.read_bytes()).hexdigest()
            assert actual==sha and destination.stat().st_size==size,'Archive integrity mismatch'
            columns=['venue','instrument_std_symbol','event_type','receive_ts_ns']+(['channel'] if kind=='order_book' else [])
            for batch in pq.ParquetFile(destination).iter_batches(batch_size=2048,columns=columns):
                for row in batch.to_pylist():
                    if row['receive_ts_ns']<cutoff:continue
                    counts[(row['instrument_std_symbol'],row['event_type'])]+=1
                    if kind=='order_book':channels[row['channel']]+=1
            checked.append({'event_type':kind,'bytes':size,'sha256':actual})
            destination.unlink()
finally:
    backend.close()
absent=[(s,t) for s in ('BTCUSDT','ETHUSDT','SOLUSDT') for t in ('trade','order_book','funding_rate','open_interest') if counts[(s,t)]==0]
print(json.dumps({'files_read':len(checked),'source':'verified_remote_archive','integrity_checked':checked,
    'counts':[{'symbol':s,'event_type':t,'rows':n} for (s,t),n in sorted(counts.items())],
    'channels':dict(channels),'missing':absent}))
assert checked and not absent,'Fresh Binance committed coverage missing'
