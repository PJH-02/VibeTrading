from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import inspect
import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any

import shutil

from .storage_backend import archive_backend, sha256_file
from .settings import RuntimeSettings, get_settings
from .sqlite_store import MaintenanceDeferred, RuntimeStore
from .maintenance_gate import claim_backup_slot_locked, maintenance_lock
from .parquet_store import _catalogued_file_sha256
from .storage_guard import StorageGuard


_GC_MAX_ABS = 2**63 - 1
_GC_MAX_SEGMENTS = 128
_GC_MAX_RECORDS = 10_000
_GC_MAX_BYTES = 64 * 1024 * 1024
_GC_PLAN_MAX_BYTES = 1024 * 1024
_GC_MAX_COMPACTION_LINKS = 32
_GC_MAX_EVIDENCE_RECORDS = 500
_GC_MAX_REMOTE_KEY_BYTES = 4096


def _gc_positive(value: Any, name: str, maximum: int = _GC_MAX_ABS) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise ValueError(f"{name} must be an explicit positive integer <= {maximum}")
    return value


def _gc_canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _gc_finalize(body: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded JSON-compatible plan carrying its own SHA-256."""
    body = dict(body)
    body.pop("plan_sha256", None)
    payload = _gc_canonical(body)
    if len(payload) > _GC_PLAN_MAX_BYTES:
        body = {
            "dry_run": True, "deleted": 0, "executable": False,
            "producer": body.get("producer"),
            "incarnation": body.get("incarnation"),
            "eligible": [],
            "blocked": [{"reason": "plan_document_budget_exceeded"}],
        }
        payload = _gc_canonical(body)
    body["plan_sha256"] = hashlib.sha256(payload).hexdigest()
    return body


def _gc_blocked(producer: Any, proof: Any, reason: str,
                detail: str | None = None, **extra: Any) -> dict[str, Any]:
    blocked = {"reason": reason}
    if detail:
        blocked["detail"] = str(detail)[:240]
    body = {
        "dry_run": True, "deleted": 0, "executable": False,
        "producer": producer if isinstance(producer, str) else None,
        "incarnation": getattr(proof, "incarnation", None),
        "eligible": [], "blocked": [blocked],
    }
    body.update(extra)
    return _gc_finalize(body)


def _gc_raw_key_snapshot(remote_keys: Mapping[Any, Any]
                         ) -> tuple[dict[int, str] | None, str | None, Any]:
    """Capture bounded string values from a supplied segment-key mapping.

    The item count is limited both by the declared size and by the number of
    entries actually yielded, so an inconsistent Mapping cannot force an
    unbounded materialization.
    """
    try:
        declared = len(remote_keys)
    except Exception:
        return None, "raw_segment_mapping_invalid", None
    if type(declared) is not int or declared > _GC_MAX_SEGMENTS:
        return None, "raw_segment_mapping_too_large", None
    snapshot: dict[int, str] = {}
    count = 0
    try:
        for segment_id, key in remote_keys.items():
            count += 1
            if count > _GC_MAX_SEGMENTS:
                return None, "raw_segment_mapping_too_large", None
            if type(segment_id) is not int or not 0 < segment_id <= _GC_MAX_ABS:
                return None, "raw_segment_id_invalid", segment_id
            if (not isinstance(key, str) or not key
                    or len(key) > _GC_MAX_REMOTE_KEY_BYTES):
                return None, "raw_segment_key_invalid", segment_id
            snapshot[segment_id] = key
    except Exception:
        return None, "raw_segment_mapping_invalid", None
    return snapshot, None, None


def _gc_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _gc_non_symlink(path: Path) -> bool:
    try:
        return not path.is_symlink()
    except OSError:
        return False


def _gc_validate_intent_path(intent_path: Any, data_root: Path,
                             spool_roots: tuple[Path, ...]) -> Path | None:
    if intent_path is None:
        return None
    try:
        path = Path(intent_path)
    except (TypeError, ValueError) as exc:
        raise ValueError("intent_path must be an absolute .json path") from exc
    if not path.is_absolute() or path.suffix.lower() != ".json":
        raise ValueError("intent_path must be an absolute .json path")
    # Reject the final path, its parent and every ancestor if any is a symlink.
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError("intent_path and its ancestors must not be symlinks")
    if path.exists():
        raise ValueError("intent_path already exists; refusing to overwrite evidence")
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise ValueError("intent_path parent directory must already exist")
    resolved_parent = parent.resolve()
    for root in (data_root, *spool_roots):
        if _gc_under(resolved_parent, Path(root).resolve()):
            raise ValueError("intent_path must be outside data_root and all spools")
    return path


@dataclass(frozen=True)
class _GcIntentTarget:
    path: Path
    parent_device: int
    parent_inode: int
    roots: tuple[Path, ...]


def _gc_pin_intent_path(intent_path: Any, data_root: Path,
                        spool_roots: tuple[Path, ...]) -> _GcIntentTarget | None:
    """Validate the destination and pin its existing evidence-directory identity."""
    path = _gc_validate_intent_path(intent_path, data_root, spool_roots)
    if path is None:
        return None
    parent = path.parent
    try:
        parent_stat = os.stat(parent, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("intent_path parent directory changed") from exc
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise ValueError("intent_path parent directory must already exist")
    roots = tuple(Path(root) for root in (data_root, *spool_roots))
    return _GcIntentTarget(path, parent_stat.st_dev, parent_stat.st_ino, roots)


def _gc_publish_intent(target: _GcIntentTarget, payload: bytes,
                       max_bytes: int) -> None:
    """Publish a bounded intent without pathname-reuse or overwrite authority.

    The evidence directory is revalidated after all callbacks, then opened once
    with O_DIRECTORY/O_NOFOLLOW. The existing checkpoint writer only receives a
    unique owned staging name through the verified directory FD. The final
    name is created with linkat (no replace), so a competing target wins.
    """
    supported = (hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")
                 and hasattr(os, "link") and hasattr(os, "unlink")
                 and os.open in os.supports_dir_fd
                 and os.link in os.supports_dir_fd
                 and os.link in os.supports_follow_symlinks
                 and os.unlink in os.supports_dir_fd
                 and Path("/proc/self/fd").is_dir())
    if not supported:
        raise RuntimeError("secure intent publication primitives are unavailable")

    path = target.path
    parent = path.parent
    for candidate in (path, *path.parents):
        try:
            if candidate.is_symlink():
                raise RuntimeError("intent path ancestry became a symlink")
        except OSError as exc:
            raise RuntimeError("intent path ancestry is unreadable") from exc
    try:
        parent_stat = os.stat(parent, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError("intent evidence parent changed") from exc
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise RuntimeError("intent evidence parent is not a directory")
    if (parent_stat.st_dev, parent_stat.st_ino) != (target.parent_device,
                                                    target.parent_inode):
        raise RuntimeError("intent evidence parent identity changed")
    resolved_parent = parent.resolve()
    for root in target.roots:
        if _gc_under(resolved_parent, Path(root).resolve()):
            raise RuntimeError("intent path is now inside a protected root")
    try:
        os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError("intent destination is unreadable") from exc
    else:
        raise RuntimeError("intent destination appeared before publication")

    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    staging_name = ".vibetrading-intent-" + uuid.uuid4().hex + ".tmp"
    try:
        opened = os.fstat(directory)
        if not stat.S_ISDIR(opened.st_mode):
            raise RuntimeError("intent evidence FD is not a directory")
        if (opened.st_dev, opened.st_ino) != (target.parent_device,
                                              target.parent_inode):
            raise RuntimeError("intent evidence FD identity changed")
        staging_path = Path("/proc/self/fd") / str(directory) / staging_name
        try:
            from .market_checkpoint import _write_checkpoint_file
            _write_checkpoint_file(staging_path, payload, max_bytes)
            try:
                os.link(staging_name, path.name, src_dir_fd=directory,
                        dst_dir_fd=directory, follow_symlinks=False)
            except FileExistsError as exc:
                raise RuntimeError(
                    "intent destination appeared before atomic publication") from exc
            os.fsync(directory)
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW,
                                 dir_fd=directory)
            try:
                actual = bytearray()
                remaining = max_bytes + 1
                while remaining > 0:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    actual.extend(chunk)
                    remaining -= len(chunk)
            finally:
                os.close(descriptor)
            if bytes(actual) != payload:
                raise RuntimeError("published intent readback mismatch")
        finally:
            try:
                os.unlink(staging_name, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.fsync(directory)
    finally:
        os.close(directory)


def _gc_manifest_lease_ok(row: Mapping[str, Any], now_ns: int) -> bool:
    until = row.get("lease_until_ts_ns")
    owner = row.get("lease_owner")
    if until is not None:
        if type(until) is not int:
            return False
        if until > now_ns:
            return False
    elif owner:
        # A lease owner without an expiry is uncertain, not expired.
        return False
    return True


def _gc_hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


class ArchiveMaintenance:
    """Single-flight upload, verify and local-cleanup maintenance worker."""
    def __init__(self,settings:RuntimeSettings|None=None,store:RuntimeStore|None=None)->None:
        self.settings=settings or get_settings();self._store=store;self.owner=f"maintenance-{uuid.uuid4().hex}"
        self._owns_store = store is None
        self._backend = None

    @property
    def backend(self):
        # Reuse the connection pool across data, sidecar and recovery objects.
        if self._backend is None:
            self._backend = archive_backend(self.settings)
        return self._backend

    @property
    def store(self) -> RuntimeStore:
        # Pressure checks must not open SQLite or run catalogue initialization.
        if self._store is None:
            self._store = RuntimeStore(self.settings)
        return self._store

    def close(self) -> None:
        try:
            if self._owns_store and self._store is not None:
                self._store.close()
                self._store = None
        finally:
            if self._backend is not None:
                close = getattr(self._backend, 'close', None)
                if close is not None:close()
                self._backend = None
    def _key(self,row:dict[str,Any])->str:
        return f"{self.settings.archive.prefix}/generations/{self.store.collection_generation()}/market_data/event_type={row['event_type']}/trading_date={row['trading_date']}/provider={row['provider']}/venue={row['venue']}/{row['file_id']}.parquet"
    def _sidecar(self,row:dict[str,Any])->str:return f"{self.settings.archive.prefix}/generations/{self.store.collection_generation()}/manifests/{row['file_id']}.json"
    def check(self)->dict[str,Any]:
        backend=self.backend; key=f"{self.settings.archive.prefix}/health/{uuid.uuid4().hex}.txt"; data=b'vibetrading archive check\n'; sha=__import__('hashlib').sha256(data).hexdigest(); backend.put_bytes(data,key,sha256=sha);return {'ok':True,'key':key}
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
        # Uploads and verified reclamation relieve disk pressure. Applying the
        # full-database maintenance threshold here can starve both forever on
        # a continuously busy ingest disk. Keep the legacy default; deployments
        # may opt into a separate, bounded relief budget.
        relief_limit=float(self.settings.env.get('ARCHIVE_RELIEF_MAX_IO_PRESSURE',
                           str(self.settings.archive.delivery_prune_max_io_pressure)))
        allowed=(age<=60 and io_full<=relief_limit)
        return allowed,{"storage_age_seconds":round(age,1),"io_full_avg10":io_full,
                        "reason":"quiet" if allowed else "storage_or_io_busy"}

    def _heavy_maintenance_allowed(self)->tuple[bool,dict[str,Any]]:
        """Stricter gate for ledger pruning and compaction, not disk relief."""
        allowed,state=self._resource_maintenance_allowed()
        if not allowed:return allowed,state
        if state.get('io_full_avg10',0)>self.settings.archive.delivery_prune_max_io_pressure:
            return False,dict(state,reason='storage_io_above_prune_limit')
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
        cycle_started=stage_started=time.monotonic()
        result['phase_seconds']={}
        def enter(name):
            nonlocal stage,stage_started
            now=time.monotonic()
            result['phase_seconds'][stage]=round(now-stage_started,3)
            stage,stage_started=name,now
        try:
            self._require_resource_quiet()
            decision=StorageGuard(self.settings).evaluate()
            result['disk']=decision.as_dict()
            force=decision.upload_priority if force is None else bool(force)
            before=time.time_ns()-self.settings.archive.upload_after_seconds*1_000_000_000
            reclaim_limit=max(64,int(self.settings.env.get('ARCHIVE_RECLAIM_BATCH','2048' if force else '128')))
            enter('reclaim_before_upload')
            result['reclaimed']=self._reclaim_with_query_budget(result,force=force,older_than_ns=time.time_ns()-self.settings.archive.local_retention_seconds*1_000_000_000,limit=reclaim_limit)
            result['reclaim_batch_full']=result['reclaimed']['files']>=reclaim_limit
            enter('wal_checkpoint')
            self._require_resource_quiet()
            if self.store.event_index_format == 'immutable-runs-v1':
                # Keep normal auto-checkpointing enabled. A live TRUNCATE can
                # stall uploads behind a long reader/writer and adds no
                # durability guarantee. This is not a new checkpoint owner.
                result['wal_checkpoint']={'ran':False,'reason':'crypto_autocheckpoint_enabled'}
            else:
                result['wal_checkpoint']=self.store.checkpoint_wal_if_large(max(1,int(self.settings.env.get('ARCHIVE_WAL_TRUNCATE_BYTES',str(256*1024*1024)))))
            enter('upload_candidates')
            rows=self._read(lambda:self.store.archive_candidates(older_than_ns=before,force=force,limit=max(1,int(self.settings.env.get('ARCHIVE_UPLOAD_BATCH','32')))))
            result['upload_candidates']=len(rows)
            enter('upload')
            for row in rows:
                self._require_resource_quiet()
                self._upload(row)
                result['uploaded']+=1
            if self.store.event_index_format == 'immutable-runs-v1':
                enter('crypto_recovery_archive')
                result['recovery_blocks']=self._archive_crypto_recovery()
            enter('backup')
            self._require_resource_quiet()
            result['sqlite_backup']=self._backup_sqlite_if_due(force=force,lock_held=True)
            # Only query a second time if this cycle actually verified files.
            if result['uploaded']:
                enter('reclaim_after_upload')
                batch=self._reclaim_with_query_budget(result,force=force,older_than_ns=time.time_ns()-self.settings.archive.local_retention_seconds*1_000_000_000,limit=128 if force else 64)
                for key in ('files','bytes'):result['reclaimed'][key]+=batch[key]
            enter('orphan_cleanup')
            self._require_resource_quiet()
            result['orphan_cleanup']=self._cleanup_orphans(force=force)
            floor_is_confirmed=(self.settings.env.get('ARCHIVE_DELIVERY_PRUNE_ACK_FLOOR_VERIFIED','false').lower()
                                in {'1','true','yes','on'})
            if self.settings.archive.delivery_history_prune_enabled and floor_is_confirmed:
                enter('delivery_prune')
                if not self.store.compact_delivery_ledger:
                    result['delivery_prune_reason']='legacy_pruning_proof_unsupported'
                else:
                    self._require_quiet()
                    result['pruned_deliveries']=self.store.prune_delivery_history(retain_seconds=self.settings.archive.delivery_history_retention_seconds,limit=max(1,self.settings.archive.delivery_history_prune_limit))
                    result['delivery_prune_reason']='complete'
            elif self.settings.archive.delivery_history_prune_enabled:
                result['delivery_prune_reason']='ack_floor_provenance_unverified'
            if self.store.event_index_format == 'immutable-runs-v1':
                enter('crypto_event_run_retirement')
                self._require_quiet()
                result['event_run_retirement']=self.store.retire_crypto_event_runs(
                    limit=max(1,int(self.settings.env.get('CRYPTO_EVENT_RUN_RETIRE_BATCH','4'))))
            result['heavy_maintenance']={'reason':'quiet'}
        except MaintenanceDeferred as exc:
            result.update(deferred=True,reason=str(exc),deferred_stage=stage,reclaim_batch_full=False)
            # A deferred cycle must use the normal cooldown, not backlog retry.
            result['heavy_maintenance']=self._heavy_maintenance_allowed()[1]
        finally:
            result['phase_seconds'][stage]=round(time.monotonic()-stage_started,3)
            result['cycle_seconds']=round(time.monotonic()-cycle_started,3)
        return result

    def _require_resource_quiet(self) -> None:
        allowed,state=self._resource_maintenance_allowed()
        if not allowed:
            raise MaintenanceDeferred(str(state['reason']))

    def _archive_crypto_recovery(self) -> dict[str,int]:
        from .delivery_recovery import prepare_document, record_verified
        limit=max(1,min(256,int(self.settings.env.get('CRYPTO_RECOVERY_ARCHIVE_BATCH','32'))))
        scan_after=0
        wrapped=False
        def candidates():
            nonlocal scan_after,wrapped
            row=self.store._conn.execute("SELECT value FROM runtime_metadata WHERE key='crypto_recovery_archive_cursor'").fetchone()
            after=int(row[0]) if row else 0
            scan_after=after
            query=('SELECT block_id FROM crypto_delivery_recovery '
                   'WHERE verified_ts_ns IS NULL AND block_id>? ORDER BY block_id LIMIT ?')
            found=self.store._conn.execute(query,(after,limit)).fetchall()
            wrapped=not bool(found)
            return found or self.store._conn.execute(query,(0,limit)).fetchall()
        blocks=self._read(candidates)
        uploaded=0
        generation=self.store.collection_generation()
        for block in blocks:
            self._require_resource_quiet()
            prepared=self._read(lambda:prepare_document(self.store._conn,int(block[0]),generation))
            if prepared is None:continue
            packed,sha=prepared
            key=f'{self.settings.archive.prefix}/generations/{generation}/recovery/delivery-v1/{block[0]}-{sha}.json.zlib'
            receipt=self.backend.put_bytes(packed,key,sha256=sha)
            # A crash before this transaction leaves an unreferenced remote
            # object, never a false prune permission. Retry is content-addressed.
            with self.store.transaction() as conn:
                record_verified(conn,int(block[0]),generation,packed,receipt)
            uploaded+=1
        if blocks:
            # An old unverified canonical file must not starve independent
            # later blocks. Rotate a bounded scan; unresolved blocks remain.
            self.store._write("INSERT INTO runtime_metadata VALUES('crypto_recovery_archive_cursor',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_ts_ns=excluded.updated_ts_ns",
                (str(blocks[-1][0]),time.time_ns()))
        return {'candidates':len(blocks),'uploaded':uploaded,'batch_limit':limit,
                'scan_progressed':bool(blocks and not wrapped and int(blocks[-1][0])!=scan_after)}

    def _require_quiet(self) -> None:
        allowed,state=self._heavy_maintenance_allowed()
        if not allowed:raise MaintenanceDeferred(str(state['reason']))

    def _read(self, operation: Any) -> Any:
        self._require_resource_quiet()
        with self.store.maintenance_read_guard(self._require_resource_quiet):
            return operation()

    def _reclaim_with_query_budget(self,result:dict,**kwargs)->dict[str,int]:
        try:
            return self._reclaim_verified(**kwargs)
        except MaintenanceDeferred as exc:
            if str(exc) != 'maintenance_read_time_budget':
                raise
            # The interrupted SELECT precedes every deletion claim. Preserve
            # all local files, but do not starve uploads and verified recovery
            # behind one expensive cleanup query. Resource gates still apply.
            result['reclaim_deferred_reason']=str(exc)
            return {'files':0,'bytes':0}

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
        permitted,state=self._bulk_work_allowed('ARCHIVE_BACKUP_MAX_IO_PRESSURE')
        if not permitted:return dict(state,ran=False)
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
                backup_limit=float(self.settings.env.get('ARCHIVE_BACKUP_MAX_IO_PRESSURE',
                                   str(self.settings.archive.delivery_prune_max_io_pressure)))
                if io_pressure is None or io_pressure>backup_limit:
                    raise BackupDeferred('backup_io_pressure')
                if total and total-remaining>0 and now_monotonic-started_monotonic>900:
                    raise BackupDeferred('backup_time_budget')

            self.store.backup_to(temporary,pages=32,sleep_seconds=0.03,progress=progress)
            sha=sha256_file(temporary);backend=self.backend
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

    def _bulk_work_allowed(self, threshold_env: str) -> tuple[bool,dict[str,Any]]:
        # Only the opt-in relief profile needs a second gate. Do this before
        # opening SQLite, copying a database or walking the entire file tree.
        if 'ARCHIVE_RELIEF_MAX_IO_PRESSURE' not in self.settings.env:
            return True,{'reason':'legacy_resource_gate'}
        pressure=self._io_full_avg10()
        limit=float(self.settings.env.get(threshold_env,
                    str(self.settings.archive.delivery_prune_max_io_pressure)))
        allowed=pressure is not None and pressure<=limit
        return allowed,{'reason':'quiet' if allowed else 'bulk_io_busy',
                        'io_full_avg10':pressure,'max_io_pressure':limit}

    def _cleanup_orphans(self, *, force: bool) -> dict[str,Any]:
        """Preserve unregistered files remotely before reclaiming their space."""
        permitted,state=self._bulk_work_allowed('ARCHIVE_ORPHAN_MAX_IO_PRESSURE')
        if not permitted:return dict(state,files=0,bytes=0,ran=False)
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
                backend=self.backend
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
            path=Path(row['path']); sha=_catalogued_file_sha256(path,row); backend=self.backend; key=self._key(row);backend.put_file(path,key,sha256=sha)
            payload=json.dumps({'file_id':row['file_id'],'key':key,'bytes':path.stat().st_size,'sha256':sha,'rows':row['rows'],'partition':{k:row[k] for k in ('event_type','trading_date','provider','venue')}},sort_keys=True).encode(); side=self._sidecar(row); side_sha=__import__('hashlib').sha256(payload).hexdigest();backend.put_bytes(payload,side,sha256=side_sha)
            self.store.mark_remote_verified(row['file_id'],key,side,path.stat().st_size,sha)
        except Exception as exc:self.store.mark_upload_error(row['file_id'],str(exc));raise
        finally:self.store.release_lease(row['file_id'],self.owner)

    def plan_spool_gc(self, storage, *, producer, retained_prefix, reader_progress,
                      required_readers, lease_snapshot, remote_keys, expected_root,
                      expected_device, now_ns, retention_ns, max_segments, max_records,
                      max_bytes, dry_run=True, intent_path=None):
        """Read-only VN09 dry-run plan; never deletes, claims or uploads.

        The method requires the already-open LocalSpoolStorage owner and its
        exact RuntimeStore. It performs only bounded readback/verification and
        returns a JSON-compatible proposal whose candidates are empty whenever
        any proof conjunct is missing or changes.
        """
        if type(dry_run) is not bool or dry_run is not True:
            raise ValueError("spool GC plan requires dry_run exactly True")
        from .local_storage import LocalSpoolStorage
        from .market_spool import (RetainedPrefix, SpoolCursor, capture_retained_prefix,
                                   read_spool_records)
        if not isinstance(storage, LocalSpoolStorage):
            raise ValueError("storage must be the open LocalSpoolStorage owner")
        if not isinstance(retained_prefix, RetainedPrefix):
            raise ValueError("retained_prefix must be a factory-issued RetainedPrefix")
        if not isinstance(producer, str) or not producer:
            raise ValueError("producer identity is required")
        now_ns = _gc_positive(now_ns, "now_ns")
        retention_ns = _gc_positive(retention_ns, "retention_ns")
        max_segments = _gc_positive(max_segments, "max_segments", _GC_MAX_SEGMENTS)
        max_records = _gc_positive(max_records, "max_records", _GC_MAX_RECORDS)
        max_bytes = _gc_positive(max_bytes, "max_bytes", _GC_MAX_BYTES)
        expected_device = _gc_positive(expected_device, "expected_device")
        if self._store is None or self._store is not storage.store:
            raise ValueError("planner requires the same existing RuntimeStore as storage")
        if Path(self.settings.data_root).resolve() != Path(storage.settings.data_root).resolve():
            raise ValueError("planner and storage data_root differ")
        try:
            storage._check()
        except Exception as exc:
            return _gc_blocked(producer, retained_prefix, "storage_not_healthy",
                               type(exc).__name__)
        items = list(storage.plan.producers)
        if storage.plan.observation is not None:
            items.append(storage.plan.observation)
        matches = [item for item in items if item.producer == producer]
        if len(matches) != 1:
            raise ValueError("producer must exactly match one selected source")
        item = matches[0]
        if item.incarnation != retained_prefix.incarnation:
            raise ValueError("selected source incarnation differs from the proof")
        limits = dict(item.limits.reader_kwargs)
        for name in ("max_disk_bytes", "max_records", "max_frame_bytes", "max_header_bytes"):
            _gc_positive(limits.get(name), name)
        if producer in storage.source_failures:
            return _gc_blocked(producer, retained_prefix, "selected_source_quarantined")

        data_root = Path(storage.settings.data_root)
        root = Path(expected_root)
        if not root.is_absolute():
            raise ValueError("expected_root must be absolute")
        for candidate in (root, *root.parents):
            if not _gc_non_symlink(candidate):
                raise ValueError("expected_root and its ancestors must not be symlinks")
        if not _gc_under(Path(os.path.abspath(root)), Path(os.path.abspath(data_root))):
            raise ValueError("expected_root must be below settings.data_root")
        if Path(os.path.abspath(root)) != Path(os.path.abspath(item.spool_root)):
            raise ValueError("expected_root is not the selected spool root")
        try:
            root_stat = root.stat()
        except OSError as exc:
            return _gc_blocked(producer, retained_prefix, "root_stat_failed",
                               type(exc).__name__)
        if not root.is_dir():
            raise ValueError("expected_root must be a directory")
        if root_stat.st_dev != expected_device:
            raise ValueError("expected_device does not match the selected spool root")
        if type(root_stat.st_ino) is not int or root_stat.st_ino <= 0:
            return _gc_blocked(producer, retained_prefix, "root_identity_invalid")
        all_spool_roots = tuple(Path(source.spool_root) for source in items)
        intent_target = _gc_pin_intent_path(intent_path, data_root,
                                            all_spool_roots)

        if not isinstance(required_readers, tuple) or not 1 <= len(required_readers) <= 17:
            raise ValueError("required_readers must be a nonempty bounded tuple")
        if any(not isinstance(name, str) or not name for name in required_readers):
            raise ValueError("reader names must be nonempty strings")
        if len(set(required_readers)) != len(required_readers) or "storage" not in required_readers:
            raise ValueError("required_readers must be unique and include storage")
        if not isinstance(reader_progress, Mapping):
            raise ValueError("reader_progress must be an explicit mapping")
        if set(reader_progress) != set(required_readers):
            return _gc_blocked(producer, retained_prefix, "reader_inventory_mismatch")
        frozen_readers = {}
        for name in required_readers:
            cursor = reader_progress.get(name)
            if (not isinstance(cursor, SpoolCursor)
                    or cursor.producer != producer
                    or cursor.incarnation != retained_prefix.incarnation
                    or type(cursor.sequence) is not int
                    or not retained_prefix.base_sequence <= cursor.sequence
                    <= retained_prefix.durable_sequence):
                return _gc_blocked(producer, retained_prefix, "reader_progress_invalid",
                                   name)
            frozen_readers[name] = (cursor.producer, cursor.incarnation, cursor.sequence)
        try:
            actual_progress = storage._ledger.progress(producer, retained_prefix.incarnation)
        except Exception as exc:
            return _gc_blocked(producer, retained_prefix, "storage_progress_unavailable",
                               type(exc).__name__)
        if (actual_progress is None
                or int(actual_progress["sequence"]) != frozen_readers["storage"][2]
                or str(actual_progress["incarnation"]) != retained_prefix.incarnation):
            return _gc_blocked(producer, retained_prefix, "storage_progress_mismatch")

        def call_lease():
            if lease_snapshot is None or not callable(lease_snapshot):
                return None, "lease_snapshot_missing"
            if inspect.iscoroutinefunction(lease_snapshot):
                return None, "lease_snapshot_async"
            try:
                value = lease_snapshot()
            except Exception as exc:
                return None, "lease_snapshot_failed:" + type(exc).__name__
            if inspect.isawaitable(value):
                close = getattr(value, "close", None)
                if close is not None:
                    close()
                return None, "lease_snapshot_async"
            if not isinstance(value, (tuple, list)) or len(value) != 2:
                return None, "lease_snapshot_malformed"
            generation, leased = value
            if generation != retained_prefix.generation:
                return None, "lease_snapshot_generation_mismatch"
            if not isinstance(leased, (tuple, list)):
                return None, "lease_snapshot_malformed"
            normalized = []
            for segment_id in leased:
                if type(segment_id) is not int or not 0 < segment_id <= _GC_MAX_ABS:
                    return None, "lease_snapshot_malformed"
                normalized.append(segment_id)
            if len(set(normalized)) != len(normalized):
                return None, "lease_snapshot_malformed"
            return tuple(normalized), None

        first_lease, reason = call_lease()
        if reason is not None:
            return _gc_blocked(producer, retained_prefix, reason)
        if set(first_lease).intersection(range(1, retained_prefix.first_retained_segment)):
            return _gc_blocked(producer, retained_prefix, "candidate_segment_leased")

        try:
            captured = capture_retained_prefix(
                root, producer, retained_prefix.incarnation,
                first_retained_segment=retained_prefix.first_retained_segment,
                generation=retained_prefix.generation,
                consumer_cursors=retained_prefix.consumer_cursors,
                max_disk_bytes=limits["max_disk_bytes"],
                max_records=limits["max_records"],
                max_frame_bytes=limits["max_frame_bytes"],
                max_header_bytes=limits["max_header_bytes"])
        except Exception as exc:
            return _gc_blocked(producer, retained_prefix,
                               "retained_prefix_revalidation_failed",
                               type(exc).__name__ + ":" + str(exc))
        if captured.to_bytes() != retained_prefix.to_bytes():
            return _gc_blocked(producer, retained_prefix, "retained_prefix_changed")

        candidate_ids = list(range(1, retained_prefix.first_retained_segment))
        if len(candidate_ids) > max_segments:
            return _gc_blocked(producer, retained_prefix, "segment_budget_exceeded")
        if retained_prefix.base_sequence > max_records:
            return _gc_blocked(producer, retained_prefix, "record_budget_exceeded")
        plan_reserve = min(_GC_PLAN_MAX_BYTES, 16 * 1024 + max_segments * 1024)
        segments = []
        local_total = 0
        for segment_id in candidate_ids:
            path = root / f"{segment_id:020d}.sealed"
            if path.is_symlink() or not path.is_file():
                return _gc_blocked(producer, retained_prefix,
                                   "prefix_segment_missing", segment_id=segment_id)
            try:
                stat = path.stat()
            except OSError as exc:
                return _gc_blocked(producer, retained_prefix, "prefix_segment_stat_failed",
                                   type(exc).__name__, segment_id=segment_id)
            if stat.st_size <= 0:
                return _gc_blocked(producer, retained_prefix, "prefix_segment_empty",
                                   segment_id=segment_id)
            if stat.st_mtime_ns > now_ns:
                return _gc_blocked(producer, retained_prefix,
                                   "segment_timestamp_in_future", segment_id=segment_id)
            if now_ns - stat.st_mtime_ns < retention_ns:
                return _gc_blocked(producer, retained_prefix, "retention_not_met",
                                   segment_id=segment_id)
            local_total += stat.st_size
            if local_total + plan_reserve > max_bytes:
                return _gc_blocked(producer, retained_prefix,
                                   "read_budget_exceeded_before_hashing",
                                   segment_id=segment_id)
            segments.append({
                "segment_id": segment_id, "path": str(path),
                "bytes": stat.st_size, "inode": stat.st_ino,
                "device": stat.st_dev, "mtime_ns": stat.st_mtime_ns,
            })
        for segment in segments:
            try:
                digest, size = _gc_hash_file(Path(segment["path"]))
            except OSError as exc:
                return _gc_blocked(producer, retained_prefix, "prefix_segment_read_failed",
                                   type(exc).__name__, segment_id=segment["segment_id"])
            if size != segment["bytes"]:
                return _gc_blocked(producer, retained_prefix,
                                   "prefix_segment_changed_during_hash",
                                   segment_id=segment["segment_id"])
            segment["sha256"] = digest

        if self._store._conn.in_transaction:
            return _gc_blocked(producer, retained_prefix, "catalogue_pending_transaction")
        generation_row = self._store._conn.execute(
            "SELECT value FROM runtime_metadata WHERE key='collection_generation'").fetchone()
        if generation_row is None or not str(generation_row[0]):
            return _gc_blocked(producer, retained_prefix, "collection_generation_missing")
        catalogue_generation = str(generation_row[0])
        namespace = f"{self.settings.archive.prefix}/generations/{catalogue_generation}/"
        if not isinstance(remote_keys, Mapping):
            raise ValueError("remote_keys must be an explicit segment mapping")
        raw_snapshot, raw_reason, raw_segment = _gc_raw_key_snapshot(remote_keys)
        if raw_reason is not None:
            extra = {} if raw_segment is None else {"segment_id": raw_segment}
            return _gc_blocked(producer, retained_prefix, raw_reason, **extra)
        raw_keys = {}
        for segment_id in candidate_ids:
            if segment_id not in raw_snapshot:
                return _gc_blocked(producer, retained_prefix,
                                   "raw_segment_archive_missing", segment_id=segment_id)
            key = raw_snapshot[segment_id]
            if not key.startswith(namespace):
                return _gc_blocked(producer, retained_prefix,
                                   "raw_segment_key_namespace", segment_id=segment_id)
            raw_keys[segment_id] = key

        try:
            records = []
            through = SpoolCursor(producer, retained_prefix.incarnation,
                                  retained_prefix.base_sequence)
            for record in read_spool_records(
                    root, producer_id=producer, incarnation=retained_prefix.incarnation,
                    after=None, through=through, durable_only=True, **limits):
                if len(records) >= max_records:
                    return _gc_blocked(producer, retained_prefix, "record_budget_exceeded")
                records.append(record)
        except Exception as exc:
            return _gc_blocked(producer, retained_prefix, "prefix_read_failed",
                               type(exc).__name__ + ":" + str(exc))
        if len(records) != retained_prefix.base_sequence:
            return _gc_blocked(producer, retained_prefix, "prefix_record_count_mismatch")
        if records and (records[0].cursor.sequence != 1
                        or records[-1].cursor.sequence != retained_prefix.base_sequence):
            return _gc_blocked(producer, retained_prefix, "prefix_sequence_mismatch")
        if any(record.segment_id not in candidate_ids for record in records):
            return _gc_blocked(producer, retained_prefix, "prefix_record_outside_cut")
        if sum(record.frame_bytes for record in records) > max_bytes:
            return _gc_blocked(producer, retained_prefix, "record_byte_budget_exceeded")

        chunks = []
        chunk, chunk_bytes = [], 0
        for record in records:
            if chunk and (len(chunk) >= _GC_MAX_EVIDENCE_RECORDS
                          or chunk_bytes + len(record.payload) > storage.budgets.batch_bytes):
                chunks.append(tuple(chunk))
                chunk, chunk_bytes = [], 0
            chunk.append(record)
            chunk_bytes += len(record.payload)
        if chunk:
            chunks.append(tuple(chunk))
        if not chunks:
            chunks.append(())
        evidences = []
        try:
            for chunk in chunks:
                evidences.append(storage.spool_gc_evidence(producer, chunk))
        except Exception as exc:
            return _gc_blocked(producer, retained_prefix, "storage_evidence_failed",
                               type(exc).__name__ + ":" + str(exc))

        generations = {(evidence["facts_generation"], evidence["catalogue_generation"])
                       for evidence in evidences}
        if len(generations) != 1:
            return _gc_blocked(producer, retained_prefix, "generation_changed")
        facts_generation, evidence_catalogue = next(iter(generations))
        if evidence_catalogue != catalogue_generation:
            return _gc_blocked(producer, retained_prefix, "catalogue_generation_changed")
        evidence_progress = evidences[0]["progress"]
        evidence_count = 0
        record_hasher = hashlib.sha256()
        manifest_snapshots = {}
        for evidence in evidences:
            if evidence["progress"] != evidence_progress:
                return _gc_blocked(producer, retained_prefix, "progress_changed")
            evidence_count += len(evidence["records"])
            for row in evidence["records"]:
                record_hasher.update(_gc_canonical(row))
                record_hasher.update(b"\n")
                if row["manifest"] is not None:
                    manifest_snapshots.setdefault(row["manifest"]["file_id"],
                                                  row["manifest"])
        if evidence_count != len(records):
            return _gc_blocked(producer, retained_prefix, "evidence_count_mismatch")
        record_chain_sha256 = record_hasher.hexdigest()

        def load_manifest(file_id):
            row = self._store._conn.execute(
                "SELECT * FROM parquet_manifest WHERE file_id=?", (file_id,)).fetchone()
            return dict(row) if row is not None else None

        terminals = {}
        visited_rows = {}
        for file_id, snapshot in manifest_snapshots.items():
            fresh = load_manifest(file_id)
            if fresh is None:
                return _gc_blocked(producer, retained_prefix, "manifest_missing",
                                   file_id=file_id)
            if fresh != snapshot:
                return _gc_blocked(producer, retained_prefix, "manifest_changed",
                                   file_id=file_id)
            current = fresh
            seen_links = set()
            links = 0
            while True:
                current_id = current.get("file_id")
                if not isinstance(current_id, str) or not current_id:
                    return _gc_blocked(producer, retained_prefix, "manifest_identity_invalid")
                if current_id in seen_links:
                    return _gc_blocked(producer, retained_prefix, "compaction_cycle")
                seen_links.add(current_id)
                visited_rows[current_id] = current
                if not _gc_manifest_lease_ok(current, now_ns):
                    return _gc_blocked(producer, retained_prefix,
                                       "unexpired_or_uncertain_lease", file_id=current_id)
                successor = current.get("superseded_by")
                if successor:
                    if links >= _GC_MAX_COMPACTION_LINKS:
                        return _gc_blocked(producer, retained_prefix,
                                           "compaction_chain_too_long")
                    links += 1
                    next_row = load_manifest(successor)
                    if next_row is None:
                        return _gc_blocked(producer, retained_prefix,
                                           "compaction_link_missing", file_id=current_id)
                    try:
                        compacted_from = json.loads(next_row.get("compacted_from") or "[]")
                    except (TypeError, ValueError):
                        compacted_from = None
                    if (not isinstance(compacted_from, list)
                            or current_id not in compacted_from):
                        return _gc_blocked(producer, retained_prefix,
                                           "compaction_membership_mismatch",
                                           file_id=current_id)
                    current = next_row
                    continue
                if current.get("lifecycle_state") != "active":
                    return _gc_blocked(producer, retained_prefix,
                                       "terminal_manifest_not_active", file_id=current_id)
                terminals[current_id] = current
                break

        terminal_sidecars = {}
        for file_id, terminal in terminals.items():
            digest = terminal.get("file_sha256")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                return _gc_blocked(producer, retained_prefix,
                                   "terminal_manifest_digest", file_id=file_id)
            size = terminal.get("byte_size")
            if type(size) is not int or size <= 0 or size > _GC_MAX_ABS:
                return _gc_blocked(producer, retained_prefix,
                                   "terminal_manifest_size", file_id=file_id)
            rows = terminal.get("rows")
            if type(rows) is not int or rows < 0:
                return _gc_blocked(producer, retained_prefix,
                                   "terminal_manifest_rows", file_id=file_id)
            if terminal.get("upload_status") != "remote_verified":
                return _gc_blocked(producer, retained_prefix,
                                   "terminal_manifest_not_remote_verified", file_id=file_id)
            remote_key = terminal.get("remote_key")
            remote_sidecar = terminal.get("remote_manifest_key")
            if (not isinstance(remote_key, str) or not remote_key
                    or not isinstance(remote_sidecar, str) or not remote_sidecar):
                return _gc_blocked(producer, retained_prefix,
                                   "terminal_remote_key_missing", file_id=file_id)
            if (not remote_key.startswith(namespace)
                    or not remote_sidecar.startswith(namespace)):
                return _gc_blocked(producer, retained_prefix,
                                   "terminal_remote_key_namespace", file_id=file_id)
            expected_sidecar = (f"{self.settings.archive.prefix}/generations/"
                                f"{catalogue_generation}/manifests/{file_id}.json")
            if remote_sidecar != expected_sidecar:
                return _gc_blocked(producer, retained_prefix,
                                   "terminal_sidecar_key_mismatch", file_id=file_id)
            sidecar = json.dumps({
                "file_id": terminal["file_id"], "key": remote_key,
                "bytes": size, "sha256": digest, "rows": rows,
                "partition": {name: terminal[name] for name in
                              ("event_type", "trading_date", "provider", "venue")},
            }, sort_keys=True).encode()
            terminal_sidecars[file_id] = (
                sidecar, hashlib.sha256(sidecar).hexdigest())

        remote_bytes = local_total
        for file_id, terminal in terminals.items():
            remote_bytes += terminal["byte_size"] + len(terminal_sidecars[file_id][0])
        if 2 * local_total + remote_bytes + plan_reserve > max_bytes:
            return _gc_blocked(producer, retained_prefix, "read_budget_exceeded")

        try:
            backend = self.backend
            for segment in segments:
                backend.verify(raw_keys[segment["segment_id"]],
                               size_bytes=segment["bytes"],
                               sha256=segment["sha256"])
            for file_id, terminal in terminals.items():
                sidecar, sidecar_sha256 = terminal_sidecars[file_id]
                backend.verify(terminal["remote_key"],
                               size_bytes=terminal["byte_size"],
                               sha256=terminal["file_sha256"])
                backend.verify(terminal["remote_manifest_key"],
                               size_bytes=len(sidecar), sha256=sidecar_sha256)
        except Exception as exc:
            return _gc_blocked(producer, retained_prefix, "remote_verification_failed",
                               type(exc).__name__ + ":" + str(exc))

        # Final value revalidation after all callbacks/readbacks.
        if set(reader_progress) != set(required_readers):
            return _gc_blocked(producer, retained_prefix, "reader_inventory_changed")
        for name in required_readers:
            cursor = reader_progress.get(name)
            if (not isinstance(cursor, SpoolCursor)
                    or (cursor.producer, cursor.incarnation, cursor.sequence)
                    != frozen_readers[name]):
                return _gc_blocked(producer, retained_prefix, "reader_progress_changed",
                                   name)
        final_lease, reason = call_lease()
        if reason is not None:
            return _gc_blocked(producer, retained_prefix, reason)
        if final_lease != first_lease:
            return _gc_blocked(producer, retained_prefix, "lease_snapshot_changed")
        if set(final_lease).intersection(candidate_ids):
            return _gc_blocked(producer, retained_prefix, "candidate_segment_leased")
        try:
            final_root_stat = root.stat()
        except OSError as exc:
            return _gc_blocked(producer, retained_prefix, "root_stat_changed",
                               type(exc).__name__)
        if ((final_root_stat.st_dev, final_root_stat.st_ino, final_root_stat.st_mtime_ns)
                != (root_stat.st_dev, root_stat.st_ino, root_stat.st_mtime_ns)):
            return _gc_blocked(producer, retained_prefix, "root_identity_changed")
        try:
            final_captured = capture_retained_prefix(
                root, producer, retained_prefix.incarnation,
                first_retained_segment=retained_prefix.first_retained_segment,
                generation=retained_prefix.generation,
                consumer_cursors=retained_prefix.consumer_cursors,
                max_disk_bytes=limits["max_disk_bytes"],
                max_records=limits["max_records"],
                max_frame_bytes=limits["max_frame_bytes"],
                max_header_bytes=limits["max_header_bytes"])
        except Exception as exc:
            return _gc_blocked(producer, retained_prefix, "source_changed",
                               type(exc).__name__ + ":" + str(exc))
        if final_captured.to_bytes() != retained_prefix.to_bytes():
            return _gc_blocked(producer, retained_prefix, "retained_prefix_changed")
        for segment in segments:
            path = Path(segment["path"])
            try:
                stat = path.stat()
                digest, size = _gc_hash_file(path)
            except OSError as exc:
                return _gc_blocked(producer, retained_prefix, "source_changed",
                                   type(exc).__name__, segment_id=segment["segment_id"])
            if ((stat.st_size, stat.st_ino, stat.st_dev, stat.st_mtime_ns, size, digest)
                    != (segment["bytes"], segment["inode"], segment["device"],
                        segment["mtime_ns"], segment["bytes"], segment["sha256"])):
                return _gc_blocked(producer, retained_prefix, "segment_changed",
                                   segment_id=segment["segment_id"])
        try:
            final_evidences = [storage.spool_gc_evidence(producer, chunk)
                               for chunk in chunks]
        except Exception as exc:
            return _gc_blocked(producer, retained_prefix, "storage_evidence_changed",
                               type(exc).__name__ + ":" + str(exc))
        for before, after in zip(evidences, final_evidences):
            if _gc_canonical(before) != _gc_canonical(after):
                return _gc_blocked(producer, retained_prefix,
                                   "storage_evidence_changed")
        for file_id, snapshot in visited_rows.items():
            if load_manifest(file_id) != snapshot:
                return _gc_blocked(producer, retained_prefix, "manifest_changed",
                                   file_id=file_id)
        if not isinstance(remote_keys, Mapping):
            return _gc_blocked(producer, retained_prefix,
                               "raw_segment_mapping_changed")
        final_raw_snapshot, final_raw_reason, _ = _gc_raw_key_snapshot(remote_keys)
        if (final_raw_reason is not None
                or final_raw_snapshot != raw_snapshot):
            return _gc_blocked(producer, retained_prefix,
                               "raw_segment_mapping_changed")

        eligible = [{
            "segment_id": segment["segment_id"], "path": segment["path"],
            "bytes": segment["bytes"], "sha256": segment["sha256"],
            "inode": segment["inode"], "device": segment["device"],
            "mtime_ns": segment["mtime_ns"],
        } for segment in segments]
        body = {
            "dry_run": True, "deleted": 0, "executable": False,
            "producer": producer,
            "incarnation": retained_prefix.incarnation,
            "generation": retained_prefix.generation,
            "root": {"path": str(root), "device": root_stat.st_dev,
                     "inode": root_stat.st_ino},
            "proof": {
                "sha256": retained_prefix.sha256,
                "generation": retained_prefix.generation,
                "checkpoint_sha256": retained_prefix.checkpoint_sha256,
                "prefix_chain_sha256": retained_prefix.prefix_chain_sha256,
                "first_retained_segment": retained_prefix.first_retained_segment,
                "base_sequence": retained_prefix.base_sequence,
                "durable_sequence": retained_prefix.durable_sequence,
            },
            "checkpoint": {"sha256": retained_prefix.checkpoint_sha256,
                           "durable_sequence": retained_prefix.durable_sequence},
            "catalogue_generation": catalogue_generation,
            "facts_generation": facts_generation,
            "readers": {name: {"producer": frozen_readers[name][0],
                               "incarnation": frozen_readers[name][1],
                               "sequence": frozen_readers[name][2]}
                        for name in required_readers},
            "evidence": {
                "record_count": len(records),
                "record_bytes": sum(record.frame_bytes for record in records),
                "record_chain_sha256": record_chain_sha256,
                "segments": [{name: segment[name] for name in
                              ("segment_id", "path", "bytes", "sha256",
                               "inode", "device", "mtime_ns")}
                             for segment in segments],
                "manifests": [{
                    "file_id": terminal["file_id"],
                    "remote_key": terminal["remote_key"],
                    "remote_manifest_key": terminal["remote_manifest_key"],
                    "file_sha256": terminal["file_sha256"],
                    "byte_size": terminal["byte_size"],
                    "rows": terminal["rows"],
                } for terminal in terminals.values()],
            },
            "raw_archive": {
                "catalogue_generation": catalogue_generation,
                "full_readback_verified": True,
                "segments": [{
                    "segment_id": segment["segment_id"],
                    "remote_key": raw_keys[segment["segment_id"]],
                    "bytes": segment["bytes"],
                    "sha256": segment["sha256"],
                } for segment in segments],
            },
            "eligible": eligible,
            "blocked": [],
        }
        final_plan = _gc_finalize(body)
        if intent_target is not None:
            try:
                intent_body = dict(body)
                intent_body.update({
                    "status": "PROPOSED_DRY_RUN",
                    "executable": False,
                    "requires_revalidation": True,
                    "plan_sha256": final_plan["plan_sha256"],
                })
                intent_document = {
                    "format": "vibetrading-spool-gc-intent",
                    "version": 1,
                    "sha256": hashlib.sha256(_gc_canonical(intent_body)).hexdigest(),
                    "body": intent_body,
                }
                payload = _gc_canonical(intent_document)
                _gc_publish_intent(intent_target, payload, _GC_PLAN_MAX_BYTES)
            except Exception as exc:
                return _gc_blocked(producer, retained_prefix,
                                   "intent_publication_failed",
                                   type(exc).__name__ + ":" + str(exc),
                                   catalogue_generation=catalogue_generation,
                                   facts_generation=facts_generation)
        return final_plan


def archive_check(settings:RuntimeSettings|None=None)->dict[str,Any]:return ArchiveMaintenance(settings).check()
