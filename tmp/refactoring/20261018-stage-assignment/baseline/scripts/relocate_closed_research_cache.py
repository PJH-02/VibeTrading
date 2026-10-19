"""Restore OS disk headroom while preserving closed research paths and bytes."""
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

source = Path('/opt/vibetrading/experiments/fv-pilot-20261004')
destination = Path('/srv/vibetrading-capacity/experiments/fv-pilot-20261004')
assert source.resolve()==source and destination.resolve().is_relative_to(Path('/srv/vibetrading-capacity'))
assert os.path.ismount('/srv/vibetrading-capacity')
assert not source.is_symlink()
assert not destination.exists()
processes = subprocess.check_output(['ps','-eo','pid,args'],text=True)
assert not any(('python' in line and ('vibetrading.fv_pilot' in line or 'run_fv_pilot.py' in line or 'collect_fv_mainnet.py' in line)) for line in processes.splitlines())
def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        while chunk:=f.read(1024*1024):
            digest.update(chunk)
    return digest.hexdigest()
before = {p.relative_to(source).as_posix():checksum(p) for p in source.rglob('*') if p.is_file()}
destination.parent.mkdir(exist_ok=True)
shutil.copytree(source,destination,symlinks=True)
assert all(checksum(destination/name)==sha and checksum(source/name)==sha for name,sha in before.items())
backup = source.with_name(source.name+'.relocating-20261013')
assert not backup.exists()
source.rename(backup)
try:
    source.symlink_to(destination,target_is_directory=True)
except BaseException:
    backup.rename(source)
    raise
assert source.resolve()==destination.resolve()
assert backup.resolve().is_relative_to(Path('/opt/vibetrading/experiments'))
shutil.rmtree(backup)
receipt = {'source':str(source),'destination':str(destination),'files':len(before),'bytes':sum((destination/name).stat().st_size for name in before),
    'verified_sha256':True,'completed_ns':time.time_ns()}
Path('/opt/vibetrading/releases/scope-20261013/os-space-relocation.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
