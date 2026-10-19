"""Compact read-only inspection of a public collector/pilot ledger."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("directory",type=Path);args=p.parse_args();d=args.directory
    result={}
    for name in ('status','complete'):
        path=d/(name+'.json')
        if path.exists():result[name]=json.loads(path.read_text())
    path=d/'run.sqlite'
    if path.exists():
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row
        result['recent_gaps']=[dict(r) for r in db.execute("SELECT venue,data FROM events WHERE kind='feed_gap' ORDER BY t DESC LIMIT 5")]
        result['trades']=[dict(r) for r in db.execute("SELECT venue,asset,count(*) n,max(source_ns) last_source_ns FROM public_trades GROUP BY venue,asset")]
        for r in result['trades']:r['last_source_utc']=datetime.fromtimestamp(r['last_source_ns']/1e9,timezone.utc).isoformat()
        row=db.execute("SELECT raw FROM public_frames WHERE venue='arcus' AND kind='trade' LIMIT 1").fetchone()
        if row:
            native=json.loads(row[0]);result['arcus_trade_native_fields']={'keys':list(native['contents'][0]),'side':native['contents'][0].get('side')}
        db.close()
    log=d/'process.log'
    if log.exists():result['process_log_tail']=log.read_text(errors='replace')[-1800:]
    print(json.dumps(result,indent=2))
