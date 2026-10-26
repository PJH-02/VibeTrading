from __future__ import annotations

import os
import hashlib
import shutil
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from .parquet_store import _canon, _catalogued_file_sha256, _fsync_dir, _require_pyarrow, file_sha256
from .settings import RuntimeSettings, get_settings
from .sqlite_store import RuntimeStore


class ParquetCompactor:
    """One bounded, manifest-driven compaction job per invocation."""
    def __init__(self,settings:RuntimeSettings|None=None,store:RuntimeStore|None=None)->None:
        self.settings=settings or get_settings(); self.store=store or RuntimeStore(self.settings); self.root=self.settings.parquet.data_root; self.owner=f"compactor-{uuid.uuid4().hex}"
    def discover_candidates(self)->list[list[dict[str,Any]]]:
        age=time.time_ns()-self.settings.parquet.min_file_age_minutes*60*1_000_000_000; groups:dict[tuple[str,str,str,str,str],list[dict[str,Any]]]=defaultdict(list)
        _,pq=_require_pyarrow()
        for r in self.store.compaction_candidates(older_than_ns=age):
            path=Path(r['path'])
            if not path.exists(): continue
            fingerprint=hashlib.sha256(str(pq.ParquetFile(path).schema_arrow).encode()).hexdigest()
            groups[(r['event_type'],r['trading_date'],r['provider'],r['venue'],fingerprint)].append(r)
        out=[]
        for rows in groups.values():
            selected=[]; size=0
            for r in rows:
                n=int(r['byte_size'] or 0)
                if selected and size+n>self.settings.parquet.compactor_max_input_bytes: break
                selected.append(r);size+=n
                if size>=self.settings.parquet.target_compact_bytes: break
            if len(selected)>=2: out.append(selected)
        return sorted(out,key=lambda x:sum(int(r['byte_size'] or 0) for r in x),reverse=True)
    def compact_once(self)->dict[str,Any]:
        disk=shutil.disk_usage(self.settings.data_root); ratio=1-disk.free/disk.total
        if ratio>=self.settings.storage_guard.direct_upload_used_ratio:return {'ran':False,'reason':'direct_upload_priority'}
        choices=self.discover_candidates()
        if not choices:return {'ran':False,'reason':'no_candidates'}
        selected=choices[0];acquired=[]
        try:
            for row in selected:
                if not self.store.acquire_lease(row['file_id'],self.owner):return {'ran':False,'reason':'lease_conflict'}
                acquired.append(row['file_id'])
            return self._compact(selected)
        finally:
            for file_id in acquired:self.store.release_lease(file_id,self.owner)
    def _compact(self,sources:list[dict[str,Any]])->dict[str,Any]:
        # The source-to-output comparison below cannot establish that sources
        # still match their committed receipts. Check those before replacement.
        source_digests={source['file_id']:_catalogued_file_sha256(Path(source['path']),source) for source in sources}
        pa,pq=_require_pyarrow(); first=sources[0]; part=self.root/f"event_type={first['event_type']}"/f"trading_date={first['trading_date']}"/f"provider={first['provider']}"/f"venue={first['venue']}"; fid=uuid.uuid4().hex; final=part/f"part-{fid}.parquet"; tmp=final.with_suffix('.parquet.tmp'); writer=None; count=0; source_hash=hashlib.sha256(); min_ts=None;max_ts=None; expected_schema=None
        # Compressed input bytes don't bound nested-book Python expansion.
        # Small batches bound to_pylist(), including the verification pass.
        batch_size=max(1,min(1024,int(self.settings.env.get('COMPACTOR_BATCH_ROWS','128'))))
        try:
            for source in sources:
                source_file=pq.ParquetFile(source['path'])
                if expected_schema is None: expected_schema=source_file.schema_arrow
                elif source_file.schema_arrow != expected_schema: raise RuntimeError('mixed parquet schemas cannot be compacted together')
                for batch in source_file.iter_batches(batch_size=batch_size):
                    if writer is None: writer=pq.ParquetWriter(tmp,batch.schema,compression='zstd',compression_level=self.settings.parquet.compression_level,use_dictionary=False)
                    writer.write_batch(batch); count+=batch.num_rows
                    for row in batch.to_pylist():
                        source_hash.update(_canon(row)); source_hash.update(b'\n')
                        ts=int(row.get('source_ts_ns') or 0);min_ts=ts if min_ts is None else min(min_ts,ts);max_ts=ts if max_ts is None else max(max_ts,ts)
                if file_sha256(Path(source['path']))!=source_digests[source['file_id']]:
                    raise RuntimeError(f"source changed during compaction file_id={source['file_id']}")
        finally:
            if writer: writer.close()
        if writer is None: raise RuntimeError('empty compaction candidate')
        with tmp.open('rb+') as f:f.flush();os.fsync(f.fileno())
        os.replace(tmp,final);_fsync_dir(part)
        verified=0; verify_hash=hashlib.sha256()
        for batch in pq.ParquetFile(final).iter_batches(batch_size=batch_size):
            verified+=batch.num_rows
            for row in batch.to_pylist(): verify_hash.update(_canon(row)); verify_hash.update(b'\n')
        logical=source_hash.hexdigest()
        if verified!=count or verify_hash.hexdigest()!=logical:
            final.rename(final.with_suffix('.parquet.quarantine'));raise RuntimeError('compaction verification mismatch')
        output={'file_id':fid,'event_type':first['event_type'],'trading_date':first['trading_date'],'provider':first['provider'],'venue':first['venue'],'path':final,'rows':count,'min_ts_ns':min_ts,'max_ts_ns':max_ts,'logical_sha256':logical,'file_sha256':file_sha256(final),'byte_size':final.stat().st_size}
        self.store.commit_compaction(output,sources)
        return {'ran':True,'output':str(final),'source_files':len(sources),'rows':count}
