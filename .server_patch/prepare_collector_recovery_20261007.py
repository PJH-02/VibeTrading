import hashlib,json,pathlib,tarfile,tempfile
r=pathlib.Path(__file__).resolve().parents[1]
names=['vibetrading/bus.py','vibetrading/runtime.py','vibetrading/providers/toss.py','vibetrading/compactor.py','vibetrading/sqlite_store.py']
names+=['tests/'+n+'.py' for n in ('test_collector_recovery_20261007','test_toss_daymarket','test_exchange_expansion','test_durable_pipeline_guards','test_storage_pipeline','test_maintenance_yield','test_maintenance_lock_retry','test_crypto_storage_readiness','test_event_filter_capacity','test_archive_reclaim_scaling')]
names+=['scripts/migrate_crypto_delivery_ledger.py','scripts/prepare_crypto_event_index.py']
files={n:(r/n).read_bytes() for n in names};hashes={n:hashlib.sha256(v).hexdigest() for n,v in files.items()};revision='collector-recovery-'+hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:16]
files['manifest.json']=json.dumps({'revision':revision,'files':hashes},indent=2).encode()
files['Dockerfile']=b'''ARG BASE
FROM ${BASE}
USER root
COPY --chown=10001:10001 vibetrading/bus.py vibetrading/runtime.py vibetrading/compactor.py vibetrading/sqlite_store.py /app/vibetrading/
COPY --chown=10001:10001 vibetrading/providers/toss.py /app/vibetrading/providers/toss.py
ARG REVISION
LABEL org.opencontainers.image.revision=${REVISION}
ENV VIBETRADING_BUILD_REVISION=${REVISION}
USER vibetrading
'''
with tempfile.TemporaryDirectory() as directory:
    p=pathlib.Path(directory)
    for n,b in files.items():f=p/n;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b)
    with tarfile.open(r/'.server_patch/collector-recovery-20261007.tar.gz','w:gz') as t:
        for n in files:t.add(p/n,arcname=n,recursive=False)
print(revision)
