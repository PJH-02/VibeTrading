"""Check whether the ratio adds information beyond already-observed volatility."""
import json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).parent))
from analyze_liquidity_regimes import ROOT,datasets,ratio,realized_rv,write
from evaluate_fv_indicators import clean_anchors,rolling

def past_rv(d,w):
    r=np.r_[0.,np.diff(d['mid'])/d['mid'][:-1]*1e4]
    return np.sqrt(np.maximum(0,rolling(r*r,w)))

def regression(tx,ex,y):
    low,high=np.quantile(tx,[.01,.99],axis=0);clip=np.clip(tx,low,high)
    mu=clip.mean(0);sd=clip.std(0);sd=np.where(sd>1e-10,sd,1.)
    z=(clip-mu)/sd;ez=(np.clip(ex,low,high)-mu)/sd;intercept=y.mean(0)
    coef=np.linalg.solve(z.T@z+10*np.eye(z.shape[1]),z.T@(y-intercept))
    return np.expm1(np.clip(ez@coef+intercept,0,25)),coef/sd[:,None]

def main():
    matched=[];models=[];hs=np.array([5,25,60,180])
    for source,v,a,train,d,split in datasets():
        fm=clean_anchors(train);em=clean_anchors(d)
        if split is not None:fm&=train['t']+180<split;em&=d['t']-60>=split
        ti=np.flatnonzero(fm)[::5 if split else 1];ei=np.flatnonzero(em)[::5 if split else 1]
        if min(len(ti),len(ei))<100:continue
        definition='notional_buffer_seconds' if source=='pilot' else 'quantity_buffer_seconds'
        for w in (5,60):
            tr,td=ratio(train,w,definition);er,ed=ratio(d,w,definition)
            ft=np.isfinite(tr[ti]);fe=np.isfinite(er[ei])
            if min(ft.sum(),fe.sum())<100:continue
            fit,test=ti[ft],ei[fe];ql,qh=np.quantile(tr[fit],[.25,.75])
            pr,ep=past_rv(train,w),past_rv(d,w)
            bounds=np.quantile(pr[fit],[.25,.5,.75]);b=np.searchsorted(bounds,ep[test],side='right')
            y=realized_rv(d,test,hs);yt=realized_rv(train,fit,hs)
            for level in range(4):
                lo=(er[test]<=ql)&(b==level);hi=(er[test]>=qh)&(b==level)
                if min(lo.sum(),hi.sum())<30:continue
                for k,h in enumerate(hs):matched.append({'source':source,'venue':v,'asset':a,'window':w,'past_rv_quartile':level+1,'horizon':int(h),
                    'high_n':int(hi.sum()),'low_n':int(lo.sum()),'future_rv_high_bps':float(y[hi,k].mean()),'future_rv_low_bps':float(y[lo,k].mean()),
                    'future_rv_high_low_ratio':float(y[hi,k].mean()/y[lo,k].mean()) if y[lo,k].mean()>0 else None})
            tx=np.column_stack([np.log1p(pr[fit]),np.log1p(td[fit]/w),np.log(tr[fit])])
            ex=np.column_stack([np.log1p(ep[test]),np.log1p(ed[test]/w),np.log(er[test])])
            base,_=regression(tx[:,:2],ex[:,:2],np.log1p(yt));plus,coef=regression(tx,ex,np.log1p(yt))
            for k,h in enumerate(hs):
                mb=float(np.mean(abs(base[:,k]-y[:,k])));mp=float(np.mean(abs(plus[:,k]-y[:,k])))
                models.append({'source':source,'venue':v,'asset':a,'window':w,'horizon':int(h),'fit_n':len(fit),'test_n':len(test),
                               'baseline_vol_mae_bps':mb,'with_ratio_vol_mae_bps':mp,'vol_mae_reduction_percent':100*(1-mp/mb) if mb else None,
                               'ratio_log_coefficient':float(coef[-1,k])})
    write(ROOT/'past_vol_matched.csv',matched);write(ROOT/'volatility_incremental.csv',models)
    print(json.dumps({'controlled_cells':len(matched),'incremental_models':len(models)}))
if __name__=='__main__':main()
