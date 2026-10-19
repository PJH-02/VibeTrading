"""Parallel per-asset computations; exact shared-connection gap mask."""
import json,os,subprocess,sys,time
from pathlib import Path
import numpy as np,pyarrow.parquet as pq
root=Path('runtime_data/fv_research_native').resolve();script=Path('scripts/fv_research_reduce.py').resolve()
for venue in sys.argv[1:]:
 arrays=[];index=[]
 for p in (root/(venue+'_native')).glob('*.parquet'):
  pf=pq.ParquetFile(p)
  arrays.append(pf.read(columns=['receive_ts_ns'],use_threads=False).column(0).to_numpy())
  col=pf.schema_arrow.names.index('receive_ts_ns')
  bound=min(pf.metadata.row_group(i).column(col).statistics.min for i in range(pf.metadata.num_row_groups))
  index.append([int(bound),p.name])
 times=np.concatenate(arrays);times.sort();ix=np.flatnonzero(np.diff(times)>3_000_000_000)
 transport={'gaps':[(float(times[i]/1e9+3),float(times[i+1]/1e9)) for i in ix],'end_ns':int(times[-1]),'rows':len(times)}
 (root/(venue+'_transport.json')).write_text(json.dumps(transport));del arrays,times
 (root/(venue+'_file_index.json')).write_text(json.dumps(index))
 processes=[]
 for asset in ('BTC','ETH','SOL'):
  env={**os.environ,'FV_RESEARCH_ROOT':str(root),'FV_RESEARCH_ASSET':asset}
  f=(root/(venue+'_'+asset+'_compute.log')).open('w',encoding='utf-8')
  flags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name=='nt' else 0
  proc=subprocess.Popen([sys.executable,str(script),venue],env=env,stdout=f,stderr=subprocess.STDOUT,creationflags=flags)
  processes.append((proc,f,asset))
 print(json.dumps({'started':venue,'transport_rows':transport['rows'],'gaps':len(transport['gaps'])}),flush=True)
 while any(p.poll() is None for p,_,_ in processes):time.sleep(2)
 for p,f,asset in processes:
  f.close()
  if p.returncode:raise RuntimeError('Reduction failed: '+venue+' '+asset+'; see log')
 print(json.dumps({'completed':venue}),flush=True)
