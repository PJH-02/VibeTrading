"""Float Arrow derived copies for local computation, preserving every event."""
import json,sys,tarfile,time
from pathlib import Path
import pyarrow as pa,pyarrow.parquet as pq
sys.path.insert(0,'/research')
from reduce import float_book_table
from extract import sha
root=Path('/research');audit={}
for venue in ('bybit','bitget','arcus','grvt'):
 dest=root/'slim'/(venue+'_native');dest.mkdir(parents=True,exist_ok=True)
 files=[]
 for i,path in enumerate((root/(venue+'_native')).glob('*.parquet')):
  pf=pq.ParquetFile(path);wanted=[k for k in pf.schema_arrow.names if k!='raw_payload']
  table=float_book_table(pf.read(columns=wanted));target=dest/path.name
  pq.write_table(table,target,compression='zstd')
  files.append({'name':path.name,'sha256':sha(target),'rows':table.num_rows})
  if i%500==0:(root/'slim_progress.json').write_text(json.dumps({'venue':venue,'files':i}))
 audit[venue]={'files':files,'rows':sum(r['rows'] for r in files)}
 (root/'slim'/('slim_'+venue+'_audit.json')).write_text(json.dumps(audit[venue]))
 tar=root/(venue+'_slim.tar.gz')
 with tarfile.open(tar,'w:gz',compresslevel=1) as out:
  out.add(dest,arcname=dest.name)
  out.add(root/'slim'/('slim_'+venue+'_audit.json'),arcname='slim_'+venue+'_audit.json')
 audit[venue]['bundle_sha256']=sha(tar);audit[venue]['bundle_bytes']=tar.stat().st_size
 (root/'slim_audit.json').write_text(json.dumps(audit))
 print(json.dumps({'venue':venue,'bytes':tar.stat().st_size,'sha256':sha(tar)}),flush=True)
