"""Direction and magnitude conditional on liquidity and signal intensity."""
import json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).parent))
from analyze_liquidity_regimes import ROOT,datasets,signals,ratio,realized_rv,write,WINDOWS,TEST_H
from evaluate_fv_indicators import clean_anchors

def main():
    rows=[]
    for source,v,a,train,d,split in datasets():
        fm=clean_anchors(train);em=clean_anchors(d)
        if split is not None:fm&=train['t']+180<split;em&=d['t']-60>=split
        ti=np.flatnonzero(fm)[::5 if split else 1];ei=np.flatnonzero(em)[::5 if split else 1]
        if min(len(ti),len(ei))<100:continue
        hs=np.array(TEST_H);y=(d['mid'][ei[:,None]+hs]/d['mid'][ei,None]-1)*1e4;rv=realized_rv(d,ei,hs)
        definition='notional_buffer_seconds' if source=='pilot' else 'quantity_buffer_seconds'
        for w in WINDOWS:
            tr,_=ratio(train,w,definition);er,_=ratio(d,w,definition);active=np.isfinite(tr[ti])
            if active.sum()<50:continue
            ql,qh=np.quantile(tr[ti][active],[.25,.75]);regime=np.where(~np.isfinite(er[ei]),'inactive',np.where(er[ei]<=ql,'low',np.where(er[ei]>=qh,'high','middle')))
            tx,xx=signals(train,w),signals(d,w)
            for name in tx:
                fit=tx[name][ti];valid=np.isfinite(fit)&(fit!=0)
                if valid.sum()<30 or np.std(fit[np.isfinite(fit)])<1e-10:continue
                bounds=np.quantile(abs(fit[valid]),[.25,.5,.75]);signal=xx[name][ei];bucket=np.searchsorted(bounds,abs(signal),side='right')
                for group in ('low','high','inactive'):
                    for strength in ('all',0,1,2,3):
                        take=(regime==group)&np.isfinite(signal)&(signal!=0)
                        if strength!='all':take&=bucket==strength
                        if take.sum()<30:continue
                        sign=np.sign(signal[take]);aligned=sign[:,None]*y[take]
                        normalized=np.divide(aligned,rv[take],out=np.zeros_like(aligned),where=rv[take]>0)
                        for k,h in enumerate(hs):
                            target=y[take,k];yy=aligned[:,k];up=target>0;down=target<0;moving=target!=0
                            rows.append({'source':source,'venue':v,'asset':a,'window':w,'indicator':name,'regime':group,
                                         'strength_bucket':strength,'horizon':int(h),'n':int(take.sum()),'moving_n':int(moving.sum()),
                                         'mean_aligned_bps':float(yy.mean()),'median_aligned_bps':float(np.median(yy)),
                                         'mean_future_rv_bps':float(rv[take,k].mean()),'mean_aligned_over_future_rv':float(normalized[:,k].mean()),
                                         'direction_accuracy_moving':float((yy[moving]>0).mean()) if moving.any() else None,
                                         'balanced_accuracy_moving':float(((sign[up]>0).mean()+(sign[down]<0).mean())/2) if up.any() and down.any() else None,
                                         'p_aligned_greater_5bp':float((yy>5).mean()),'p_aligned_greater_10bp':float((yy>10).mean())})
        print(json.dumps({'direction_completed':source+':'+v+':'+a}),flush=True)
    write(ROOT/'regime_direction.csv',rows)

if __name__=='__main__':main()
