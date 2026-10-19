"""Chronological training and disjoint error calibration; no evaluation refits."""
from __future__ import annotations
import bisect
import math
import statistics


def future(rows, index, horizon):
    times=[r["t"] for r in rows]
    k=bisect.bisect_left(times,rows[index]["t"]+horizon)
    if k>=len(rows) or times[k]-(times[index]+horizon)>2.5:
        return None
    return rows[k]["mid"]-rows[index]["mid"]


def quantile(values, p):
    if not values:
        return None
    values=sorted(values); x=(len(values)-1)*p; lo=int(x); hi=math.ceil(x)
    return values[lo]+(values[hi]-values[lo])*(x-lo)


def calibrate(rows, duration, horizon=30):
    if not rows:
        return {"valid":False,"reason":"no_samples"}
    rows=sorted(rows,key=lambda r:r["t"]); start=rows[0]["t"]
    fit_end=start+duration*.65
    cal_start=fit_end+horizon
    basis_rows=[r for r in rows if r["t"]<start+duration*.25 and r["ref_mid"] and r["ref_mid"]>0]
    s2=rows[0]["strategy"]=="S2"
    basis=statistics.median(math.log(r["mid"]/r["ref_mid"]) for r in basis_rows) if basis_rows else None
    def input_x(r):
        if s2:
            return r["ref_mid"]*math.exp(basis)-r["mid"] if r["ref_mid"] and basis is not None else None
        return r["x"] if r["reason"] in ("ok","unfitted_coefficient") else None
    fit=[]; calibrating=[]
    for n,r in enumerate(rows):
        x,y=input_x(r),future(rows,n,horizon)
        if x is None or y is None or not math.isfinite(x):
            continue
        if s2 and r["t"]<start+duration*.25:
            continue
        if r["t"]+horizon<fit_end:
            fit.append((x,y))
        elif r["t"]>=cal_start:
            calibrating.append((x,y))
    denominator=sum(x*x for x,y in fit)
    result={"valid":False,"basis":basis if s2 else None,"horizon":horizon,"fit_samples":len(fit),"error_calibration_samples":len(calibrating),"fit_end":fit_end,"cal_start":cal_start,"method":"zero-intercept OLS, frozen; chronological horizon purge; 90th percentile adverse error"}
    if denominator<=1e-18 or len(fit)<30 or len(calibrating)<30:
        return {**result,"reason":"insufficient_excitation_or_calibration_history"}
    coefficient=sum(x*y for x,y in fit)/denominator
    result["coefficient"]=coefficient
    if s2 and not 0<coefficient<=1:
        return {**result,"reason":"lambda_outside_(0,1]"}
    # Directional overprediction is the relevant adverse FV error.
    errors=[max(0.,(coefficient*x-y)*(1 if coefficient*x>=0 else -1)) for x,y in calibrating]
    allowance=quantile(errors,.9)
    return {**result,"valid":True,"error_allowance":allowance,"reason":"pilot_frozen_model_not_statistically_validated"}
