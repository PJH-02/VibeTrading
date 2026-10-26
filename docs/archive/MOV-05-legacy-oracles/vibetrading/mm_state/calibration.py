"""Train-only thresholds and offline S2 diagnostics; never called per market tick."""
import json
import math
import statistics


def percentile(values,p):
    if not values:
        return None
    values = sorted(values)
    x = (len(values)-1)*p
    lo,hi = int(x),math.ceil(x)
    return values[lo]+(values[hi]-values[lo])*(x-lo)


def spread_diagnostics(values):
    result = {"samples":len(values),"method":"ADF+KPSS; ACF/PACF; Ljung-Box; Anderson-Darling; train-only"}
    if len(values)<200 or statistics.pstdev(values)<1e-8:
        return {**result,"reason":"INSUFFICIENT_VARIATION"}
    try:
        import numpy as np
        from scipy.stats import anderson
        from statsmodels.tsa.stattools import adfuller,kpss,acf,pacf
        from statsmodels.stats.diagnostic import acorr_ljungbox
        import warnings
        x = np.asarray(values)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result.update(adf_p=float(adfuller(x,maxlag=10,autolag="AIC")[1]),
                          kpss_p=float(kpss(x,regression="c",nlags="auto")[1]),
                          acf=acf(x,nlags=10).tolist(),pacf=pacf(x,nlags=10).tolist(),
                          ljung_box_p=float(acorr_ljungbox(x,lags=[10],return_df=True).lb_pvalue.iloc[0]),
                          anderson_normal_stat=float(anderson(x,dist="norm").statistic))
        dx = np.diff(x)
        denom = float(np.dot(x[:-1]-x[:-1].mean(),x[:-1]-x[:-1].mean()))
        beta = float(np.dot(x[:-1]-x[:-1].mean(),dx-dx.mean())/denom) if denom else 0
        result["half_life_samples"] = -math.log(2)/math.log(1+beta) if -1<beta<0 else None
    except ImportError:
        result["reason"] = "OFFLINE_STATS_DEPENDENCIES_UNAVAILABLE"
    return result


def fit_states(rows,sid,maker_fee=0):
    records = [r for r in rows if r.get("ready")]
    if len(records)<200:
        return {"schema":"mm-state-v1","valid":False,"reason":"INSUFFICIENT_STATE_TRAINING","samples":len(records)}
    features = [json.loads(r["metadata"]).get("features",{}) if isinstance(r["metadata"],str) else r["metadata"]["features"] for r in records]
    risk = {"r_low":percentile([f["liquidity_seconds"] for f in features if f.get("liquidity_seconds") is not None],.25),
            "rv_high":percentile([f["rv_bps"] for f in features],.9),
            "spread_high":percentile([f["spread_bps"] for f in features],.9)}
    config = {"ofi_abs_q90":max(percentile([abs(f["ofi"]) for f in features],.9),1e-6),
              "obi_abs_q90":max(percentile([abs(f["obi"]) for f in features],.9),1e-6),
              "flow_imbalance":max(.3,percentile([abs(f["trade_imbalance"]) for f in features],.9)),
              "maker_roundtrip_bp":2*max(0.,float(maker_fee))*1e4,
              "risk":{k:v for k,v in risk.items() if v is not None}}
    if sid=="S2":
        # Do not concatenate disjoint reference-valid blocks for time-series
        # tests. Thresholds and diagnostics use the longest causal training block.
        blocks=[];block=[];previous=None
        for r in rows:
            valid=r.get("ready") and r.get("ref_mid") and r.get("t") is not None
            if not valid or previous is not None and r["t"]-previous>2.5:
                if block:blocks.append(block)
                block=[]
            if valid:
                block.append(math.log(r["mid"]/r["ref_mid"])*1e4);previous=r["t"]
            else:previous=None
        if block:blocks.append(block)
        spreads=max(blocks,key=len,default=[])
        if len(spreads)<200:
            return {"schema":"mm-state-v1","valid":False,"reason":"INSUFFICIENT_CONTIGUOUS_MATCHED_REFERENCE"}
        center = statistics.median(spreads)
        config.update(baseline_bp=center,residual_abs_q90=percentile([abs(x-center) for x in spreads],.9),
                      mad_bp=statistics.median(abs(x-center) for x in spreads),stationarity=spread_diagnostics(spreads))
        config["stationarity"].update(block_selection="longest contiguous training block; max sample spacing 2.5 seconds",blocks=len(blocks))
    unknown = sum(f["unknown"] for f in features)
    valid = sid!="S3" or (unknown==0 and sum(f["buy"]+f["sell"] for f in features)>0)
    return {"schema":"mm-state-v1","valid":valid,"samples":len(records),"calibration":config,
            "reason":"FROZEN_TRAIN_THRESHOLDS_NOT_PROFIT_VALIDATION" if valid else "UNKNOWN_AGGRESSOR_DATA"}


def purged_walk_forward(times, train_seconds, test_seconds, horizon=30, embargo=5):
    """Yield chronological integer index sets; future target never crosses fit edge."""
    if train_seconds<=horizon or test_seconds<=0 or horizon<0 or embargo<0:
        raise ValueError("Invalid purged split")
    start,end = float(times[0]),float(times[-1])
    cut = start+train_seconds
    while cut+embargo+test_seconds<=end:
        fit = [i for i,t in enumerate(times) if cut-train_seconds<=t and t+horizon<cut]
        test = [i for i,t in enumerate(times) if cut+embargo<=t<cut+embargo+test_seconds]
        yield fit,test
        cut += test_seconds
