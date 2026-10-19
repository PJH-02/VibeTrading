"""Matched routes and calendar overlap audit for the original L1 OBI curve."""
import json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).parent))
from analyze_liquidity_regimes import ROOT,datasets,signals,HORIZONS,write,contrast
from evaluate_fv_indicators import clean_anchors

def main():
    pilot={};curves=[];audit=[];stats=[]
    for source,v,a,train,d,split in datasets():
        if a=='XRP' or v=='binance':continue
        fm=clean_anchors(train);em=clean_anchors(d)
        if split is not None:fm&=train['t']+180<split;em&=d['t']-60>=split
        fit=signals(train,5)['l1_obi'][fm];qlo,qhi=np.quantile(fit,[.25,.75]);x=signals(d,5)['l1_obi']
        if source=='pilot':pilot[v,a]=(d,x,em);sets={'pilot':np.flatnonzero(em)}
        else:
            pd,px,pm=pilot[v,a];start,end=pd['t'][0],pd['t'][-1];base=np.flatnonzero(em)[::5]
            sets={'production_full':base,'production_excluding_pilot':base[(d['t'][base]+180<start)|(d['t'][base]>end)]}
            times=np.intersect1d(d['t'][em],pd['t'][pm]);ix=(times-d['t'][0]).astype(int);pi=(times-start).astype(int)
            sets['production_same_pilot_period']=ix
            audit.append({'venue':v,'asset':a,'shared_gap_free_seconds':len(ix),
                          'obi_correlation':float(np.corrcoef(x[ix],px[pi])[0,1]),
                          'median_abs_obi_difference':float(np.median(abs(x[ix]-px[pi]))),
                          'median_abs_mid_difference_bps':float(np.median(abs(d['mid'][ix]/pd['mid'][pi]-1))*1e4),
                          'fraction_full_test_anchors_overlapping_pilot':float(np.mean((d['t'][base]>=start)&(d['t'][base]<=end)))})
        for label,ix in sets.items():
            if not len(ix):continue
            y=(d['mid'][ix[:,None]+HORIZONS]/d['mid'][ix,None]-1)*1e4
            high=x[ix]>qhi;low=x[ix]<qlo
            for k,h in enumerate(HORIZONS):
                curves.append({'source':label,'venue':v,'asset':a,'horizon':int(h),'high_n':int(high.sum()),'low_n':int(low.sum()),
                               'high_mean_bps':float(y[high,k].mean()) if high.any() else None,
                               'low_mean_bps':float(y[low,k].mean()) if low.any() else None,
                               'high_median_bps':float(np.median(y[high,k])) if high.any() else None,
                               'low_median_bps':float(np.median(y[low,k])) if low.any() else None})
            if label in ('production_excluding_pilot','pilot'):
                stats.append({'source':label,'venue':v,'asset':a,'horizon':25,
                              **contrast(d['t'][ix],y[:,24],high,low,60)})
    write(ROOT/'l1_matched_curves.csv',curves);write(ROOT/'l1_overlap_audit.csv',audit);write(ROOT/'l1_h25_inference.csv',stats)
    print(json.dumps({'l1_matched_routes':len(audit)}))

if __name__=='__main__':main()
