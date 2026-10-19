"""Build the isolated collection/ETF image and run tests before activation."""
import hashlib
import json
import subprocess
import tarfile
import time
from pathlib import Path

root = Path('/opt/vibetrading/releases/scope-20261013')
root.mkdir(exist_ok=True)
with tarfile.open('/tmp/etf-scope-20261013.tar.gz') as archive:
    assert all(m.isfile() and (root/m.name).resolve().is_relative_to(root) for m in archive.getmembers())
    archive.extractall(root,filter='data')
manifest = json.loads((root/'manifest.json').read_text())
assert all(hashlib.sha256((root/n).read_bytes()).hexdigest()==h for n,h in manifest['files'].items())
base = 'sha256:279ad22189ef35848f19f980143e9597debea5410500ad9aae5cf8ae4be4d83e'
def run(*args):
    return subprocess.check_output(args,text=True).strip()
run('docker','tag',base,'vibetrading-etf-base:20261013')
run('docker','build','--network=none','--pull=false','--memory=256m','--cpu-quota=50000',
    '--build-arg','BASE=vibetrading-etf-base:20261013','--build-arg','REVISION='+manifest['revision'],
    '-t','vibetrading:'+manifest['revision'],str(root))
image = run('docker','image','inspect','--format','{{.Id}}','vibetrading:'+manifest['revision'])
tests = run('docker','run','--rm','--network=none','--memory=512m','--cpus=0.5',
    '-v',str(root/'tests')+':/app/tests:ro',image,'python','-m','pytest','-p','no:cacheprovider',
    *['/app/'+n for n in manifest['files'] if n.startswith('tests/')],'-q','--tb=short')
receipt = {'image':image,'revision':manifest['revision'],'tests':tests,'built_ns':time.time_ns()}
(root/'build.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
