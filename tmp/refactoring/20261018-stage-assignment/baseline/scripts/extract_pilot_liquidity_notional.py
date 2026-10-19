"""Exact receipt-time quote stock integrals and public trade notionals.

Reads immutable pilot SQLite only. No financial/runtime/order APIs imported.
"""
import json,sqlite3,sys
from pathlib import Path
import numpy as np,pandas as pd,pyarrow.parquet as pq,pyarrow as pa

OUT=Path('runtime_data/fv_regime_20261005')

def stock_integral(times,stock,endpoints):
    """Piecewise-constant visible stock integral over (t-1,t]."""
    cumulative=np.r_[0.,np.cumsum(stock[:-1]*np.diff(times))]
    def at(t):
        j=np.searchsorted(times,t,side='right')-1;ok=j>=0;safe=np.maximum(j,0)
        return np.where(ok,cumulative[safe]+stock[safe]*(t-times[safe]),0.)
    return at(endpoints)-at(endpoints-1.)

def main():
    OUT.mkdir(exist_ok=True);audit={}
    raw=Path('runtime_data/fv_pilot/server_evidence/runtime_data')
    seconds=Path('runtime_data/fv_research_20261004')
    for run in ('mainnet_training_verified_20261004','pilot_30min_20261004'):
        frame=pq.read_table(seconds/(run+'_seconds.parquet')).to_pandas();out=[]
        c=sqlite3.connect((raw/run/'run.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
        a={}
        for (venue,asset),part in frame.groupby(['venue','asset']):
            q=pd.read_sql_query('SELECT wall_ns,bid,ask,bid_qty,ask_qty FROM quotes WHERE venue=? AND asset=? ORDER BY wall_ns,rowid',c,params=(venue,asset))
            start=int(part.t.min())-1;end=int(part.t.max());grid=np.arange(start+1,end+1,dtype=float)
            # Subtract integer epoch before converting to seconds.
            times=(q.wall_ns.to_numpy(dtype=np.int64)-start*10**9)/1e9
            b,ask,qb,qa=(q[k].to_numpy(dtype=float) for k in ('bid','ask','bid_qty','ask_qty'))
            valid=(b>0)&(ask>b)&(qb>=0)&(qa>=0)&(qb+qa>0)
            stock=np.where(valid,b*qb+ask*qa,0.);quantity=np.where(valid,qb+qa,0.)
            supply=stock_integral(times,stock,grid-start)
            supply_q=stock_integral(times,quantity,grid-start)
            event_supply=np.zeros(len(grid));event_count=np.zeros(len(grid))
            bins=np.floor(times).astype(int);inside=(bins>=0)&(bins<len(grid))&valid
            np.add.at(event_supply,bins[inside],stock[inside]);np.add.at(event_count,bins[inside],1)
            tr=pd.read_sql_query('SELECT wall_ns,source_ns,price,qty,trade_id FROM public_trades WHERE venue=? AND asset=? ORDER BY wall_ns,rowid',c,params=(venue,asset))
            historical=(tr.source_ns.to_numpy(dtype=np.int64)>0)&((tr.wall_ns-tr.source_ns).to_numpy(dtype=np.int64)>10**10)
            identifiers=tr.trade_id.fillna('').astype(str)
            duplicate=identifiers.duplicated().to_numpy()&(identifiers.to_numpy()!='')
            tb=np.floor((tr.wall_ns.to_numpy(dtype=np.int64)-start*10**9)/1e9).astype(int)
            ok=(~historical)&(~duplicate)&(tb>=0)&(tb<len(grid))
            demand=np.zeros(len(grid));demand_q=np.zeros(len(grid));trade_count=np.zeros(len(grid))
            volume=tr.qty.to_numpy(dtype=float);notional=volume*tr.price.to_numpy(dtype=float)
            np.add.at(demand,tb[ok],notional[ok]);np.add.at(demand_q,tb[ok],volume[ok]);np.add.at(trade_count,tb[ok],1)
            out.append(pd.DataFrame({'venue':venue,'asset':asset,'t':grid,'supply_notional_seconds':supply,
                                    'supply_quantity_seconds':supply_q,'trade_notional':demand,'trade_quantity':demand_q,
                                    'quote_event_stock_sum':event_supply,'quote_events':event_count,'trade_events':trade_count}))
            a[venue+':'+asset]={'quotes':len(q),'invalid_quotes':int((~valid).sum()),'public_trades':len(tr),
                              'historical_trades':int(historical.sum()),'duplicate_trade_ids':int(duplicate.sum()),
                              'accepted_trades':int(ok.sum()),'notional':float(demand.sum())}
        pq.write_table(pa.Table.from_pandas(pd.concat(out,ignore_index=True),preserve_index=False),OUT/(run+'_notional.parquet'),compression='zstd')
        audit[run]=a;c.close();print(json.dumps({'extracted':run,'routes':len(out)}),flush=True)
    (OUT/'notional_audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')

if __name__=='__main__':main()
