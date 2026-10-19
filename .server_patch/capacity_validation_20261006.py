import json,pathlib,time,urllib.request,subprocess
r=pathlib.Path('/srv/vibetrading-capacity/incident-20261006');rows=[]
for i in range(16):
 row={'ts_ns':time.time_ns(),'health':{}}
 for root in (pathlib.Path('/srv/vibetrading-data/runtime_data/state/health'),pathlib.Path('/srv/vibetrading-crypto-state/health')):
  for p in root.glob('*.json'):
   if p.stem.startswith('collectors-') or p.stem in ('crypto-storage','storage','maintenance'):
    row['health'][str(p)]=json.loads(p.read_text())
 for ep in ('/varz','/jsz?streams=true&consumers=true&config=true'):
  with urllib.request.urlopen('http://127.0.0.1:8222'+ep,timeout=10) as f:row[ep]=json.load(f)
 rows.append(row);(r/'capacity-validation.json').write_text(json.dumps(rows,indent=2))
 crypto={pathlib.Path(k).stem:v.get('phase') for k,v in row['health'].items() if k.endswith('-perp.json')}
 store=next((v for k,v in row['health'].items() if k.endswith('/crypto-storage.json')),{});print(json.dumps({'sample':i,'crypto':crypto,'storage':{k:store.get(k) for k in ('ready','messages','acked','pending','rss_bytes','event_index')}}),flush=True)
 if i<15:time.sleep(20)
print('five-minute validation complete',flush=True)
