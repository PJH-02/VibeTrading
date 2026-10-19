import sys,json
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).parent))
from analyze_liquidity_regimes import ROOT,datasets,ratio,analyze_ranges,write
from evaluate_fv_indicators import clean_anchors
rows=[]
for source,v,a,train,d,split in datasets():
    if source!='production':continue
    fm=clean_anchors(train)&(train['t']+180<split);ti=np.flatnonzero(fm)[::5]
    r,_=ratio(train,60,'quantity_buffer_seconds');active=np.isfinite(r[ti])
    if active.sum()<50:continue
    thresholds=np.quantile(r[ti][active],[.25,.75])
    rows.extend(analyze_ranges(source,v,a,d,split,thresholds,stride=1800))
write(ROOT/'range_nonoverlap.csv',rows)
print(json.dumps({'nonoverlap_range_cells':len(rows)}))
