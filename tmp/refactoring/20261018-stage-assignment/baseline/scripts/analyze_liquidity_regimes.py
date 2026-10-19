"""Held-out liquidity/volatility regimes, realized price responses and ranges."""
import csv,json,sys
from pathlib import Path
import numpy as np,pandas as pd,pyarrow.parquet as pq
sys.path.insert(0,str(Path(__file__).parent))
from evaluate_fv_indicators import clean_anchors,dense_frame,features,rolling

ROOT=Path('docs/evidence/fv_regime_20261005');DATA=Path('runtime_data/fv_regime_20261005')
WINDOWS=(1,5,10,30,60);HORIZONS=np.arange(1,181);TEST_H=(5,25,30,60,180)
SIGNALS={'l1_obi':0,'l2_obi':1,'ofi':3,'trade_imbalance':6,'signed_trade_flow':7,'replenishment':4}
def write(path,rows):
    if rows:pd.DataFrame(rows).to_csv(path,index=False,encoding='utf-8-sig')

def signals(d,w):
    x=features(d,w);result={name:x[:,j] for name,j in SIGNALS.items()}
    ba,aa,br,ar=(rolling(d[k],w) for k in ('bid_add','ask_add','bid_remove','ask_remove'))
    bid=np.divide(ba,ba+br,out=np.zeros(len(ba)),where=ba+br>0)
    ask=np.divide(aa,aa+ar,out=np.zeros(len(aa)),where=aa+ar>0)
    signed=x[:,7];consumed=np.where(signed>=0,ask,bid)
    # Retain the direction of aggressive flow; weaken when consumed side refills.
    result['aggressive_weak_refill']=signed*(1-consumed)
    activity=np.where(signed>=0,aa+ar,ba+br)
    result['aggressive_weak_refill'][(signed!=0)&(activity==0)]=np.nan
    return result

def ratio(d,w,definition):
    if definition=='quantity_buffer_seconds':
        supply=2*rolling(d['avg_depth'],w);demand=rolling(d['trade_buy']+d['trade_sell']+d['trade_unknown'],w)
    elif definition=='notional_buffer_seconds':
        supply=rolling(d['supply_notional_seconds'],w);demand=rolling(d['trade_notional'],w)
    else:supply=rolling(d['quote_event_stock_sum'],w);demand=rolling(d['trade_notional'],w)
    return np.divide(supply,demand,out=np.full(len(supply),np.nan),where=demand>0),demand

def realized_rv(d,ix,hs):
    r=np.r_[0.,np.diff(d['mid'])/d['mid'][:-1]*1e4]
    cs=np.r_[0.,np.cumsum(np.nan_to_num(r*r))]
    return np.sqrt(np.maximum(0,cs[ix[:,None]+hs+1]-cs[ix[:,None]+1]))

def contrast(t,y,high,low,block,draws=1000):
    use=high|low;result={'n_high':int(high.sum()),'n_low':int(low.sum()),'block_seconds':block}
    if not high.any() or not low.any():return result
    estimate=float(y[high].mean()-y[low].mean());result['mean_high_minus_low']=estimate
    ids=np.floor((t-t[0])/block).astype(int);present=np.unique(ids[use]);n=len(present);result['blocks']=n
    if n<10 or min(high.sum(),low.sum())<30:return result
    def agg(mask,weights=None):return np.bincount(ids[mask],weights=weights,minlength=ids.max()+1)[present]
    hn,ln=agg(high),agg(low);hy,ly=agg(high,y[high]),agg(low,y[low])
    selected=np.random.default_rng(20261005).integers(0,n,(draws,n))
    dh,dl=hn[selected].sum(1),ln[selected].sum(1);ok=(dh>0)&(dl>0)
    boot=hy[selected].sum(1)[ok]/dh[ok]-ly[selected].sum(1)[ok]/dl[ok]
    result['ci_low'],result['ci_high']=np.quantile(boot,[.025,.975]).tolist()
    result['p_centered']=float((1+(abs(boot-estimate)>=abs(estimate)).sum())/(1+len(boot)))
    return result

def mean_ci(t,y,block=360):
    n=len(y);result={'n':n,'mean_bps':float(np.mean(y)) if n else None}
    if n<20:return result
    ids=np.floor((t-t[0])/block).astype(int);counts=np.bincount(ids);sums=np.bincount(ids,weights=y);ok=counts>0
    counts,sums=counts[ok],sums[ok];b=len(counts);result['blocks']=b
    if b<10:return result
    selection=np.random.default_rng(20261005).integers(0,b,(1000,b))
    boot=sums[selection].sum(1)/counts[selection].sum(1)
    result['ci_low'],result['ci_high']=np.quantile(boot,[.025,.975]).tolist();return result

def price_stats(y):
    return {'mean_bps':float(np.mean(y)),'median_bps':float(np.median(y)),
            'p25_bps':float(np.quantile(y,.25)),'p75_bps':float(np.quantile(y,.75)),
            'p_up':float(np.mean(y>0)),'p_down':float(np.mean(y<0)),'p_flat':float(np.mean(y==0))}

def datasets():
    base=Path('runtime_data/fv_research_20261004')
    extra={}
    for run in ('mainnet_training_verified_20261004','pilot_30min_20261004'):
        f=pq.read_table(DATA/(run+'_notional.parquet')).to_pandas()
        extra[run]={(v,a):dense_frame(g) for (v,a),g in f.groupby(['venue','asset'])}
    training=pq.read_table(base/'mainnet_training_verified_20261004_seconds.parquet').to_pandas()
    train={}
    for key,g in training.groupby(['venue','asset']):
        d=dense_frame(g);d.update({k:x for k,x in extra['mainnet_training_verified_20261004'][key].items() if k!='t'});train[key]=d
    pilot=pq.read_table(base/'pilot_30min_20261004_seconds.parquet').to_pandas()
    for key,g in pilot.groupby(['venue','asset']):
        d=dense_frame(g);d.update({k:x for k,x in extra['pilot_30min_20261004'][key].items() if k!='t'})
        yield 'pilot',*key,train[key],d,None
    paths=[base/'binance_seconds.parquet']
    for v in ('bybit','bitget','arcus','grvt'):paths.extend(sorted((Path('runtime_data')/('fv_research_production_'+v)).glob('*_seconds.parquet')))
    for path in paths:
        f=pq.read_table(path).to_pandas()
        for (v,a),g in f.groupby(['venue','asset']):
            d=dense_frame(g);split=d['t'][0]+.6*(d['t'][-1]-d['t'][0])
            yield 'production',v,a,d,d,split

def onsets(x,ix,qlo,qhi,cooldown=180):
    candidates=[];last=-cooldown
    for j in ix:
        if j<1 or not np.isfinite(x[j-1]):continue
        side=1 if x[j]>=qhi and x[j-1]<qhi else -1 if x[j]<=qlo and x[j-1]>qlo else 0
        if side and j-last>=cooldown:candidates.append((j,side));last=j
    return candidates

def analyze_ranges(source,v,a,d,split,thresholds,stride=60):
    if source!='production' or thresholds is None:return []
    rows=[];r,_=ratio(d,60,'quantity_buffer_seconds');ql,qh=thresholds
    for lookback in (900,3600):
        good=clean_anchors(d,lookback,1800)&(d['t']-lookback>=split)
        ix=np.flatnonzero(good)[::stride]
        if not len(ix):continue
        past=pd.Series(d['mid']);lo=past.rolling(lookback).min().shift(1).to_numpy()[ix]
        upper=past.rolling(lookback).max().shift(1).to_numpy()
        lower=past.rolling(lookback).min().shift(1).to_numpy()
        hi=upper[ix];center=(lo+hi)/2
        center_series=(lower+upper)/2
        path=d['mid'][ix[:,None]+np.arange(1,1801)]
        regime=np.where(~np.isfinite(r[ix]),'inactive',np.where(r[ix]<=ql,'low',np.where(r[ix]>=qh,'high','middle')))
        for band in ('past_minmax','fixed_plusminus_1percent'):
            width=(hi-lo)/2 if band=='past_minmax' else center*.01
            z=np.divide(d['mid'][ix]-center,width,out=np.full(len(ix),np.nan),where=width>0)
            inside=abs(z)<1;direction=-np.sign(z)
            y=direction[:,None]*(path/d['mid'][ix,None]-1)*1e4
            pcenter=(path-center[:,None])*np.sign(z)[:,None]<=0
            pexit=(path<center[:,None]-width[:,None])|(path>center[:,None]+width[:,None])
            firstcenter=np.where(pcenter.any(1),np.argmax(pcenter,axis=1)+1,1801)
            firstexit=np.where(pexit.any(1),np.argmax(pexit,axis=1)+1,1801)
            for group in ('all','low','high','inactive'):
                mask=(regime==group) if group!='all' else np.ones(len(ix),bool)
                for eligible in ('inside','outer_half_entry'):
                    take=mask&inside&(abs(z)>=.5 if eligible=='outer_half_entry' else np.ones(len(ix),bool))
                    if not take.any():continue
                    for h in (180,900,1800):
                        yy=y[take,h-1];fc=firstcenter[take];fe=firstexit[take]
                        rows.append({'source':source,'venue':v,'asset':a,'anchor_stride':stride,'lookback':lookback,'band':band,'regime':group,
                                     'eligibility':eligible,'horizon':h,'n':int(take.sum()),
                                     'median_halfwidth_bps':float(np.median(width[take]/center[take]*1e4)),
                                     'median_start_abs_z':float(np.median(abs(z[take]))),
                                     'toward_center_mean_bps':float(yy.mean()),'toward_center_median_bps':float(np.median(yy)),
                                     'p_center_before_exit':float(np.mean((fc<=h)&(fc<fe))),
                                     'p_exit_before_center':float(np.mean((fe<=h)&(fe<fc))),
                                     'p_neither':float(np.mean((fc>h)&(fe>h))),
                                     'p_same_second_ambiguous':float(np.mean((fc==fe)&(fc<=h))),
                                     'p_stay_in_band':float(np.mean(fe>h)),
                                     'median_future_center_shift_halfwidth':float(np.median(abs(center_series[ix[take]+h]-center[take])/width[take])),
                                     'mean_max_adverse_bps':float(np.mean(np.minimum(0,y[take,:h].min(1)))),
                                     'p_gross_over_5bp':float(np.mean(yy>5)),
                                     'p_gross_over_10bp':float(np.mean(yy>10))})
    return rows

def main():
    ROOT.mkdir(exist_ok=True);vols=[];curves=[];cohorts=[];strength=[];events=[];examples=[];inference=[];ranges=[];coverage=[];missing=[];quality=[];calibrations=[]
    for source,v,a,train,d,split in datasets():
        fm=clean_anchors(train);em=clean_anchors(d)
        if split is not None:fm&=train['t']+180<split;em&=d['t']-60>=split
        ti=np.flatnonzero(fm)[::5 if split else 1];ei=np.flatnonzero(em)[::5 if split else 1]
        coverage.append({'source':source,'venue':v,'asset':a,'fit_n':len(ti),'test_n':len(ei),'start':d['t'][0],'end':d['t'][-1]})
        if min(len(ti),len(ei))<100:missing.append({'source':source,'venue':v,'asset':a,'reason':'too_few_gap_free_anchors'});continue
        y=(d['mid'][ei[:,None]+HORIZONS]/d['mid'][ei,None]-1)*1e4;rv=realized_rv(d,ei,HORIZONS)
        threshold60=None;primary_regimes={}
        for w in WINDOWS:
            definitions=['quantity_buffer_seconds']+(['notional_buffer_seconds','quote_event_stock_sum'] if source=='pilot' else [])
            for definition in definitions:
                tr,td=ratio(train,w,definition);er,ed=ratio(d,w,definition)
                active_fit=np.isfinite(tr[ti]);active_test=np.isfinite(er[ei])
                if active_fit.sum()<50 or active_test.sum()<50:
                    missing.append({'source':source,'venue':v,'asset':a,'window':w,'definition':definition,'reason':'insufficient_active_ratio_anchors','fit_active':int(active_fit.sum()),'test_active':int(active_test.sum())});continue
                ql,qh=np.quantile(tr[ti][active_fit],[.25,.75])
                if ql==qh:continue
                calibrations.append({'source':source,'venue':v,'asset':a,'window':w,'definition':definition,'q25':ql,'q75':qh,'fit_active':int(active_fit.sum())})
                regime=np.where(~active_test,'inactive',np.where(er[ei]<=ql,'low',np.where(er[ei]>=qh,'high','middle')))
                primary='notional_buffer_seconds' if source=='pilot' else 'quantity_buffer_seconds'
                if definition==primary:primary_regimes[w]=regime
                if w==60 and definition=='quantity_buffer_seconds':threshold60=(ql,qh)
                for group in ('low','middle','high','inactive'):
                    take=regime==group
                    if not take.any():continue
                    for k,h in enumerate(HORIZONS):
                        vols.append({'source':source,'venue':v,'asset':a,'window':w,'definition':definition,'regime':group,'horizon':int(h),'n':int(take.sum()),
                                     'mean_realized_vol_bps':float(rv[take,k].mean()),'median_realized_vol_bps':float(np.median(rv[take,k])),
                                     'mean_abs_move_bps':float(np.mean(abs(y[take,k]))),'p_flat':float(np.mean(y[take,k]==0))})
                for h in TEST_H:
                    if definition!=primary or w not in (5,60):continue
                    inference.append({'kind':'ratio_future_vol','source':source,'venue':v,'asset':a,'window':w,'horizon':h,
                                      **contrast(d['t'][ei],rv[:,h-1],regime=='high',regime=='low',max(60,2*h))})
                if source=='pilot' and definition=='notional_buffer_seconds':
                    qr,_=ratio(d,w,'quantity_buffer_seconds');both=np.isfinite(er[ei])&np.isfinite(qr[ei])
                    quality.append({'venue':v,'asset':a,'window':w,'n':int(both.sum()),'median_abs_ratio_difference_percent':float(np.median(abs(er[ei][both]/qr[ei][both]-1))*100),
                                    'ratio_spearman':float(pd.Series(er[ei][both]).corr(pd.Series(qr[ei][both]),method='spearman'))})
            if w!=5:continue
            sx=signals(train,w);ex=signals(d,w)
            for name in sx:
                tx=sx[name][ti];xx=ex[name][ei];ft=np.isfinite(tx);fe=np.isfinite(xx)
                if ft.sum()<100 or fe.sum()<100 or np.std(tx[ft])<1e-10:
                    missing.append({'source':source,'venue':v,'asset':a,'indicator':name,'reason':'unexcited_or_unknown_direction'});continue
                qlo,qhi=np.quantile(tx[ft],[.1,.9]);bounds=np.quantile(tx[ft],np.arange(.1,1,.1));bins=np.searchsorted(bounds,xx,side='right')
                if qlo==qhi:continue
                for group in ('low','high'):
                    mask=fe&((xx<=qlo) if group=='low' else (xx>=qhi))
                    if not mask.any():continue
                    for k,h in enumerate(HORIZONS):curves.append({'source':source,'venue':v,'asset':a,'indicator':name,'window':w,'group':group,'horizon':int(h),'n':int(mask.sum()),**price_stats(y[mask,k])})
                for h in TEST_H:
                    inference.append({'kind':'signal_price_contrast','source':source,'venue':v,'asset':a,'window':w,'indicator':name,'horizon':h,
                                      **contrast(d['t'][ei],y[:,h-1],fe&(xx>=qhi),fe&(xx<=qlo),max(60,2*h))})
                for b in range(10):
                    take=fe&(bins==b)
                    if not take.any():continue
                    for k,h in enumerate(HORIZONS):cohorts.append({'source':source,'venue':v,'asset':a,'indicator':name,'window':w,'decile':b+1,'horizon':int(h),'n':int(take.sum()),'mean_signal':float(xx[take].mean()),**price_stats(y[take,k])})
                ab=np.quantile(abs(tx[ft&(tx!=0)]),[.2,.4,.6,.8]) if (ft&(tx!=0)).any() else []
                if len(ab):
                    strengthbin=np.searchsorted(ab,abs(xx),side='right')
                    for b in range(5):
                        take=fe&(xx!=0)&(strengthbin==b)
                        if not take.any():continue
                        for h in TEST_H:strength.append({'source':source,'venue':v,'asset':a,'indicator':name,'strength_quintile':b+1,'horizon':h,'n':int(take.sum()),'mean_abs_signal':float(abs(xx[take]).mean()),**price_stats(np.sign(xx[take])*y[take,h-1])})
                # Regime-conditioned raw signed movement, W=5s ratio, same route.
                if 5 in primary_regimes:
                    for group in ('low','high','inactive'):
                        take=fe&(xx!=0)&(primary_regimes[5]==group)
                        if not take.any():continue
                        for h in TEST_H:cohorts.append({'source':source,'venue':v,'asset':a,'indicator':name,'window':w,'regime':group,'horizon':h,'n':int(take.sum()),'aligned':True,**price_stats(np.sign(xx[take])*y[take,h-1])})
                onset=onsets(ex[name],np.flatnonzero(em),qlo,qhi)
                if onset:
                    oi=np.array([j for j,s in onset]);side=np.array([s for j,s in onset]);oy=side[:,None]*(d['mid'][oi[:,None]+HORIZONS]/d['mid'][oi,None]-1)*1e4
                    favorable=np.maximum.accumulate(np.maximum(0,oy),axis=1);adverse=np.minimum.accumulate(np.minimum(0,oy),axis=1)
                    for k,h in enumerate(HORIZONS):events.append({'source':source,'venue':v,'asset':a,'indicator':name,'horizon':int(h),'n':len(oi),**price_stats(oy[:,k]),'mean_favorable_bps':float(favorable[:,k].mean()),'mean_adverse_bps':float(adverse[:,k].mean())})
                    for h in TEST_H:inference.append({'kind':'onset_aligned_mean','source':source,'venue':v,'asset':a,'indicator':name,'horizon':h,**mean_ci(d['t'][oi],oy[:,h-1])})
                    chosen=np.unique(np.linspace(0,len(oi)-1,min(12,len(oi)),dtype=int))
                    for j in chosen:
                        for k,h in enumerate(HORIZONS):examples.append({'source':source,'venue':v,'asset':a,'indicator':name,'event_t':d['t'][oi[j]],'side':int(side[j]),'horizon':int(h),'aligned_move_bps':float(oy[j,k])})
        ranges.extend(analyze_ranges(source,v,a,d,split,threshold60))
        print(json.dumps({'completed':source+':'+v+':'+a,'test_anchors':len(ei)}),flush=True)
        # Keep peak memory bounded; publish per-route partitions immediately.
        route=ROOT/'parts'/(source+'_'+v+'_'+a);route.mkdir(parents=True,exist_ok=True)
        for name,rows in [('volatility',vols),('price_curves',curves),('signal_deciles',cohorts),('signal_strength',strength),('onset_curves',events),('event_examples',examples)]:
            write(route/(name+'.csv'),rows);rows.clear()
    for name,rows in [('inference',inference),('ranges',ranges),('coverage',coverage),('unavailable',missing),('ratio_quality',quality),('ratio_thresholds',calibrations)]:write(ROOT/(name+'.csv'),rows)
    (ROOT/'methods.json').write_text(json.dumps({'ratio_windows':WINDOWS,'price_signal_window':5,'horizons':HORIZONS.tolist(),
        'grid_lookbacks':[900,3600],'grid_horizons':[180,900,1800],'range_ratio_window':60,'test_split':'same immutable first 60%/last 40% production split; separate pilot train/eval',
        'primary_ratio':'pilot exact notional stock integral / trade notional; production quantity stock integral / trade quantity; zero trade separate',
        'event_definition':'train 10/90 percentile crossing; combined-direction 180s cooldown; complete past60/future180 path',
        'grid_interpretation':'Frozen past bands; midpoint first hit and band exit; no assumed order fills or grid profits',
        'price_stats':'Raw future mid movement in bp; mean median quartiles all-sample up/down/flat; direction-aligned intensity/onsets',
        'discovery_only':True},indent=2),encoding='utf-8')

if __name__=='__main__':main()
