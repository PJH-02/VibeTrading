"""Package source for recovery; never include credentials or runtime data."""
import hashlib
import io
import json
import tarfile
from pathlib import Path

root=Path(__file__).resolve().parents[1]
files={}
for directory in ['vibetrading','config','strategy','strategies','tests','deploy']:
    for path in (root/directory).rglob('*'):
        if path.is_file() and path.suffix in {'.py','.yaml','.yml','.json','.conf','.sh','.service'} and '__pycache__' not in path.parts:
            files[path.relative_to(root).as_posix()]=path.read_bytes()
files['main.py']=(root/'main.py').read_bytes()
files['Dockerfile']=b'''FROM vibetrading-capacity-maintenance-base:20261006
USER root
COPY --chown=10001:10001 vibetrading/ /app/vibetrading/
COPY --chown=10001:10001 config/ /app/config/
COPY --chown=10001:10001 strategy/ /app/strategy/
COPY --chown=10001:10001 strategies/ /app/strategies/
COPY --chown=10001:10001 main.py /app/main.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER vibetrading
'''
hashes={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
revision='telegram-recovery-'+hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:16]
files['manifest.json']=json.dumps({'revision':revision,'files':hashes},indent=2).encode()
path=root/'.server_patch/telegram-recovery-20261014.tar.gz'
with tarfile.open(path,'w:gz') as archive:
    for name,data in files.items():
        entry=tarfile.TarInfo(name); entry.size=len(data)
        archive.addfile(entry,io.BytesIO(data))
print(json.dumps({'revision':revision,'files':len(hashes),'bytes':path.stat().st_size}))
