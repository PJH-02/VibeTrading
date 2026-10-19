import json,sqlite3
from pathlib import Path
out=[]
for p in (Path('/srv/vibetrading-data/runtime_data/state/collection_gaps.sqlite3'),Path('/srv/vibetrading-crypto-state/collection_gaps.sqlite3')):
 if not p.exists():continue
 c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
 for r in c.execute('SELECT provider,symbols,started_ns,ended_ns,reason,unsent_events FROM gaps'):
  if r['provider'].replace('-perp','') in ('binance','bybit','bitget','grvt','arcus'):out.append(dict(r))
 c.close()
Path('/srv/vibetrading-capacity/fv-research-20261004/collector_gaps.json').write_text(json.dumps(out),encoding='utf-8')
print(json.dumps({'gap_records':len(out),'open_gap_records':sum(r['ended_ns'] is None for r in out)}))
