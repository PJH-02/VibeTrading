from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .storage_backend import archive_backend, sha256_file
from .settings import RuntimeSettings, get_settings
from .sqlite_store import RuntimeStore


class ArchiveMaintenance:
    """Single-flight upload, verify and local-cleanup maintenance worker."""
    def __init__(self,settings:RuntimeSettings|None=None,store:RuntimeStore|None=None)->None:
        self.settings=settings or get_settings();self.store=store or RuntimeStore(self.settings);self.owner=f"maintenance-{uuid.uuid4().hex}"
    def _key(self,row:dict[str,Any])->str:
        return f"{self.settings.archive.prefix}/generations/{self.store.collection_generation()}/market_data/event_type={row['event_type']}/trading_date={row['trading_date']}/provider={row['provider']}/venue={row['venue']}/{row['file_id']}.parquet"
    def _sidecar(self,row:dict[str,Any])->str:return f"{self.settings.archive.prefix}/generations/{self.store.collection_generation()}/manifests/{row['file_id']}.json"
    def check(self)->dict[str,Any]:
        backend=archive_backend(self.settings); key=f"{self.settings.archive.prefix}/health/{uuid.uuid4().hex}.txt"; data=b'vibetrading archive check\n'; sha=__import__('hashlib').sha256(data).hexdigest(); backend.put_bytes(data,key,sha256=sha);backend.verify(key,size_bytes=len(data),sha256=sha);return {'ok':True,'key':key}
    def run_once(self)->dict[str,Any]:
        if not self.settings.archive.enabled:return {'ran':False,'reason':'archive_not_configured'}
        from .storage_guard import StorageGuard
        decision=StorageGuard(self.settings).evaluate();force=decision.upload_priority; before=time.time_ns()-self.settings.archive.upload_after_seconds*1_000_000_000
        rows=self.store.archive_candidates(older_than_ns=before,force=force,limit=32)
        for row in rows:self._upload(row)
        # At >=60% reclaim verified data; at normal operation retain 24h.
        retention=time.time_ns()-self.settings.archive.local_retention_seconds*1_000_000_000
        for row in self.store.deletion_candidates(older_than_ns=retention,force=force,limit=64):
            if not self.store.acquire_lease(row['file_id'],self.owner):continue
            try:
                path=Path(row['path'])
                if path.exists(): path.unlink()
                self.store.mark_local_deleted(row['file_id'])
            finally:self.store.release_lease(row['file_id'],self.owner)
        return {'ran':True,'upload_candidates':len(rows),'disk':decision.as_dict()}
    def _upload(self,row:dict[str,Any])->None:
        if not self.store.acquire_lease(row['file_id'],self.owner):return
        try:
            path=Path(row['path']); backend=archive_backend(self.settings); sha=sha256_file(path); key=self._key(row);backend.put_file(path,key,sha256=sha);backend.verify(key,size_bytes=path.stat().st_size,sha256=sha)
            payload=json.dumps({'file_id':row['file_id'],'key':key,'bytes':path.stat().st_size,'sha256':sha,'rows':row['rows'],'partition':{k:row[k] for k in ('event_type','trading_date','provider','venue')}},sort_keys=True).encode(); side=self._sidecar(row); side_sha=__import__('hashlib').sha256(payload).hexdigest();backend.put_bytes(payload,side,sha256=side_sha);backend.verify(side,size_bytes=len(payload),sha256=side_sha)
            self.store.mark_remote_verified(row['file_id'],key,side,path.stat().st_size,sha)
        except Exception as exc:self.store.mark_upload_error(row['file_id'],str(exc));raise
        finally:self.store.release_lease(row['file_id'],self.owner)


def archive_check(settings:RuntimeSettings|None=None)->dict[str,Any]:return ArchiveMaintenance(settings).check()
