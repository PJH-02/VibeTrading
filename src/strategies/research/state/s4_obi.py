"""S4: L1 OBI core direction; L2 is an auxiliary diagnostic."""
from strategies.research.state.features import StateStrategy, direction_score, legacy_instance
STRATEGY_ID = "S4"

class Strategy(StateStrategy):
    def __new__(cls, coefficient=None, mode=None, **kwargs):
        return legacy_instance("s4_obi",coefficient,mode,kwargs) or super().__new__(cls)

    def signal(self, quote, now, reference=None):
        f = self.engine.snapshot(now)
        score = direction_score(f.obi,self.calibration,"obi_abs_q90",.6) if f.ready else 0.
        state = "STRONG_BUY" if score>=.9 else "BUY" if score>0 else "STRONG_SELL" if score<=-.9 else "SELL" if score<0 else "NEUTRAL"
        return self.make_signal(STRATEGY_ID,f,score,state,"DIRECTION_OBI_"+state,
                                risk_thresholds=self.calibration.get("risk",{}))
