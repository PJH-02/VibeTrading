import hashlib,json,pathlib,tarfile,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
names=['vibetrading/sqlite_store.py','vibetrading/event_index_runs.py','tests/test_event_filter_capacity.py',
       'tests/test_event_index_runs.py','tests/test_event_index_runs_storage.py','tests/test_crypto_storage_readiness.py',
       'tests/test_exchange_expansion.py','tests/test_durable_pipeline_guards.py','tests/test_split_state_disk_guard.py']
files={p:(ROOT/p).read_bytes() for p in names}
manifest={p:hashlib.sha256(v).hexdigest() for p,v in files.items()}
revision='capacity-'+hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:20]
files['manifest.json']=json.dumps({'revision':revision,'files':manifest},indent=2).encode()
files['Dockerfile']=b'''ARG BASE
FROM ${BASE}
USER root
COPY --chown=10001:10001 vibetrading/sqlite_store.py vibetrading/event_index_runs.py /app/vibetrading/
ARG REVISION
LABEL org.opencontainers.image.revision=${REVISION}
ENV VIBETRADING_BUILD_REVISION=${REVISION} PYTHONDONTWRITEBYTECODE=1
USER vibetrading
'''
output=ROOT/'.server_patch/capacity-20261006.tar.gz'
with tempfile.TemporaryDirectory() as temp:
    temp=pathlib.Path(temp)
    for name,data in files.items():
        p=temp/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    with tarfile.open(output,'w:gz') as tar:
        for name in files:tar.add(temp/name,arcname=name,recursive=False)
print(json.dumps({'revision':revision,'bytes':output.stat().st_size}))
