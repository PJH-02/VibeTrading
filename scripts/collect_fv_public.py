"""Read-only pre-calibration collector; no account clients or order submissions."""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
import time
import aiohttp
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from vibetrading.fv_mm.feeds import Feeds
from vibetrading.fv_mm.journal import Journal
from vibetrading.fv_mm.market import Markets,ROUTES
from vibetrading.fv_mm.calibration import calibrate
from vibetrading.mm_state.calibration import fit_states


async def configure_public_ticks(market,journal):
    """Public testnet instrument metadata only; no login, signer or order calls."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async def fetch(venue,asset):
            if venue=="bybit":
                async with session.get("https://api-testnet.bybit.com/v5/market/instruments-info",params={"category":"linear","symbol":asset+"USDT"}) as r:
                    r.raise_for_status();data=await r.json()
                    if data.get("retCode")!=0:raise RuntimeError("Public testnet metadata unavailable")
                    row=data["result"]["list"][0];tick=float(row["priceFilter"]["tickSize"])
            else:
                async with session.post("https://market-data.testnet.grvt.io/full/v1/instrument",json={"instrument":asset+"_USDT_Perp"}) as r:
                    r.raise_for_status();row=(await r.json())["result"];tick=float(row["tick_size"])
            if tick<=0:raise ValueError("Invalid public tick size")
            market.models[venue,asset].engine.tick=tick
            return venue+":"+asset,row
        pairs=await asyncio.gather(*(fetch(v,a) for v in ("bybit","grvt") for _,a in ROUTES.values()))
        journal.save("instruments.json",dict(pairs))


async def main(args):
    if (args.output/"run.sqlite").exists():
        raise ValueError("Use a new pre-calibration directory")
    journal=Journal(args.output)
    market=Markets(journal,"legacy_fv" if args.legacy_fv else "state")
    if not args.legacy_fv:await configure_public_ticks(market,journal)
    feeds=Feeds(market.on_book,market.on_trade,market.on_gap,market.on_reference)
    tasks=feeds.tasks(); start=time.monotonic()
    journal.save("manifest.json",{"mode":"public_precalibration_no_orders","start_ns":time.time_ns(),"duration":args.duration,"routes":ROUTES})
    try:
        while time.monotonic()-start<args.duration:
            market.sample(); journal.commit()
            journal.save("status.json",{"elapsed":round(time.monotonic()-start,1),"quotes":len(market.quotes),"samples":journal.db.execute("SELECT count(*) FROM samples").fetchone()[0],"feed_gaps":journal.db.execute("SELECT count(*) FROM events WHERE kind='feed_gap'").fetchone()[0]})
            await asyncio.sleep(1)
    finally:
        feeds.closed=True
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        models={}
        for venue in ("bybit","grvt"):
            for sid in ROUTES:
                table="samples" if args.legacy_fv else "state_samples"
                rows=[dict(r) for r in journal.db.execute("SELECT * FROM "+table+" WHERE venue=? AND strategy=? ORDER BY t",(venue,sid))]
                models[venue+":"+sid]=calibrate(rows,args.duration) if args.legacy_fv else fit_states(rows,sid,.0002 if venue=="bybit" else 0)
        journal.save("models.json",models)
        journal.save("complete.json",{"end_ns":time.time_ns(),"elapsed":time.monotonic()-start,"models":models})
        journal.close()
    print(json.dumps(models,indent=2),flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--duration",type=int,default=600)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--legacy-fv",action="store_true",help="Historical OLS reproduction only")
    asyncio.run(main(p.parse_args()))
