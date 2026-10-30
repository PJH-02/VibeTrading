"""S2: causal robust basis residual + origin, unhedged local maker policy."""
from collections import deque
import math
import statistics
from decimal import Decimal as D, ROUND_FLOOR
from strategies.research.state.features import StateStrategy, legacy_instance
STRATEGY_ID = "S2"

class Strategy(StateStrategy):
    def __new__(cls, coefficient=None, mode=None, **kwargs):
        return legacy_instance("s2_external_anchor",coefficient,mode,kwargs) or super().__new__(cls)

    def __init__(self, max_reference_age=1.5, **kwargs):
        super().__init__(**kwargs)
        self.max_reference_age = max_reference_age
        self.baseline,self.prices = deque(maxlen=1800),deque(maxlen=120)
        self.last_pair,self.last_sample = None,-float("inf")
        self.cached_baseline = self.calibration.get("baseline_bp")

    def on_book(self, quote, reset=False, book=None):
        if reset:
            self.baseline.clear();self.prices.clear();self.last_pair=None
            self.last_sample=-float("inf")
            self.cached_baseline=self.calibration.get("baseline_bp")
        super().on_book(quote,reset,book)

    def signal(self, quote, now, reference=None):
        f = self.engine.snapshot(now)
        if reference is None or not reference.valid() or not 0<=now-reference.received<=self.max_reference_age:
            return self.make_signal(STRATEGY_ID,f,0.,"STALE_OR_UNKNOWN","CROSS_STALE_REFERENCE")
        if quote.source_ns and reference.source_ns and abs(quote.source_ns-reference.source_ns)>int(self.calibration.get("source_skew_ms",500))*10**6:
            return self.make_signal(STRATEGY_ID,f,0.,"STALE_OR_UNKNOWN","CROSS_SOURCE_TIME_SKEW")
        spread = math.log(float(quote.mid/reference.mid))*1e4
        baseline = self.cached_baseline
        residual = spread-float(baseline) if baseline is not None else None
        historical = next((p for p in reversed(self.prices) if p[0]<=now-1),None)
        br,lr = (math.log(float(reference.mid)/historical[1])*1e4,math.log(float(quote.mid)/historical[2])*1e4) if historical else (0.,0.)
        pair = (quote.received,reference.received)
        if pair!=self.last_pair:
            if now-self.last_sample>=1:
                self.baseline.append(spread);self.last_sample=now
                if len(self.baseline)>=120:self.cached_baseline=statistics.median(self.baseline)
            self.prices.append((now,float(reference.mid),float(quote.mid)));self.last_pair=pair
        diagnostics = self.calibration.get("stationarity",{})
        stationary = diagnostics.get("adf_p",1)<.05 and diagnostics.get("kpss_p",0)>.05 and diagnostics.get("samples",0)>=200
        impulse = float(self.calibration.get("impulse_bp",1))
        origin,score = "STALE_OR_UNKNOWN",0.
        if abs(br)>=impulse and abs(lr)<abs(br)*.5:
            origin = "BINANCE_LEADS"
        elif abs(lr)>=impulse and abs(br)<abs(lr)*.5:
            origin = "LOCAL_IDIOSYNCRATIC"
        elif abs(br)>=impulse and abs(lr)>=impulse:
            origin = "COMMON_SHOCK"
        fee_buffer = float(self.calibration.get("maker_roundtrip_bp",4))+float(self.calibration.get("uncertainty_bp",1))
        entry = max(float(self.calibration.get("residual_abs_q90",2)),fee_buffer)
        train_baseline=self.calibration.get("baseline_bp")
        drift=abs(float(baseline)-float(train_baseline)) if baseline is not None and train_baseline is not None else 0.
        drift_limit=max(float(self.calibration.get("baseline_drift_min_bp",5)),6*float(self.calibration.get("mad_bp",0)))
        stable_baseline=drift<=drift_limit
        if f.ready and stationary and stable_baseline and residual is not None and abs(residual)>entry:
            side = 1 if residual<0 else -1
            aligned = side*br>0 if origin=="BINANCE_LEADS" else side*lr<0 if origin=="LOCAL_IDIOSYNCRATIC" else False
            if aligned:
                score = side*min(1.,abs(residual)/entry)
        reason="CROSS_BASELINE_DRIFT" if not stable_baseline else "CROSS_"+origin if stationary else "CROSS_STATIONARITY_UNVERIFIED"
        return self.make_signal(STRATEGY_ID,f,score,origin,reason,
            residual_bp=residual,baseline_bp=baseline,spread_bp=spread,binance_return_bp=br,local_return_bp=lr,
            baseline_stationary=stationary,baseline_drift_bp=drift,baseline_drift_limit_bp=drift_limit,risk_thresholds=self.calibration.get("risk",{}))

    def order_intents(self, signal, quote, position_context=None, force_reduce=False, **risk):
        inventory = risk["inventory"]
        intents = super().order_intents(signal,quote,position_context=position_context,force_reduce=force_reduce,**risk)
        if not inventory or force_reduce:
            return intents
        p = position_context or {}
        entry_price,entry_residual = p.get("entry_price"),p.get("entry_residual_bp")
        residual = signal.metadata.get("residual_bp")
        if not entry_price or entry_residual is None or residual is None:
            return [i for i in intents if not i.reduce_only]
        side = 1 if inventory>0 else -1
        pnl_bp = side*(float(quote.mid/D(str(entry_price)))-1)*1e4
        shrink = abs(float(entry_residual))-abs(float(residual))
        cost_bp = float(risk["cost"]/quote.mid)*1e4
        if shrink<cost_bp or pnl_bp<cost_bp:
            return [i for i in intents if not i.reduce_only]
        if abs(float(residual))<=float(self.calibration.get("exit_residual_bp",.5)):
            return intents
        initial = D(str(p.get("initial_quantity",abs(inventory))))
        if abs(inventory)<=initial/2:
            return []
        from dataclasses import replace
        quantity = (min(initial/2,abs(inventory))/risk["lot"]).to_integral_value(rounding=ROUND_FLOOR)*risk["lot"]
        return [replace(i,quantity=quantity,role="S2_TP1_MAKER") for i in intents if i.reduce_only and quantity>0]
