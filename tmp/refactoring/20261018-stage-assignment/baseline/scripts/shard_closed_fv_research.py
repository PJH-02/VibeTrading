"""Shard a closed server reduction without changing the observed values."""
import hashlib,json,sys
from pathlib import Path
import pyarrow.compute as pc,pyarrow.parquet as pq

root=Path('/research');audits=json.loads((root/'reduce_audit.json').read_text())
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()
for venue in sys.argv[1:]:
    audit=audits[venue];source=root/f'{venue}_seconds.parquet'
    if sha(source)!=audit['seconds_sha256']:raise ValueError('Source not closed or changed')
    pf=pq.ParquetFile(source);writers={}
    for batch in pf.iter_batches(batch_size=8192):
        import pyarrow as pa
        table=pa.Table.from_batches([batch])
        for asset in ('BTC','ETH','SOL'):
            subset=table.filter(pc.equal(table['asset'],asset))
            if not len(subset):continue
            if asset not in writers:writers[asset]=pq.ParquetWriter(root/f'{venue}_{asset}_seconds.parquet',table.schema,compression='zstd')
            writers[asset].write_table(subset)
    for asset,writer in writers.items():
        writer.close();counts=audit['series'][asset]
        a={**audit,'rows':counts.get('book_rows',0)+counts.get('trade_rows',0),
           'series':{asset:counts},'closed_unsharded_sha256':audit['seconds_sha256'],
           'seconds_sha256':sha(root/f'{venue}_{asset}_seconds.parquet')}
        (root/f'reduce_audit_{venue}_{asset}.json').write_text(json.dumps({venue:a},indent=2))
    print(json.dumps({'sharded':venue,'source_events':audit['rows']}),flush=True)
