from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import EventType
from .settings import RuntimeSettings, get_settings
from .sqlite_store import RuntimeStore


def _require_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        return pa, pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for Parquet storage") from exc


def _fsync_dir(path: Path) -> None:
    try:
        fd=os.open(path,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    except OSError: pass


def file_sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()


def _canon(row: dict[str,Any]) -> bytes:
    return json.dumps(row,sort_keys=True,separators=(",",":"),ensure_ascii=False,default=str).encode()


def logical_hash_rows(rows: list[dict[str,Any]]) -> str:
    h=hashlib.sha256()
    for row in rows: h.update(_canon(row)); h.update(b"\n")
    return h.hexdigest()


def flatten_event(event: BaseModel | dict[str,Any], *, stream: str="", sequence: int|None=None) -> dict[str,Any]:
    d=event.model_dump(mode="json") if isinstance(event,BaseModel) else dict(event)
    instrument=d.pop("instrument",{}) or {}
    for k,v in instrument.items(): d[f"instrument_{k}"]=v
    d["nats_stream"]=stream or None; d["nats_stream_sequence"]=sequence
    return d


def _schema(event_type: str) -> Any:
    pa,_=_require_pyarrow(); dec=pa.decimal128(38,18); level=pa.struct([pa.field("price",dec),pa.field("quantity",dec)])
    common=[pa.field("schema_version",pa.int16()),pa.field("event_id",pa.string()),pa.field("event_type",pa.dictionary(pa.int32(),pa.string())),pa.field("source_ts_ns",pa.int64()),pa.field("receive_ts_ns",pa.int64()),pa.field("sampled_at_ns",pa.int64()),pa.field("trading_date",pa.string()),pa.field("session",pa.dictionary(pa.int32(),pa.string())),pa.field("venue",pa.dictionary(pa.int32(),pa.string())),pa.field("provider",pa.dictionary(pa.int32(),pa.string())),pa.field("sequence",pa.string()),pa.field("nats_stream",pa.string()),pa.field("nats_stream_sequence",pa.int64()),pa.field("instrument_venue",pa.string()),pa.field("instrument_asset_class",pa.string()),pa.field("instrument_raw_symbol",pa.string()),pa.field("instrument_std_symbol",pa.string()),pa.field("instrument_contract_month",pa.string()),pa.field("instrument_expiry",pa.string()),pa.field("instrument_strike",dec),pa.field("instrument_option_right",pa.string()),pa.field("instrument_underlying",pa.string()),pa.field("instrument_currency",pa.string()),pa.field("instrument_exchange",pa.string())]
    if event_type==EventType.TRADE.value: return pa.schema(common+[pa.field("price",dec),pa.field("quantity",dec),pa.field("aggressor_side",pa.dictionary(pa.int32(),pa.string())),pa.field("trade_id",pa.string())])
    return pa.schema(common+[pa.field("update_type",pa.dictionary(pa.int32(),pa.string())),pa.field("depth",pa.int32()),pa.field("is_full_depth",pa.bool_()),pa.field("bids",pa.list_(level)),pa.field("asks",pa.list_(level))])


def _normalize(row: dict[str,Any], event_type: str) -> dict[str,Any]:
    out={name:None for name in _schema(event_type).names}; out.update({k:v for k,v in row.items() if k in out})
    for key in ("price","quantity","instrument_strike"):
        if out.get(key) is not None: out[key]=Decimal(str(out[key]))
    for side in ("bids","asks"):
        if side in out and out.get(side) is not None:
            out[side]=[{"price":Decimal(str(x["price"])),"quantity":Decimal(str(x["quantity"]))} for x in out[side]]
    if out.get("sequence") is not None: out["sequence"]=str(out["sequence"])
    return out


class ParquetBatchWriter:
    def __init__(self, settings: RuntimeSettings|None=None, store: RuntimeStore|None=None) -> None:
        self.settings=settings or get_settings(); self.store=store or RuntimeStore(self.settings); self.root=self.settings.parquet.data_root; self.root.mkdir(parents=True,exist_ok=True)
        self._rows: dict[tuple[str,str,str,str],list[dict[str,Any]]]=defaultdict(list); self._deliveries: dict[tuple[str,str,str,str],list[tuple[str,str,int,str]]]=defaultdict(list); self._first: dict[tuple[str,str,str,str],int]={}; self.last_committed_ids:set[str]=set(); self.reconcile_startup()

    @property
    def buffer_bytes(self)->int:
        pa,_=_require_pyarrow(); total=0
        for key,rows in self._rows.items():
            if rows: total+=pa.Table.from_pylist(rows,schema=_schema(key[0])).nbytes
        return total

    def add(self,event:BaseModel|dict[str,Any], *, stream_name:str="", stream_generation:str="", stream_sequence:int|None=None, event_id:str="", commit_id:str="") -> bool:
        row=flatten_event(event,stream=stream_name,sequence=stream_sequence); typ=str(row.get("event_type") or "")
        if typ not in {EventType.TRADE.value,EventType.ORDER_BOOK.value}: return False
        key=(typ,str(row.get("trading_date") or "unknown"),str(row.get("provider") or "unknown"),str(row.get("venue") or "unknown")); self._rows[key].append(_normalize(row,typ)); self._first.setdefault(key,time.time_ns())
        if stream_name and stream_generation and stream_sequence is not None: self._deliveries[key].append((stream_name,stream_generation,int(stream_sequence),event_id or str(row.get("event_id") or "")))
        return True

    def should_flush(self)->bool:
        now=time.time_ns(); age=int(self.settings.parquet.flush_seconds*1e9)
        return any(now-self._first.get(k,now)>=age for k,r in self._rows.items() if r) or self.buffer_bytes>=self.settings.parquet.max_buffer_bytes or any(self._partition_bytes(k)>=self.settings.parquet.flush_bytes for k in self._rows)

    def _partition_bytes(self,key:tuple[str,str,str,str])->int:
        pa,_=_require_pyarrow(); rows=self._rows[key]; return pa.Table.from_pylist(rows,schema=_schema(key[0])).nbytes if rows else 0

    def flush_due(self)->list[Path]:
        now=time.time_ns(); age=int(self.settings.parquet.flush_seconds*1e9); keys=[k for k,r in self._rows.items() if r and (now-self._first.get(k,now)>=age or self._partition_bytes(k)>=self.settings.parquet.flush_bytes or self.buffer_bytes>=self.settings.parquet.max_buffer_bytes)]
        return self._flush(keys)
    def flush_all(self)->list[Path]: return self._flush([k for k,r in self._rows.items() if r])
    def _flush(self,keys:list[tuple[str,str,str,str]])->list[Path]:
        self.last_committed_ids=set(); out=[]
        for key in keys:
            rows=self._rows[key]; deliveries=self._deliveries[key]
            if not rows: continue
            path=self._write(key,rows,deliveries); out.append(path); self.last_committed_ids.update(f"{a}:{b}:{c}" for a,b,c,_ in deliveries); self._rows[key]=[]; self._deliveries[key]=[]; self._first.pop(key,None)
        return out
    def _write(self,key:tuple[str,str,str,str],rows:list[dict[str,Any]],deliveries:list[tuple[str,str,int,str]])->Path:
        pa,pq=_require_pyarrow(); typ,date,provider,venue=key; part=self.root/f"event_type={typ}"/f"trading_date={date}"/f"provider={provider}"/f"venue={venue}"; part.mkdir(parents=True,exist_ok=True); fid=uuid.uuid4().hex; final=part/f"part-{fid}.parquet"; tmp=final.with_suffix(".parquet.tmp")
        schema=_schema(typ); writer=pq.ParquetWriter(tmp,schema,compression="zstd",compression_level=self.settings.parquet.compression_level,use_dictionary=True)
        try:
            # bounded row groups; the buffer itself is capped at 128MiB.
            for start in range(0,len(rows),4096): writer.write_table(pa.Table.from_pylist(rows[start:start+4096],schema=schema))
        finally: writer.close()
        with tmp.open("rb+") as f: f.flush(); os.fsync(f.fileno())
        os.replace(tmp,final); _fsync_dir(part); minimum=min(int(x.get("source_ts_ns") or 0) for x in rows); maximum=max(int(x.get("source_ts_ns") or 0) for x in rows)
        self.store.commit_file(file_id=fid,event_type=typ,trading_date=date,provider=provider,venue=venue,path=final,rows=len(rows),min_ts_ns=minimum,max_ts_ns=maximum,logical_sha256=logical_hash_rows(rows),file_sha256=file_sha256(final),byte_size=final.stat().st_size,deliveries=deliveries)
        return final
    def reconcile_startup(self)->None:
        for tmp in self.root.glob("**/*.parquet.tmp"): tmp.unlink(missing_ok=True)
        for path in self.root.glob("**/*.parquet"):
            if self.store.local_manifest(path) is None: path.rename(path.with_suffix(path.suffix+".orphan"))


class ParquetHistoryReader:
    def __init__(self,data_root:Path,store:RuntimeStore|None=None,settings:RuntimeSettings|None=None)->None: self.settings=settings or get_settings(); self.store=store or RuntimeStore(self.settings); self.data_root=data_root
    def history(self,*,instrument_key:str,event_type:str,limit:int=1000)->list[dict[str,Any]]:
        _,pq=_require_pyarrow(); rows=[]
        for meta in self.store.list_manifest(active_only=True):
            if meta['event_type']!=event_type or not meta['local_present']: continue
            path=Path(meta['path'])
            if not path.exists(): continue
            for batch in pq.ParquetFile(path).iter_batches(batch_size=4096):
                for row in batch.to_pylist():
                    key=':'.join(str(row.get(n) or '') for n in ('instrument_venue','instrument_asset_class','instrument_std_symbol'))
                    if key==instrument_key: rows.append(row)
        return sorted(rows,key=lambda x:int(x.get('source_ts_ns') or 0))[-limit:]


def market_data_subject_for_type(event_type:EventType|str)->str: return f"md.{event_type.value if isinstance(event_type,EventType) else event_type}.>"
