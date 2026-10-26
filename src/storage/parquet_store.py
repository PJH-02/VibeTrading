from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from core.models import EventType
from vibetrading.settings import RuntimeSettings, get_settings
from .sqlite_store import RuntimeStore


def _require_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        return pa, pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for Parquet storage") from exc


def _fsync_dir(path: Path) -> None:
    # Windows does not expose the Linux directory-fsync contract used by the
    # production ext4 volume. Keep local tests portable; on production systems
    # every open/fsync error is fatal and prevents manifest commit and ACK.
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def file_sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()


def _catalogued_file_sha256(path: Path, manifest: dict[str, Any]) -> str:
    """Check an existing receipt before trusting a local immutable source.

    Older compatibility manifests may have no file digest. A nonempty but
    malformed digest is an integrity error, never an absent legacy receipt.
    """
    expected = manifest.get("file_sha256")
    if expected not in (None, ""):
        if (not isinstance(expected, str) or len(expected) != 64
                or any(char not in "0123456789abcdefABCDEF" for char in expected)):
            raise RuntimeError(f"invalid catalogued file checksum file_id={manifest.get('file_id')}")
    actual = file_sha256(path)
    if expected and actual != expected.lower():
        raise RuntimeError(f"local source checksum mismatch file_id={manifest.get('file_id')}")
    return actual


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


@lru_cache(maxsize=8)
def _schema(event_type: str) -> Any:
    pa,_=_require_pyarrow(); dec=pa.decimal128(38,18); level=pa.struct([pa.field("price",dec),pa.field("quantity",dec),pa.field("order_count",pa.int32())])
    common=[pa.field("schema_version",pa.int16()),pa.field("event_id",pa.string()),pa.field("event_type",pa.dictionary(pa.int32(),pa.string())),pa.field("source_ts_ns",pa.int64()),pa.field("source_timestamp_kind",pa.dictionary(pa.int32(),pa.string())),pa.field("observed_delay_ms",pa.int64()),pa.field("wallclock_ts_ns",pa.int64()),pa.field("receive_ts_ns",pa.int64()),pa.field("receive_monotonic_ns",pa.int64()),pa.field("collector_run_id",pa.string()),pa.field("connection_id",pa.string()),pa.field("receive_sequence",pa.int64()),pa.field("record_index",pa.int32()),pa.field("native_event_id",pa.string()),pa.field("collection_start_ts_ns",pa.int64()),pa.field("ingest_mode",pa.dictionary(pa.int32(),pa.string())),pa.field("sampled_at_ns",pa.int64()),pa.field("trading_date",pa.string()),pa.field("session",pa.dictionary(pa.int32(),pa.string())),pa.field("venue",pa.dictionary(pa.int32(),pa.string())),pa.field("provider",pa.dictionary(pa.int32(),pa.string())),pa.field("sequence",pa.string()),pa.field("nats_stream",pa.string()),pa.field("nats_stream_sequence",pa.int64()),pa.field("instrument_venue",pa.string()),pa.field("instrument_asset_class",pa.string()),pa.field("instrument_raw_symbol",pa.string()),pa.field("instrument_std_symbol",pa.string()),pa.field("instrument_contract_month",pa.string()),pa.field("instrument_expiry",pa.string()),pa.field("instrument_strike",dec),pa.field("instrument_option_right",pa.string()),pa.field("instrument_underlying",pa.string()),pa.field("instrument_security_kind",pa.string()),pa.field("instrument_currency",pa.string()),pa.field("instrument_exchange",pa.string())]
    if event_type==EventType.TRADE.value: return pa.schema(common+[pa.field("price",dec),pa.field("quantity",dec),pa.field("aggressor_side",pa.dictionary(pa.int32(),pa.string())),pa.field("trade_id",pa.string())])
    if event_type==EventType.ORDER_BOOK.value: return pa.schema(common+[pa.field("update_type",pa.dictionary(pa.int32(),pa.string())),pa.field("depth",pa.int32()),pa.field("is_full_depth",pa.bool_()),pa.field("channel",pa.string()),pa.field("provider_depth",pa.int32()),pa.field("first_update_id",pa.int64()),pa.field("final_update_id",pa.int64()),pa.field("previous_update_id",pa.int64()),pa.field("sync_generation",pa.int32()),pa.field("fast",pa.bool_()),pa.field("raw_payload",pa.string()),pa.field("bids",pa.list_(level)),pa.field("asks",pa.list_(level))])
    if event_type==EventType.BAR_1M.value: return pa.schema(common+[pa.field("interval",pa.dictionary(pa.int32(),pa.string())),pa.field("open_ts_ns",pa.int64()),pa.field("close_ts_ns",pa.int64()),pa.field("open",dec),pa.field("high",dec),pa.field("low",dec),pa.field("close",dec),pa.field("volume",dec),pa.field("trade_count",pa.int64()),pa.field("closed",pa.bool_())])
    if event_type==EventType.ETF_NAV.value: return pa.schema(common+[pa.field("nav",dec),pa.field("nav_change",dec),pa.field("nav_change_pct",dec),pa.field("index_disparity_pct",dec),pa.field("etf_disparity_pct",dec),pa.field("raw_payload",pa.string())])
    if event_type==EventType.FUNDING_RATE.value: return pa.schema(common+[pa.field("funding_rate",dec),pa.field("premium",dec),pa.field("funding_interval_hours",pa.int16())])
    if event_type==EventType.OPEN_INTEREST.value: return pa.schema(common+[pa.field("open_interest",dec),pa.field("open_interest_unit",pa.string()),pa.field("open_interest_value",dec),pa.field("value_currency",pa.string()),pa.field("mark_price",dec),pa.field("observation_method",pa.string())])
    if event_type==EventType.PARTICIPANT_FLOW.value: return pa.schema(common+[pa.field("market_code",pa.string()),pa.field("market_subcode",pa.string()),pa.field("participant",pa.dictionary(pa.int32(),pa.string())),pa.field("participant_label",pa.string()),pa.field("buy_volume",dec),pa.field("sell_volume",dec),pa.field("net_volume",dec),pa.field("buy_value",dec),pa.field("sell_value",dec),pa.field("net_value",dec),pa.field("quantity_unit",pa.string()),pa.field("value_currency",pa.string()),pa.field("value_unit",pa.string()),pa.field("observation_method",pa.string()),pa.field("raw_payload",pa.string())])
    if event_type==EventType.INDEX_QUOTE.value: return pa.schema(common+[pa.field("value",dec),pa.field("unit",pa.string()),pa.field("currency",pa.string()),pa.field("open",dec),pa.field("high",dec),pa.field("low",dec),pa.field("change",dec),pa.field("change_pct",dec),pa.field("metadata",pa.string())])
    raise ValueError(f"Unsupported parquet event_type={event_type!r}")


@lru_cache(maxsize=8)
def _schema_names(event_type: str) -> tuple[str, ...]:
    # Arrow schemas are immutable, but schema.names creates a fresh list.
    # Cache only successful supported-type lookups and share immutable names.
    return tuple(_schema(event_type).names)


def _normalize(row: dict[str,Any], event_type: str) -> dict[str,Any]:
    out={name:None for name in _schema_names(event_type)}; out.update({k:v for k,v in row.items() if k in out})
    for key in ("price","quantity","instrument_strike","open","high","low","close","volume","nav","nav_change","nav_change_pct","index_disparity_pct","etf_disparity_pct","funding_rate","premium","open_interest","open_interest_value","mark_price","buy_volume","sell_volume","net_volume","buy_value","sell_value","net_value","value","change","change_pct"):
        if out.get(key) is not None: out[key]=Decimal(str(out[key]))
    if isinstance(out.get("metadata"),dict): out["metadata"]=json.dumps(out["metadata"],sort_keys=True)
    for side in ("bids","asks"):
        if side in out and out.get(side) is not None:
            out[side]=[{"price":Decimal(str(x["price"])),"quantity":Decimal(str(x["quantity"])),"order_count":int(x["order_count"]) if x.get("order_count") is not None else None} for x in out[side]]
    if out.get("sequence") is not None: out["sequence"]=str(out["sequence"])
    return out


@dataclass
class _PartitionBuffer:
    rows: list[dict[str, Any]] = field(default_factory=list)
    batches: list[Any] = field(default_factory=list)
    deliveries: list[tuple[str, str, int, str]] = field(default_factory=list)
    first_ts_ns: int = field(default_factory=time.time_ns)
    arrow_bytes: int = 0
    staged_bytes: int = 0
    row_count: int = 0
    min_ts_ns: int | None = None
    max_ts_ns: int | None = None
    logical_hasher: Any = field(default_factory=hashlib.sha256)


class ParquetBatchWriter:
    def __init__(self, settings: RuntimeSettings|None=None, store: RuntimeStore|None=None) -> None:
        self.settings=settings or get_settings(); self.store=store or RuntimeStore(self.settings); self.root=self.settings.parquet.data_root; self.root.mkdir(parents=True,exist_ok=True)
        self._buffers: dict[tuple[str,str,str,str],_PartitionBuffer]=defaultdict(_PartitionBuffer); self.last_committed_ids:set[str]=set(); self._pending_event_partitions:dict[str,tuple[str,str,str,str]]={}; self.reconcile_startup()
        self._batch_rows=max(64,int(self.settings.env.get("PARQUET_ARROW_BATCH_ROWS","512")))

    @property
    def buffer_bytes(self)->int:
        return sum(buf.arrow_bytes+buf.staged_bytes for buf in self._buffers.values())

    def add(self,event:BaseModel|dict[str,Any], *, stream_name:str="", stream_generation:str="", stream_sequence:int|None=None, event_id:str="", commit_id:str="") -> bool:
        row=flatten_event(event,stream=stream_name,sequence=stream_sequence); typ=str(row.get("event_type") or "")
        if typ not in {EventType.TRADE.value,EventType.ORDER_BOOK.value,EventType.BAR_1M.value,EventType.ETF_NAV.value,EventType.FUNDING_RATE.value,EventType.OPEN_INTEREST.value,EventType.PARTICIPANT_FLOW.value,EventType.INDEX_QUOTE.value}: return False
        key=(typ,str(row.get("trading_date") or "unknown"),str(row.get("provider") or "unknown"),str(row.get("venue") or "unknown")); normalized=_normalize(row,typ); buf=self._buffers[key]; buf.rows.append(normalized); buf.row_count+=1
        canonical=_canon(normalized); buf.logical_hasher.update(canonical); buf.logical_hasher.update(b"\n"); buf.staged_bytes+=len(canonical)
        timestamp=int(normalized.get("source_ts_ns") or normalized.get("wallclock_ts_ns") or 0); buf.min_ts_ns=timestamp if buf.min_ts_ns is None else min(buf.min_ts_ns,timestamp); buf.max_ts_ns=timestamp if buf.max_ts_ns is None else max(buf.max_ts_ns,timestamp)
        if stream_name and stream_generation and stream_sequence is not None: buf.deliveries.append((stream_name,stream_generation,int(stream_sequence),event_id or str(row.get("event_id") or "")))
        effective_event_id=event_id or str(row.get("event_id") or "")
        if effective_event_id:self._pending_event_partitions[effective_event_id]=key
        if len(buf.rows)>=self._batch_rows: self._materialize(key)
        return True

    def add_pending_delivery_alias(self,event_id:str,*,stream_name:str,stream_generation:str,
                                   stream_sequence:int,event_key:str="")->bool:
        """Attach a duplicate delivery to its not-yet-committed canonical row."""
        key=self._pending_event_partitions.get(event_id)
        if key is None:return False
        buf=self._buffers.get(key)
        if buf is None or not buf.row_count:return False
        buf.deliveries.append((stream_name,stream_generation,int(stream_sequence),event_key or event_id))
        return True

    def should_flush(self)->bool:
        now=time.time_ns(); age=int(self.settings.parquet.flush_seconds*1e9)
        return any(now-b.first_ts_ns>=age for b in self._buffers.values() if b.row_count) or self.buffer_bytes>=self.settings.parquet.max_buffer_bytes or any(self._partition_bytes(k)>=self.settings.parquet.flush_bytes for k in self._buffers)

    def _partition_bytes(self,key:tuple[str,str,str,str])->int:
        buf=self._buffers[key]; return buf.arrow_bytes+buf.staged_bytes

    def _materialize(self,key:tuple[str,str,str,str])->None:
        buf=self._buffers[key]
        if not buf.rows:return
        pa,_=_require_pyarrow(); batch=pa.RecordBatch.from_pylist(buf.rows,schema=_schema(key[0])); buf.batches.append(batch); buf.arrow_bytes+=batch.nbytes; buf.rows=[]; buf.staged_bytes=0

    def flush_due(self)->list[Path]:
        now=time.time_ns(); age=int(self.settings.parquet.flush_seconds*1e9); total=self.buffer_bytes; keys=[k for k,b in self._buffers.items() if b.row_count and (now-b.first_ts_ns>=age or self._partition_bytes(k)>=self.settings.parquet.flush_bytes or total>=self.settings.parquet.max_buffer_bytes)]
        return self._flush(keys)
    def flush_aged(self,max_age_seconds:float)->list[Path]:
        cutoff=time.time_ns()-int(max(0.1,max_age_seconds)*1e9)
        keys=[key for key,buf in self._buffers.items() if buf.row_count and buf.first_ts_ns<=cutoff]
        return self._flush(keys)
    def flush_largest(self,target_buffer_bytes:int)->list[Path]:
        remaining=self.buffer_bytes;keys=[]
        for key,buf in sorted(self._buffers.items(),key=lambda item:self._partition_bytes(item[0]),reverse=True):
            if remaining<=max(0,int(target_buffer_bytes)):break
            if not buf.row_count:continue
            keys.append(key);remaining-=self._partition_bytes(key)
        return self._flush(keys)
    def flush_all(self)->list[Path]: return self._flush([k for k,b in self._buffers.items() if b.row_count])
    def _flush(self,keys:list[tuple[str,str,str,str]])->list[Path]:
        self.last_committed_ids=set(); out=[]; manifests=[]; touched_dirs:set[Path]=set()
        for key in keys:
            buf=self._buffers[key]
            if not buf.row_count: continue
            self._materialize(key)
            path,manifest=self._write_staged(key,buf)
            out.append(path); manifests.append(manifest); touched_dirs.add(path.parent)
        if manifests:
            # All data files are complete before one atomic manifest/delivery
            # transaction makes any delivery ACK-eligible. This groups SQLite
            # FULL commits by flush while retaining per-file data/directory fsync.
            for directory in touched_dirs:_fsync_dir(directory)
            self.store.commit_files(manifests)
            for key in keys:
                buf=self._buffers.get(key)
                if not buf or not buf.row_count:continue
                self.last_committed_ids.update(f"{a}:{b}:{c}" for a,b,c,_ in buf.deliveries)
                for _,_,_,event_id in buf.deliveries:
                    if self._pending_event_partitions.get(event_id)==key:
                        self._pending_event_partitions.pop(event_id,None)
                self._buffers.pop(key,None)
        return out
    def _write(self,key:tuple[str,str,str,str],buf:_PartitionBuffer)->Path:
        path,manifest=self._write_staged(key,buf)
        _fsync_dir(path.parent)
        self.store.commit_files((manifest,))
        return path
    def _write_staged(self,key:tuple[str,str,str,str],buf:_PartitionBuffer)->tuple[Path,dict[str,Any]]:
        pa,pq=_require_pyarrow(); typ,date,provider,venue=key; part=self.root/f"event_type={typ}"/f"trading_date={date}"/f"provider={provider}"/f"venue={venue}"; part.mkdir(parents=True,exist_ok=True); fid=uuid.uuid4().hex; final=part/f"part-{fid}.parquet"; tmp=final.with_suffix(".parquet.tmp")
        schema=_schema(typ); writer=pq.ParquetWriter(tmp,schema,compression="zstd",compression_level=self.settings.parquet.compression_level,use_dictionary=True)
        try:
            for batch in buf.batches: writer.write_batch(batch)
        finally: writer.close()
        with tmp.open("rb+") as f: f.flush(); os.fsync(f.fileno())
        os.replace(tmp,final)
        manifest={"file_id":fid,"event_type":typ,"trading_date":date,"provider":provider,"venue":venue,
                  "path":final,"rows":buf.row_count,"min_ts_ns":buf.min_ts_ns,"max_ts_ns":buf.max_ts_ns,
                  "logical_sha256":buf.logical_hasher.hexdigest(),"file_sha256":file_sha256(final),
                  "byte_size":final.stat().st_size,"deliveries":buf.deliveries}
        return final,manifest
    def reconcile_startup(self)->None:
        for tmp in self.root.glob("**/*.parquet.tmp"): tmp.unlink(missing_ok=True)
        for path in self.root.glob("**/*.parquet"):
            if self.store.local_manifest(path) is None: path.rename(path.with_suffix(path.suffix+".orphan"))


class ParquetHistoryReader:
    def __init__(self,data_root:Path,store:RuntimeStore|None=None,settings:RuntimeSettings|None=None)->None: self.settings=settings or get_settings(); self.store=store or RuntimeStore(self.settings); self.data_root=data_root
    def history(self,*,instrument_key:str,event_type:str,limit:int=1000)->list[dict[str,Any]]:
        _,pq=_require_pyarrow(); rows=[]
        for meta in self.store.list_manifest(active_only=True):
            if meta['event_type']!=event_type: continue
            with self._reader_pin(meta) as owner:
                path=self._available_path_locked(meta,owner)
                if path is None: continue
                # A download may outlive the lease. Reacquire before opening;
                # the open handle pins bytes for the whole read even if the
                # lease later expires (POSIX unlink / Windows delete denial).
                if not self.store.acquire_lease(str(meta['file_id']),owner):
                    raise RuntimeError(f"history file lease lost file_id={meta['file_id']}")
                with path.open('rb') as handle:
                    for batch in pq.ParquetFile(handle).iter_batches(batch_size=4096):
                        for row in batch.to_pylist():
                            key=':'.join(str(row.get(n) or '') for n in ('instrument_venue','instrument_asset_class','instrument_std_symbol'))
                            if key==instrument_key: rows.append(row)
        return sorted(rows,key=lambda x:int(x.get('source_ts_ns') or 0))[-limit:]

    @contextmanager
    def _reader_pin(self,meta:dict[str,Any]):
        owner=f"history-{uuid.uuid4().hex}"
        if not self.store.acquire_lease(str(meta['file_id']),owner):
            raise RuntimeError(f"history file busy file_id={meta['file_id']}")
        try: yield owner
        finally: self.store.release_lease(str(meta['file_id']),owner)

    def _available_path(self,meta:dict[str,Any])->Path|None:
        with self._reader_pin(meta) as owner:
            path=self._available_path_locked(meta,owner)
            if path is not None and not path.exists():
                raise RuntimeError(f"history file disappeared file_id={meta['file_id']}")
            return path

    def _available_path_locked(self,meta:dict[str,Any],owner:str)->Path|None:
        local=Path(meta['path'])
        if meta.get('local_present') and local.exists():
            _catalogued_file_sha256(local,meta)
            return local
        remote_key=str(meta.get('remote_key') or '')
        if not remote_key or meta.get('upload_status')!='remote_verified' or not self.settings.archive.enabled:return None
        cache=self.settings.data_root/'archive_cache'/f"{meta['file_id']}.parquet"
        size=int(meta.get('byte_size') or 0)
        if size<=0 or size>self.settings.archive.cache_max_bytes:
            raise RuntimeError(f"archive file exceeds cache byte budget or has unknown size file_id={meta['file_id']}")
        if not cache.exists():
            from .storage_backend import archive_backend
            # Reserve the known size before I/O. Concurrent readers see this
            # row and its manifest pin, so they cannot spend the same budget.
            self.store.touch_archive_cache(str(meta['file_id']),cache,size)
            temporary=cache.with_name(cache.name+'.'+owner+'.tmp')
            try:
                self._trim_cache()
                archive_backend(self.settings).download_file(remote_key,temporary)
                if temporary.stat().st_size!=size:
                    raise RuntimeError(f"archive cache size mismatch file_id={meta['file_id']}")
                _catalogued_file_sha256(temporary,meta)
                # A long download can outlive its lease/reservation. Never
                # overwrite another owner's final path or spend stale budget.
                if not self.store.acquire_lease(str(meta['file_id']),owner):
                    raise RuntimeError(f"history file lease lost during download file_id={meta['file_id']}")
                self.store.touch_archive_cache(str(meta['file_id']),cache,size)
                self._trim_cache()
                cache.parent.mkdir(parents=True,exist_ok=True)
                os.replace(temporary,cache)
            except BaseException:
                # Only this attempt's staging file belongs to this reader.
                # Another lease owner may have published a valid cache while
                # the failed download was awaiting the remote object.
                if self.store.acquire_lease(str(meta['file_id']),owner) and not cache.exists():
                    self.store.remove_archive_cache(str(meta['file_id']))
                raise
            finally:
                # Both names belong to this reader's unique staging attempt.
                # Also defend against a backend that leaves its partial copy;
                # never remove the shared final cache or another owner's temp.
                temporary.unlink(missing_ok=True)
                temporary.with_name(temporary.name+'.downloading').unlink(missing_ok=True)
        actual_size=cache.stat().st_size
        if actual_size!=size:
            raise RuntimeError(f"archive cache size mismatch file_id={meta['file_id']}")
        _catalogued_file_sha256(cache,meta)
        self.store.touch_archive_cache(str(meta['file_id']),cache,actual_size);self._trim_cache()
        return cache

    def _trim_cache(self)->None:
        rows=self.store.archive_cache_rows();total=sum(int(row['byte_size']) for row in rows)
        owner=f"cache-trim-{uuid.uuid4().hex}"
        for row in rows:
            if total<=self.settings.archive.cache_max_bytes:break
            if not self.store.acquire_lease(str(row['file_id']),owner):continue
            try:
                path=Path(row['path']);size=int(row['byte_size'])
                try: path.unlink(missing_ok=True)
                except PermissionError: continue
                self.store.remove_archive_cache(str(row['file_id']));total-=size
            finally:self.store.release_lease(str(row['file_id']),owner)
        if sum(int(row['byte_size']) for row in self.store.archive_cache_rows())>self.settings.archive.cache_max_bytes:
            raise RuntimeError("archive cache byte budget unavailable: pinned or active files")


def market_data_subject_for_type(event_type:EventType|str)->str: return f"md.{event_type.value if isinstance(event_type,EventType) else event_type}.>"
