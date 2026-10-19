"""Reconstruct L2 pilot evidence using the same read-only extractor as archives."""
import heapq,json,sqlite3,sys
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
sys.path.insert(0,str(Path(__file__).parent))
from fv_research_extract import Series,mask_transport

def frames(c):
    for t,v,a,raw in c.execute("SELECT wall_ns,venue,asset,raw FROM public_frames WHERE kind='book' ORDER BY rowid"):
        m=json.loads(raw);prev=None
        if v=='bybit':
            d=m['data'];bids=d['b'];asks=d['a'];seq=d['u'];snap=m['type']=='snapshot'
        elif v=='bitget':
            d=m['data'][0];bids=d['b'];asks=d['a'];seq=d['seq'];prev=d.get('pseq');snap=m.get('action')=='snapshot' or int(prev or -1)==0
        elif v=='arcus':
            d=m['contents'];bids=d['bids'];asks=d['asks'];seq=d['lastSequenceId'];snap=m['type']=='subscribed'
        else:
            d=m['feed'];bids=[[x['price'],x['size']] for x in d['bids']];asks=[[x['price'],x['size']] for x in d['asks']]
            seq=m['sequence_number'];prev=m.get('prev_sequence_number');snap=int(seq)==0
        r={'receive_ts_ns':t,'connection_id':'pilot','update_type':'snapshot' if snap else 'delta',
           'final_update_id':seq,'previous_update_id':prev,'sync_generation':1,'raw_payload':raw,
           'bids':[{'price':p,'quantity':q} for p,q,*_ in bids],
           'asks':[{'price':p,'quantity':q} for p,q,*_ in asks]}
        yield t,0,v,a,r

def trades(c):
    for t,v,a,source,side,qty in c.execute('SELECT wall_ns,venue,asset,source_ns,side,qty FROM public_trades ORDER BY rowid'):
        yield t,1,v,a,{'receive_ts_ns':t,'source_ts_ns':source,'aggressor_side':side,'quantity':qty}

def main():
    root=Path('runtime_data/fv_pilot/server_evidence/runtime_data');out=Path('runtime_data/fv_research_20261004');out.mkdir(exist_ok=True)
    audit={}
    for run in ('mainnet_training_verified_20261004','pilot_30min_20261004'):
        c=sqlite3.connect((root/run/'run.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
        result=[];s={};last=0;transport={};gaps={}
        for t,kind,v,a,r in heapq.merge(frames(c),trades(c),key=lambda x:x[:2]):
            key=(v,a);s.setdefault(key,Series(v,a,result.append))
            now=t/1e9
            if v in transport and now-transport[v]>3:gaps.setdefault(v,[]).append((transport[v]+3,now))
            transport[v]=now
            if kind==0:s[key].book(r)
            else:s[key].trade(r)
            last=max(last,t/1e9)
        for st in s.values():st.advance(last)
        for v,end in transport.items():
            if last-end>3:gaps.setdefault(v,[]).append((end+3,last))
        mask_transport(result,gaps)
        pq.write_table(pa.Table.from_pylist(result),out/(run+'_seconds.parquet'),compression='zstd')
        audit[run]={'seconds_rows':len(result),'transport_gaps':gaps,'series':{v+'_'+a:dict(st.counts) for (v,a),st in s.items()}}
        print(json.dumps({'run':run,**audit[run]}),flush=True)
    (out/'pilot_extract_audit.json').write_text(json.dumps(audit,indent=2))

if __name__=='__main__':main()
