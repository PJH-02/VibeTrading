"""Freeze measured testnet timing evidence and explicitly named cross-venue proxies."""
import argparse
import json
from pathlib import Path
import sqlite3


def percentile(values,p):
    values=sorted(values);x=(len(values)-1)*p;n=int(x)
    return values[n]+(values[min(n+1,len(values)-1)]-values[n])*(x-n)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--source-root",type=Path,required=True);p.add_argument("--output",type=Path,required=True);args=p.parse_args()
    final=args.source_root/"fv_30min_verified_20261004"/"run.sqlite"
    earlier=args.source_root/"fv_30min_final_20261004"/"run.sqlite"
    db=sqlite3.connect(final);db.row_factory=sqlite3.Row
    bybit=[r[0] for r in db.execute("SELECT live-receive FROM orders WHERE venue='bybit' AND live IS NOT NULL")]
    cancel=[]
    for r in db.execute("SELECT t,data FROM events WHERE venue='bybit' AND kind='cancel_send'"):
        cid=json.loads(r['data'])['client_id'];terminal=db.execute("SELECT terminal FROM orders WHERE client_id=? AND status='Cancelled'",(cid,)).fetchone()
        if terminal:cancel.append(terminal[0]-r['t'])
    db.close();db=sqlite3.connect(earlier)
    grvt=[r[0] for r in db.execute("SELECT live-receive FROM orders WHERE venue='grvt' AND live IS NOT NULL")];db.close()
    if not bybit or not grvt or not cancel:raise ValueError("Required measured timing evidence is missing")
    budget=max(percentile(bybit,.95),percentile(grvt,.95));result={}
    result['bybit']={"entry_seconds":percentile(bybit,.95),"cancel_seconds":percentile(cancel,.95),"entry_samples":len(bybit),"cancel_samples":len(cancel),"origin":"Bybit testnet 2026-10-04; decision receipt→first live observation, cancel_send→terminal observation","proxy":False,"measurement_note":"Few observations; descriptive quantile, not a stable mainnet p95. Receipt→live includes private/REST observation delay."}
    result['grvt']={"entry_seconds":percentile(grvt,.95),"cancel_seconds":budget,"entry_samples":len(grvt),"cancel_samples":0,"origin":"GRVT testnet interrupted execution check; decision receipt→first live observation","proxy":False,"cancel_proxy":True,"measurement_note":"3 live observations; includes private/REST observation delay. Valid cancel timing unavailable; entry budget used as cancel proxy."}
    for venue in ('arcus','bitget'):
        result[venue]={"entry_seconds":budget,"cancel_seconds":budget,"entry_samples":0,"cancel_samples":0,"origin":"Conservative cross-venue proxy: maximum recorded Bybit/GRVT descriptive 95th percentile","proxy":True,"measurement_note":"Not measured on this venue. Primary assumption, with separate 2x stress scenario."}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
