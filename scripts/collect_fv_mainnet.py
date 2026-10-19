"""Public-only, four-venue live pre-calibration. No authentication or order API."""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from vibetrading.fv_mm.journal import Journal
from vibetrading.fv_mm.market import ROUTES
from vibetrading.fv_mm.calibration import calibrate
from vibetrading.mm_state.calibration import fit_states
from vibetrading.fv_pilot.market import PilotMarkets
from vibetrading.fv_pilot.public import PublicFeeds,metadata,VENUES,ASSETS


async def main(args):
    if (args.output/"run.sqlite").exists():raise ValueError("Use a new output directory")
    instruments,native=await metadata()
    journal=Journal(args.output);market=PilotMarkets(journal,"legacy_fv" if args.legacy_fv else "state")
    if not args.legacy_fv:
        for (venue,asset),model in market.models.items():model.engine.tick=float(instruments[venue][asset].tick)
    journal.save("metadata.json",native)
    journal.save("instruments.json",{v:{a:asdict(i) for a,i in rows.items()} for v,rows in instruments.items()})
    feeds=PublicFeeds(market.book,market.trade,market.on_gap,market.on_reference);tasks=feeds.tasks()
    warm=time.monotonic()
    while len([key for key in market.quotes if key[0] in VENUES])<16 or len(market.references)<4:
        journal.save("status.json",{"phase":"warming","elapsed_seconds":round(time.monotonic()-warm,1),"books":[v+":"+a for v,a in market.quotes],"references":list(market.references),"errors":[dict(r) for r in journal.db.execute("SELECT venue,data FROM events WHERE kind='feed_gap' ORDER BY t DESC LIMIT 5")]})
        journal.commit()
        if time.monotonic()-warm>90:raise RuntimeError("Not all 16 mainnet books/reference feeds ready")
        await asyncio.sleep(.25)
    start=time.monotonic();journal.save("manifest.json",{"mode":"mainnet_public_training_no_orders","start_ns":time.time_ns(),"duration":args.duration,"routes":ROUTES,"venues":VENUES})
    try:
        while time.monotonic()-start<args.duration:
            market.sample();journal.commit()
            journal.save("status.json",{"phase":"training","elapsed_seconds":round(time.monotonic()-start,1),"books":{v+":"+a:n for (v,a),n in market.book_counts.items()},"trades":{v+":"+a:n for (v,a),n in market.trade_counts.items()},"unknown_sides":{v+":"+a:n for (v,a),n in market.unknown_counts.items()},"feed_gaps":journal.db.execute("SELECT count(*) FROM events WHERE kind='feed_gap'").fetchone()[0]})
            await asyncio.sleep(1)
    finally:
        feeds.closed=True
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    if args.legacy_fv:
        models={v+":"+sid:calibrate([dict(r) for r in journal.db.execute("SELECT * FROM samples WHERE venue=? AND strategy=? ORDER BY t",(v,sid))],args.duration) for v in VENUES for sid in ROUTES}
    else:
        models={v+":"+sid:fit_states([dict(r) for r in journal.db.execute("SELECT * FROM state_samples WHERE venue=? AND strategy=? ORDER BY t",(v,sid))],sid,instruments[v][ROUTES[sid][1]].maker_fee) for v in VENUES for sid in ROUTES}
    journal.save("models.json",models);journal.save("complete.json",{"end_ns":time.time_ns(),"elapsed_seconds":time.monotonic()-start,"models":models})
    journal.close();print(json.dumps({k:{x:v.get(x) for x in ('valid','coefficient','fit_samples','error_calibration_samples','reason')} for k,v in models.items()},indent=2),flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--duration",type=int,default=600);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--legacy-fv",action="store_true",help="Historical OLS reproduction only")
    args=p.parse_args()
    if not 120<=args.duration<=3600:p.error("Duration must be 120..3600 seconds")
    asyncio.run(main(args))
