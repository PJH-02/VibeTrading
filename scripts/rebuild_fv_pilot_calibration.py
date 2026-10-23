"""Rebuild Bitget S1/S3 from immutable training frames after snapshot semantics fix."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys
from decimal import Decimal as D

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vibetrading.fv_mm.types import Quote
from vibetrading.fv_mm.calibration import calibrate
from vibetrading.fv_mm.market import ROUTES


def rebuild(directory,output):
    models=json.loads((directory/'models.json').read_text());manifest=json.loads((directory/'manifest.json').read_text())
    db=sqlite3.connect(f'file:{(directory/"run.sqlite").as_posix()}?mode=ro',uri=True);db.row_factory=sqlite3.Row
    if db.execute("SELECT count(*) FROM events WHERE venue='bitget' AND kind='feed_gap'").fetchone()[0]:raise ValueError('Replay requires a gap-free Bitget training interval')
    audit={}
    for sid in ('S1','S3'):
        module,asset=ROUTES[sid];model=importlib.import_module('vibetrading.strategies.research.state.'+module).Strategy()
        rows=[dict(r) for r in db.execute("SELECT * FROM samples WHERE venue='bitget' AND strategy=? ORDER BY t",(sid,))]
        events=[(r['t'],0,dict(r)) for r in db.execute("SELECT * FROM quotes WHERE venue='bitget' AND asset=? ORDER BY t",(asset,))]
        events.extend((r['t'],1,dict(r)) for r in db.execute("SELECT * FROM public_trades WHERE venue='bitget' AND asset=? ORDER BY t",(asset,)))
        events.sort(key=lambda r:(r[0],r[1]));index=0;q=None;previous=None
        for row in rows:
            while index<len(events) and events[index][0]<=row['t']:
                t,kind,e=events[index];index+=1
                if kind==0:
                    q=Quote(D(e['bid']),D(e['ask']),D(e['bid_qty']),D(e['ask_qty']),t,e['source_ns'],e['seq'])
                    reset=previous is None or e['seq']<=previous
                    model.on_book(q,reset=reset);previous=e['seq']
                elif q is not None and (not q.source_ns or e['source_ns']>=q.source_ns-10*10**9):model.on_trade(e['side'],D(e['qty']),t)
            if q:
                s=model.signal(q,row['t']);row.update(x=float(s.x),reason=s.reason,window=s.window_seconds)
        key='bitget:'+sid;before=models[key]
        models[key]=calibrate(rows,manifest['duration'])
        audit[key]={'before':before,'after':models[key],'reconstructed_samples':len(rows)}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(models,indent=2),encoding='utf-8')
    receipt={'original_training':str(directory),'raw_sqlite_sha256':hashlib.sha256((directory/'run.sqlite').read_bytes()).hexdigest(),'original_models_sha256':hashlib.sha256((directory/'models.json').read_bytes()).hexdigest(),'evaluation_data_used':False,'method':'Bitget full snapshots with increasing sequence retain OFI/flow history; replay original BBO/trade receipts at original sample times, then original chronological fit/error split','repaired':audit}
    output.with_suffix('.audit.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8');db.close()
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=rebuild(a.directory,a.output)
    print(json.dumps({k:v['after'] for k,v in r['repaired'].items()},indent=2))
