import hashlib,json,pathlib,resource,time
from types import SimpleNamespace
import pyarrow as pa,pyarrow.parquet as pq
from vibetrading.compactor import ParquetCompactor
from vibetrading.settings import RuntimeSettings
root=pathlib.Path('/probe/work');root.mkdir(parents=True,exist_ok=True)
env=root/'.env';env.write_text('VIBETRADING_DATA_ROOT='+str(root)+'\nCOMPACTOR_BATCH_ROWS=64\n')
settings=RuntimeSettings.from_env(env)
part=settings.parquet.data_root/'event_type=order_book/trading_date=2026-10-06/provider=probe/venue=probe';part.mkdir(parents=True,exist_ok=True)
# Highly compressed nested books still expand to many Python dictionaries.
# All data are synthetic and isolated; no production DB or files are opened.
schema=pa.schema([('source_ts_ns',pa.int64()),('bids',pa.list_(pa.struct([('price',pa.string()),('quantity',pa.string())]))),('raw_payload',pa.string())])
book=[{'price':str(1000+i),'quantity':'1.2345'} for i in range(250)]
record={'source_ts_ns':1791250000000000000,'bids':book,'raw_payload':json.dumps(book)}
sources=[]
for file_number in range(2):
    path=part/f'input-{file_number}.parquet'
    with pq.ParquetWriter(path,schema,compression='zstd') as w:
        for _ in range(32):w.write_table(pa.Table.from_pylist([record]*64,schema=schema))
    sources.append({'path':str(path),'file_id':str(file_number),'event_type':'order_book','trading_date':'2026-10-06','provider':'probe','venue':'probe'})
outputs=[]
store=SimpleNamespace(commit_compaction=lambda output,sources:outputs.append(output))
before=time.monotonic();result=ParquetCompactor(settings,store)._compact(sources)
assert result['rows']==4096 and outputs[0]['rows']==4096
from vibetrading.parquet_store import _canon
expected=hashlib.sha256()
for _ in range(4096):expected.update(_canon(record));expected.update(b'\n')
assert outputs[0]['logical_sha256']==expected.hexdigest()
print(json.dumps({'result':result,'duration_seconds':round(time.monotonic()-before,2),'peak_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1),'logical_sha256':outputs[0]['logical_sha256']}),flush=True)
