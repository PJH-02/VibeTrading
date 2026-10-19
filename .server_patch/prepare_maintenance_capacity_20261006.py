import hashlib,json,pathlib,tarfile,tempfile
r=pathlib.Path(__file__).resolve().parents[1];names=['vibetrading/runtime.py','vibetrading/sqlite_store.py','vibetrading/archive.py']+['tests/'+n+'.py' for n in ('test_archive_reclaim_scaling','test_storage_pipeline','test_maintenance_yield','test_maintenance_lock_retry','test_event_filter_capacity','test_event_index_runs','test_event_index_runs_storage','test_crypto_storage_readiness','test_exchange_expansion','test_durable_pipeline_guards','test_split_state_disk_guard')]+['scripts/prepare_crypto_event_index.py','scripts/migrate_crypto_delivery_ledger.py'];files={n:(r/n).read_bytes() for n in names};hashes={n:hashlib.sha256(v).hexdigest() for n,v in files.items()};rev='maintenance-'+hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:20]
files['manifest.json']=json.dumps({'revision':rev,'files':hashes},indent=2).encode();files['Dockerfile']=b'''ARG BASE
FROM ${BASE}
USER root
COPY --chown=10001:10001 vibetrading/runtime.py vibetrading/sqlite_store.py vibetrading/archive.py /app/vibetrading/
ARG REVISION
LABEL org.opencontainers.image.revision=${REVISION}
ENV VIBETRADING_BUILD_REVISION=${REVISION}
USER vibetrading
'''
with tempfile.TemporaryDirectory() as temp:
 p=pathlib.Path(temp)
 for n,b in files.items():f=p/n;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b)
 with tarfile.open(r/'.server_patch/maintenance-capacity-20261006.tar.gz','w:gz') as t:
  for n in files:t.add(p/n,arcname=n,recursive=False)
print(rev)
