"""Stage a source-only review, then network-disabled Linux tests and a capped benchmark."""
import argparse,hashlib,json,subprocess,tarfile
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='root@49.50.141.106');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    root=Path(__file__).resolve().parents[1];args.output.mkdir(parents=True,exist_ok=True)
    files=sorted(Path('vibetrading').rglob('*.py'))+sorted(Path('strategy').glob('*.py'))
    files += [Path('scripts/benchmark_mm_state.py'),Path('pytest.ini')]
    test_names=['test_mm_state_revision.py','test_fv_mm.py','test_fv_pilot.py','test_fv_analysis.py','test_fv_pilot_analysis.py','test_fv_prediction_diagnosis.py']
    files += [Path('tests')/name for name in test_names]
    # Analysis tests import these modules; no external network or real keys used.
    files += [Path('scripts')/name for name in ('analyze_fv_run.py','analyze_fv_pilot.py','diagnose_fv_prediction.py','inspect_fv_pilot.py')]
    h=hashlib.sha256()
    for path in files:h.update(path.as_posix().encode());h.update(path.read_bytes())
    revision=h.hexdigest();archive=args.output/(revision[:16]+'.tar.gz')
    with tarfile.open(archive,'w:gz') as tar:
        for path in files:tar.add(path,arcname=path.as_posix())
    remote='/root/vibetrading-mm-state-review-20261007/'+revision[:16]
    ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=12',args.host]
    subprocess.run(ssh+['mkdir -p '+remote],check=True,timeout=20,capture_output=True)
    subprocess.run(['scp','-o','BatchMode=yes',str(archive),args.host+':'+remote+'/source.tar.gz'],check=True,timeout=45,capture_output=True)
    code="""
import json,pathlib,subprocess,tarfile
root=pathlib.Path(%r).resolve()
with tarfile.open(root/'source.tar.gz') as tar:
    for item in tar.getmembers():
        if not (root/item.name).resolve().is_relative_to(root) or item.issym() or item.islnk():raise ValueError('Invalid source archive path')
    tar.extractall(root)
container=next(x for x in subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines() if 'binance' in x)
image=subprocess.check_output(['docker','inspect',container,'--format','{{.Image}}'],text=True).strip()
base=['docker','run','--rm','--network','none','--cpus','.25','--memory','256m','--pids-limit','128','-e','PYTHONDONTWRITEBYTECODE=1','-v',str(root)+':/review:ro','-w','/review','--entrypoint','python',image]
tests=subprocess.run(base+['-m','pytest']+%r+['-q'],capture_output=True,text=True,timeout=120)
benchmark=subprocess.run(base+['scripts/benchmark_mm_state.py','--events','16000','--routes','16','--journal'],capture_output=True,text=True,timeout=180)
out={'remote_source':str(root),'image':image,'cpu_quota':.25,'memory_limit_bytes':268435456,'network':'none','tests_exit':tests.returncode,'tests_output':tests.stdout[-6000:],'tests_stderr':tests.stderr[-2000:],'benchmark_exit':benchmark.returncode,'benchmark_stderr':benchmark.stderr[-2000:]}
if benchmark.returncode==0:out['benchmark']=json.loads(benchmark.stdout.splitlines()[-1])
else:out['benchmark_output']=benchmark.stdout[-2000:]
print(json.dumps(out))
""" % (remote,['tests/'+x for x in test_names])
    r=subprocess.run(ssh+['python3 -'],input=code,text=True,capture_output=True,timeout=330)
    if r.returncode:raise RuntimeError('Isolated verification failed: '+r.stderr[-500:])
    result=json.loads(r.stdout);result['source_sha256']=revision
    (args.output/'server_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('remote_source','tests_exit','benchmark_exit')}))
    if 'benchmark' in result:print(json.dumps(result['benchmark']))
if __name__=='__main__':main()
