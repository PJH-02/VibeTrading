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
        backend=archive_backend(self.settings); key=f"{self.settings.archive.prefix}/health/{uuid.uuid4().hex}.txt"; data=b'vibetrading archive check\n'; sha=__import__('hashlib').sha256(data).hexdigest(); backend.put_bytes(data,key,sha256=sha);return {'ok':True,'key':key}
    def _heavy_maintenance_allowed(self)->tuple[bool,dict[str,Any]]:
        component="crypto-storage" if self.store.compact_delivery_ledger else "storage"
        path=self.settings.state_dir/"health"/f"{component}.json"
        try:
            health=json.loads(path.read_text(encoding="utf-8"))
            age=max(0.0,(time.time_ns()-int(health.get("ts_ns",0)))/1_000_000_000)
            pending=int(health.get("pending",0))
        except (OSError,ValueError,TypeError):
            return False,{"reason":"storage_health_unavailable"}
        try:
            full=next(line for line in Path("/proc/pressure/io").read_text(encoding="utf-8").splitlines() if line.startswith("full "))
            io_full=float(dict(item.split("=",1) for item in full.split()[1:]).get("avg10","100"))
        except (OSError,ValueError,StopIteration):
            return False,{"reason":"io_pressure_unavailable","pending":pending}
        allowed=(age<=60 and pending<=self.settings.archive.delivery_prune_max_pending
                 and io_full<=self.settings.archive.delivery_prune_max_io_pressure)
        return allowed,{"storage_age_seconds":round(age,1),"pending":pending,"io_full_avg10":io_full,
                        "reason":"quiet" if allowed else "storage_or_io_busy"}
    def run_once(self, force: bool | None = None)->dict[str,Any]:
        if not self.settings.archive.enabled:return {'ran':False,'reason':'archive_not_configured'}
        from .storage_guard import StorageGuard
        decision=StorageGuard(self.settings).evaluate();force=decision.upload_priority if force is None else bool(force); before=time.time_ns()-self.settings.archive.upload_after_seconds*1_000_000_000
        # Reclaim already verified files before a potentially long upload
        # batch. On a small shared volume this is the fastest safe way to
        # reopen collection after a disk-pressure pause.
        reclaim_limit=max(64,int(self.settings.env.get("ARCHIVE_RECLAIM_BATCH","2048" if force else "128")))
        reclaimed=self._reclaim_verified(force=force,older_than_ns=time.time_ns()-self.settings.archive.local_retention_seconds*1_000_000_000,limit=reclaim_limit)
        checkpoint=self.store.checkpoint_wal_if_large(max(1,int(self.settings.env.get("ARCHIVE_WAL_TRUNCATE_BYTES",str(256*1024*1024)))))
        rows=self.store.archive_candidates(older_than_ns=before,force=force,
                                           limit=max(1,int(self.settings.env.get("ARCHIVE_UPLOAD_BATCH","32"))))
        uploaded=0
        for row in rows:
            self._upload(row);uploaded+=1
            if force and uploaded%32==0:
                batch=self._reclaim_verified(force=True,older_than_ns=0,limit=128)
                reclaimed['files']+=batch['files'];reclaimed['bytes']+=batch['bytes']
        heavy_allowed,heavy_state=self._heavy_maintenance_allowed()
        backup=self._backup_sqlite_if_due(force=force,allowed=heavy_allowed)
        # Also clear newly verified files after the final upload chunk.
        batch=self._reclaim_verified(force=force,older_than_ns=time.time_ns()-self.settings.archive.local_retention_seconds*1_000_000_000,limit=128 if force else 64)
        reclaimed['files']+=batch['files'];reclaimed['bytes']+=batch['bytes']
        orphan_result=self._cleanup_orphans(force=force)
        # This DELETE may scan a large catalogue while holding SQLite's sole
        # writer lock.  Storage commits are latency-sensitive, so delivery
        # history is pruned only by an explicitly enabled off-hours run.
        pruned_deliveries = 0
        prune_reason="disabled"
        if self.settings.archive.delivery_history_prune_enabled and heavy_allowed:
            pruned_deliveries = self.store.prune_delivery_history(
                retain_seconds=self.settings.archive.delivery_history_retention_seconds,
                limit=max(1, self.settings.archive.delivery_history_prune_limit)
            )
            prune_reason="complete"
        elif self.settings.archive.delivery_history_prune_enabled:prune_reason=heavy_state["reason"]
        return {'ran':True,'upload_candidates':len(rows),'uploaded':uploaded,'reclaimed':reclaimed,'reclaim_batch_full':reclaimed['files']>=reclaim_limit,'wal_checkpoint':checkpoint,'sqlite_backup':backup,'orphan_cleanup':orphan_result,'pruned_deliveries':pruned_deliveries,'delivery_history_prune_enabled':self.settings.archive.delivery_history_prune_enabled,'delivery_prune_reason':prune_reason,'heavy_maintenance':heavy_state,'ledger':self.store.delivery_ledger_stats(),'disk':decision.as_dict()}

    def _reclaim_verified(self, *, force:bool, older_than_ns:int, limit:int)->dict[str,int]:
        files=bytes_removed=0
        rows=self.store.deletion_candidates(older_than_ns=older_than_ns,force=force,limit=limit)
        for start in range(0,len(rows),128):
            chunk=rows[start:start+128]
            claimed=self.store.claim_local_deletions((row['file_id'] for row in chunk),self.owner)
            deleted=[]
            try:
                for row in chunk:
                    if row['file_id'] not in claimed:continue
                    path=Path(row['path'])
                    if path.exists():
                        size=path.stat().st_size
                        path.unlink()
                        bytes_removed+=size
                    deleted.append(row['file_id'])
                    files+=1
            finally:
                self.store.finish_local_deletions(claimed,deleted,self.owner)
        return {'files':files,'bytes':bytes_removed}

    def _backup_sqlite_if_due(self, *, force: bool, allowed:bool=True) -> dict[str,Any]:
        now=time.time_ns(); last=self.store.last_archive_backup_ts_ns(); interval=self.settings.archive.sqlite_backup_interval_seconds*1_000_000_000
        # Disk pressure prioritizes Parquet uploads and deletion, but must not
        # turn the full SQLite backup into an endless disk/network workload.
        if last is not None and now-last<interval:return {'ran':False,'reason':'not_due'}
        if not allowed:return {'ran':False,'reason':'storage_or_io_busy'}
        backup_id=f"{now}-{uuid.uuid4().hex}"
        temporary=self.settings.state_dir/'backups'/f"vibetrading-{backup_id}.sqlite3"
        key=f"{self.settings.archive.prefix}/generations/{self.store.collection_generation()}/state/{temporary.name}"
        try:
            self.store.backup_to(temporary);sha=sha256_file(temporary);backend=archive_backend(self.settings)
            # put_file verifies the remote object, including its full SHA-256.
            # A second verify downloaded this large SQLite backup again.
            backend.put_file(temporary,key,sha256=sha)
            self.store.record_archive_backup(backup_id,key,sha,now)
            return {'ran':True,'key':key,'bytes':temporary.stat().st_size,'sha256':sha}
        finally:
            temporary.unlink(missing_ok=True)

    def _cleanup_orphans(self, *, force: bool) -> dict[str,int]:
        """Preserve unregistered files remotely before reclaiming their space."""
        cutoff=time.time()-(3600 if force else 24*3600)
        limit=4 if force else 1
        files=bytes_removed=0
        # Reclaim the most space per verified upload while the disk is tight.
        orphans=sorted(self.settings.parquet.data_root.glob('**/*.parquet.orphan'),key=lambda path:path.stat().st_size,reverse=True)
        for path in orphans:
            if files>=limit:break
            try:
                if path.stat().st_mtime>cutoff:continue
                original=Path(str(path)[:-len('.orphan')])
                if self.store.local_manifest(path) is not None or self.store.local_manifest(original) is not None:continue
                size=path.stat().st_size
                sha=sha256_file(path)
                relative=path.relative_to(self.settings.parquet.data_root)
                key=f"{self.settings.archive.prefix}/generations/{self.store.collection_generation()}/recovery/orphans/{relative.as_posix()}"
                backend=archive_backend(self.settings)
                backend.put_file(path,key,sha256=sha)
                payload=json.dumps({'key':key,'relative_path':relative.as_posix(),'bytes':size,'sha256':sha,'catalogue_state':'unregistered'},sort_keys=True).encode()
                side=f"{key}.json";side_sha=__import__('hashlib').sha256(payload).hexdigest()
                backend.put_bytes(payload,side,sha256=side_sha)
                path.unlink()
                files+=1;bytes_removed+=size
            except FileNotFoundError:continue
        return {'files':files,'bytes':bytes_removed}
    def _upload(self,row:dict[str,Any])->None:
        if not self.store.acquire_lease(row['file_id'],self.owner):return
        try:
            path=Path(row['path']); backend=archive_backend(self.settings); sha=sha256_file(path); key=self._key(row);backend.put_file(path,key,sha256=sha)
            payload=json.dumps({'file_id':row['file_id'],'key':key,'bytes':path.stat().st_size,'sha256':sha,'rows':row['rows'],'partition':{k:row[k] for k in ('event_type','trading_date','provider','venue')}},sort_keys=True).encode(); side=self._sidecar(row); side_sha=__import__('hashlib').sha256(payload).hexdigest();backend.put_bytes(payload,side,sha256=side_sha)
            self.store.mark_remote_verified(row['file_id'],key,side,path.stat().st_size,sha)
        except Exception as exc:self.store.mark_upload_error(row['file_id'],str(exc));raise
        finally:self.store.release_lease(row['file_id'],self.owner)


def archive_check(settings:RuntimeSettings|None=None)->dict[str,Any]:return ArchiveMaintenance(settings).check()
