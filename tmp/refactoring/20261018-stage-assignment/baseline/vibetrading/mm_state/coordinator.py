"""Ablation profiles share one feature engine; strategy files stay independent."""
import importlib
from dataclasses import replace
from decimal import Decimal as D
from .features import StateStrategy

PROFILES = {"static":(),"obi":("S4",),"book":("S4","S1"),"flow":("S4","S1","S3"),
            "cross":("S4","S1","S3","S2"),"volatility":("S4","S1","S3","S2"),
            "inventory":("S4","S1","S3","S2")}
MODULES = {"S1":"s1_net_liquidity","S2":"s2_external_anchor","S3":"s3_aggressive_flow","S4":"s4_obi"}

class Coordinator(StateStrategy):
    def __init__(self, profile="inventory", calibrations=None, **kwargs):
        super().__init__(**kwargs)
        if profile not in PROFILES:raise ValueError("Unknown ablation profile")
        self.profile = profile
        kwargs={k:v for k,v in kwargs.items() if k!="calibration"}
        self.modules = {}
        for sid in PROFILES[profile]:
            m = importlib.import_module("strategy."+MODULES[sid]).Strategy(mode="state",calibration=(calibrations or {}).get(sid,{}),**kwargs)
            m.engine,m.external_features = self.engine,True
            self.modules[sid] = m

    def on_book(self,q,reset=False,book=None):
        self.engine.book(q,reset,book)
        for m in self.modules.values():m.on_book(q,reset,book)

    def on_trade(self,side,quantity,received,price=None):
        self.engine.trade(side,quantity,received,price)
        for m in self.modules.values():m.on_trade(side,quantity,received,price)

    def signal(self,q,now,reference=None):
        f = self.engine.snapshot(now)
        results = {sid:m.signal(q,now,reference) for sid,m in self.modules.items()}
        weights = {"S4":.45,"S1":.3,"S3":.15,"S2":.1}
        total = sum(weights[sid] for sid in results)
        score = sum(weights[sid]*float(s.x) for sid,s in results.items())/total if total else 0.
        if abs(score)<.2:score=0.
        result = self.make_signal("UNIFIED",f,score,"COMBINED","STATE_MODULE_COMBINATION",
            module_reasons={sid:s.reason for sid,s in results.items()},
            risk_thresholds=self.calibration.get("risk",{}),profile=self.profile)
        return result

    def order_intents(self,signal,q,**risk):
        if self.profile=="static":
            from vibetrading.fv_mm.types import Intent
            inv=risk["inventory"]
            if inv:
                qty=(abs(inv)/risk["lot"]).to_integral_value(rounding="ROUND_FLOOR")*risk["lot"]
                return [Intent("Sell" if inv>0 else "Buy",q.ask if inv>0 else q.bid,qty,"STATIC_MAKER_UNWIND",True)] if qty>0 else []
            qty = (risk["unit"]/risk["lot"]).to_integral_value(rounding="ROUND_FLOOR")*risk["lot"]
            if qty<=0 or not signal.ready or not risk.get("new_exposure",True):return []
            room_buy = risk["max_inventory"]-risk["inventory"]
            room_sell = risk["max_inventory"]+risk["inventory"]
            return [Intent(side,p,min(qty,(room/risk["lot"]).to_integral_value(rounding="ROUND_FLOOR")*risk["lot"]),"STATIC",False)
                    for side,p,room in (("Buy",q.bid,room_buy),("Sell",q.ask,room_sell)) if room>=risk["lot"]]
        # Ablations remove a component while retaining the same operational risk
        # limits; inventory limits and emergency safeguards are never ablated.
        if self.profile not in ("volatility","inventory"):
            signal=replace(signal,metadata={**signal.metadata,"features":{**signal.metadata["features"],"liquidity_seconds":1.,"rv_bps":0.,"bid_gap_ticks":None,"ask_gap_ticks":None,"ofi":0.}})
        if self.profile!="inventory":risk={**risk,"inventory_skew":False}
        return super().order_intents(signal,q,**risk)
