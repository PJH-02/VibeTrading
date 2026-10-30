"""Atomically install ticker CSVs and preserve the former strategy files."""
import hashlib
import json
import os
import tarfile
import time
from pathlib import Path

base = Path('/srv/vibetrading-data/runtime_data/state/etf-mock')
destination = base/'history'
staging = base/('history-install-'+str(time.time_ns()))
staging.mkdir()
with tarfile.open('/tmp/etf-reference-csv-20261013.tar.gz') as archive:
    assert all(m.isfile() and (staging/m.name).resolve().is_relative_to(staging.resolve()) for m in archive.getmembers())
    archive.extractall(staging,filter='data')
manifest = json.loads((staging/'manifest.json').read_text())
assert len(manifest['files'])==10
for row in manifest['files'].values():
    path = staging/row['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
    os.chown(path,10001,10001)
    os.chmod(path,0o644)
for path in [staging,staging/'raw']:
    os.chown(path,10001,10001)
    os.chmod(path,0o755)
os.chown(staging/'manifest.json',10001,10001)
os.chmod(staging/'manifest.json',0o644)
backup = base/('history-before-ticker-'+str(time.time_ns()))
if destination.exists(): os.rename(destination,backup)
os.rename(staging,destination)
Path('/opt/vibetrading/releases/scope-20261013/csv-import.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps({'files':len(manifest['files']),'bytes':sum(r['bytes'] for r in manifest['files'].values()),'backup':str(backup)}))
