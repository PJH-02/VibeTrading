"""Causal flat-account eligibility audit for zero-intent paths, no order engine."""
import importlib,json,sys
from collections import Counter
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vibetrading.mm_state.replay import sqlite_events,NativeReplay
from vibetrading.mm_state.policy import risk_state
from vibetrading.fv_pilot.public import Instrument

out=Path('docs/evidence/mm_state_revision_20261007')
src=Path('runtime_data/fv_pilot/server_evidence/runtime_data')
raw=json.loads((src/'mainnet_training_verified_20261004/instruments.json').read_text(encoding='utf-8'))
fits=json.loads((out/'replay_sealed/models.json').read_text(encoding='utf-8'))
targets={('grvt','BTC'):('S1','s1_net_liquidity'),('arcus','XRP'):('S4','s4_obi')}
models={};instruments={};counts={k:Counter() for k in targets};last={};prices={k:[] for k in targets}
for key,(sid,module) in targets.items():
    v,a=key;c=fits[v+':'+sid].get('calibration',{});r=raw[v][a]
    i=Instrument(**{k:D(x) if k in ('tick','lot','minimum','min_notional','maker_fee','taker_fee') else x for k,x in r.items()})
    instruments[key]=i;models[key]=importlib.import_module('strategies.research.state.'+module).Strategy(mode='state',calibration=c,tick=float(i.tick))
path=src/'pilot_30min_20261004/run.sqlite';decoder=NativeReplay(path)
for t,_,kind,r in sqlite_events(path):
    key=r['venue'],r.get('asset','')
    if kind=='GAP':
        for (v,a),m in models.items():
            if v==r['venue']:m.engine.reset()
        continue
    if key not in targets:continue
    result=decoder.decode(kind,r)
    if not result:continue
    event,v,a,payload=result;m=models[key];i=instruments[key]
    if event=='GAP':m.engine.reset();continue
    if event=='BOOK':
        q,b,reset=payload;m.on_book(q,reset,b);prices[key].append(float(q.mid))
    elif event=='TRADE':
        side,qty,price,now,source=payload;m.on_trade(side,qty,now,price);q=m.engine.previous
    else:continue
    if q is None or t-last.get(key,-float('inf'))<.1:continue
    last[key]=t;s=m.signal(q,t);sid=targets[key][0]
    if not fits[v+':'+sid].get('valid'):
        counts[key]['MODEL_TRAINING_INVALID']+=1;continue
    if not s.ready:counts[key]['WINDOW_NOT_READY']+=1;continue
    if not s.direction:counts[key]['NEUTRAL_DIRECTION']+=1;continue
    regime,scale,_=risk_state(s)
    if regime=='JUMP_RISK':counts[key]['JUMP_RISK']+=1;continue
    unit=i.floor(min(D(150)/q.mid,D('.1')*q.depth))
    if unit<=0:counts[key]['CAP_OR_DEPTH_UNIT_ZERO']+=1;continue
    targets_now=m.order_intents(s,q,cost=q.mid*2*max(D(0),i.maker_fee)+i.tick,tick=i.tick,lot=i.lot,unit=unit,inventory=D(0),max_inventory=i.floor(D(150)/q.mid),new_exposure=True)
    if not targets_now:counts[key]['RISK_SCALED_QUANTITY_ZERO']+=1;continue
    if not any(i.valid(x.quantity,x.price,x.reduce_only) for x in targets_now):counts[key]['EXCHANGE_MINIMUM_NOTIONAL_OR_QUANTITY']+=1;continue
    counts[key]['ELIGIBLE']+=1
decoder.close()
result={v+':'+a:{'dominant_reasons':dict(counts[v,a]),'lot':str(instruments[v,a].lot),'minimum':str(instruments[v,a].minimum),'min_notional':str(instruments[v,a].min_notional),'observed_mid_min':min(prices[v,a]) if prices[v,a] else None,'observed_mid_max':max(prices[v,a]) if prices[v,a] else None} for v,a in targets}
(out/'no_intent_diagnosis.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
