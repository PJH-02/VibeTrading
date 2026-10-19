"""Stream every sealed active crypto archive into causal one-second statistics.

Run in an isolated, resource-limited image. Source objects are read-only and
checksum verified. Only derived statistics and an audit are written.
"""
from __future__ import annotations
import concurrent.futures as cf
import bisect
from collections import Counter
import hashlib, json, math, os, tempfile, time
import sys
sys.path.insert(0,'/app')
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

STATE_NAMES=['mid','spread_bps','depth','obi_l1','obi_l2_10','obi_visible','microprice_bps']
FLOW_NAMES=['ofi','bid_add','ask_add','bid_remove','ask_remove','trade_buy','trade_sell','trade_unknown']

class PriceBook(dict):
    """Exact sorted prices with incremental total; no depth truncation."""
    def __init__(self):super().__init__();self.prices=[];self.total=0.
    def __setitem__(self,p,q):
        old=self.get(p,0.)
        if p not in self:bisect.insort(self.prices,p)
        self.total+=q-old;super().__setitem__(p,q)
    def pop(self,p,default=None):
        if p not in self:return default
        old=super().pop(p);self.total-=old;self.prices.pop(bisect.bisect_left(self.prices,p));return old
    def clear(self):super().clear();self.prices=[];self.total=0.
    def top(self,rev):
        prices=self.prices[-10:][::-1] if rev else self.prices[:10]
        return {p:self[p] for p in prices}
    def apply(self,levels):
        pairs=[(float(x['price']),float(x['quantity'])) for x in levels]
        if len(pairs)>max(32,len(self)//10):
            # Bulk C-level insertion + sorting is cheaper for broad updates;
            # exact same visible prices/quantities as incremental insertion.
            dict.update(self,((p,q) for p,q in pairs if q>0))
            for p,q in pairs:
                if q<=0:dict.pop(self,p,None)
            self.prices=sorted(self);self.total=sum(self.values())
        else:
            for p,q in pairs:
                if q>0:self[p]=q
                else:self.pop(p,None)

def bbo_features(bids,asks):
    b=bids.prices[-10:][::-1] if isinstance(bids,PriceBook) else sorted(bids,reverse=True)
    a=asks.prices[:10] if isinstance(asks,PriceBook) else sorted(asks)
    if not b or not a or b[0]>=a[0]: return None
    qb,qa=bids[b[0]],asks[a[0]]; mid=(b[0]+a[0])/2
    if qb+qa<=0 or len(b)<10 or len(a)<10: return None
    b10=sum(bids[p] for p in b[:10]); a10=sum(asks[p] for p in a[:10])
    bt=bids.total if isinstance(bids,PriceBook) else sum(bids.values())
    at=asks.total if isinstance(asks,PriceBook) else sum(asks.values())
    obi=(qb-qa)/(qb+qa)
    return [mid,(a[0]-b[0])/mid*1e4,(qb+qa)/2,obi,
            (b10-a10)/(b10+a10),(bt-at)/(bt+at),
            ((a[0]*qb+b[0]*qa)/(qb+qa)-mid)/mid*1e4]

def ofi_value(old,new):
    if old is None:return 0.
    b,a,qb,qa=old; nb,na,nqb,nqa=new
    return (nqb if nb>=b else 0)-(qb if nb<=b else 0)-(nqa if na<=a else 0)+(qa if na>=a else 0)

class Series:
    def __init__(self,venue,asset,emit):
        self.venue,self.asset,self.emit=venue,asset,emit
        self.bids=PriceBook();self.asks=PriceBook();self.last=None;self.conn=None;self.seq=None;self.first=False
        self.bucket=None;self.cursor=None;self.state=None;self.bbo=None;self.last_book=0
        self.integral=[0.]*7;self.flows=[0.]*8;self.valid_seconds=0.;self.bad=0
        self.counts=Counter();self.segment=0
    def advance(self,t):
        if self.cursor is None:self.cursor=t;self.bucket=math.floor(t)
        while self.cursor<t:
            end=min(t,self.bucket+1);dt=end-self.cursor
            # Quiet per-asset books are not missing data. Transport silence is
            # filtered across the shared venue connection by each wrapper.
            live=self.state is not None
            if dt<=0:live=False;end=min(t,self.bucket+1);dt=end-self.cursor
            if live:
                self.valid_seconds+=dt
                for i,x in enumerate(self.state):self.integral[i]+=x*dt
            self.cursor=end
            if self.cursor>=self.bucket+1-1e-7:
                row={'venue':self.venue,'asset':self.asset,'t':self.bucket+1,
                     'valid_seconds':self.valid_seconds,'bad':self.bad,'segment':self.segment,
                     'mid':self.state[0] if live and self.state else None}
                for i,n in enumerate(STATE_NAMES):row['avg_'+n]=self.integral[i]/self.valid_seconds if self.valid_seconds else None
                row.update(zip(FLOW_NAMES,self.flows))
                self.emit(row);self.bucket+=1;self.integral=[0.]*7;self.flows=[0.]*8;self.valid_seconds=0.;self.bad=0
    def book(self,r):
        t=r['receive_ts_ns']/1e9
        if self.cursor is not None and t<self.cursor-1e-7:
            self.counts['out_of_order']+=1;self.state=None;self.bad+=1;return
        self.advance(t);self.counts['book_rows']+=1
        seq=int(r.get('final_update_id') or 0);snapshot=r['update_type']=='snapshot'
        conn=r.get('connection_id');raw=json.loads(r.get('raw_payload') or '{}') if self.venue=='binance' or (self.venue=='bitget' and not snapshot) else {}
        # Producers preserve pre-snapshot/stale/boundary frames for audit with
        # sync_generation=0. Nonadvancing frames on the existing connection
        # are not a newly missing delta and must not erase the synced book.
        if not snapshot and self.seq is not None and conn==self.conn and seq<=self.seq and not r.get('sync_generation'):
            self.counts['stale_nonadvancing_ignored']+=1;return
        prev=r.get('previous_update_id')
        if self.venue=='binance':prev=(raw.get('data') or raw).get('pu',prev)
        if self.venue=='bitget':
            d=raw.get('data') or []
            if d:prev=d[0].get('pseq',prev)
        continuous_snapshot=snapshot and self.venue=='bitget' and conn==self.conn and self.seq is not None and seq>self.seq
        reset=snapshot and not continuous_snapshot
        gap=False
        if not snapshot:
            gap=self.seq is None or conn!=self.conn or not r.get('sync_generation')
            if self.seq is not None:
                if seq<=self.seq:gap=True
                if self.venue in ('arcus','grvt') and not self.first and seq!=self.seq+1:gap=True
                if prev is not None and int(prev)!=self.seq and not self.first:gap=True
                if self.first and self.venue=='binance':
                    wire=raw.get('data') or raw
                    if not int(wire.get('U',seq+1))<=self.seq<=seq:gap=True
                if self.first and self.venue=='grvt' and self.seq>0 and prev is not None and int(prev)!=self.seq:gap=True
                if self.first and self.venue=='bitget' and prev is not None and not int(prev)<=self.seq<seq:gap=True
        if gap:
            self.counts['invalid_delta']+=1;self.state=None;self.bbo=None;self.bad+=1
            self.seq=None;return
        oldb,olda=self.bids.top(True),self.asks.top(False)
        if snapshot:self.bids.clear();self.asks.clear()
        for book,levels in ((self.bids,r['bids']),(self.asks,r['asks'])):
            book.apply(levels)
        self.seq=seq;self.conn=conn;self.first=reset
        if not snapshot:self.first=False
        self.last_book=t
        if reset:
            self.segment+=1;self.bad+=1;self.bbo=None;self.counts['resets']+=1
        self.state=bbo_features(self.bids,self.asks)
        if self.state is None:
            self.bad+=1;self.counts['invalid_book']+=1;self.bbo=None
            if len(self.bids)<10 or len(self.asks)<10:self.counts['insufficient_ten_levels']+=1
            elif self.bids.prices[-1]>=self.asks.prices[0]:self.counts['crossed_or_locked_book']+=1
            return
        b=self.bids.prices[-1];a=self.asks.prices[0];new=(b,a,self.bids[b],self.asks[a])
        if not reset:
            self.flows[0]+=ofi_value(self.bbo,new)
            # Shared top-ten prices only: avoids rank-entry artifacts and does
            # not relabel disappeared levels as cancellations or executions.
            for old,book,add_i,remove_i,rev in ((oldb,self.bids,1,3,True),(olda,self.asks,2,4,False)):
                shared=set(old)&set(book.top(rev))
                for p in shared:
                    diff=book[p]-old[p]
                    self.flows[add_i]+=max(diff,0);self.flows[remove_i]+=max(-diff,0)
        self.bbo=new
    def trade(self,r):
        t=r['receive_ts_ns']/1e9
        if self.cursor is not None and t<self.cursor-1e-7:
            self.counts['out_of_order_trade']+=1;return
        self.advance(t);self.counts['trade_rows']+=1
        source=r.get('source_ts_ns') or 0
        if source and t-source/1e9>10:self.counts['historical_trade_excluded']+=1;return
        i={'buy':5,'sell':6}.get(str(r['aggressor_side']).lower(),7)
        self.flows[i]+=float(r['quantity'])

def mask_transport(rows,gaps):
    """Exclude each second intersecting >3s silence on the venue connection."""
    for r in rows:
        if any(start<r['t'] and end>r['t']-1 for start,end in gaps.get(r['venue'],())):
            r['valid_seconds']=0.;r['bad']+=1

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def main():
    os.environ['ARCHIVE_WORKER_ENABLED']='true'
    os.environ['ARCHIVE_ACCESS_KEY_FILE']='/run/secrets/archive_access_key'
    os.environ['ARCHIVE_SECRET_KEY_FILE']='/run/secrets/archive_secret_key'
    from vibetrading.settings import get_settings
    from vibetrading.storage_backend import archive_backend
    root=Path('/research');catalogue=json.loads((root/'catalogue.json').read_text())
    backend=archive_backend(get_settings());audit={'files':[],'series':{},'started':time.time(),'catalogue_sha256':sha(root/'catalogue.json')}
    columns={'order_book':['receive_ts_ns','instrument_std_symbol','connection_id','update_type','final_update_id','previous_update_id','sync_generation','raw_payload','bids','asks'],
             'trade':['receive_ts_ns','source_ts_ns','instrument_std_symbol','aggressor_side','quantity']}
    # Download once into derived workspace; remove only that verified temporary
    # copy after use. Existing local objects are never removed or modified.
    def load(meta):
        p=Path(meta['path']);downloaded=False
        if not p.is_file():
            if meta.get('upload_status')!='remote_verified':raise RuntimeError('Unavailable active object '+meta['file_id'])
            p=root/'temp'/(meta['file_id']+'.parquet');backend.download_file(meta['remote_key'],p);downloaded=True
        checksum=meta.get('file_sha256') or meta.get('content_sha256') or meta.get('checksum')
        if p.stat().st_size!=meta['byte_size'] or sha(p)!=checksum:raise RuntimeError('Checksum mismatch '+meta['file_id'])
        return meta,p,downloaded
    (root/'temp').mkdir(exist_ok=True)
    for venue in ('binance','bybit','bitget','arcus','grvt'):
        selected=[m for m in catalogue if m['venue']==venue and m['event_type'] in columns]
        selected.sort(key=lambda m:(m['min_ts_ns'],m['file_id']))
        # Files of book/trades overlap in time. Persist compact normalized rows
        # first, then DuckDB performs a disk-spilling receipt-time merge later.
        norm=root/(venue+'_native');norm.mkdir(exist_ok=True)
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            pending={};index=0
            while index<len(selected) or pending:
                while index<len(selected) and len(pending)<8:
                    pending[index]=pool.submit(load,selected[index]);index+=1
                key=min(pending);meta,p,downloaded=pending.pop(key).result()
                wanted=columns[meta['event_type']];pf=pq.ParquetFile(p)
                target=norm/(meta['file_id']+'.parquet')
                writer=None
                for batch in pf.iter_batches(batch_size=512,columns=wanted):
                    table=pa.Table.from_batches([batch])
                    table=table.append_column('event_type',pa.array([meta['event_type']]*batch.num_rows))
                    if writer is None:writer=pq.ParquetWriter(target,table.schema,compression='zstd')
                    writer.write_table(table)
                if writer:writer.close()
                audit['files'].append({'file_id':meta['file_id'],'sha256':sha(p),'rows':pf.metadata.num_rows,'source':'remote' if downloaded else 'local'})
                if downloaded:p.unlink()
                if len(audit['files'])%200==0:
                    (root/'progress.json').write_text(json.dumps({'done':len(audit['files']),'total':len(catalogue),'venue':venue,'elapsed':time.time()-audit['started']}))
        print(json.dumps({'venue_normalized':venue,'files':len(selected)}),flush=True)
    audit['finished']=time.time();(root/'archive_audit.json').write_text(json.dumps(audit))

if __name__=='__main__':main()
