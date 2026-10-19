import hashlib, json, pathlib, tarfile, tempfile

root=pathlib.Path(__file__).resolve().parents[1]
paths=[root/'main.py',root/'requirements.txt']
for name in ('vibetrading','config','strategies','tests'):
    paths.extend(p for p in (root/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.yaml','.yml','.json'})
payload={p.relative_to(root).as_posix():p.read_bytes() for p in paths}
dockerfile='''ARG BASE
FROM ${BASE}
USER root
COPY wheels /tmp/expansion-wheels
RUN pip install --no-cache-dir --no-index --find-links=/tmp/expansion-wheels eth-account==0.13.7 cryptography==46.0.3 && rm -rf /tmp/expansion-wheels
COPY --chown=10001:10001 vibetrading /app/vibetrading
COPY --chown=10001:10001 config /app/config
COPY --chown=10001:10001 strategies /app/strategies
COPY --chown=10001:10001 main.py /app/main.py
ARG REVISION
ARG CONFIG_HASH
LABEL org.opencontainers.image.revision=${REVISION} org.vibetrading.config-hash=${CONFIG_HASH}
ENV VIBETRADING_BUILD_REVISION=${REVISION} VIBETRADING_BUILD_CONFIG_HASH=${CONFIG_HASH} PYTHONDONTWRITEBYTECODE=1
USER vibetrading
'''
payload['Dockerfile.expansion']=dockerfile.encode()
manifest={k:hashlib.sha256(v).hexdigest() for k,v in sorted(payload.items())}
source_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
config_hash=hashlib.sha256(json.dumps({k:v for k,v in manifest.items() if k.startswith('config/')},sort_keys=True).encode()).hexdigest()
payload['bundle-manifest.json']=(json.dumps({'files':manifest,'source_hash':source_hash,'config_hash':config_hash,'revision':'expansion-'+source_hash[:20]},indent=2)+'\n').encode()
out=root/'.server_patch/expansion-20261003.tar.gz'
with tempfile.TemporaryDirectory() as temp:
    temp=pathlib.Path(temp)
    for name,content in payload.items():
        p=temp/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(content)
    with tarfile.open(out,'w:gz') as tar:
        for name in sorted(payload): tar.add(temp/name,arcname=name,recursive=False)
print(json.dumps({'path':str(out),'bytes':out.stat().st_size,'revision':'expansion-'+source_hash[:20]}))
