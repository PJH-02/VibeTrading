"""Independent receipt-order numeric replay of recorded strategy signals.

No strategy classes or runtime ActivityWindow are imported. Market data and
already-accepted trade ledger are read-only; no new orders or coefficients.
"""
import heapq,json,math,sqlite3
from collections import deque
from pathlib import Path
root=Path('runtime_data/fv_pilot/server_evidence/runtime_data/pilot_30min_20261004')
c=sqlite3.connect((root/'run.sqlite').resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'));out={}
routes={'S1':'BTC','S2':'ETH','S3':'SOL','S4':'XRP'}
for venue in ('arcus','bybit','bitget','grvt'):
 for sid,asset in routes.items():
  model=manifest['models'][venue+':'+sid];coef=model.get('coefficient');basis=model.get('basis')
  quotes=[dict(r) for r in c.execute('SELECT * FROM quotes WHERE venue=? AND asset=? ORDER BY t',(venue,asset))]
  samples=[dict(r) for r in c.execute('SELECT * FROM samples WHERE venue=? AND strategy=? ORDER BY t',(venue,sid))]
  trades=[dict(r) for r in c.execute('SELECT * FROM trades WHERE venue=? AND asset=? ORDER BY t',(venue,asset))] if sid=='S3' else []
  events=deque();previous=None;current=None;bad_steps=[]
  result={'samples':len(samples),'signal_mismatches':0,'fv_mismatches':0,'reason_availability_mismatches':0,
          'max_abs_x_difference':0.,'max_abs_fv_difference':0.,'invalid_book_steps':0,'samples_using_invalid_book_activity':0}
  stream=heapq.merge(((r['t'],0,r) for r in quotes),((r['t'],1,r) for r in trades),((r['t'],2,r) for r in samples),key=lambda x:x[:2])
  def valid(q):return q[0]>0 and q[1]>q[0] and q[2]>=0 and q[3]>=0 and q[2]+q[3]>0
  for t,kind,r in stream:
   if kind==0:
    q=tuple(float(r[k]) for k in ('bid','ask','bid_qty','ask_qty'));current=q
    if r['reset']:events.clear();previous=None
    if sid=='S1' and previous is not None:
     b,a,qb,qa=previous;nb,na,nqb,nqa=q
     u=(nqb if nb>=b else 0)-(qb if nb<=b else 0)
     v=(nqa if na<=a else 0)-(qa if na>=a else 0)
     if abs(u)+abs(v)>0:events.append((t,u-v,abs(u)+abs(v)))
     if not valid(q) or not valid(previous):bad_steps.append(t);result['invalid_book_steps']+=1
    previous=q
    while events and (events[0][0]<t-120 or len(events)>20000):events.popleft()
   elif kind==1:
    if r['side'] in ('Buy','Sell'):
     qty=float(r['qty']);events.append((t,qty if r['side']=='Buy' else -qty,qty))
     while events and (events[0][0]<t-120 or len(events)>20000):events.popleft()
   else:
    if current is None:continue
    b,a,qb,qa=current;depth=(qb+qa)/2;mid=(b+a)/2;spread=a-b
    x=0.;available=False;oldest=t
    if sid in ('S1','S3'):
     total=signed=0.
     for when,value,activity in reversed(events):
      if when<t-120:break
      used=min(activity,max(0.,depth-total));signed+=value*used/activity;total+=used;oldest=when
      if total>=depth-1e-12*max(1.,depth):available=True;break
     if available:x=spread*signed/depth
     if sid=='S1' and available and any(oldest<=z<=t for z in bad_steps):result['samples_using_invalid_book_activity']+=1
    elif sid=='S4':x=spread*(qb-qa)/(qb+qa);available=True
    else:
     available=r['ref_mid'] is not None and basis is not None
     if available:x=r['ref_mid']*math.exp(basis)-mid
    diff=abs(x-r['x']);result['max_abs_x_difference']=max(result['max_abs_x_difference'],diff)
    if diff>max(1e-9,abs(r['x'])*1e-6):result['signal_mismatches']+=1
    expected_fv=mid+coef*x if available and coef is not None and (sid!='S2' or 0<coef<=1) else None
    if (r['fv'] is None)!=(expected_fv is None):result['reason_availability_mismatches']+=1
    if r['fv'] is not None and expected_fv is not None:
     fd=abs(expected_fv-r['fv']);result['max_abs_fv_difference']=max(result['max_abs_fv_difference'],fd)
     if fd>max(1e-8,abs(r['fv'])*1e-10):result['fv_mismatches']+=1
  out[venue+':'+sid]=result
path=Path('docs/evidence/fv_research_20261004/original_formula_audit.json')
path.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out))
