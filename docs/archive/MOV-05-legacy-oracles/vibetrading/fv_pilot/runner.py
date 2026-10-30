from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal as D,ROUND_FLOOR,ROUND_CEILING
import hashlib
import json
from pathlib import Path
import signal
import time

from vibetrading.fv_mm.journal import Journal
from vibetrading.fv_mm.market import ROUTES
from vibetrading.fv_mm.types import Intent
from .market import PilotMarkets
from .public import PublicFeeds,metadata,VENUES
from .simulation import PaperMatching
from vibetrading.mm_state.lifecycle import LifecyclePolicy
from vibetrading.mm_state.policy import risk_state
from vibetrading.mm_state.risk import RiskGuard,validate_latency_profile


class PilotRunner:
    def __init__(self,args):
        self.args=args
        if (args.output/"run.sqlite").exists():raise ValueError("Use a new pilot ledger")
        self.strategy_mode="legacy_fv" if getattr(args,"legacy_fv",False) else "state"
        self.journal=Journal(args.output);self.market=PilotMarkets(self.journal,self.strategy_mode);self.market.phase="warming"
        self.lifecycle=LifecyclePolicy()
        self.emergency_taker=getattr(args,"emergency_taker",False)
        self.models=json.loads(args.models.read_text(encoding="utf-8"))
        if time.time()-args.models.stat().st_mtime>1800:raise ValueError("Fresh mainnet pre-calibration required")
        self.latencies=json.loads(args.latency.read_text(encoding="utf-8"))
        self.baseline_scenario="measured" if all(x.get("provenance") in ("measured_testnet","measured_mainnet") for x in self.latencies.values()) else "configured"
        for venue in VENUES:
            validate_latency_profile(self.latencies[venue],venue,require_provenance=self.strategy_mode=="state")
            for sid,(_,asset) in ROUTES.items():
                m=self.models[venue+":"+sid];s=self.market.models[venue,asset]
                if self.strategy_mode=="state":
                    if m.get("schema")!="mm-state-v1":raise ValueError("State mode requires new state training; use --legacy-fv only for historical reproduction")
                    s.calibration=m.get("calibration",{})
                    if sid=="S2":s.cached_baseline=s.calibration.get("baseline_bp")
                else:
                    if m.get("valid"):s.coefficient=D(str(m["coefficient"]))
                    if sid=="S2" and m.get("basis") is not None:s.basis=D(str(m["basis"]))
        self.engines={};self.risk_guards={};self.states={};self.working={};self.last_intent={};self.invalid_since={};self.halted={};self.peaks={}
        self.last_decision={}
        self.last_compute={}
        self.journal.db.execute("CREATE TABLE IF NOT EXISTS quote_policy_audit(t REAL,scenario TEXT,venue TEXT,strategy TEXT,state TEXT,direction INTEGER,decision TEXT,metadata TEXT)")
        self.journal.db.execute("CREATE TABLE IF NOT EXISTS pilot_decisions(t REAL,wall_ns INTEGER,venue TEXT,strategy TEXT,asset TEXT,source_ns INTEGER,mid TEXT,x TEXT,fv TEXT,cost TEXT,bid_candidate TEXT,ask_candidate TEXT,unit TEXT,inventory TEXT,eligible INTEGER,reason TEXT)")
        self.stop=asyncio.Event();self.closing=False;self.close_started=0;self.start_ns=0
        self.policy={"strategy_mode":self.strategy_mode,"emergency_taker":self.emergency_taker,"minimum_quote_lifetime_seconds":1.5,"maximum_ttl_seconds":15,"reprice_ticks":2,"resize_fraction":.2,"virtual_equity":10000,"unit_notional_cap":150,"unit_equity_fraction":.015,"depth_participation":.1,"account_gross_fraction":.08,"account_stop_fraction":.002,"holding_cap_seconds":120,"position_stop_fraction":.0035,"minimum_intent_interval_seconds":.25,"feed_stale_seconds":3,"shutdown_maker_grace_seconds":15,"shutdown_timeout_seconds":120,"funding":"PnL excludes funding; public rates are not a private settled ledger","queue":"entire displayed size ahead; actual opposite-side volume only; no touch fills"}

    def fill(self,order,qty,price,fee,now):
        key=order.identifier.split("-")[1],order.venue,order.strategy
        state=self.states[key];sign=1 if order.intent.side=="Buy" else -1;old=state["inventory"]
        state["inventory"]+=sign*qty;state["cash"]-=sign*qty*price;state["fees"]+=fee
        if old==0:
            state["since"]=now;state["entry"]=price
            state["initial_quantity"]=qty
            state["entry_residual_bp"]=(order.signal_snapshot or {}).get("metadata",{}).get("residual_bp")
        elif old*sign>0:
            state["entry"]=(abs(old)*state["entry"]+qty*price)/(abs(old)+qty)
            state["initial_quantity"]=state.get("initial_quantity",abs(old))+qty
        if state["inventory"]==0:state["since"]=None;state["entry"]=D(0)

    def pnl(self,scenario,venue):
        total=D(0)
        for sid,(_,asset) in ROUTES.items():
            state=self.states[scenario,venue,sid];q=self.market.quotes.get((venue,asset))
            total+=state["cash"]-state["fees"]+state["inventory"]*(q.mid if q else state["mark"])
        return total

    def gross(self,scenario,venue):
        total=D(0)
        for sid,(_,asset) in ROUTES.items():
            q=self.market.quotes.get((venue,asset))
            if not q:continue
            state=self.states[scenario,venue,sid];order=self.working.get((scenario,venue,sid))
            total+=(abs(state["inventory"])+(order.leaves if order and not order.done and not order.intent.reduce_only else D(0)))*q.mid
        return total

    def decide(self,venue,sid,now):
        if now-self.last_compute.get((venue,sid),-float("inf"))<.1:return
        self.last_compute[venue,sid]=now
        asset=ROUTES[sid][1];q,s=self.market.signal(venue,asset,now)
        raw=self.market.quotes.get((venue,asset));model=self.models[venue+":"+sid];instrument=self.instruments[venue][asset]
        for scenario,engine in self.engines.items():
            key=scenario,venue,sid;state=self.states[key];working=self.working.get(key)
            guard=self.risk_guards[scenario]
            if working and working.done:
                if working.status in ("POST_ONLY_REJECTED","REJECTED_STALE"):guard.event(venue,"REJECT",now)
                self.working.pop(key);working=None
            if venue in guard.halted:self.halted.setdefault((scenario,venue),guard.halted[venue])
            target=None;ioc=False;inventory=state["inventory"]
            valid=q is not None and (s.ready if self.strategy_mode=="state" else s.fv is not None) and model.get("valid",False)
            if valid:self.invalid_since.pop(key,None)
            else:self.invalid_since.setdefault(key,now)
            pnl=self.pnl(scenario,venue);account_key=scenario,venue
            peak=max(self.peaks.get(account_key,D(0)),pnl);self.peaks[account_key]=peak
            if pnl<D(-20) or peak-pnl>D(20):self.halted[account_key]="paper_account_loss_or_drawdown"
            if q:
                cost=q.mid*2*max(D(0),instrument.maker_fee)+instrument.tick
                if self.strategy_mode=="legacy_fv":cost+=D(str(model.get("error_allowance") or 0))
                equity=max(D(0),D(10000)+pnl)
                unit=instrument.floor(min(D(".015")*equity/q.mid,D(150)/q.mid,D(".1")*q.depth))
                unit=min(unit,instrument.floor(max(D(0),D(".08")*equity-self.gross(scenario,venue))/q.mid))
                extra={"position_context":{"entry_price":state["entry"],"entry_residual_bp":state.get("entry_residual_bp"),"initial_quantity":state.get("initial_quantity",abs(inventory))}} if self.strategy_mode=="state" else {}
                intents=self.market.models[venue,asset].order_intents(s,q,cost=cost,tick=instrument.tick,lot=instrument.lot,unit=unit,inventory=inventory,max_inventory=max(unit,abs(inventory)),new_exposure=valid and not self.closing and account_key not in self.halted and inventory==0,**extra)
                target=next((i for i in intents if i.reduce_only),intents[0] if intents else None)
                if scenario==self.baseline_scenario:
                    fv=s.fv if s.fv is not None else q.mid;half=max(q.spread/2,cost)
                    bid=min(q.bid,((fv-half)/instrument.tick).to_integral_value(rounding=ROUND_FLOOR)*instrument.tick)
                    ask=max(q.ask,((fv+half)/instrument.tick).to_integral_value(rounding=ROUND_CEILING)*instrument.tick)
                    reason=s.reason if not valid else "closing" if self.closing else "account_halted" if account_key in self.halted else "reduce_inventory" if inventory else "edge_below_cost" if abs(fv-q.mid)<=cost else "size_below_exchange_minimum" if not target or not instrument.valid(target.quantity,target.price) else "eligible"
                    if self.strategy_mode=="state":
                        bid=next((i.price for i in intents if i.side=="Buy"),None)
                        ask=next((i.price for i in intents if i.side=="Sell"),None)
                        reason=s.reason if not valid else "closing" if self.closing else "account_halted" if account_key in self.halted else "state_neutral_or_exit_wait" if not target else "size_below_exchange_minimum" if not instrument.valid(target.quantity,target.price,target.reduce_only) else "eligible"
                    signature=bid,ask,unit,inventory,reason,s.state
                    if self.last_decision.get((venue,sid))!=signature:
                        self.journal.db.execute("INSERT INTO pilot_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(now,time.time_ns(),venue,sid,asset,q.source_ns,str(q.mid),str(s.x),str(s.fv) if s.fv is not None else None,str(cost),str(bid),str(ask),str(unit),str(inventory),int(reason=="eligible"),reason))
                        self.last_decision[venue,sid]=signature
                        self.journal.db.execute("INSERT INTO quote_policy_audit VALUES(?,?,?,?,?,?,?,?)",(now,scenario,venue,sid,s.state,s.direction,json.dumps(asdict(target),default=str) if target else None,json.dumps(s.metadata,default=str)))
            elapsed=now-state["since"] if state["since"] is not None else 0
            entry=state["entry"]
            loss=raw and entry>0 and (raw.mid-entry)*(1 if inventory>0 else -1)<-entry*D(".0035")
            overdue=elapsed>=120 or (not valid and now-self.invalid_since[key]>=15) or account_key in self.halted or loss or (self.closing and now-self.close_started>=15)
            if inventory and overdue and raw and raw.valid() and now-raw.received<=3:
                price=((raw.bid*D(".998"))/instrument.tick).to_integral_value(rounding=ROUND_FLOOR)*instrument.tick if inventory>0 else ((raw.ask*D("1.002"))/instrument.tick).to_integral_value(rounding=ROUND_CEILING)*instrument.tick
                if self.emergency_taker:
                    target=Intent("Sell" if inventory>0 else "Buy",price,abs(inventory),"EMERGENCY_TAKER",True);ioc=True
                else:
                    target=Intent("Sell" if inventory>0 else "Buy",raw.ask if inventory>0 else raw.bid,abs(inventory),"RISK_MAKER_UNWIND",True)
            if working:
                jump=s is not None and self.strategy_mode=="state" and risk_state(s)[0]=="JUMP_RISK"
                flip=s is not None and self.strategy_mode=="state" and s.direction!=0 and s.direction!=(1 if working.intent.side=="Buy" else -1) and not working.intent.reduce_only
                hard=(not q) or (working.intent.reduce_only and not inventory) or (not working.intent.reduce_only and (self.closing or account_key in self.halted or jump or loss or inventory!=0))
                if self.emergency_taker and overdue and not working.ioc:hard=True
                reason=self.lifecycle.reason(working,target,now,instrument.tick,hard_risk=hard,state_flip=flip)
                if reason and working.cancel_at is None:
                    guard.event(venue,"CANCEL",now);engine.cancel_intent(working,now,reason)
                continue
            if target and instrument.valid(target.quantity,target.price,target.reduce_only) and now-self.last_intent.get(key,0)>=.25:
                guard.event(venue,"SEND",now)
                self.working[key]=engine.submit_intent(venue,sid,asset,target,now,ioc,signal_snapshot=asdict(s) if s else None);self.last_intent[key]=now

    def book(self,venue,asset,q,reset,book,raw):
        self.market.book(venue,asset,q,reset,book,raw)
        if self.strategy_mode=="state" and (venue,asset) in self.market.models:
            self.market.models[venue,asset].engine.tick=float(self.instruments[venue][asset].tick)
        sid=next(s for s,(_,a) in ROUTES.items() if a==asset)
        for scenario in self.engines:self.states[scenario,venue,sid]["mark"]=q.mid
        for engine in self.engines.values():engine.on_book(venue,asset,q,book,reset)
        if self.market.phase in ("evaluation","cleanup"):
            sid=next(s for s,(_,a) in ROUTES.items() if a==asset);self.decide(venue,sid,q.received)

    def trade(self,venue,asset,side,qty,price,identifier,received,source,raw):
        self.market.trade(venue,asset,side,qty,price,identifier,received,source,raw)
        for engine in self.engines.values():engine.on_trade(venue,asset,side,qty,price,received,source)
        if self.market.phase in ("evaluation","cleanup"):
            sid=next(s for s,(_,a) in ROUTES.items() if a==asset);self.decide(venue,sid,received)

    def reference(self,asset,q):
        self.market.on_reference(asset,q)
        if asset==ROUTES["S2"][1] and self.market.phase in ("evaluation","cleanup"):
            for venue in VENUES:self.decide(venue,"S2",q.received)

    def gap(self,venue,reason):
        self.market.on_gap(venue,reason)
        for engine in self.engines.values():engine.on_gap(venue,time.monotonic())

    def snapshot(self):
        self.market.sample();now=time.monotonic()
        for (scenario,venue,sid),state in self.states.items():
            asset=ROUTES[sid][1];q=self.market.quotes.get((venue,asset));mid=q.mid if q else state["mark"]
            pnl=state["cash"]-state["fees"]+state["inventory"]*(mid or 0)
            self.journal.db.execute("INSERT INTO pilot_states VALUES(?,?,?,?,?,?,?,?,?,?,?)",(now,time.time_ns(),scenario,venue,sid,asset,str(state["inventory"]),str(state["cash"]),str(state["fees"]),str(mid) if mid else None,str(pnl)))
        self.journal.commit()

    def status(self,start):
        return {"phase":self.market.phase,"elapsed_seconds":round(time.monotonic()-start,3),"inventory":{":".join(k):str(s["inventory"]) for k,s in self.states.items()},"paper_open_orders":sum(len(e.pending) for e in self.engines.values()),"counts":{name:e.counts for name,e in self.engines.items()},"halted":{":".join(k):v for k,v in self.halted.items()},"books":{v+":"+a:n for (v,a),n in self.market.book_counts.items()},"unknown_sides":{v+":"+a:n for (v,a),n in self.market.unknown_counts.items()},"external_order_submissions":0}

    @staticmethod
    def source_hash():
        package=Path(__file__).resolve().parents[1]
        root=package.parent.parent if package.parent.name=="src" and (package.parent.parent/"pyproject.toml").is_file() else package.parent
        canonical_state=package/"strategies"/"research"/"state"
        canonical_fair_value=package/"strategies"/"legacy"/"fair_value"
        if canonical_state.is_dir() or canonical_fair_value.is_dir():
            strategy_folders=tuple(folder for folder in (canonical_state,canonical_fair_value) if folder.is_dir())
        else:
            # Frozen flat exports predate the canonical package layout.
            strategy_folders=(root/"strategy",)
        digest=hashlib.sha256()
        for folder in (*strategy_folders,package/"fv_pilot",package/"mm_state"):
            for path in sorted(folder.glob("*.py")):digest.update(path.name.encode());digest.update(path.read_bytes())
        for name in ("types.py","market.py","journal.py","calibration.py"):
            path=package/"fv_mm"/name;digest.update(name.encode());digest.update(path.read_bytes())
        return digest.hexdigest()

    async def run(self):
        self.instruments,native=await metadata()
        for (venue,asset),model in self.market.models.items():
            if self.strategy_mode=="state":model.engine.tick=float(self.instruments[venue][asset].tick)
        self.journal.save("metadata.json",native)
        for scenario,factor in ((self.baseline_scenario,1),("stress2x",2)):
            self.risk_guards[scenario]=RiskGuard()
            self.engines[scenario]=PaperMatching(self.instruments,self.latencies,self.journal,scenario,factor,self.fill,emergency_taker=self.emergency_taker,position_callback=lambda order:self.states[order.identifier.split("-")[1],order.venue,order.strategy]["inventory"])
            self.engines[scenario].markouts.observer=self.risk_guards[scenario].markout
            for venue in VENUES:
                for sid in ROUTES:self.states[scenario,venue,sid]={"inventory":D(0),"cash":D(0),"fees":D(0),"since":None,"entry":D(0),"mark":D(0)}
        feeds=PublicFeeds(self.book,self.trade,self.gap,self.reference);tasks=feeds.tasks()
        start=None;end_ns=None
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGTERM,signal.SIGINT):
            try:loop.add_signal_handler(sig,self.stop.set)
            except NotImplementedError:pass
        try:
            warm=time.monotonic()
            while len([k for k in self.market.quotes if k[0] in VENUES])<16 or len(self.market.references)<4:
                self.journal.save("status.json",self.status(warm))
                if time.monotonic()-warm>90:raise RuntimeError("All mainnet books/reference must be ready")
                await asyncio.sleep(.25)
            self.market.phase="evaluation";start=time.monotonic();self.start_ns=time.time_ns()
            self.journal.save("manifest.json",{"mode":"mainnet_public_pilot_no_external_orders","start_ns":self.start_ns,"duration":self.args.duration,"venues":VENUES,"routes":ROUTES,"models":self.models,"latencies":self.latencies,"scenarios":{self.baseline_scenario:1,"stress2x":2},"policy":self.policy,"instruments":{v:{a:asdict(i) for a,i in rows.items()} for v,rows in self.instruments.items()},"source_sha256":self.source_hash(),"model_sha256":hashlib.sha256(self.args.models.read_bytes()).hexdigest(),"external_order_submissions":0})
            last_snapshot=0
            while time.monotonic()-start<self.args.duration and not self.stop.is_set() and not (self.args.output/"STOP").exists():
                now=time.monotonic()
                for engine in self.engines.values():engine.advance(now)
                for venue in VENUES:
                    for sid in ROUTES:self.decide(venue,sid,now)
                if now-last_snapshot>=1:
                    self.snapshot();self.journal.save("status.json",self.status(start));last_snapshot=now
                await asyncio.sleep(.05)
            end_ns=time.time_ns();elapsed=time.monotonic()-start
            self.closing=True;self.close_started=time.monotonic();self.market.phase="cleanup"
            while time.monotonic()-self.close_started<120:
                now=time.monotonic()
                for engine in self.engines.values():engine.advance(now)
                for venue in VENUES:
                    for sid in ROUTES:self.decide(venue,sid,now)
                if all(s["inventory"]==0 for s in self.states.values()) and all(not e.pending for e in self.engines.values()):break
                if now-last_snapshot>=1:self.snapshot();last_snapshot=now
                await asyncio.sleep(.05)
            self.snapshot()
            for engine in self.engines.values():engine.markouts.finish()
            complete={**self.status(start),"evaluation_start_ns":self.start_ns,"evaluation_end_ns":end_ns,"evaluation_elapsed_seconds":elapsed,"requested_duration_met":elapsed>=self.args.duration,"cleanup_end_ns":time.time_ns(),"complete":all(s["inventory"]==0 for s in self.states.values()) and all(not e.pending for e in self.engines.values())}
            self.journal.save("complete.json",complete);self.journal.save("status.json",complete)
            print(json.dumps(complete,indent=2),flush=True)
        finally:
            feeds.closed=True
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            self.journal.close()
