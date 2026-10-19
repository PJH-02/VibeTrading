"""Receipt-time external merge of immutable archive rows into seconds."""
import json,sys,time,heapq,os
import pyarrow.compute as pc
from pathlib import Path
sys.path.insert(0,'/research/deps')
sys.path.insert(0,'/research')
import pyarrow as pa,pyarrow.parquet as pq
try:
    from extract import Series,sha
except ImportError:
    from fv_research_extract import Series,sha

def float_book_table(table):
    """Convert in Arrow rather than allocate millions of Decimal objects."""
    fields=[]
    for field in table.schema:
        if field.name in ('bids','asks'):
            value=field.type.value_type
            typ=pa.list_(pa.struct([pa.field(f.name,pa.float64() if f.name in ('price','quantity') else f.type) for f in value]))
            fields.append(pa.field(field.name,typ))
        elif field.name=='quantity':fields.append(pa.field(field.name,pa.float64()))
        else:fields.append(field)
    return table.cast(pa.schema(fields))

def main():
    root=Path(os.environ.get('FV_RESEARCH_ROOT','/research'));audit=json.loads((root/'reduce_audit.json').read_text()) if (root/'reduce_audit.json').exists() else {}
    for venue in (sys.argv[1:] or ('binance','bybit','bitget','arcus','grvt')):
        asset_filter=os.environ.get('FV_RESEARCH_ASSET')
        buffer=[];writer=None;transport_gaps=[];last_transport=None
        fixed_transport=json.loads((root/(venue+'_transport.json')).read_text()) if asset_filter else None
        if fixed_transport:transport_gaps=fixed_transport['gaps']
        ledger=json.loads((root/'collector_gaps.json').read_text()) if (root/'collector_gaps.json').exists() else []
        operational_gaps=[(r['started_ns']/1e9,(r['ended_ns'] or 10**20)/1e9) for r in ledger if r['provider']==venue+'-perp']
        target=root/(venue+('_'+asset_filter if asset_filter else '')+'_seconds.parquet')
        def emit(row):
            nonlocal writer,buffer
            if any(start<row['t'] and end>row['t']-1 for start,end in transport_gaps+operational_gaps):
                row['valid_seconds']=0.;row['bad']+=1
            buffer.append(row)
            if len(buffer)>=2048:flush()
        def flush():
            nonlocal writer,buffer
            if not buffer:return
            table=pa.Table.from_pylist(buffer)
            # Null-only initial buffers still have a fixed numerical schema.
            schema=pa.schema([(k,pa.string() if k in ('venue','asset') else pa.float64()) for k in buffer[0]])
            table=table.cast(schema)
            if writer is None:writer=pq.ParquetWriter(target,schema,compression='zstd')
            writer.write_table(table);buffer=[]
        series={};last=0;n=0
        # Sealed file receive bounds allow an exact bounded overlap merge.
        # No full-payload global sort or thousands of open readers are needed.
        indexed=[]
        index_path=root/(venue+'_file_index.json')
        if index_path.exists():
            indexed=[(ts,root/(venue+'_native')/name) for ts,name in json.loads(index_path.read_text())]
        else:
            for path in (root/(venue+'_native')).glob('*.parquet'):
                pf=pq.ParquetFile(path);col=pf.schema_arrow.names.index('receive_ts_ns')
                bounds=[pf.metadata.row_group(i).column(col).statistics.min for i in range(pf.metadata.num_row_groups)]
                indexed.append((min(bounds),path))
        indexed.sort();pos=0;active=[];serial=0
        def add_file(path):
            nonlocal serial
            pf=pq.ParquetFile(path);wanted=pf.schema_arrow.names
            if venue!='binance':wanted=[k for k in wanted if k!='raw_payload']
            table=pf.read(columns=wanted,use_threads=False)
            if asset_filter:table=table.filter(pc.equal(table['instrument_std_symbol'],asset_filter+'USDT'))
            rows=float_book_table(table).to_pylist();rows.sort(key=lambda r:(r['receive_ts_ns'],r['event_type']))
            it=iter(rows);r=next(it,None)
            if r is not None:heapq.heappush(active,(r['receive_ts_ns'],r['event_type'],serial,r,it));serial+=1
        while active or pos<len(indexed):
            while pos<len(indexed) and (not active or indexed[pos][0]<=active[0][0]):
                add_file(indexed[pos][1]);pos+=1
            _,_,sid,r,it=heapq.heappop(active)
            nxt=next(it,None)
            if nxt is not None:heapq.heappush(active,(nxt['receive_ts_ns'],nxt['event_type'],sid,nxt,it))
            if r is not None:
                wall=r['receive_ts_ns'];asset=r['instrument_std_symbol'].replace('USDT','');kind=r['event_type']
                if not fixed_transport and last_transport is not None and wall/1e9-last_transport>3:transport_gaps.append((last_transport+3,wall/1e9))
                last_transport=wall/1e9
                st=series.setdefault(asset,Series(venue,asset,emit))
                if kind=='order_book':st.book(r)
                else:st.trade(r)
                last=max(last,wall/1e9);n+=1
            if n%100000==0:
                (root/('reduce_progress_'+venue+'_'+asset_filter+'.json' if asset_filter else 'reduce_progress.json')).write_text(json.dumps({'venue':venue,'asset':asset_filter,'rows':n,'t':last}))
        for st in series.values():st.advance(fixed_transport['end_ns']/1e9 if fixed_transport else last)
        flush()
        if writer:writer.close()
        audit[venue]={'rows':n,'transport_gaps':transport_gaps,'operational_gap_count':len(operational_gaps),'series':{a:dict(st.counts) for a,st in series.items()},'seconds_sha256':sha(target)}
        (root/('reduce_audit_'+venue+'_'+asset_filter+'.json' if asset_filter else 'reduce_audit.json')).write_text(json.dumps(audit,indent=2))
        print(json.dumps({venue:audit[venue]}),flush=True)

if __name__=='__main__':main()
