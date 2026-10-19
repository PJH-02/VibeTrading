"""Eight independently routed policies, shared account risk and event feeds."""
from __future__ import annotations
import asyncio
import hashlib
import json
import secrets
import signal
import time
from dataclasses import dataclass
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

import aiohttp

from .feeds import Feeds
from .journal import Journal
from .market import Markets, ROUTES
from .private import bybit_private, grvt_private
from .types import Intent
from .venues import Bybit, Grvt
from vibetrading.mm_state.lifecycle import LifecyclePolicy
from vibetrading.mm_state.policy import risk_state
from vibetrading.mm_state.analytics import Markouts
from vibetrading.mm_state.risk import RiskGuard
from types import SimpleNamespace


@dataclass
class Working:
    client_id: str
    intent: Intent
    leaves: D
    born: float
    live: bool=False
    done: bool=False
    cancel_sent: float=0
    status: str="PENDING"
    active_ts: float | None=None
    signal_snapshot: dict | None=None


class Runner:
    def __init__(self,args):
        self.args=args
        if (args.output/"run.sqlite").exists():
            raise ValueError("Use a new output directory for every run; existing ledgers cannot be overwritten")
        self.config=json.loads(args.config.read_text(encoding="utf-8"))
        if self.config["environment"]!="testnet":
            raise ValueError("Testnet required")
        self.journal=Journal(args.output)
        self.strategy_mode="legacy_fv" if getattr(args,"legacy_fv",False) else "state"
        self.market=Markets(self.journal,self.strategy_mode); self.market.phase="evaluation"
        self.lifecycle=LifecyclePolicy()
        self.position_context={}
        self.risk_guard=RiskGuard(max_ack_seconds=self.config.get("maximum_ack_seconds",5))
        self.markouts=Markouts(self.journal,observer=self.risk_guard.markout)
        self.market.quote_observers.append(self.markouts.observe)
        self.market.gap_observers.append(self.markouts.gap)
        self.emergency_taker=self.config.get("emergency_taker",False)
        self.models=json.loads(args.models.read_text(encoding="utf-8"))
        self.routes={k:v for k,v in ROUTES.items() if not getattr(args,"strategy",None) or k==args.strategy}
        if time.time()-args.models.stat().st_mtime>self.config["maximum_model_age_seconds"]:
            raise ValueError("Precalibration models expired; collect fresh training data")
        self.working={}; self.registry={}; self.inventory={}; self.position_since={}; self.entry_mid={}
        self.ready={v:asyncio.Event() for v in ("bybit","grvt")}
        self.account={}; self.equity_start={}; self.equity_peak={}; self.halted={}
        self.invalid_since={}; self.stop=asyncio.Event(); self.requested_stop=asyncio.Event(); self.closing=False
        self.last_api={v:0 for v in self.ready}; self.fill_generation={v:0 for v in self.ready}
        self.clients={}; self.locks={v:asyncio.Lock() for v in self.ready}
        self.started=0; self.start_ns=0; self.end_ns=0
        for venue in self.ready:
            for sid,(_,asset) in ROUTES.items():
                model=self.models[venue+":"+sid]
                s=self.market.models[venue,asset]
                if self.strategy_mode=="state":
                    if model.get("schema")!="mm-state-v1":raise ValueError("New state training required; legacy OLS models cannot configure state trading")
                    s.calibration=model.get("calibration",{})
                    if sid=="S2":s.cached_baseline=s.calibration.get("baseline_bp")
                else:
                    if model.get("valid"):s.coefficient=D(str(model["coefficient"]))
                    if sid=="S2" and model.get("basis") is not None:s.basis=D(str(model["basis"]))

    def on_order(self,venue,row,origin):
        if venue=="bybit":
            cid=row.get("orderLinkId"); status=row.get("orderStatus")
            leaves=D(row.get("leavesQty") or 0)
            terminal=status in ("Filled","Cancelled","Rejected","Deactivated","PartiallyFilledCanceled")
            live=status in ("New","PartiallyFilled")
        else:
            cid=str(row.get("metadata",{}).get("client_order_id",row.get("client_order_id","")))
            state=row.get("state",{}); status=state.get("status")
            working=self.registry.get((venue,cid))
            traded=state.get("traded_size",[]) or ["0"]
            book=state.get("book_size",[]) or []
            leaves=D(book[0]) if book else max(D(0),working.intent.quantity-D(traded[0])) if working else D(0)
            terminal=status in ("FILLED","REJECTED","CANCELLED")
            live=status=="OPEN"
        working=self.registry.get((venue,str(cid)))
        if working is None:
            return
        if working.done and not terminal:
            return
        was_done=working.done
        now=time.monotonic(); working.status=status; working.leaves=leaves
        working.live=live and not terminal; working.done|=terminal
        if working.live and working.active_ts is None:working.active_ts=now
        if hasattr(self,"risk_guard") and status in ("Rejected","REJECTED") and not was_done:
            self.risk_guard.event(venue,"REJECT",now)
        self.journal.db.execute("UPDATE orders SET status=?, live=CASE WHEN ? THEN COALESCE(live,?) ELSE live END, terminal=CASE WHEN ? THEN COALESCE(terminal,?) ELSE terminal END WHERE client_id=?",(status,int(live),now,int(terminal),now,str(cid)))
        self.journal.event("order_state",venue,client_id=str(cid),status=status,origin=origin,leaves=leaves)
        self.market.dirty[venue]=True

    def on_fill(self,venue,row):
        if venue=="bybit":
            cid=str(row.get("orderLinkId","")); asset=row.get("symbol","").removesuffix("USDT")
            exec_id=str(row.get("execId")); side=row.get("side"); qty=D(row.get("execQty") or 0); price=D(row.get("execPrice") or 0)
            fee=D(row.get("execFee") or 0); maker=bool(row.get("isMaker")); source=int(row.get("execTime") or 0)*10**6
        else:
            cid=str(row.get("client_order_id","")); asset=row["instrument"].split("_")[0]
            exec_id=str(row["order_id"])+":"+str(row["trade_id"]); side="Buy" if row["is_buyer"] else "Sell"
            qty,price,fee=D(row["size"]),D(row["price"]),D(row["fee"])+D(row.get("builder_fee") or 0)
            maker=not row["is_taker"]; source=int(row["event_time"])
        if (venue,cid) not in self.registry or qty<=0:
            return
        now=time.monotonic()
        inserted=self.journal.db.execute("INSERT OR IGNORE INTO fills VALUES(?,?,?,?,?,?,?,?,?,?,?)",(venue,exec_id,cid,asset,side,str(qty),str(price),str(fee),int(maker),source,now)).rowcount
        if not inserted:
            return
        self.fill_generation[venue]+=1
        key=venue,asset; old=self.inventory.get(key,D(0))
        new=old+qty*(1 if side=="Buy" else -1); self.inventory[key]=new
        if old==0 and new:
            self.position_since[key]=now; self.entry_mid[key]=price
            if hasattr(self,"position_context"):
                w=self.registry[venue,cid]
                self.position_context[key]={"entry_price":price,"initial_quantity":qty,"entry_residual_bp":(w.signal_snapshot or {}).get("residual_bp")}
        elif old*(1 if side=="Buy" else -1)>0 and hasattr(self,"position_context"):
            context=self.position_context.setdefault(key,{"initial_quantity":abs(old),"entry_price":self.entry_mid.get(key,price)})
            total=D(str(context["initial_quantity"]))+qty
            context["entry_price"]=(D(str(context["initial_quantity"]))*D(str(context["entry_price"]))+qty*price)/total
            context["initial_quantity"]=total
            self.entry_mid[key]=context["entry_price"]
        if not new:
            self.position_since.pop(key,None); self.entry_mid.pop(key,None)
            if hasattr(self,"position_context"):self.position_context.pop(key,None)
        self.market.dirty[venue]=True
        self.journal.event("fill",venue,asset,client_id=cid,quantity=qty,maker=maker)
        if hasattr(self,"markouts") and maker:
            self.markouts.add(SimpleNamespace(identifier=cid,venue=venue,asset=asset,intent=SimpleNamespace(side=side),ioc=False),qty,price,fee,now)

    async def reconcile(self,venue):
        client=self.clients[venue]
        # Re-read each reserved order before releasing its worst-case quantity.
        for (v,asset),working in list(self.working.items()):
            if v!=venue:
                continue
            if working.status=="SHADOW":
                continue
            try:
                state=await client.order(asset,working.client_id)
                if state:
                    self.on_order(venue,state["raw"],"rest_reconciliation")
            except Exception as exc:
                self.journal.event("order_reconcile_error",venue,asset,reason=type(exc).__name__)
                if not working.done:
                    working.status="UNKNOWN"
        fills=await client.fills(max(self.start_ns,time.time_ns()-120*10**9),time.time_ns())
        for row in fills: self.on_fill(venue,row)
        generation=self.fill_generation[venue]
        positions=await client.positions()
        if generation!=self.fill_generation[venue]:
            return
        unexpected=set(positions)-{a for _,a in self.routes.values()}
        if unexpected:
            self.halted[venue]="unexpected_external_position"
            self.journal.event("unexpected_position",venue,assets=sorted(unexpected))
        for sid,(_,asset) in self.routes.items():
            key=venue,asset; size=positions.get(asset,D(0)); old=self.inventory.get(key,D(0))
            if size!=old:
                self.journal.event("inventory_reconciliation",venue,asset,old=old,new=size)
            self.inventory[key]=size
            if size and key not in self.position_since:
                self.position_since[key]=time.monotonic()
                q=self.market.quotes.get(key)
                self.entry_mid[key]=q.mid if q else D(0)
            if not size:
                self.position_since.pop(key,None); self.entry_mid.pop(key,None)
            w=self.working.get(key)
            if w and w.done:
                del self.working[key]
        self.market.dirty[venue]=True

    async def account_update(self,venue):
        state=await self.clients[venue].account()
        self.account[venue]=state
        peak=max(self.equity_peak.get(venue,state["equity"]),state["equity"]); self.equity_peak[venue]=peak
        initial=self.equity_start.get(venue,state["equity"])
        c=self.config
        if state["equity"]<initial*(1-D(c["account_loss_fraction"])) or state["equity"]<peak*(1-D(c["account_drawdown_fraction"])) or state["margin"]>=D(c["maximum_margin_rate"]):
            self.halted[venue]="account_risk_limit"
        self.journal.db.execute("INSERT INTO equity VALUES(?,?,?,?,?)",(time.monotonic(),venue,*map(str,(state["equity"],state["available"],state["margin"]))))

    def gross_reserved(self,venue):
        total=D(0)
        for sid,(_,asset) in self.routes.items():
            q=self.market.quotes.get((venue,asset))
            if not q: continue
            inventory=abs(self.inventory.get((venue,asset),D(0)))
            working=self.working.get((venue,asset))
            # Reserve both sides independently. Reduce-only cannot add exposure.
            pending=working.leaves if working and not working.intent.reduce_only else D(0)
            total+=(inventory+pending)*q.mid
        return total

    async def cancel(self,venue,asset,working):
        if time.monotonic()-working.cancel_sent<2:
            return
        working.cancel_sent=time.monotonic()
        if hasattr(self,"risk_guard"):self.risk_guard.event(venue,"CANCEL",working.cancel_sent)
        self.journal.event("cancel_send",venue,asset,client_id=working.client_id)
        try:
            await self.clients[venue].cancel(asset,working.client_id)
            self.journal.event("cancel_ack",venue,asset,client_id=working.client_id)
        except Exception as exc:
            self.journal.event("cancel_error",venue,asset,client_id=working.client_id,reason=type(exc).__name__,message=str(exc)[:200])
        # Cancel ACK is deliberately not a terminal confirmation.

    async def create(self,venue,sid,asset,intent,q,ioc=False):
        if ioc and not getattr(self,"emergency_taker",False):raise ValueError("Emergency taker is disabled")
        if not ioc and (intent.price>=q.ask if intent.side=="Buy" else intent.price<=q.bid):raise ValueError("Marketable maker intent rejected before send")
        now=time.monotonic()
        if now-self.last_api[venue]<self.config["minimum_api_interval_seconds"]:
            self.market.dirty[venue]=True
            return
        cid="fv4-"+sid+"-"+secrets.token_hex(9) if venue=="bybit" else str(secrets.randbits(63)|(1<<63))
        w=Working(cid,intent,intent.quantity,now)
        if getattr(self,"strategy_mode",None)=="state":
            q0,s0=self.market.signal(venue,asset,now)
            w.signal_snapshot=s0.metadata if s0 else {}
        self.working[venue,asset]=w; self.registry[venue,cid]=w
        self.journal.db.execute("INSERT INTO orders(client_id,venue,strategy,asset,side,role,qty,price,decision,source_ns,receive,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(cid,venue,sid,asset,intent.side,intent.role,str(intent.quantity),str(intent.price),now,q.source_ns,q.received,"PENDING"))
        self.journal.commit()  # Persist intent before any exchange mutation.
        if not self.args.trade_testnet:
            self.journal.event("shadow_intent",venue,asset,client_id=cid)
            w.done=True; w.status="SHADOW"; self.journal.db.execute("UPDATE orders SET status='SHADOW',terminal=? WHERE client_id=?",(now,cid))
            return
        self.last_api[venue]=time.monotonic()
        if hasattr(self,"risk_guard"):self.risk_guard.event(venue,"SEND",self.last_api[venue])
        self.journal.db.execute("UPDATE orders SET send=? WHERE client_id=?",(self.last_api[venue],cid))
        try:
            await asyncio.wait_for(self.clients[venue].create(asset,intent,cid,ioc),timeout=self.config.get("maximum_ack_seconds",5))
            ack=time.monotonic()
            self.journal.db.execute("UPDATE orders SET ack=? WHERE client_id=?",(ack,cid))
            if hasattr(self,"risk_guard"):self.risk_guard.acknowledgement(venue,self.last_api[venue],ack)
        except (RuntimeError,ValueError) as exc:
            # Explicit API rejection / pre-send validation is distinguishable
            # from transport loss. No fee/key/signature payload is stored.
            w.done=True; w.status="REJECTED"
            self.journal.db.execute("UPDATE orders SET status='REJECTED',terminal=?,error=? WHERE client_id=?",(time.monotonic(),str(exc)[:250],cid))
            self.journal.event("create_rejected",venue,asset,reason=str(exc)[:250])
            if hasattr(self,"risk_guard"):self.risk_guard.event(venue,"REJECT",time.monotonic())
        except Exception as exc:
            w.status="UNKNOWN"
            self.halted[venue]="unknown_submission"
            self.journal.db.execute("UPDATE orders SET status='UNKNOWN',error=? WHERE client_id=?",(type(exc).__name__,cid))
            self.journal.event("create_unknown",venue,asset,reason=type(exc).__name__)

    async def decide(self,venue):
        now=time.monotonic(); c=self.config; client=self.clients[venue]
        if venue in self.risk_guard.halted:self.halted.setdefault(venue,self.risk_guard.halted[venue])
        for sid,(_,asset) in self.routes.items():
            key=venue,asset; q,s=self.market.signal(venue,asset,now)
            raw=self.market.quotes.get(key)
            inventory=self.inventory.get(key,D(0)); working=self.working.get(key)
            model=self.models[venue+":"+sid]
            valid=q is not None and (s.ready if self.strategy_mode=="state" else s.fv is not None) and model.get("valid",False)
            if valid: self.invalid_since.pop(key,None)
            else: self.invalid_since.setdefault(key,now)
            no_new=self.closing or venue in self.halted or not self.ready[venue].is_set() or inventory!=0
            target=None; ioc=False
            if q:
                model=self.models[venue+":"+sid]
                maker,taker=client.fees[asset]
                cost=q.mid*2*max(D(0),maker)+client.instruments[asset].tick
                if self.strategy_mode=="legacy_fv":cost+=D(str(model.get("error_allowance") or 0))
                equity=self.account[venue]["equity"]
                unit=min(D(c["unit_equity_fraction"])*equity/q.mid,D(c["unit_notional_cap_usdt"])/q.mid,D(c["depth_participation"])*q.depth)
                unit=client.instruments[asset].floor(unit)
                headroom=max(D(0),D(c["account_gross_fraction"])*equity-self.gross_reserved(venue))
                unit=min(unit,client.instruments[asset].floor(headroom/q.mid))
                extra={"position_context":self.position_context.get(key,{})} if self.strategy_mode=="state" else {}
                intents=self.market.models[key].order_intents(s,q,cost=cost,tick=client.instruments[asset].tick,lot=client.instruments[asset].lot,unit=unit,inventory=inventory,max_inventory=max(unit,abs(inventory)),new_exposure=valid and not no_new,**extra)
                target=next((i for i in intents if i.reduce_only),intents[0] if intents else None)
            since=self.position_since.get(key,now)
            entry=self.entry_mid.get(key,D(0))
            loss=raw is not None and entry>0 and (raw.mid-entry)*(1 if inventory>0 else -1)<-entry*D(c["position_stop_fraction"])
            overdue=now-since>=c["holding_cap_seconds"] or (not valid and now-self.invalid_since[key]>=c["invalid_model_reduce_grace_seconds"]) or venue in self.halted or loss
            if self.closing and now-self.close_started>=c["shutdown_maker_grace_seconds"]:
                overdue=True
            if inventory and overdue and raw and raw.valid() and now-raw.received<c["feed_stale_seconds"]:
                tick=client.instruments[asset].tick
                price=((raw.bid*(1-D("0.002")))/tick).to_integral_value(rounding=ROUND_FLOOR)*tick if inventory>0 else ((raw.ask*(1+D("0.002")))/tick).to_integral_value(rounding=ROUND_CEILING)*tick
                target=Intent("Sell" if inventory>0 else "Buy",price if self.emergency_taker else raw.ask if inventory>0 else raw.bid,abs(inventory),"EMERGENCY_TAKER" if self.emergency_taker else "RISK_MAKER_UNWIND",True); q=raw; ioc=self.emergency_taker
            if working:
                self.market.own[key]=[(working.intent,working.leaves,working.live)]
                jump=s is not None and self.strategy_mode=="state" and risk_state(s)[0]=="JUMP_RISK"
                flip=s is not None and self.strategy_mode=="state" and s.direction!=0 and s.direction!=(1 if working.intent.side=="Buy" else -1) and not working.intent.reduce_only
                hard=(not q) or (working.intent.reduce_only and not inventory) or (not working.intent.reduce_only and (self.closing or venue in self.halted or jump or loss or inventory!=0))
                if self.emergency_taker and overdue and working.intent.role!="EMERGENCY_TAKER":hard=True
                reason=self.lifecycle.reason(working,target,now,client.instruments[asset].tick,hard_risk=hard,state_flip=flip)
                if not working.done and reason:
                    self.journal.event("policy_cancel",venue,asset,client_id=working.client_id,reason=reason,state=s.state if s else "INVALID")
                    if self.args.trade_testnet: await self.cancel(venue,asset,working)
                    else: working.done=True
                continue
            self.market.own[key]=[]
            if target and q and client.instruments[asset].valid(target.quantity,target.price,target.reduce_only):
                await self.create(venue,sid,asset,target,q,ioc)

    async def worker(self,venue):
        last_reconcile,last_account=0,0
        while not self.stop.is_set():
            try:
                now=time.monotonic()
                if now-last_reconcile>=2:
                    await self.reconcile(venue); last_reconcile=time.monotonic()
                if now-last_account>=10:
                    await self.account_update(venue); last_account=time.monotonic()
                if self.market.dirty[venue] or self.closing:
                    self.market.dirty[venue]=False
                    await self.decide(venue)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.halted[venue]="account_or_order_reconciliation_failed"
                self.journal.event("worker_error",venue,reason=type(exc).__name__,message=str(exc)[:200])
                for (v,asset),w in list(self.working.items()):
                    if v==venue and not w.done and self.args.trade_testnet:
                        await self.cancel(v,asset,w)
                await asyncio.sleep(1)
            await asyncio.sleep(.1)

    def status(self):
        return {"phase":"cleanup" if self.closing else "evaluation","elapsed_seconds":round(time.monotonic()-self.started,1) if self.started else 0,"duration_seconds":self.args.duration,"inventory":{v+":"+a:str(q) for (v,a),q in self.inventory.items()},"working_orders":len(self.working),"orders":self.journal.db.execute("SELECT count(*) FROM orders").fetchone()[0],"fills":self.journal.db.execute("SELECT count(*) FROM fills").fetchone()[0],"halted":self.halted,"private_ready":{v:e.is_set() for v,e in self.ready.items()}}

    async def run(self):
        tasks=[]; complete=False
        session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        try:
            if True:
                self.clients={name:cls(session,self.args.trade_testnet) for name,cls in (("bybit",Bybit),("grvt",Grvt)) if getattr(self.args,"venue","all") in ("all",name)}
                self.ready={v:self.ready[v] for v in self.clients}
                for venue,client in self.clients.items():
                    await client.connect()
                    for sid,(_,asset) in self.routes.items():
                        if self.strategy_mode=="state":self.market.models[venue,asset].engine.tick=float(client.instruments[asset].tick)
                    positions,orders,state=await asyncio.gather(client.positions(),client.open_orders(),client.account())
                    if positions or orders:
                        raise RuntimeError(f"{venue}: nonempty account; no existing position/order takeover")
                    if state["available"]<D(1000) or state["margin"]>=D(self.config["maximum_margin_rate"]):
                        raise RuntimeError(f"{venue}: testnet balance or margin preflight failed")
                    self.account[venue]=state; self.equity_start[venue]=state["equity"]; self.equity_peak[venue]=state["equity"]
                feeds=Feeds(self.market.on_book,self.market.on_trade,self.market.on_gap,self.market.on_reference)
                tasks+=feeds.tasks()
                for v,fn in (("bybit",bybit_private),("grvt",grvt_private)):
                    if v in self.clients:
                        tasks.append(asyncio.create_task(fn(self.clients[v],self.on_order,self.on_fill,self.ready[v],self.journal)))
                await asyncio.wait_for(asyncio.gather(*(e.wait() for e in self.ready.values())),45)
                wait_start=time.monotonic()
                while any(self.market.signal(v,a,time.monotonic())[0] is None for v in self.clients for _,a in self.routes.values()):
                    if time.monotonic()-wait_start>45: raise RuntimeError("All eight public BBO feeds required")
                    await asyncio.sleep(.2)
                self.started=time.monotonic(); self.start_ns=time.time_ns()
                manifest={"version":"fv-mm-v4.0","mode":"trade-testnet" if self.args.trade_testnet else "shadow","start_ns":self.start_ns,"duration":self.args.duration,"routes":self.routes,"config":self.config,"models":self.models,"equity_start":self.equity_start,"fee_budget":{v:c.fees for v,c in self.clients.items()},"instruments":{v:{a:{"tick":i.tick,"lot":i.lot,"minimum":i.minimum,"min_notional":i.min_notional} for a,i in c.instruments.items()} for v,c in self.clients.items()},"model_sha256":hashlib.sha256(self.args.models.read_bytes()).hexdigest(),"source_sha256":self.source_hash(),"training_directory":str(self.args.models.parent)}
                self.journal.save("manifest.json",manifest)
                tasks+=[asyncio.create_task(self.worker(v),name="fv-worker-"+v) for v in self.clients]
                while time.monotonic()-self.started<self.args.duration and not self.requested_stop.is_set():
                    if (self.args.output/"STOP").exists(): break
                    self.market.sample(); self.journal.commit(); self.journal.save("status.json",self.status())
                    await asyncio.sleep(1)
                self.end_ns=time.time_ns(); self.closing=True; self.close_started=time.monotonic(); self.market.phase="cleanup"
                self.journal.event("evaluation_ended",duration=time.monotonic()-self.started)
                while time.monotonic()-self.close_started<self.config["shutdown_timeout_seconds"]:
                    self.market.sample(); self.journal.commit(); self.journal.save("status.json",self.status())
                    if not self.working and not any(self.inventory.values()): complete=True; break
                    await asyncio.sleep(1)
                self.stop.set()
                await asyncio.gather(*(self.reconcile(v) for v in self.clients))
                histories={}
                for v,client in self.clients.items():
                    await self.account_update(v)
                    fills=await client.fills(self.start_ns,time.time_ns())
                    for r in fills: self.on_fill(v,r)
                    try: funding=await client.funding(self.start_ns,time.time_ns())
                    except Exception as exc: funding={"unavailable":type(exc).__name__}
                    histories[v]={"funding":funding,"account_end":self.account[v],"positions":await client.positions(),"open_orders":len(await client.open_orders())}
                self.journal.save("account_end.json",histories)
                result={**self.status(),"complete":complete and not any(self.inventory.values()) and not self.working,"evaluation_start_ns":self.start_ns,"evaluation_end_ns":self.end_ns,"evaluation_elapsed_seconds":(self.end_ns-self.start_ns)/1e9,"cleanup_end_ns":time.time_ns()}
                self.journal.save("complete.json",result)
                print(json.dumps(result,indent=2),flush=True)
        finally:
            self.stop.set()
            workers=[t for t in tasks if t.get_name().startswith("fv-worker-")]
            if workers:
                try: await asyncio.wait_for(asyncio.gather(*workers,return_exceptions=True),15)
                except asyncio.TimeoutError: pass
            if self.started and not complete and self.args.trade_testnet:
                # Keep public feeds and HTTP session alive during emergency
                # reconciliation; only this run's orders/positions are managed.
                self.closing=True; self.close_started=time.monotonic()
                while time.monotonic()-self.close_started<self.config["shutdown_timeout_seconds"]:
                    for v in self.clients:
                        try:
                            await self.reconcile(v)
                            await self.decide(v)
                        except Exception as exc:
                            self.journal.event("emergency_cleanup_error",v,reason=type(exc).__name__)
                    self.journal.commit()
                    if not self.working and not any(self.inventory.values()):
                        complete=True; break
                    await asyncio.sleep(1)
            for t in tasks: t.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            if self.started and not complete:
                self.journal.save("attention.json",{"reason":"Run interrupted or cleanup not verified; reconcile reservations before restarting",**self.status()})
            await session.close()
            self.markouts.finish()
            self.journal.close()

    @staticmethod
    def source_hash():
        root=Path(__file__).resolve().parents[2]
        digest=hashlib.sha256()
        for folder in (root/"strategy",root/"vibetrading"/"fv_mm",root/"vibetrading"/"mm_state"):
            for p in sorted(folder.glob("*.py")):
                digest.update(p.name.encode()); digest.update(p.read_bytes())
        return digest.hexdigest()
