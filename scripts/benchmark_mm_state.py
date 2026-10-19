"""Bounded synthetic CPU/RSS benchmark; includes JSON/L2, state policy and paper lifecycle."""
import argparse,json,sys,time,statistics,tempfile
from pathlib import Path
from decimal import Decimal as D
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vibetrading.fv_mm.types import Quote
from vibetrading.fv_pilot.public import Book,Instrument
from vibetrading.fv_pilot.simulation import PaperMatching
from vibetrading.mm_state.coordinator import Coordinator
from vibetrading.mm_state.lifecycle import LifecyclePolicy
from vibetrading.fv_mm.journal import Journal


def main():
    p=argparse.ArgumentParser();p.add_argument('--events',type=int,default=20000);p.add_argument('--routes',type=int,default=16);p.add_argument('--journal',action='store_true');args=p.parse_args()
    if not 1<=args.events<=200000 or not 1<=args.routes<=32:p.error('Bounded benchmark only')
    assets=[f'A{i}' for i in range(args.routes)]
    i=Instrument('SYNTHETIC','USDT',D('.01'),D('.1'),D('.1'),D(1),D('.0002'),D('.00055'))
    models={a:Coordinator(profile='inventory',tick=.01) for a in assets}
    instruments={'benchmark':{a:i for a in assets}}
    position={a:D(0) for a in assets};fills=0
    def fill(o,qty,price,fee,t):
        nonlocal fills
        position[o.asset]+=qty*(1 if o.intent.side=='Buy' else -1);fills+=1
    temporary=tempfile.TemporaryDirectory();j=Journal(Path(temporary.name)) if args.journal else None
    engine=PaperMatching(instruments,{'benchmark':{'entry_seconds':.05,'cancel_seconds':.05}},j,
        fill_callback=fill,position_callback=lambda o:position[o.asset])
    policy=LifecyclePolicy();books={a:Book() for a in assets};working={};durations=[];decisions=0
    peak_labels=0;wall=time.perf_counter();cpu=time.process_time()
    for n in range(args.events):
        began=time.perf_counter();a=assets[n%args.routes];step=n//args.routes;t=step*.02
        rows={'b':[[100-k*.01,5+(step%10) if k==0 else 5] for k in range(50)],'a':[[100.02+k*.01,1 if k==0 else 5] for k in range(50)]}
        wire=json.loads(json.dumps(rows));b=books[a]
        b.update(wire['b'],wire['a'],step+1,True)
        q=Quote(D(100),D('100.02'),D(str(5+step%10)),D(1),t,0,step+1)
        models[a].on_book(q,step==0,b);engine.on_book('benchmark',a,q,b,step==0)
        if step%4==0:
            side,px=('Buy',D('100.60')) if position[a]>0 else ('Sell',D('99.50'))
            models[a].on_trade(side,D(20),t,px);engine.on_trade('benchmark',a,side,D(20),px,t,0)
        if step%5==0:
            s=models[a].signal(q,t);decisions+=1
            targets=models[a].order_intents(s,q,cost=D('.05'),tick=i.tick,lot=i.lot,unit=D(1),inventory=position[a],max_inventory=D(2),new_exposure=position[a]==0)
            target=next((x for x in targets if x.reduce_only),targets[0] if targets else None)
            old=working.get(a)
            if old and old.done:working.pop(a);old=None
            if old:
                why=policy.reason(old,target,t,i.tick)
                if why:engine.cancel_intent(old,t,why)
            elif target:working[a]=engine.submit_intent('benchmark','UNIFIED',a,target,t)
        if j and n%1000==0:j.commit()
        peak_labels=max(peak_labels,len(engine.markouts.pending))
        durations.append((time.perf_counter()-began)*1e6)
    engine.markouts.finish()
    elapsed,cpusec=time.perf_counter()-wall,time.process_time()-cpu
    if j:j.close()
    temporary.cleanup()
    try:
        import resource
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    except ImportError:rss=None
    d=sorted(durations)
    result={'events':args.events,'routes':args.routes,'wall_seconds':elapsed,'cpu_seconds':cpusec,
        'events_per_wall_second':args.events/elapsed,'events_per_cpu_second':args.events/cpusec,
        'event_p50_us':statistics.median(d),'event_p99_us':d[int(.99*(len(d)-1))],
        'peak_rss_bytes':rss,'decision_count':decisions,'max_feature_buckets':max(len(m.engine.buckets) for m in models.values()),
        'retained_orders':len(engine.orders),'pending_orders':len(engine.pending),'markout_pending':len(engine.markouts.pending),
        'fill_events':fills,'peak_markout_pending':peak_labels,'markout_rows_retained':len(engine.markouts.rows),
        'journal':args.journal,'network_orders':0,'interpretation':'synthetic computation benchmark, not profitability or whole collector/storage throughput'}
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
