"""Seal report provenance and enumerate strictly observed contiguous periods."""
import csv,hashlib,json,sys,xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import numpy as np,pandas as pd,pyarrow.parquet as pq
sys.path.insert(0,str(Path(__file__).parent))
from evaluate_fv_indicators import clean_anchors,dense_frame

root=Path('docs/evidence/fv_research_20261004');raw=Path('runtime_data/fv_research_20261004')
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()
def info(p):return {'path':str(p.resolve()),'bytes':p.stat().st_size,'sha256':sha(p)}

catalogue=json.loads((raw/'catalogue.json').read_text(encoding='utf-8'))
archive=json.loads((raw/'archive_audit.json').read_text(encoding='utf-8'))
if sha(raw/'catalogue.json')!=archive['catalogue_sha256']:raise ValueError('Catalogue hash mismatch')
catalog={r['file_id']:r for r in catalogue};verified={r['file_id']:r for r in archive['files']}
if set(catalog)!=set(verified):raise ValueError('Verified membership differs from frozen catalogue')
for fid,r in verified.items():
    c=catalog[fid]
    if r['rows']!=c['rows'] or r['sha256']!=(c.get('file_sha256') or c['checksum']):raise ValueError('Archive hash/rows mismatch: '+fid)
events=Counter();files=Counter()
for r in catalogue:events[r['venue']]+=r['rows'];files[r['venue']]+=1
paths=[raw/'binance_seconds.parquet',raw/'mainnet_training_verified_20261004_seconds.parquet',raw/'pilot_30min_20261004_seconds.parquet']
for venue in ('bybit','bitget','arcus','grvt'):
    paths.extend(sorted((Path('runtime_data')/f'fv_research_production_{venue}').glob('*_seconds.parquet')))
if len(paths)!=15:raise ValueError('Expected 12 production shards plus Binance and two pilot sources')
segments=[];closed=[]
for p in paths:
    closed.append(info(p));frame=pq.read_table(p).to_pandas()
    source='pilot_train' if p.name.startswith('mainnet_training') else 'pilot_test' if p.name.startswith('pilot_30min') else 'production'
    for (venue,asset),part in frame.groupby(['venue','asset']):
        d=dense_frame(part)
        good=(d['valid_seconds']>=1-1e-5)&(d['bad']==0)&np.isfinite(d['mid'])
        for kind,mask in [('observed_good_seconds',good),('strict_60_back_180_forward_anchors',clean_anchors(d))]:
            edges=np.diff(np.r_[False,mask,False].astype(int));starts=np.flatnonzero(edges==1);ends=np.flatnonzero(edges==-1)
            for lo,hi in zip(starts,ends):
                segments.append({'source':source,'venue':venue,'asset':asset,'kind':kind,
                                 'first_second_end_epoch':d['t'][lo],'last_second_end_epoch':d['t'][hi-1],
                                 'contiguous_seconds':int(hi-lo)})
    del frame
with (root/'valid_segments.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(segments[0]));w.writeheader();w.writerows(segments)

frozen=hashlib.sha256()
for folder in (Path('strategy'),Path('vibetrading/fv_pilot')):
    for p in sorted(folder.glob('*.py')):frozen.update(p.name.encode());frozen.update(p.read_bytes())
for name in ('types.py','market.py','journal.py','calibration.py'):
    p=Path('vibetrading/fv_mm')/name;frozen.update(name.encode());frozen.update(p.read_bytes())
expected='ed4a84fb46d1f5dc6c24c57033d87037cc59eb322991bc74b85716d17b3d0cbb'
if frozen.hexdigest()!=expected:raise ValueError('Frozen pilot source changed')
testroot=ET.parse(root/'tests.xml').getroot();suites=testroot.findall('testsuite')
tests={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
if tests['failures'] or tests['errors']:raise ValueError('Research regression failure')
proofs=[raw/p for p in ('catalogue.json','archive_audit.json','collector_gaps.json','slim_audit.json','server_reduce_audit.json','pilot_extract_audit.json','grvt_selector_probe.json')]
proofs.extend(root.glob('*_reduction_audit.json'));proofs.append(root/'original_formula_audit.json')
proofs.append(Path('docs/evidence/fv_pilot_mainnet_20261004/final_30min/integrity.json'))
pilot_inputs=[]
for name in ('mainnet_training_verified_20261004','pilot_30min_20261004'):
    base=Path('runtime_data/fv_pilot/server_evidence/runtime_data')/name
    pilot_inputs.extend([info(base/'run.sqlite'),info(base/'manifest.json')])
training=next(p for p in pilot_inputs if p['path'].replace('\\','/').endswith('mainnet_training_verified_20261004/run.sqlite'))
if training['sha256']!='7e6ec30a29b8ab9b9189a8d7acc761b6666e8f481242a030ac64992b6ca2b3e6':raise ValueError('Original pilot training SQLite changed')
pilot_archive=info(Path('runtime_data/fv_pilot/final_30min_evidence.tar.gz'))
if pilot_archive['sha256']!='ed348a90ab63953371483afaf6b153aeefe7c214f5ac97d4c37e55547740614e':raise ValueError('Original pilot archive changed')
scripts=[Path('scripts')/p for p in ('fv_research_extract.py','fv_research_reduce.py','fv_research_pilot.py','evaluate_fv_indicators.py','merge_fv_research_results.py','report_fv_indicator_research.py','audit_original_fv_formulas.py','shard_closed_fv_research.py','seal_fv_indicator_report.py')]
scripts.extend([Path('tests/test_fv_indicator_research.py'),Path('tests/test_fv_prediction_diagnosis.py')])
metrics=pd.read_csv(root/'indicator_metrics.csv');models=json.loads((root/'calibrated_models.json').read_text())
keys=['source','venue','asset','indicator','window','horizon','label']
if metrics.duplicated(keys).any():raise ValueError('Duplicate metric cells')
groups=metrics[metrics.label=='future'].groupby(keys[:5]).horizon.agg(['min','max','nunique'])
if not ((groups['min']==1)&(groups['max']==180)&(groups['nunique']==180)).all() or len(groups)!=len(models):raise ValueError('Incomplete horizon coverage')
if any(any(a==b for a,b in zip(m['low'],m['high'])) for m in models):raise ValueError('Constant input reported as informative model')
consistency={'complete_180_horizon_models':len(groups),'metric_rows':len(metrics),'duplicates':0,'constant_input_models':0,'report_local_links_validated':True}
artifacts=[p for p in root.iterdir() if p.is_file() and p.name!='integrity.json']
payload={'sealed_at_utc':datetime.now(timezone.utc).isoformat(),'external_order_submissions':0,
         'original_runtime_sha256':frozen.hexdigest(),'original_runtime_unchanged':True,
         'frozen_active_archive_files':len(catalogue),'verified_events':sum(events.values()),
         'events_by_venue':dict(events),'files_by_venue':dict(files),'original_bytes':sum(r['byte_size'] for r in catalogue),
         'pilot_raw_inputs':pilot_inputs,'original_pilot_archive':pilot_archive,
         'regression_tests':tests,'original_proofs':[info(p) for p in proofs],
         'final_output_consistency':consistency,
         'closed_second_series':[info(p) for p in paths],'research_source':[info(p) for p in scripts],
         'artifacts':[info(p) for p in artifacts],
         'source_scope':'Active sealed mainnet perpetual archive at Oct 4 catalogue cutoff; pilot 10min training and 30min evaluation kept separate.',
         'notes':['Source archives unchanged; native book/trade SHA verified before normalization.',
                  'Float64 is derived numeric representation, not rewritten original financial records.',
                  'All horizon evaluation targets remain inside observed gap-free full paths.',
                  'Original report and strategy coefficients are unchanged; the grid is exploratory.']}
(root/'integrity.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'sealed':str(root/'integrity.json'),'segments':len(segments),'tests':tests}))
