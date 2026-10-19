"""Read-only horizon, reference-feed and same-asset feature diagnostics.

All labels use the last received quote at t+H, never the next quote after it.
Alternative fits use only the separate training ledger; no evaluation tuning.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sqlite3

import numpy as np

VENUES = ('arcus', 'bybit', 'bitget', 'grvt')
HORIZONS = (.1, .5, 1, 2, 5, 10, 30, 60, 120, 300)
FEATURES = ('ofi_5s', 'signed_trade_5s', 'obi', 'signed_visible_add_5s',
            'signed_visible_remove_5s', 'trade_intensity_5s', 'ofi_x_tfi', 'tfi_x_obi')


def connect(path):
    c = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args)]


def quantiles(a):
    return dict(zip(('min', 'p50', 'p95', 'p99', 'max'),
                    map(float, np.quantile(a, (0, .5, .95, .99, 1))))) if len(a) else None


def quote_arrays(items):
    return {k: np.array([float(r[k]) for r in items]) for k in
            ('t', 'bid', 'ask', 'bid_qty', 'ask_qty', 'reset')}


def future_mid(q, t, horizon, max_age=3):
    """Receipt-time, right-continuous price with strict evidence bounds."""
    target = np.asarray(t) + horizon
    ix = np.searchsorted(q['t'], target, side='right') - 1
    safe = np.clip(ix, 0, len(q['t']) - 1)
    good = (ix >= 0) & (target <= q['t'][-1]) & (target - q['t'][safe] <= max_age)
    return (q['bid'][safe] + q['ask'][safe]) / 2, good


def loss_metrics(t, y, pred, horizon, seed=20261005):
    y, pred, t = map(np.asarray, (y, pred, t))
    gain = y*y - (y-pred)**2
    base = float(np.mean(y*y))
    block_seconds = max(60, 2*horizon)
    ids = np.floor((t-t[0])/block_seconds).astype(int)
    blocks = [gain[ids == b] for b in sorted(set(ids))]
    result = {'n':len(y), 'rmse':float(np.sqrt(np.mean((y-pred)**2))),
              'no_change_rmse':float(np.sqrt(base)),
              'rmse_reduction_percent':100*(1-float(np.sqrt(np.mean((y-pred)**2)/base))) if base else None,
              'mse_reduction_fraction':float(np.mean(gain)/base) if base else None,
              'block_seconds':block_seconds, 'time_blocks':len(blocks),
              'mse_gain_ci95':None, 'p_loss_gain_block_centered':None}
    nonzero = (y != 0) & (pred != 0)
    yt, pt = y[nonzero], pred[nonzero]
    up, down = yt > 0, yt < 0
    result['direction_evaluable_pairs'] = int(nonzero.sum())
    result['up_pairs'] = int(up.sum()); result['down_pairs'] = int(down.sum())
    result['zero_future_moves'] = int((y==0).sum())
    result['median_absolute_future_move_bps'] = float(np.median(np.abs(y)))
    result['median_absolute_forecast_move_bps'] = float(np.median(np.abs(pred)))
    result['direction_accuracy_nonzero'] = float(np.mean(np.sign(yt)==np.sign(pt))) if len(yt) else None
    result['direction_balanced_accuracy'] = float(((pt[up]>0).mean()+(pt[down]<0).mean())/2) if up.any() and down.any() else None
    if len(blocks) >= 10:
        rng = np.random.default_rng(seed)
        sums = np.array([b.sum() for b in blocks]); sizes = np.array([len(b) for b in blocks])
        selected = rng.integers(0, len(blocks), (1000, len(blocks)))
        bootstrap = sums[selected].sum(axis=1)/sizes[selected].sum(axis=1)
        result['mse_gain_ci95'] = np.quantile(bootstrap, [.025,.975]).tolist()
        result['p_loss_gain_block_centered'] = float((1+(np.abs(bootstrap-gain.mean())>=abs(gain.mean())).sum())/1001)
    else:
        result['inference_limitation'] = 'Fewer than 10 time blocks; descriptive only.'
    return result


def bh(records):
    values = sorted((r['p_loss_gain_block_centered'], i) for i,r in enumerate(records)
                    if r.get('p_loss_gain_block_centered') is not None)
    q = 1.
    for rank in range(len(values)-1, -1, -1):
        p, i = values[rank]; q = min(q, p*len(values)/(rank+1))
        records[i]['q_loss_gain_bh_exploratory'] = q


def window_sum(event_t, value, grid, width=5):
    cum = np.r_[0, np.cumsum(value)]
    right = np.searchsorted(event_t, grid, side='right')
    left = np.searchsorted(event_t, grid-width, side='right')
    return cum[right]-cum[left]


def feature_matrix(q, trades, grid):
    ix = np.searchsorted(q['t'], grid, side='right')-1
    safe = np.clip(ix, 0, len(q['t'])-1)
    b,a,bq,aq = (q[k] for k in ('bid','ask','bid_qty','ask_qty'))
    # Price-aware OFI; changed BBO observations, not a cancellation classifier.
    u = np.r_[0, np.where(b[1:]>=b[:-1], bq[1:],0)-np.where(b[1:]<=b[:-1],bq[:-1],0)]
    v = np.r_[0, np.where(a[1:]<=a[:-1], aq[1:],0)-np.where(a[1:]>=a[:-1],aq[:-1],0)]
    same_b = np.r_[False, b[1:]==b[:-1]]; same_a = np.r_[False, a[1:]==a[:-1]]
    db, da = np.r_[0,np.diff(bq)]*same_b, np.r_[0,np.diff(aq)]*same_a
    add = np.maximum(db,0)-np.maximum(da,0)
    remove = np.maximum(-da,0)-np.maximum(-db,0)
    depth = np.maximum((bq[safe]+aq[safe])/2, 1e-12)
    ofi = window_sum(q['t'],u-v,grid)/depth
    tt = np.array([x['t'] for x in trades]); qty = np.array([float(x['qty']) for x in trades])
    sign = np.array([1 if x['side']=='Buy' else -1 for x in trades])
    tfi = window_sum(tt, qty*sign, grid)/depth
    intensity = window_sum(tt,qty,grid)/depth
    obi = (bq[safe]-aq[safe])/(bq[safe]+aq[safe])
    features = np.column_stack((ofi,tfi,obi,window_sum(q['t'],add,grid)/depth,
                window_sum(q['t'],remove,grid)/depth,intensity,ofi*tfi,tfi*obi))
    # A snapshot reset within the feature window invalidates flow continuity.
    resets = window_sum(q['t'],q['reset'],grid)
    valid = (ix>=0) & (grid-q['t'][safe]<=3) & (grid-q['t'][0]>=5) & (resets==0)
    return features, (b[safe]+a[safe])/2, valid & np.isfinite(features).all(axis=1)


def fit_ridge(x, y, penalty=10.):
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.where(scale>1e-12,scale,1.)
    z = (x-mean)/scale
    coef = np.linalg.solve(z.T@z+penalty*np.eye(z.shape[1]),z.T@(y-y.mean()))
    return {'mean':mean,'scale':scale,'coef':coef,'intercept':float(y.mean()),'penalty':penalty}


def predict(model, x):
    return (x-model['mean'])/model['scale']@model['coef']+model['intercept']


def legacy_bitget_quotes(qr):
    """The immutable pre-fix training ledger marked every full snapshot reset.

    Its native books50 stream was audited as continuous with increasing seq.
    Repair logical flags in memory only; do not modify the original ledger.
    """
    repaired=[];last=None
    for r in qr:
        r=dict(r);r['reset']=int(last is None or r['seq']<=last)
        last=r['seq'];repaired.append(r)
    return repaired


def load_market(c, venue, asset, before_ns=None, legacy_bitget_snapshots=False):
    suffix=' AND wall_ns<=?' if before_ns is not None else ''
    args=(venue,asset,before_ns) if before_ns is not None else (venue,asset)
    qr=rows(c,'SELECT * FROM quotes WHERE venue=? AND asset=?'+suffix+' ORDER BY t',args)
    if venue=='bitget' and legacy_bitget_snapshots:
        assert c.execute("SELECT count(*) FROM events WHERE venue='bitget' AND kind='feed_gap'").fetchone()[0]==0
        qr=legacy_bitget_quotes(qr)
    q=quote_arrays(qr)
    tr=rows(c,"SELECT * FROM public_trades WHERE venue=? AND asset=? AND side IN ('Buy','Sell')"+suffix+' ORDER BY t',args)
    # Match the runtime's exclusion of historical trade snapshots.
    kept=[]
    for trd in tr:
        i=np.searchsorted(q['t'],trd['t'],side='right')-1
        if i>=0 and (not qr[i]['source_ns'] or trd['source_ns']>=qr[i]['source_ns']-10*10**9): kept.append(trd)
    return q,qr,kept


def analyze(directory, training, output):
    output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((directory/'manifest.json').read_text())
    complete=json.loads((directory/'complete.json').read_text())
    c,tc=connect(directory/'run.sqlite'),connect(training/'run.sqlite')
    horizons=[]; shape={}; quality={}; market_cache={}
    end=complete['evaluation_end_ns']; start=complete['evaluation_start_ns']
    for venue in VENUES:
        for sid,(_,asset) in manifest['routes'].items():
            key=venue+':'+sid
            q,qr,tr=load_market(c,venue,asset,end); market_cache[venue,asset]=(q,qr,tr)
            samples=rows(c,"SELECT * FROM samples WHERE venue=? AND strategy=? AND phase='evaluation' ORDER BY t",(venue,sid))
            valid=[r for r in samples if r['fv'] is not None]
            shape[key]={'fv_available_samples':len(valid),'coefficient':manifest['models'][key].get('coefficient'),
                'window_seconds':quantiles([r['window'] for r in valid]),
                'spread_bps':quantiles([r['spread']/r['mid']*1e4 for r in valid]),
                'fv_shift_bps':quantiles([abs(r['fv']-r['mid'])/r['mid']*1e4 for r in valid])}
            if valid:
                t=np.array([r['t'] for r in valid]); mid=np.array([r['mid'] for r in valid]); pred=np.array([r['fv']-r['mid'] for r in valid])/mid*1e4
                for h in HORIZONS:
                    future,mask=future_mid(q,t,h)
                    if mask.sum()<30:continue
                    metric=loss_metrics(t[mask],(future[mask]-mid[mask])/mid[mask]*1e4,pred[mask],h)
                    horizons.append({'venue_strategy':key,'horizon_seconds':h,**metric})
    bh(horizons)
    refs={}
    for asset in ('BTC','ETH','SOL','XRP'):
        rr=rows(c,"SELECT * FROM quotes WHERE venue='binance' AND asset=? AND wall_ns BETWEEN ? AND ? ORDER BY t",(asset,start,end))
        refs[asset]=rr
        rt=np.array([x['t'] for x in rr]); source=np.array([x['source_ns'] for x in rr],dtype=np.int64)
        quality['binance:'+asset]={'changed_quote_rows':len(rr),
            'source_to_journal_ms':quantiles([(x['wall_ns']-x['source_ns'])/1e6 for x in rr]),
            'receipt_gap_seconds':quantiles(np.diff(rt)),
            'source_time_regressions':int((np.diff(source)<0).sum()),
            'sequence_regressions':int((np.diff([x['seq'] for x in rr])<0).sum())}
    rr=refs['ETH']; rt=np.array([x['t'] for x in rr]); s2={}
    for venue in VENUES:
        ss=rows(c,"SELECT * FROM samples WHERE venue=? AND strategy='S2' AND phase='evaluation' ORDER BY t",(venue,))
        st=np.array([x['t'] for x in ss]); ix=np.searchsorted(rt,st,side='right')-1; valid=ix>=0
        m=manifest['models'][venue+':S2']; gap=[(x['ref_mid']*np.exp(m['basis'])-x['mid'])/x['mid']*1e4 for x in ss if x['ref_mid']]
        inst=manifest['instruments'][venue]['ETH']; fee_bps=(float(inst['maker_fee'])+float(inst['taker_fee']))*10000
        s2[venue]={'basis_bps':m['basis']*10000,'lambda':m.get('coefficient'),'deployable':m['valid'],
            'missing_reference_samples':sum(x['ref_mid'] is None for x in ss),
            'reference_age_since_last_changed_quote_ms':quantiles((st[valid]-rt[ix[valid]])*1000),
            'basis_adjusted_gap_bps':quantiles(gap),'maker_plus_taker_fee_bps':fee_bps,
            'reason_counts':dict(Counter(x['reason'] for x in ss))}
    combo=[]
    # Same BTC asset for all features; do not combine S1 BTC with S3 SOL.
    groups={'OFI5':(0,), 'OFI5_TFI5':(0,1), 'combined8':tuple(range(8))}
    for venue in VENUES:
        tq,_,ttr=load_market(tc,venue,'BTC',legacy_bitget_snapshots=True);eq,_,etr=market_cache[venue,'BTC']
        tg=np.arange(tq['t'][0]+5,tq['t'][-1],1.)
        eval_quotes=[r for r in market_cache[venue,'BTC'][1] if start<=r['wall_ns']<=end]
        eg=np.arange(eval_quotes[0]['t']+5,eval_quotes[-1]['t'],1.)
        tx,tm,tmask=feature_matrix(tq,ttr,tg);ex,em,emask=feature_matrix(eq,etr,eg)
        for h in (1,5,30,60):
            tf,tv=future_mid(tq,tg,h);ef,ev=future_mid(eq,eg,h)
            a,b=tmask&tv,emask&ev
            if a.sum()<100 or b.sum()<100:continue
            ty=(tf[a]-tm[a])/tm[a]*1e4;ey=(ef[b]-em[b])/em[b]*1e4
            for group,columns in groups.items():
                model=fit_ridge(tx[a][:,columns],ty)
                yp=predict(model,ex[b][:,columns])
                metric=loss_metrics(eg[b],ey,yp,h)
                train_z=(tx[a][:,columns]-model['mean'])/model['scale']
                eval_z=(ex[b][:,columns]-model['mean'])/model['scale']
                extrapolation={'max_absolute_train_standardized_feature':float(np.max(np.abs(train_z))),
                    'max_absolute_eval_standardized_feature':float(np.max(np.abs(eval_z))),
                    'eval_pairs_with_any_feature_over_10_train_std':int(np.any(np.abs(eval_z)>10,axis=1).sum())}
                combo.append({'venue':venue,'asset':'BTC','group':group,'features':[FEATURES[i] for i in columns],
                    'horizon_seconds':h,'training_pairs':int(a.sum()),'training_target_mean_bps':model['intercept'],
                    'frozen_standardized_coefficients':model['coef'].tolist(),'ridge_penalty':model['penalty'],**extrapolation,**metric})
    bh(combo)
    result={'method':'Post-run exploratory analysis; original 30s coefficients frozen across horizons; labels use last received BBO at t+H, max changed-quote age 3s; subsecond signals sampled at original ~1s decision times. Alternatives fit independent 600s training only, fixed ridge=10, no evaluation selection. Loss-gain BH families are separate from the earlier feature-association tests.',
        'horizons':horizons,'signal_shape':shape,'reference_quality':quality,'s2_diagnosis':s2,'same_asset_combination':combo,
        'limits':['Displayed decreases do not identify cancellations; remove/add variables are net visible-size proxies at unchanged BBO prices.',
            'OFI already contains executions in book changes; trade features are separate regressors, not added to OFI as duplicate volume.',
            'Bitget pre-fix training full snapshots were marked reset at every frame; logical continuity is reconstructed in memory from increasing sequence in the audited gap-free original training stream, as in the prior calibration replay.',
            'No trading/fill profitability tested here; short training and 30min evaluation do not validate persistent alpha.',
            'Source-to-journal delay includes source clock offset, network, event-loop and logging; not pure one-way network latency.',
            'Subsecond signal availability is limited by source-to-receipt latency and original ~1s signal samples.']}
    (output/'prediction_diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for name,data in (('horizon_metrics',horizons),('combination_metrics',combo)):
        keys=sorted({k for r in data for k in r if not isinstance(r[k],(list,dict))})
        with (output/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,keys,extrasaction='ignore');w.writeheader();w.writerows(data)
    c.close();tc.close();return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--training',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=analyze(a.directory,a.training,a.output)
    print(json.dumps({'frozen_horizon_rows':len(r['horizons']),'combined_rows':len(r['same_asset_combination']),'s2':r['s2_diagnosis']},indent=2))
