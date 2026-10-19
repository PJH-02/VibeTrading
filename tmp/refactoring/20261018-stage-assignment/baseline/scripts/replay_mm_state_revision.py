"""Train-frozen native replay and incremental ablations, isolated virtual accounts."""
import argparse,json,importlib,sys,time,hashlib,csv
from pathlib import Path
from decimal import Decimal as D
from dataclasses import asdict
from collections import defaultdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vibetrading.fv_mm.market import ROUTES
from vibetrading.fv_mm.journal import Journal
from vibetrading.fv_pilot.public import Instrument
from vibetrading.fv_pilot.simulation import PaperMatching
from vibetrading.mm_state.replay import sqlite_events,NativeReplay
from vibetrading.mm_state.calibration import fit_states
from vibetrading.mm_state.coordinator import Coordinator,PROFILES
from vibetrading.mm_state.lifecycle import LifecyclePolicy
from vibetrading.mm_state.policy import risk_state


def factory(sid,c,tick):return importlib.import_module('strategy.'+ROUTES[sid][0]).Strategy(mode='state',calibration=c,tick=float(tick))


class Case:
    def __init__(self,name,v,a,model,instrument,latency,journal,queue,factor,enabled=True):
        self.name,self.venue,self.asset,self.model,self.i=name,v,a,model,instrument
        self.position,self.cash,self.fees,self.realized,self.entry,self.initial=D(0),D(0),D(0),D(0),D(0),D(0)
        self.since,self.entry_residual,self.q,self.signal=None,None,None,None
        self.last_decision=-float('inf');self.working={};self.policy=LifecyclePolicy()
        self.engine=PaperMatching({v:{a:instrument}},{v:latency},journal,name,factor,self.fill,
            queue_model=queue,position_callback=lambda o:self.position)
        self.enabled=enabled;self.fills=0;self.filled_notional=D(0);self.peak=0.;self.drawdown=0.;self.max_position=D(0)
        self.state_counts=defaultdict(int);self.journal=journal

    def fill(self,o,qty,price,fee,t):
        side=1 if o.intent.side=='Buy' else -1;old=self.position
        if old==0:
            self.entry,self.initial,self.since=price,qty,t
            self.entry_residual=(o.signal_snapshot or {}).get('residual_bp')
        elif old*side>0:
            self.entry=(abs(old)*self.entry+qty*price)/(abs(old)+qty);self.initial+=qty
        else:self.realized+=min(abs(old),qty)*(price-self.entry)*(1 if old>0 else -1)
        self.position+=side*qty;self.cash-=side*qty*price;self.fees+=fee
        if not self.position:self.entry,self.initial,self.since=D(0),D(0),None
        self.max_position=max(self.max_position,abs(self.position));self.fills+=1;self.filled_notional+=qty*price

    def decide(self,t,reference):
        if not self.q or t-self.last_decision<.1:return
        self.last_decision=t;s=self.model.signal(self.q,t,reference);self.signal=s;self.state_counts[s.state]+=1
        riskregime=risk_state(s)[0]
        unit=self.i.floor(min(D(150)/self.q.mid,D('.1')*self.q.depth))
        cap=self.i.floor(D(150)/self.q.mid)
        overdue=self.position!=0 and (self.since is not None and t-self.since>=120 or self.entry>0 and (self.q.mid/self.entry-1)*(1 if self.position>0 else -1)<D('-.0035'))
        ctx={'entry_price':self.entry,'initial_quantity':self.initial,'entry_residual_bp':self.entry_residual}
        targets=self.model.order_intents(s,self.q,cost=self.q.mid*2*max(D(0),self.i.maker_fee)+self.i.tick,
            tick=self.i.tick,lot=self.i.lot,unit=unit,inventory=self.position,max_inventory=cap,
            new_exposure=self.enabled and self.position==0 and self.drawdown<20,force_reduce=overdue,position_context=ctx)
        if overdue:
            from vibetrading.fv_mm.types import Intent
            targets=[Intent('Sell' if self.position>0 else 'Buy',self.q.ask if self.position>0 else self.q.bid,abs(self.position),'RISK_MAKER_UNWIND',True)]
        desired={x.side:x for x in targets if self.i.valid(x.quantity,x.price,x.reduce_only)}
        for side,o in list(self.working.items()):
            if o.done:self.working.pop(side);continue
            target=desired.pop(side,None)
            hard=(not s.ready) or o.intent.reduce_only and self.position==0 or not o.intent.reduce_only and (self.position!=0 or riskregime=='JUMP_RISK' or self.drawdown>=20)
            flip=s.direction and s.direction!=(1 if side=='Buy' else -1) and not o.intent.reduce_only
            why=self.policy.reason(o,target,t,self.i.tick,hard_risk=hard,state_flip=bool(flip))
            if why:self.engine.cancel_intent(o,t,why)
        for side,x in desired.items():
            if side not in self.working:self.working[side]=self.engine.submit_intent(self.venue,self.name,self.asset,x,t,signal_snapshot=s.metadata)

    def book(self,q,book,reset,reference):
        self.q=q;self.model.on_book(q,reset,book)
        self.engine.on_book(self.venue,self.asset,q,book,reset)
        self.decide(q.received,reference)
        pnl=float(self.cash-self.fees+self.position*q.mid)
        self.peak=max(self.peak,pnl);self.drawdown=max(self.drawdown,self.peak-pnl)

    def trade(self,values,reference):
        side,qty,price,t,source=values
        self.model.on_trade(side,qty,t,price)
        self.engine.on_trade(self.venue,self.asset,side,qty,price,t,source)
        self.decide(t,reference)

    def finish(self,t):
        for o in list(self.engine.pending.values()):self.engine.cancel_intent(o,t,'REPLAY_END')
        self.engine.advance(t+30)
        self.engine.markouts.finish()
        counts={k.rsplit(':',1)[-1]:n for k,n in self.engine.counts.items()}
        pnl=self.cash-self.fees+self.position*(self.q.mid if self.q else 0)
        return dict(case=self.name,venue=self.venue,asset=self.asset,enabled=self.enabled,
            **counts,fill_events=self.fills,filled_notional=float(self.filled_notional),maker_fees=float(self.fees),
            gross_mtm_pnl=float(pnl+self.fees),net_mtm_pnl=float(pnl),realized_gross_pnl=float(self.realized),
            unrealized_gross_pnl=float(pnl+self.fees-self.realized),max_drawdown=self.drawdown,
            end_inventory=float(self.position),max_inventory=float(self.max_position),
            end_pending_orders=len(self.engine.pending),state_counts=dict(self.state_counts))


def main(args):
    args.output.mkdir(parents=True,exist_ok=True)
    if (args.output/'run.sqlite').exists():raise ValueError('Use a new replay ledger')
    source=args.source
    train=source/'mainnet_training_verified_20261004/run.sqlite';test=source/'pilot_30min_20261004/run.sqlite'
    original=json.loads((source/'mainnet_training_verified_20261004/instruments.json').read_text(encoding='utf-8'))
    instruments={v:{a:Instrument(**{k:D(x) if k in ('tick','lot','minimum','min_notional','maker_fee','taker_fee') else x for k,x in row.items()}) for a,row in assets.items()} for v,assets in original.items()}
    latencies=json.loads(args.latency.read_text(encoding='utf-8'))
    models={(v,a):factory(sid,{},instruments[v][a].tick) for v in instruments for sid,(_,a) in ROUTES.items()}
    # Same-asset ablation thresholds; no strategy/asset ranking confound.
    ablation_model=factory('S1',{},instruments['bybit']['BTC'].tick)
    rows=defaultdict(list);refs={};last_sample={};decoder=NativeReplay(train);began=time.perf_counter()
    for t,priority,kind,r in sqlite_events(train):
        value=decoder.decode(kind,r)
        if not value:continue
        kind,v,a,payload=value
        if kind=='REFERENCE':refs[a]=payload;continue
        if kind=='GAP':
            if v=='binance':refs.clear()
            for (venue,asset),m in models.items():
                if venue==v:m.engine.reset()
            continue
        key=v,a;m=models.get(key)
        if m is None:continue
        if kind=='BOOK':
            q,b,reset=payload;m.on_book(q,reset,b)
            if v=='bybit' and a=='BTC':ablation_model.on_book(q,reset,b)
            if t-last_sample.get(key,-float('inf'))>=1:
                ref=refs.get(a);s=m.signal(q,t,ref)
                record={'t':t,'mid':float(q.mid),'ref_mid':float(ref.mid) if ref and 0<=t-ref.received<=1.5 else None,'ready':s.ready,'metadata':s.metadata}
                rows[key].append(record);last_sample[key]=t
        elif kind=='TRADE':
            side,qty,price,now,src=payload;m.on_trade(side,qty,now,price)
            if v=='bybit' and a=='BTC':ablation_model.on_trade(side,qty,now,price)
    train_audit=decoder.audit;decoder.close()
    fitted={v+':'+sid:fit_states(rows[v,a],sid,instruments[v][a].maker_fee) for v in instruments for sid,(_,a) in ROUTES.items()}
    same={sid:fit_states(rows['bybit','BTC'],sid,instruments['bybit']['BTC'].maker_fee) for sid in ROUTES}
    (args.output/'models.json').write_text(json.dumps(fitted,indent=2),encoding='utf-8')
    (args.output/'same_asset_models.json').write_text(json.dumps(same,indent=2),encoding='utf-8')
    j=Journal(args.output);cases=[]
    for label,queue,factor in (('C1','conservative',1),('O1','proportional_cancel',1),('C4','conservative',4)):
        for v in instruments:
            for sid,(_,a) in ROUTES.items():
                f=fitted[v+':'+sid];m=factory(sid,f.get('calibration',{}),instruments[v][a].tick)
                cases.append(Case(label+'.'+v+'.'+sid,v,a,m,instruments[v][a],latencies[v],j,queue,factor,f.get('valid',False)))
        for profile in PROFILES:
            m=Coordinator(profile=profile,calibrations={sid:x.get('calibration',{}) for sid,x in same.items()},
                          calibration=same['S4'].get('calibration',{}),tick=float(instruments['bybit']['BTC'].tick))
            cases.append(Case(label+'.ablation.'+profile,'bybit','BTC',m,instruments['bybit']['BTC'],latencies['bybit'],j,queue,factor))
    routes=defaultdict(list)
    for case in cases:routes[case.venue,case.asset].append(case)
    refs={};decoder=NativeReplay(test);last_commit=-float('inf');end=0
    for t,priority,kind,r in sqlite_events(test):
        end=t;value=decoder.decode(kind,r)
        if not value:continue
        kind,v,a,payload=value
        if kind=='REFERENCE':refs[a]=payload;continue
        if kind=='GAP':
            if v=='binance':refs.clear()
            for case in cases:
                if case.venue==v:case.engine.on_gap(v,t);case.model.engine.reset()
            continue
        for case in routes[v,a]:
            if kind=='BOOK':case.book(*payload,refs.get(a))
            else:case.trade(payload,refs.get(a))
        if t-last_commit>=10:j.commit();last_commit=t
    results=[case.finish(end) for case in cases]
    test_audit=decoder.audit;decoder.close();j.commit()
    markout=[dict(r) for r in j.db.execute("SELECT order_id,venue,asset,horizon,count(*) AS fills,avg(markout_bps) AS mean_bps,avg(markout_bps-fee_bps) AS mean_net_entry_fee_bps FROM maker_markouts WHERE status='OBSERVED' GROUP BY order_id,venue,asset,horizon")]
    # Lifecycle metrics count orders once, not partial-fill events as independent orders.
    execution=[dict(r) for r in j.db.execute("SELECT o.scenario,count(*) AS intents,sum(CASE WHEN f.n>0 THEN 1 ELSE 0 END) AS filled_orders,avg(CASE WHEN f.n>0 THEN f.first-o.live END) AS mean_time_to_first_fill,sum(CASE WHEN CAST(o.leaves AS REAL)>0 AND f.n>0 THEN 1 ELSE 0 END) AS partial_orders,sum(o.status='POST_ONLY_REJECTED') AS post_only_rejects,sum(o.cancel_request IS NOT NULL AND coalesce(f.n,0)=0) AS cancelled_without_fill FROM pilot_orders o LEFT JOIN (SELECT order_id,count(*) n,min(t) first FROM pilot_fills GROUP BY order_id) f ON f.order_id=o.id GROUP BY o.scenario")]
    j.close()
    report={'schema':'mm-state-native-replay-v1','training_audit':train_audit,'test_audit':test_audit,'latencies':latencies,
            'queue_scenarios':{'C1':'conservative','O1':'uncertain proportional cancellation credit','C4':'conservative, 4x configured latency'},
            'results':results,'execution':execution,'wall_seconds':time.perf_counter()-began,'external_order_submissions':0,
            'limitations':['already-seen historical data; exploratory implementation replay','latencies are configured sensitivities, not mainnet order measurements','virtual accounts isolated per policy; no queue volume reused between simultaneous orders within an account','terminal inventory marked at final mid, not fabricated shutdown fills','funding and USD/USDT conversion excluded','no queue touch fills or assumed hidden liquidity']}
    (args.output/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with (args.output/'markouts.csv').open('w',newline='',encoding='utf-8-sig') as f:
        if markout:w=csv.DictWriter(f,fieldnames=list(markout[0]));w.writeheader();w.writerows(markout)
    print(json.dumps({'cases':len(cases),'fill_events':sum(x['fill_events'] for x in results),'elapsed':report['wall_seconds'],'audit':test_audit}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('runtime_data/fv_pilot/server_evidence/runtime_data'));p.add_argument('--output',type=Path,required=True);p.add_argument('--latency',type=Path,default=Path('config/mm_state_latency_scenarios.json'));args=p.parse_args();main(args)
