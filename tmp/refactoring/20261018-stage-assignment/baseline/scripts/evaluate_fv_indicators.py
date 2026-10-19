"""Gap-free, chronologically held-out indicator/FV response surface.

All 1..180 second horizons are fitted separately. Horizon zero is expressly
contemporaneous/backward-window association, never a forecast.
"""
from __future__ import annotations
import argparse,csv,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
sys.path.insert(0,str(Path(__file__).parent))
from diagnose_fv_prediction import bh

WINDOWS=(1,5,10,30,60)
HORIZONS=np.arange(1,181)
NAMES=('l1_obi','l2_total_obi_10','tracked_depth_obi','ofi','replenishment',
       'depletion','trade_imbalance','signed_trade_flow','microprice','ofi_trade_joint',
       'replenishment_rate_imbalance','trade_x_replenishment')
SELECT_H=(1,2,5,10,30,60,120,180)

def block_inference(t,y,p,h,seed=20261005):
    """Same centered block bootstrap as prior audit, linear-time aggregation."""
    gain=y*y-(y-p)**2;block=max(60,2*h)
    ids=np.floor((t-t[0])/block).astype(int)
    sizes=np.bincount(ids);sums=np.bincount(ids,weights=gain);present=sizes>0
    sizes=sizes[present];sums=sums[present];n=len(sizes)
    out={'block_seconds':block,'time_blocks':n,'mse_gain_ci95':None,'p_loss_gain_block_centered':None}
    if n>=10:
        selected=np.random.default_rng(seed).integers(0,n,(1000,n))
        bootstrap=sums[selected].sum(axis=1)/sizes[selected].sum(axis=1)
        out['mse_gain_ci95']=np.quantile(bootstrap,[.025,.975]).tolist()
        out['p_loss_gain_block_centered']=float((1+(abs(bootstrap-gain.mean())>=abs(gain.mean())).sum())/1001)
    return out

def rolling(x,w):
    cs=np.r_[0,np.cumsum(np.nan_to_num(x))]
    return np.r_[np.full(w-1,np.nan),cs[w:]-cs[:-w]]

def clean_anchors(d,lookback=60,forward=180):
    good=(d['valid_seconds']>=1-1e-5)&(d['bad']==0)&np.isfinite(d['mid'])
    bad=np.r_[0,np.cumsum(~good)]
    ix=np.arange(len(good));lo=ix-lookback;hi=ix+forward
    bounded=(lo>=0)&(hi<len(good))
    ok=np.zeros(len(good),bool);j=ix[bounded]
    ok[j]=(bad[hi[j]+1]-bad[lo[j]])==0
    return ok

def dense(items):
    items=sorted(items,key=lambda x:x['t']);start=int(items[0]['t']);end=int(items[-1]['t'])
    d={k:np.full(end-start+1,np.nan) for k in items[0] if k not in ('venue','asset')}
    d['t']=np.arange(start,end+1,dtype=float)
    for r in items:
        i=int(r['t'])-start
        for k in d:
            if k!='t':d[k][i]=float(r[k]) if r[k] is not None else np.nan
    return d

def dense_frame(frame):
    frame=frame.sort_values('t')
    if frame['t'].duplicated().any():raise ValueError('Duplicated seconds in the same source/venue/asset')
    start,end=int(frame.t.iloc[0]),int(frame.t.iloc[-1])
    frame=frame.drop(columns=['venue','asset']).set_index('t').reindex(range(start,end+1))
    return {'t':np.arange(start,end+1,dtype=float),**{k:frame[k].to_numpy(dtype=float) for k in frame.columns}}

def features(d,w):
    depth=np.maximum(rolling(d['avg_depth'],w)/w,1e-12)
    buy,sell=(rolling(d[k],w) for k in ('trade_buy','trade_sell'))
    unknown=rolling(d['trade_unknown'],w)
    ba,aa,br,ar=(rolling(d[k],w) for k in ('bid_add','ask_add','bid_remove','ask_remove'))
    ofi=np.arcsinh(rolling(d['ofi'],w)/depth)
    signed=np.arcsinh((buy-sell)/depth)
    replen=np.divide(ba-aa,ba+aa,out=np.zeros(len(ba)),where=ba+aa>0)
    depletion=np.divide(ar-br,ar+br,out=np.zeros(len(br)),where=ar+br>0)
    tfi=np.divide(buy-sell,buy+sell,out=np.zeros(len(buy)),where=buy+sell>0)
    bid_rate=np.divide(ba,ba+br,out=np.zeros(len(ba)),where=ba+br>0)
    ask_rate=np.divide(aa,aa+ar,out=np.zeros(len(aa)),where=aa+ar>0)
    # Unknown aggressor volume never becomes zero signed flow.
    tfi[unknown>0]=np.nan;signed[unknown>0]=np.nan
    return np.column_stack([rolling(d[k],w)/w for k in
           ('avg_obi_l1','avg_obi_l2_10','avg_obi_visible')]+[ofi,replen,depletion,tfi,signed,
           rolling(d['avg_microprice_bps'],w)/w,ofi*signed,bid_rate-ask_rate,signed*(ask_rate-bid_rate)])

def fit_features(x,y):
    """Train-only winsorization; univariate fits plus OFI+trade joint ridge."""
    low,high=np.quantile(x,[.01,.99],axis=0)
    clipped=np.clip(x,low,high);mean=clipped.mean(0);scale=clipped.std(0)
    scale=np.where(scale>1e-10,scale,1.)
    z=(clipped-mean)/scale;ym=y.mean(0)
    coef=(z.T@(y-ym))/(np.sum(z*z,axis=0)[:,None]+10.)
    return {'low':low,'high':high,'mean':mean,'scale':scale,'coef':coef,'intercept':ym}

def metrics_columns(t,y,p,h):
    b=np.mean(y*y);err=np.mean((y-p)**2)
    moving=y!=0;up=y>0;down=y<0
    return {'n':len(y),'rmse_bps':float(np.sqrt(err)),'mid_hold_rmse_bps':float(np.sqrt(b)),
            'rmse_reduction_percent':float(100*(1-np.sqrt(err/b))) if b>0 else None,
            'mae_bps':float(np.mean(abs(y-p))),
            'balanced_accuracy_moving':float(((p[up]>0).mean()+(p[down]<0).mean())/2) if up.any() and down.any() else None,
            'moving_pairs':int(moving.sum()),'zero_pairs':int((~moving).sum()),
            'median_abs_forecast_bps':float(np.median(abs(p))),
            'direction_accuracy_moving':float((np.sign(p[moving])==np.sign(y[moving])).mean()) if moving.any() else None}

def analyze_pair(source,venue,asset,train,test,fitmask,testmask,stride,models=None):
    rows=[];curves=[];missing=[];tr=features(train,1)
    ti=np.flatnonzero(fitmask)[::stride];ei=np.flatnonzero(testmask)[::stride]
    if len(ti)<100 or len(ei)<100:
        return [],[],[{'source':source,'venue':venue,'asset':asset,'reason':'fewer_than_100_gap_free_fit_or_test_anchors','fit_n':len(ti),'test_n':len(ei)}]
    ytrain=(train['mid'][ti[:,None]+HORIZONS]/train['mid'][ti,None]-1)*1e4
    ytest=(test['mid'][ei[:,None]+HORIZONS]/test['mid'][ei,None]-1)*1e4
    for w in WINDOWS:
        xt,xe=features(train,w)[ti],features(test,w)[ei]
        for j,name in enumerate(NAMES):
            cols=[3,7] if name=='ofi_trade_joint' else [j]
            ft=np.isfinite(xt[:,cols]).all(1);fe=np.isfinite(xe[:,cols]).all(1)
            if ft.sum()<100 or fe.sum()<100 or np.min(np.std(xt[ft][:,cols],axis=0))<1e-10:
                missing.append({'source':source,'venue':venue,'asset':asset,'window':w,'indicator':name,'reason':'unknown_side_or_constant_or_insufficient_excitation','fit_n':int(ft.sum()),'test_n':int(fe.sum())});continue
            tx,ex=xt[ft][:,cols],xe[fe][:,cols];yt,ye=ytrain[ft],ytest[fe]
            model=fit_features(tx,yt)
            if np.min(np.std(np.clip(tx,model['low'],model['high']),axis=0))<1e-10:
                missing.append({'source':source,'venue':venue,'asset':asset,'window':w,'indicator':name,
                                'reason':'train_winsorized_feature_constant','fit_n':int(ft.sum()),'test_n':int(fe.sum())});continue
            z=(np.clip(ex,model['low'],model['high'])-model['mean'])/model['scale']
            if name=='ofi_trade_joint':
                tz=(np.clip(tx,model['low'],model['high'])-model['mean'])/model['scale']
                coef=np.linalg.solve(tz.T@tz+10*np.eye(len(cols)),tz.T@(yt-model['intercept']))
            else:coef=model['coef']
            pred=z@coef+model['intercept']
            if models is not None:
                models.append({'source':source,'venue':venue,'asset':asset,'window':w,'indicator':name,
                               'feature_columns':[NAMES[k] for k in cols],'fit_n':int(ft.sum()),
                               'horizons':HORIZONS.tolist(),'low':model['low'].tolist(),'high':model['high'].tolist(),
                               'mean':model['mean'].tolist(),'scale':model['scale'].tolist(),
                               'standardized_coefficients':coef.tolist(),'intercept_bps':model['intercept'].tolist(),
                               'fit_first_t':float(train['t'][ti[ft][0]]),'fit_last_t':float(train['t'][ti[ft][-1]]),
                               'test_first_t':float(test['t'][ei[fe][0]])})
            times=test['t'][ei[fe]]
            # Same endpoint signal predicts backward-window return: separate,
            # descriptive label at H=0; never included in forecast ranking.
            past_train=(train['mid'][ti[ft]]/train['mid'][ti[ft]-w]-1)*1e4
            past_test=(test['mid'][ei[fe]]/test['mid'][ei[fe]-w]-1)*1e4
            past_model=fit_features(tx,past_train[:,None])
            past_coef=np.linalg.solve(tz.T@tz+10*np.eye(len(cols)),tz.T@(past_train[:,None]-past_model['intercept'])) if name=='ofi_trade_joint' else past_model['coef']
            pp=z@past_coef+past_model['intercept']
            zero={'source':source,'venue':venue,'asset':asset,'window':w,'indicator':name,'horizon':0,
                  'label':'contemporaneous_past_window','fit_n':int(ft.sum()),**metrics_columns(times,past_test,pp[:,0],0)}
            rows.append(zero)
            # Conditional response is eval descriptive, with thresholds frozen
            # at training quartiles. Do not use it to select/fix coefficients.
            q25,q75=np.quantile(tx[:,0],[.25,.75]);high=ex[:,0]>q75;low=ex[:,0]<q25
            curves.append({'source':source,'venue':venue,'asset':asset,'window':w,'indicator':name,'horizon':0,
                           'high_n':int(high.sum()),'low_n':int(low.sum()),'high_mean_response_bps':0.,'low_mean_response_bps':0.})
            for k,h in enumerate(HORIZONS):
                row={'source':source,'venue':venue,'asset':asset,'window':w,'indicator':name,'horizon':int(h),
                     'label':'future','fit_n':int(ft.sum()),'fit_signal_coef_bps':float(coef[0,k]/model['scale'][0]),
                     'fit_intercept_bps':float(model['intercept'][k]),
                     **metrics_columns(times,ye[:,k],pred[:,k],h)}
                drift=np.mean((ye[:,k]-model['intercept'][k])**2)
                row['rmse_reduction_vs_training_drift_percent']=float(100*(1-row['rmse_bps']/np.sqrt(drift))) if drift>0 else None
                if h in SELECT_H:
                    # Primary inference tests incremental signal information
                    # beyond training mean drift, not a lucky intercept.
                    m=block_inference(times,ye[:,k]-model['intercept'][k],pred[:,k]-model['intercept'][k],max(w,h))
                    row.update({key:m[key] for key in ('block_seconds','time_blocks','mse_gain_ci95','p_loss_gain_block_centered')})
                rows.append(row)
                curves.append({'source':source,'venue':venue,'asset':asset,'window':w,'indicator':name,'horizon':int(h),
                               'high_n':int(high.sum()),'low_n':int(low.sum()),
                               'high_mean_response_bps':float(ye[high,k].mean()) if high.any() else None,
                               'low_mean_response_bps':float(ye[low,k].mean()) if low.any() else None})
            if name=='microprice':
                for k,h in enumerate(HORIZONS):
                    rows.append({'source':source,'venue':venue,'asset':asset,'window':w,'indicator':'weighted_mid_raw',
                                 'horizon':int(h),'label':'future_uncalibrated','fit_n':0,
                                 **metrics_columns(times,ye[:,k],ex[:,0],h)})
        print(json.dumps({'source':source,'venue':venue,'asset':asset,'window':w,'rows':len(rows)}),flush=True)
    return rows,curves,missing

def write_csv(path,items):
    if not items:return
    keys=list(dict.fromkeys(k for r in items for k in r))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(items)

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('runtime_data/fv_research_20261004'))
    p.add_argument('--production-only',action='store_true')
    p.add_argument('--output',type=Path,default=Path('docs/evidence/fv_research_20261004'));a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    groups={};coverage=[];allrows=[];allcurves=[];missing=[];models=[]
    # Only one archive venue is resident; avoid millions of Python row dicts.
    paths=sorted(a.data.glob('*_seconds.parquet'),key=lambda p:0 if p.name.startswith('mainnet_training') else 1 if p.name.startswith('pilot_30min') else 2)
    if a.production_only:paths=[p for p in paths if not p.name.startswith(('mainnet_training','pilot_30min'))]
    def record(source,venue,asset,d):
        mask=clean_anchors(d)
        coverage.append({'source':source,'venue':venue,'asset':asset,'start':float(d['t'][0]),'end':float(d['t'][-1]),'seconds':len(d['t']),
                         'good_seconds':int(((d['valid_seconds']>=1-1e-5)&(d['bad']==0)).sum()),'gap_free_60_back_180_forward_anchors':int(mask.sum())})
        return mask
    for path in paths:
        source='pilot_train' if path.name.startswith('mainnet_training') else 'pilot_test' if path.name.startswith('pilot_30min') else 'production'
        frame=pq.read_table(path).to_pandas()
        for (venue,asset),part in frame.groupby(['venue','asset'],sort=True):
            d=dense_frame(part);mask=record(source,venue,asset,d)
            if source=='pilot_train':groups[venue,asset]=d;continue
            if source=='pilot_test':
                train=groups[venue,asset];test=d;fm=clean_anchors(train);em=mask;stride=1
            else:
                train=test=d;split=d['t'][0]+.6*(d['t'][-1]-d['t'][0]);fm=mask&(d['t']+180<split);em=mask&(d['t']-60>=split);stride=5
            r,c,m=analyze_pair(source,venue,asset,train,test,fm,em,stride,models)
            allrows.extend(r);allcurves.extend(c);missing.extend(m)
        del frame
    bh(allrows)
    write_csv(a.output/'indicator_metrics.csv',allrows);write_csv(a.output/'response_curves.csv',allcurves)
    write_csv(a.output/'coverage.csv',coverage);write_csv(a.output/'unavailable.csv',missing)
    (a.output/'calibrated_models.json').write_text(json.dumps(models,separators=(',',':')),encoding='utf-8')
    summary={'metrics_rows':len(allrows),'response_rows':len(allcurves),'coverage':coverage,'unavailable':missing,
             'windows':WINDOWS,'forecast_horizons':HORIZONS.tolist(),'inference_horizons':SELECT_H,
             'strict_common_support':'All 60 prior seconds and 180 future seconds valid; receipt-time source causality.',
             'production_split':'First 60% chronological fit, last 40% test; 180s label purge and 60s test feature embargo; anchors every 5s.',
             'pilot_split':'Separate immutable 10min train and 30min evaluation; anchors every 1s.',
             'calibration':'Per-horizon univariate intercept ridge=10; train-only 1/99 winsor; OFI+trade joint ridge.',
             'multiple_testing':'Exploratory BH for MSE gain beyond frozen training-mean drift, over every non-null block-centered p-value across both sources, all venues/assets/indicators/windows and 8 horizons; 1000 bootstrap draws (p resolution 1/1001).',
             'discovery_only':True}
    (a.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'completed':len(allrows),'output':str(a.output)}))

if __name__=='__main__':main()
