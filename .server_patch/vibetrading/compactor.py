from __future__ import annotations

import os
import shutil
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from .parquet_store import _fsync_dir, _require_pyarrow, file_sha256, logical_hash_rows
from .settings import RuntimeSettings, get_settings
from .sqlite_store import RuntimeStore


class ParquetCompactor:
    """One bounded, manifest-driven compaction job per invocation."""
    def __init__(self,settings:RuntimeSettings|None=None,store:RuntimeStore|None=None)->None:
        self.settings=settings or get_settings(); self.store=store or RuntimeStore(self.settings); self.root=self.settings.parquet.data_root
    def discover_candidates(self)->list[list[dict[str,Any]]]:
        age=time.time_ns()-self.settings.parquet.min_file_age_minutes*60*1_000_000_000; groups:dict[tuple[str,str,str,str],list[dict[str,Any]]]=defaultdict(list)
        for r in self.store.compaction_candidates(older_than_ns=age): groups[(r['event_type'],r['trading_date'],r['provider'],r['venue'])].append(r)
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
        return self._compact(choices[0])
    def _compact(self,sources:list[dict[str,Any]])->dict[str,Any]:
        pa,pq=_require_pyarrow(); first=sources[0]; part=self.root/f"event_type={first['event_type']}"/f"trading_date={first['trading_date']}"/f"provider={first['provider']}"/f"venue={first['venue']}"; fid=uuid.uuid4().hex; final=part/f"part-{fid}.parquet"; tmp=final.with_suffix('.parquet.tmp'); writer=None; count=0; checksum_rows=[]; min_ts=None;max_ts=None
        try:
            for source in sources:
                for batch in pq.ParquetFile(source['path']).iter_batches(batch_size=4096):
                    if writer is None: writer=pq.ParquetWriter(tmp,batch.schema,compression='zstd',compression_level=self.settings.parquet.compression_level,use_dictionary=True)
                    writer.write_batch(batch); count+=batch.num_rows
                    rows=batch.to_pylist(); checksum_rows.extend(rows)
                    for row in rows:
                        ts=int(row.get('source_ts_ns') or 0);min_ts=ts if min_ts is None else min(min_ts,ts);max_ts=ts if max_ts is None else max(max_ts,ts)
        finally:
            if writer: writer.close()
        if writer is None: raise RuntimeError('empty compaction candidate')
        with tmp.open('rb+') as f:f.flush();os.fsync(f.fileno())
        os.replace(tmp,final);_fsync_dir(part)
        verified=0; verify_rows=[]
        for batch in pq.ParquetFile(final).iter_batches(batch_size=4096): verified+=batch.num_rows;verify_rows.extend(batch.to_pylist())
        logical=logical_hash_rows(checksum_rows)
        if verified!=count or logical_hash_rows(verify_rows)!=logical:
            final.rename(final.with_suffix('.parquet.quarantine'));raise RuntimeError('compaction verification mismatch')
        output={'file_id':fid,'event_type':first['event_type'],'trading_date':first['trading_date'],'provider':first['provider'],'venue':first['venue'],'path':final,'rows':count,'min_ts_ns':min_ts,'max_ts_ns':max_ts,'logical_sha256':logical,'file_sha256':file_sha256(final),'byte_size':final.stat().st_size}
        self.store.commit_compaction(output,sources)
        return {'ran':True,'output':str(final),'source_files':len(sources),'rows':count}
