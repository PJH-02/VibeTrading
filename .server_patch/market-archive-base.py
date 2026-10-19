from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import shutil

from .storage_backend import archive_backend, sha256_file
from .settings import RuntimeSettings, get_settings
from .sqlite_store import MaintenanceDeferred, RuntimeStore
from .maintenance_gate import claim_backup_slot_locked, maintenance_lock
from .storage_guard import StorageGuard


class ArchiveMaintenance:
    """Single-flight upload, verify and local-cleanup maintenance worker."""
    def __init__(self,settings:RuntimeSettings|None=None,store:RuntimeStore|None=None)->None:
        self.settings=settings or get_settings();self._store=store;self.owner=f"maintenance-{uuid.uuid4().hex}"
        self._owns_store = store is None

    @property
    def store(self) -> RuntimeStore:
        # Pressure checks must not open SQLite or run catalogue initialization.
        if self._store is None:
            self._store = RuntimeStore(self.settings)
        return self._store

    def close(self) -> None:
        if self._owns_store and self._store is not None:
            self._store.close()
            self._store = None
    def _key(self,row:dict[str,Any])->str:
        return f"{self.settings.archive.prefix}/generations/{self.store.collection_generation()}/market_data/event_type={row['event_type']}/trading_date={row['trading_date']}/provider={row['provider']}/venue={row['venue']}/{row['file_id']}.parquet"
    def _sidecar(self,row:dict[str,Any])->str:return f"{self.settings.archive.prefix}/generations/{self.store.collection_generation()}/manifests/{row['file_id']}.json"
    def check(self)->dict[str,Any]:
        backend=archive_backend(self.settings); key=f"{self.settings.archive.prefix}/health/{uuid.uuid4().hex}.txt"; data=b'vibetrading archive check\n'; sha=__import__('hashlib').sha256(data).hexdigest(); backend.put_bytes(data,key,sha256=sha);return {'ok':True,'key':key}
    def _resource_maintenance_allowed(self)->tuple[bool,dict[str,Any]]:
        compact = (self._store.compact_delivery_ledger if self._store is not None else
                   self.settings.env.get("DELIVERY_LEDGER_FORMAT", "legacy").strip().lower() == "compact")
        component="crypto-storage" if compact else "storage"
        path=self.settings.state_dir/"health"/f"{component}.json"
        try:
            health=json.loads(path.read_text(encoding="utf-8"))
            age=max(0.0,(time.time_ns()-int(health.get("ts_ns",0)))/1_000_000_000)
        except (OSError,ValueError,TypeError):
            return False,{"reason":"storage_health_unavailable"}
        try:
            full=next(line for line in Path("/proc/pressure/io").read_text(encoding="utf-8").splitlines() if line.startswith("full "))
            io_full=float(dict(item.split("=",1) for item in full.split()[1:]).get("avg10","100"))
        except (OSError,ValueError,StopIteration):
            return False,{"reason":"io_pressure_unavailable"}
        allowed=(age<=60 and io_full<=self.settings.archive.delivery_prune_max_io_pressure)
        return allowed,{"storage_age_seconds":round(age,1),"io_full_avg10":io_full,
                        "reason":"quiet" if allowed else "storage_or_io_busy"}

    def _heavy_maintenance_allowed(self)->tuple[bool,dict[str,Any]]:
        """Stricter gate for ledger pruning and compaction, not disk relief."""
        allowed,state=self._resource_maintenance_allowed()
        if not allowed:return allowed,state
        compact = (self._store.compact_delivery_ledger if self._store is not None else
                   self.settings.env.get("DELIVERY_LEDGER_FORMAT", "legacy").strip().lower() == "compact")
        component="crypto-storage" if compact else "storage"
        try:
            health=json.loads((self.settings.state_dir/"health"/f"{component}.json").read_text(encoding="utf-8"))
            pending=int(health.get("pending",0))
        except (OSError,ValueError,TypeError):
            return False,{"reason":"storage_health_unavailable"}
        state=dict(state,pending=pending)
        if pending>self.settings.archive.delivery_prune_max_pending:
            return False,dict(state,reason="storage_pending_above_prune_limit")
        return True,dict(state,reason="quiet")
    def run_once(self, force: bool | None = None)->dict[str,Any]:
        if not self.settings.archive.enabled:return {'ran':False,'reason':'archive_not_configured'}
        allowed,state=self._resource_maintenance_allowed()
        if not allowed:
            return {'ran':False,'reason':state['reason'],'heavy_maintenance':state}
        with maintenance_lock(self.settings.data_root) as lock:
            if not lock["acquired"]:
                return {'ran':False,'reason':lock["reason"]}
            return self._run_once_locked(force)

    def _run_once_locked(self, force: bool | None)->dict[str,Any]:
        result: dict[str,Any] = {
            'ran':True,'uploaded':0,'upload_candidates':0,'reclaimed':{'files':0,'bytes':0},
            'reclaim_batch_full':False,'pruned_deliveries':0,
            'delivery_history_prune_enabled':self.settings.archive.delivery_history_prune_enabled,
            'delivery_prune_reason':'disabled',
            # COUNT(*) scans the delivery index. It is not a heartbeat metric.
            'ledger':{'measured':False,'reason':'not_sampled_in_maintenance'},
        }
        stage='start'
        try:
            self._require_resource_quiet()
            decision=StorageGuard(self.settings).evaluate()
            result['disk']=decision.as_dict()
            force=decision.upload_priority if force is None else bool(force)
            before=time.time_ns()-self.settings.archive.upload_after_seconds*1_000_000_000
            reclaim_limit=max(64,int(self.settings.env.get('ARCHIVE_RECLAIM_BATCH','2048' if force else '128')))
            stage='reclaim_before_upload'
            result['reclaimed']=self._reclaim_verified(force=force,older_than_ns=time.time_ns()-self.settings.archive.local_retention_seconds*1_000_000_000,limit=reclaim_limit)
            result['reclaim_batch_full']=result['reclaimed']['files']>=reclaim_limit
            stage='wal_checkpoint'
            self._require_resource_quiet()
            result['wal_checkpoint']=self.store.checkpoint_wal_if_large(max(1,int(self.settings.env.get('ARCHIVE_WAL_TRUNCATE_BYTES',str(256*1024*1024)))))
            stage='upload_candidates'
            rows=self._read(lambda:self.store.archive_candidates(older_than_ns=before,force=force,limit=max(1,int(self.settings.env.get('ARCHIVE_UPLOAD_BATCH','32')))))
            result['upload_candidates']=len(rows)
            stage='upload'
            for row in rows:
                self._require_resource_quiet()
                self._upload(row)
                result['uploaded']+=1
            stage='backup'
            self._require_resource_quiet()
            result['sqlite_backup']=self._backup_sqlite_if_due(force=force,lock_held=True)
            # Only query a second time if this cycle actually verified files.
            if result['uploaded']:
                stage='reclaim_after_upload'
                batch=self._reclaim_verified(force=force,older_than_ns=time.time_ns()-self.settings.archive.local_retention_seconds*1_000_000_000,limit=128 if force else 64)
                for key in ('files','bytes'):result['reclaimed'][key]+=batch[key]
            stage='orphan_cleanup'
            self._require_resource_quiet()
            result['orphan_cleanup']=self._cleanup_orphans(force=force)
            floor_is_confirmed=(self.settings.env.get('ARCHIVE_DELIVERY_PRUNE_ACK_FLOOR_VERIFIED','false').lower()
                                in {'1','true','yes','on'})
            if self.settings.archive.delivery_history_prune_enabled and floor_is_confirmed:
                stage='delivery_prune'
                self._require_quiet()
                result['pruned_deliveries']=self.store.prune_delivery_history(retain_seconds=self.settings.archive.delivery_history_retention_seconds,limit=max(1,self.settings.archive.delivery_history_prune_limit))
                result['delivery_prune_reason']='complete'
            elif self.settings.archive.delivery_history_prune_enabled:
                result['delivery_prune_reason']='ack_floor_provenance_unverified'
            result['heavy_maintenance']={'reason':'quiet'}
        except MaintenanceDeferred as exc:
            result.update(deferred=True,reason=str(exc),deferred_stage=stage,reclaim_batch_full=False)
            # A deferred cycle must use the normal cooldown, not backlog retry.
            result['heavy_maintenance']=self._heavy_maintenance_allowed()[1]
        return result

    def _require_resource_quiet(self) -> None:
        allowed,state=self._resource_maintenance_allowed()
        if not allowed:
            raise MaintenanceDeferred(str(state['reason']))

    def _require_quiet(self) -> None:
        allowed,state=self._heavy_maintenance_allowed()
        if not allowed:raise MaintenanceDeferred(str(state['reason']))

    def _read(self, operation: Any) -> Any:
        self._require_resource_quiet()
        with self.store.maintenance_read_guard(self._require_resource_quiet):
            return operation()

    def _reclaim_verified(self, *, force:bool, older_than_ns:int, limit:int)->dict[str,int]:
        files=bytes_removed=0
        rows=self._read(lambda:self.store.deletion_candidates(older_than_ns=older_than_ns,force=force,limit=limit))
        for start in range(0,len(rows),128):
            # Once a chunk is claimed, finish its catalogue bookkeeping before
            # yielding; a pressure change must not strand deletion leases.
            self._require_resource_quiet()
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

    def _backup_sqlite_if_due(self, *, force: bool, allowed:bool=True, lock_held:bool=False) -> dict[str,Any]:
        if not allowed:return {'ran':False,'reason':'storage_or_io_busy'}
        now=time.time_ns(); last=self.store.last_archive_backup_ts_ns(); interval=self.settings.archive.sqlite_backup_interval_seconds*1_000_000_000
        # Disk pressure prioritizes Parquet uploads and deletion, but must not
        # turn the full SQLite backup into an endless disk/network workload.
        if last is not None and now-last<interval:return {'ran':False,'reason':'not_due'}
        domain="crypto" if self.store.compact_delivery_ledger else "market"
        if lock_held:
            slot=claim_backup_slot_locked(self.settings.data_root,domain,1800)
            if not slot["acquired"]:return {'ran':False,'reason':slot["reason"]}
            return self._backup_sqlite_locked(now)
        with maintenance_lock(self.settings.data_root,backup_domain=domain,backup_cooldown_seconds=1800) as lock:
            if not lock["acquired"]:return {'ran':False,'reason':lock["reason"]}
            return self._backup_sqlite_locked(now)

    def _backup_sqlite_locked(self, now:int) -> dict[str,Any]:
        backup_id=f"{now}-{uuid.uuid4().hex}"
        temporary=self.settings.state_dir/'backups'/f"vibetrading-{backup_id}.sqlite3"
        key=f"{self.settings.archive.prefix}/generations/{self.store.collection_generation()}/state/{temporary.name}"
        try:
            temporary.parent.mkdir(parents=True,exist_ok=True)
            page_count=self.store.backup_page_count();page_size=self.store.backup_page_size()
            estimated_bytes=max(int(page_count*page_size*1.2),int(self.store.path.stat().st_size)+256*1024**2)
            usage=shutil.disk_usage(temporary.parent)
            guard=StorageGuard(self.settings).evaluate()
            required=guard.reserve_bytes+guard.resume_margin_bytes
            if usage.free-estimated_bytes<required:
                return {'ran':False,'reason':'backup_space_budget','estimated_bytes':estimated_bytes,
                        'free_bytes':usage.free,'required_free_after_backup':required}

            import time as _time
            started_monotonic=_time.monotonic()
            last_check=started_monotonic

            class BackupDeferred(RuntimeError):
                pass

            def progress(_status:int,remaining:int,total:int)->None:
                nonlocal last_check
                now_monotonic=_time.monotonic()
                if now_monotonic-last_check<1.0:return
                last_check=now_monotonic
                current=shutil.disk_usage(temporary.parent)
                decision=StorageGuard(self.settings).evaluate()
                io_pressure=self._io_full_avg10()
                if current.free<=decision.reserve_bytes+decision.resume_margin_bytes:
                    raise BackupDeferred('backup_space_guard')
                if io_pressure is None or io_pressure>self.settings.archive.delivery_prune_max_io_pressure:
                    raise BackupDeferred('backup_io_pressure')
                if total and total-remaining>0 and now_monotonic-started_monotonic>900:
                    raise BackupDeferred('backup_time_budget')

            self.store.backup_to(temporary,pages=32,sleep_seconds=0.03,progress=progress)
            sha=sha256_file(temporary);backend=archive_backend(self.settings)
            # put_file verifies the remote object, including its full SHA-256.
            # A second verify downloaded this large SQLite backup again.
            backend.put_file(temporary,key,sha256=sha)
            self.store.record_archive_backup(backup_id,key,sha,now)
            return {'ran':True,'key':key,'bytes':temporary.stat().st_size,'sha256':sha,
                    'estimated_bytes':estimated_bytes,'duration_seconds':round(_time.monotonic()-started_monotonic,2)}
        except Exception as exc:
            if type(exc).__name__=="BackupDeferred":
                return {'ran':False,'reason':str(exc),'estimated_bytes':locals().get('estimated_bytes',0)}
            raise
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _io_full_avg10()->float|None:
        try:
            full=next(line for line in Path('/proc/pressure/io').read_text(encoding='utf-8').splitlines() if line.startswith('full '))
            return float(dict(item.split('=',1) for item in full.split()[1:]).get('avg10','100'))
        except (OSError,ValueError,StopIteration):return None

    def _cleanup_orphans(self, *, force: bool) -> dict[str,int]:
        """Preserve unregistered files remotely before reclaiming their space."""
        cutoff=time.time()-(3600 if force else 24*3600)
        limit=4 if force else 1
        files=bytes_removed=0
        # Reclaim the most space per verified upload while the disk is tight.
        orphans=sorted(self.settings.parquet.data_root.glob('**/*.parquet.orphan'),key=lambda path:path.stat().st_size,reverse=True)
        for path in orphans:
            if files>=limit:break
            self._require_resource_quiet()
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
