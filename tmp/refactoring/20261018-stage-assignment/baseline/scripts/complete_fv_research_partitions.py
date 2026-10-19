"""Publish and evaluate only fully closed, hash-verified reduction shards."""
import hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path

native=Path('runtime_data/fv_research_native')
root=Path('docs/evidence/fv_research_20261004')
expected={'bybit':26269929,'bitget':15582523,'arcus':13759868,'grvt':948186}
def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    return h.hexdigest()

for venue,total in expected.items():
    audits=[native/f'reduce_audit_{venue}_{asset}.json' for asset in ('BTC','ETH','SOL')]
    while not all(p.exists() for p in audits):time.sleep(5)
    parsed=[json.loads(p.read_text())[venue] for p in audits]
    actual=sum(r['rows'] for r in parsed)
    if actual!=total:raise ValueError(f'{venue}: row reconciliation {actual} != {total}')
    data=Path('runtime_data')/f'fv_research_production_{venue}';data.mkdir(exist_ok=True)
    for asset,audit in zip(('BTC','ETH','SOL'),parsed):
        p=native/f'{venue}_{asset}_seconds.parquet'
        if digest(p)!=audit['seconds_sha256']:raise ValueError('Closed output SHA mismatch: '+str(p))
        shutil.copy2(p,data/p.name)
    (root/f'{venue}_reduction_audit.json').write_text(json.dumps(parsed,indent=2))
    print(json.dumps({'ready':venue,'reconciled_events':actual}),flush=True)
    env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'}
    flags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name=='nt' else 0
    output=root/f'production_{venue}'
    with (root/f'evaluation_{venue}.log').open('w',encoding='utf-8') as log:
        subprocess.run([sys.executable,'scripts/evaluate_fv_indicators.py','--production-only',
                        '--data',str(data),'--output',str(output)],env=env,stdout=log,
                       stderr=subprocess.STDOUT,creationflags=flags,check=True)
    print(json.dumps({'evaluated':venue}),flush=True)
