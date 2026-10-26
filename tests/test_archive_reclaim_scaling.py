import time
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from runtime.settings import RuntimeSettings
from storage.sqlite_store import RuntimeStore, MaintenanceDeferred
from storage.archive import ArchiveMaintenance

def store_for(tmp_path):
    p=tmp_path/'.env';p.write_text(f'VIBETRADING_DATA_ROOT={tmp_path}\n',encoding='utf-8');return RuntimeStore(RuntimeSettings.from_env(p))

def insert(store,fid,*,local=1,status='remote_verified',verified=1,sidecar='remote-sidecar',state='active',replacement=None,lease=None):
    store._conn.execute('INSERT INTO parquet_manifest(path,file_id,event_type,trading_date,venue,rows,checksum,created_ts_ns,local_present,upload_status,verified_ts_ns,remote_manifest_key,lifecycle_state,superseded_by,lease_until_ts_ns) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,fid,'trade','2026-09-01','kis',1,'sha',1,local,status,verified,sidecar,state,replacement,lease))

def test_reclaim_work_scales_with_local_candidates_not_remote_history(tmp_path):
    s=store_for(tmp_path)
    with s.transaction():
        for n in range(10000):insert(s,f'old-{n}',local=0)
        insert(s,'still-local')
    ticks=[];s._conn.set_progress_handler(lambda:ticks.append(1) or 0,100)
    rows=s.deletion_candidates(older_than_ns=2,force=False,limit=1)
    s._conn.set_progress_handler(None,0)
    assert [r['file_id'] for r in rows]==['still-local']
    assert len(ticks)*100<10000
    s.close()


def test_upload_and_compaction_discovery_skip_remote_history(tmp_path):
    s=store_for(tmp_path)
    with s.transaction():
        for n in range(10000):insert(s,f'old-{n}',local=0)
        insert(s,'pending',status='local_only')
        insert(s,'retry',status='upload_failed')
        s._conn.execute("UPDATE parquet_manifest SET next_retry_ts_ns=? WHERE file_id='retry'",(time.time_ns()+60_000_000_000,))
    ticks=[];s._conn.set_progress_handler(lambda:ticks.append(1) or 0,100)
    assert [r['file_id'] for r in s.archive_candidates(older_than_ns=2)]==['pending']
    assert [r['file_id'] for r in s.compaction_candidates(older_than_ns=2)]==['pending']
    s._conn.set_progress_handler(None,0)
    assert len(ticks)*100<1000
    s.close()

def test_forward_chain_preserves_verified_age_sidecar_lease_and_cycle_guards(tmp_path):
    s=store_for(tmp_path)
    insert(s,'verified',local=0)
    insert(s,'middle',local=0,status='not_required',sidecar=None,state='superseded',replacement='verified')
    insert(s,'source',status='not_required',sidecar=None,state='superseded',replacement='middle')
    insert(s,'no-sidecar',sidecar=None)
    insert(s,'too-young',verified=100)
    insert(s,'leased',lease=time.time_ns()+60_000_000_000)
    insert(s,'dangling',status='not_required',sidecar=None,state='superseded',replacement='absent')
    insert(s,'cycle1',status='not_required',sidecar=None,state='superseded',replacement='cycle2')
    insert(s,'cycle2',status='not_required',sidecar=None,state='superseded',replacement='cycle1')
    assert {r['file_id'] for r in s.deletion_candidates(older_than_ns=2,force=False,limit=32)}=={'source'}
    s.close()

def test_cleanup_query_budget_does_not_bypass_resource_guards_or_starve_other_stages():
    a=ArchiveMaintenance(SimpleNamespace());result={}
    with patch.object(a,'_reclaim_verified',side_effect=MaintenanceDeferred('maintenance_read_time_budget')):
        assert a._reclaim_with_query_budget(result,force=False,older_than_ns=1,limit=32)=={'files':0,'bytes':0}
        assert result['reclaim_deferred_reason']=='maintenance_read_time_budget'
    with patch.object(a,'_reclaim_verified',side_effect=MaintenanceDeferred('storage_or_io_busy')):
        with pytest.raises(MaintenanceDeferred):a._reclaim_with_query_budget({},force=False,older_than_ns=1,limit=32)
