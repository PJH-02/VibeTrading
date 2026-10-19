"""Static research figure: frozen FV price displacement vs actual future mid."""
import argparse
import json
from pathlib import Path
import sqlite3

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


def plot(directory,output):
    result=json.loads((output/"results.json").read_text(encoding="utf-8"))
    db=sqlite3.connect(directory/"run.sqlite");db.row_factory=sqlite3.Row
    for venue in result["manifest"].get("venues",result.get("account_end",{})):
        fig,axes=plt.subplots(2,2,figsize=(12,8),layout="constrained")
        for ax,(sid,(_,asset)) in zip(axes.flat,result["manifest"]["routes"].items()):
            rows=[dict(r) for r in db.execute("SELECT * FROM samples WHERE venue=? AND strategy=? AND phase='evaluation' ORDER BY t",(venue,sid))]
            times=np.array([r["t"] for r in rows]); x,y=[],[]
            model=result["performance"][venue+":"+sid]["model"]
            currency=result["manifest"].get("instruments",{}).get(venue,{}).get(asset,{}).get("currency","USDT")
            for n,r in enumerate(rows):
                k=int(np.searchsorted(times,r["t"]+30))
                if k>=len(rows) or times[k]-r["t"]-30>2.5:continue
                if model.get("valid") and r["fv"] is not None:
                    x.append(r["fv"]-r["mid"])
                elif not model.get("valid") and r["reason"] in ("unfitted_coefficient","convergence_coefficient_out_of_range_or_unfitted"):
                    x.append(r["x"])
                else:continue
                y.append(rows[k]["mid"]-r["mid"])
            if x:
                ax.scatter(x,y,s=9,alpha=.28,color="#247b96",edgecolors="none")
                if model.get("valid"):
                    low,high=min(x),max(x)
                    ax.plot([low,high],[low,high],color="#c6643c",lw=1,label="Perfect 30s FV forecast")
                    ax.legend(loc="best",fontsize=8)
            else:ax.text(.5,.5,"Insufficient valid activity / future labels",ha="center",va="center",transform=ax.transAxes,fontsize=9)
            ax.axhline(0,color="#888",lw=.6);ax.axvline(0,color="#888",lw=.6)
            ax.set_title(f"{sid} / {asset} - n={len(x)}",loc="left",fontsize=11)
            ax.set_xlabel(f"Frozen FV - current mid ({currency})" if model.get("valid") else f"Raw feature x ({currency}); no deployable FV",fontsize=9)
            ax.set_ylabel(f"Actual mid(t+30s) - mid(t) ({currency})",fontsize=9)
            ax.xaxis.set_major_locator(MaxNLocator(5))
            ax.yaxis.set_major_locator(MaxNLocator(6))
            ax.ticklabel_format(axis="both",style="sci",scilimits=(-3,4),useMathText=True)
            ax.tick_params(labelsize=8)
            ax.spines[["right","top"]].set_visible(False)
        label=f"exploratory {result['manifest']['duration']/60:g}-minute FV validation" if result.get("requested_duration_met") else f"interrupted execution check ({result['completion']['evaluation_elapsed_seconds']/60:.1f} min)"
        environment="MAINNET PILOT" if result['manifest']['mode'].startswith('mainnet_public') else "TESTNET"
        fig.suptitle(f"{venue.upper()} {environment} - {label}",fontsize=15)
        fig.savefig(output/(venue+"_fv_validation.png"),dpi=160);plt.close(fig)
    db.close()


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("directory",type=Path);p.add_argument("--output",type=Path,required=True)
    args=p.parse_args();plot(args.directory,args.output)
