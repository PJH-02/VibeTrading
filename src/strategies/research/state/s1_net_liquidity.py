"""S1: causal OFI onset/persistence direction; standalone entry point."""
from strategies.research.state.features import StateStrategy, direction_score, legacy_instance
STRATEGY_ID = "S1"

class Strategy(StateStrategy):
    def __new__(cls, coefficient=None, mode=None, **kwargs):
        return legacy_instance("s1_net_liquidity",coefficient,mode,kwargs) or super().__new__(cls)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.previous_sign,self.previous_ofi,self.onset_at = 0,0.,None

    def on_book(self, quote, reset=False, book=None):
        if reset:
            self.previous_sign,self.previous_ofi,self.onset_at = 0,0.,None
        super().on_book(quote,reset,book)

    def signal(self, quote, now, reference=None):
        f = self.engine.snapshot(now)
        score = direction_score(f.ofi,self.calibration,"ofi_abs_q90",1.5) if f.ready else 0.
        sign = 1 if score>0 else -1 if score<0 else 0
        onset = sign!=0 and sign!=self.previous_sign
        if sign!=self.previous_sign:
            self.onset_at = now if sign else None
        duration = now-self.onset_at if self.onset_at is not None else 0.
        state = "OFI_ONSET" if onset else "OFI_PERSISTENT" if sign else "NEUTRAL"
        acceleration = f.ofi-self.previous_ofi
        self.previous_sign,self.previous_ofi = sign,f.ofi
        return self.make_signal(STRATEGY_ID,f,score,state,"DIRECTION_"+state,
            onset=onset,persistence_seconds=duration,acceleration=acceleration,
            risk_thresholds=self.calibration.get("risk",{}))
