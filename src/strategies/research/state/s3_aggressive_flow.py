"""S3: flow/absorption -> exhaustion transition, not trade sign alone."""
from strategies.research.state.features import StateStrategy, legacy_instance
STRATEGY_ID = "S3"

class Strategy(StateStrategy):
    def __new__(cls, coefficient=None, mode=None, **kwargs):
        return legacy_instance("s3_aggressive_flow",coefficient,mode,kwargs) or super().__new__(cls)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.clear_episode()

    def clear_episode(self):
        self.phase,self.side,self.onset = "NEUTRAL",0,None
        self.start_price,self.start_depth = 0.,0.
        self.flow,self.visible_refill,self.visible_depletion,self.peak_flow = 0.,0.,0.,0.
        self.levels = None

    def on_book(self, quote, reset=False, book=None):
        if reset:
            self.clear_episode()
        levels = ({p:float(q) for p,q in sorted(book.bids.items(),reverse=True)[:10]},
                  {p:float(q) for p,q in sorted(book.asks.items())[:10]}) if book else ({quote.bid:float(quote.bid_qty)},{quote.ask:float(quote.ask_qty)})
        if self.side and self.levels:
            index = 1 if self.side>0 else 0
            before,after = self.levels[index],levels[index]
            for p in before.keys() & after.keys():
                diff = after[p]-before[p]
                self.visible_refill += max(0.,diff)
                self.visible_depletion += max(0.,-diff)
        self.levels = levels
        super().on_book(quote,reset,book)

    def on_trade(self, side, quantity, received, price=None):
        previous=self.engine.previous
        if not quantity.is_finite() or quantity<=0 or price is not None and (not price.is_finite() or price<=0) or previous is None or not 0<=received-previous.received<=self.engine.stale or self.engine.cursor is not None and received<self.engine.cursor:
            return
        if self.side and side==("Buy" if self.side>0 else "Sell"):
            self.flow += float(quantity)
        super().on_trade(side,quantity,received,price)

    def signal(self, quote, now, reference=None):
        f = self.engine.snapshot(now)
        score,reason = 0.,"FLOW_NEUTRAL"
        if not f.ready or f.unknown>0:
            self.clear_episode()
            return self.make_signal(STRATEGY_ID,f,0.,"UNKNOWN","FLOW_UNKNOWN_OR_WARMUP")
        threshold = float(self.calibration.get("flow_imbalance",.6))
        if self.onset is not None and now-self.onset>float(self.calibration.get("episode_seconds",30)):
            self.clear_episode()
        if self.phase=="NEUTRAL" and abs(f.trade_imbalance)>=threshold and f.buy+f.sell>=float(quote.depth):
            self.side = 1 if f.trade_imbalance>0 else -1
            self.phase,self.onset = "PRESSURE",now
            self.start_price = float(quote.ask if self.side>0 else quote.bid)
            self.start_depth = float(quote.ask_qty if self.side>0 else quote.bid_qty)
        if self.side:
            current_flow = f.buy if self.side>0 else f.sell
            self.peak_flow = max(self.peak_flow,current_flow)
            move = self.side*(float(quote.ask if self.side>0 else quote.bid)-self.start_price)/self.engine.tick
            refill_ratio = self.visible_refill/max(self.flow,1e-12)
            if self.phase=="PRESSURE" and self.flow>=self.start_depth and refill_ratio>=.5 and move<1:
                self.phase = "ABSORPTION"
                reason = "FLOW_ABSORPTION_"+("BUY" if self.side>0 else "SELL")
            elif self.phase=="ABSORPTION":
                if self.side*f.trade_imbalance>=threshold and refill_ratio<.3 and move>=1 and self.visible_depletion>0:
                    self.phase = "SUPPLY_EXHAUSTED"
                    score = float(self.side)
                    reason = "FLOW_"+("ASK" if self.side>0 else "BID")+"_EXHAUSTED"
                elif current_flow<self.peak_flow*.5 and self.visible_refill>0 and move<1:
                    self.phase = "AGGRESSOR_EXHAUSTED"
                    score = float(-self.side)
                    reason = "FLOW_"+("BUY" if self.side>0 else "SELL")+"_EXHAUSTED"
            elif self.phase in ("SUPPLY_EXHAUSTED","AGGRESSOR_EXHAUSTED"):
                score = float(self.side if self.phase=="SUPPLY_EXHAUSTED" else -self.side)
                reason = "FLOW_TRANSITION_ACTIVE"
        return self.make_signal(STRATEGY_ID,f,score,self.phase,reason,
            onset_seconds=self.onset,episode_flow=self.flow,observed_net_refill=self.visible_refill,
            observed_net_depletion=self.visible_depletion,risk_thresholds=self.calibration.get("risk",{}))
