import hashlib,json,pathlib,subprocess,tarfile,time
R=pathlib.Path('/opt/vibetrading/releases/collector-recovery-20261007');R.mkdir(exist_ok=True)
BASE='sha256:3f0a8ade56fbfba6fbf5b9304b54d7d45b35dbdf7319f2acd1f37fdf6eeec2fe'
def run(*args):return subprocess.check_output(args,text=True).strip()
with tarfile.open('/tmp/collector-recovery-20261007.tar.gz') as t:
    assert all(m.isfile() and (R/m.name).resolve().is_relative_to(R) for m in t.getmembers());t.extractall(R,filter='data')
m=json.loads((R/'manifest.json').read_text());assert all(hashlib.sha256((R/n).read_bytes()).hexdigest()==h for n,h in m['files'].items())
run('docker','tag',BASE,'vibetrading-recovery-base:20261007')
run('docker','build','--network=none','--pull=false','--memory=256m','--cpu-quota=50000','--build-arg','BASE=vibetrading-recovery-base:20261007','--build-arg','REVISION='+m['revision'],'-t','vibetrading:'+m['revision'],str(R))
image=run('docker','image','inspect','--format','{{.Id}}','vibetrading:'+m['revision'])
tests=run('docker','run','--rm','--network=none','--memory=512m','--memory-swap','512m','--cpus=0.5','-v',str(R/'tests')+':/app/tests:ro','-v',str(R/'scripts')+':/app/scripts:ro',image,'python','-m','pytest','-p','no:cacheprovider',*[ '/app/'+n for n in m['files'] if n.startswith('tests/')],'-q');assert '95 passed' in tests
probe=pathlib.Path('/srv/vibetrading-capacity/incident-20261007/compactor-new');probe.mkdir(exist_ok=True);probe.chmod(0o777)
p=run('docker','run','--name','vibetrading-compactor-new-probe-20261007','--network','none','--memory','512m','--memory-swap','512m','--cpus','0.5','-e','PYTHONPATH=/app','-v','/tmp/compactor_probe_20261007.py:/probe/probe.py:ro','-v',str(probe)+':/probe/work',image,'python','/probe/probe.py')
receipt={'built_ts_ns':time.time_ns(),'image':image,'base':BASE,'revision':m['revision'],'tests':tests,'compactor_probe':json.loads(p),'old_probe':json.loads(run('docker','inspect','--format','{{json .State}}','vibetrading-compactor-old-probe-20261007'))}
assert receipt['compactor_probe']['peak_rss_mib']<300
assert receipt['old_probe']['ExitCode']==137 and receipt['old_probe']['OOMKilled']
(R/'build.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)
