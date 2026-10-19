"""Use local Ubuntu WSL, a fresh Linux /tmp source copy and offline guard."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

RUN=Path(__file__).resolve().parent
ROOT=RUN.parents[3]
CHILD=r'''
import hashlib,json,os,pathlib,shutil,subprocess,sys,tempfile,time
source,output,guard,package_home,label,*tests=sys.argv[1:]
assert not (pathlib.Path(source)/'.env').exists()
base=pathlib.Path(tempfile.mkdtemp(prefix='vt-r-'))
target=base/'s';shutil.copytree(source,target)
shutil.copy2(guard,target/'sitecustomize.py')
temporary=base/'t';temporary.mkdir()
home=base/'home';home.mkdir()
env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(home),'LANG':'C.UTF-8',
'PYTHONUSERBASE':package_home,'PYTHONPATH':str(target)+':/home/pjh/.cache/vt-refactor-deps','PYTHONDONTWRITEBYTECODE':'1',
'REFACTOR_LINUX_DEPS':'/home/pjh/.cache/vt-refactor-deps',
'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1',
'TMP':str(temporary),'TMPDIR':str(temporary),'TEMP':str(temporary),
'REFACTOR_GUARD_RECEIPT':str(pathlib.Path(output)/'guard_receipts.jsonl')}
command=[sys.executable,'-c',"import sys; assert getattr(sys,'_vibetrading_offline_guard',False); import pytest; raise SystemExit(pytest.main(sys.argv[1:]))",'-p','pytest_asyncio.plugin','-q','-m','not live_data','--basetemp',str(temporary/'p'),'--junitxml',str(pathlib.Path(output)/'junit.xml'),*tests]
started=time.monotonic()
with (pathlib.Path(output)/'stdout.txt').open('w',encoding='utf-8') as handle:
    result=subprocess.run(command,cwd=target,env=env,stdout=handle,stderr=subprocess.STDOUT,timeout=420)
hashes={}
for folder in ('vibetrading','tests','scripts/refactoring','strategies'):
    for path in (target/folder).rglob('*.py'):
        if '__pycache__' not in path.parts:hashes[path.relative_to(target).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
report={'source':str(target),'source_prepared':source,'exit_code':result.returncode,'elapsed_seconds':time.monotonic()-started,'command':command,'environment_keys':sorted(env),'tested_source_hashes':hashes,'guard_sha256':hashlib.sha256(pathlib.Path(guard).read_bytes()).hexdigest(),'scope':'Local Linux WSL source copy, cooperative offline guard; no production/account verification'}
(pathlib.Path(output)/'linux_result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='tested_source_hashes'},ensure_ascii=False))
'''

def wsl_path(path):
    return subprocess.check_output(['wsl','-d','Ubuntu','--exec','wslpath','-u',path.as_posix()],text=True,encoding='utf-8').strip()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--label',required=True)
    parser.add_argument('tests',nargs='*')
    args=parser.parse_args()
    if not args.label.replace('-','').replace('_','').isalnum():raise ValueError('invalid label')
    source=args.source.resolve()
    if not source.is_relative_to(ROOT/'tmp/refactoring') or (source/'.env').exists():raise ValueError('expected safe source copy')
    result=RUN/args.label;result.mkdir(exist_ok=False)
    # Query library location only; do not inherit WSL credential variables.
    package_home=subprocess.check_output(['wsl','-d','Ubuntu','--exec','python3','-c','import site; print(site.USER_BASE)'],text=True,encoding='utf-8').strip()
    command=['wsl','-d','Ubuntu','--exec','env','-i','PATH=/usr/bin:/bin','LANG=C.UTF-8','python3','-c',CHILD,wsl_path(source),wsl_path(result),wsl_path(RUN/'offline_guard.py'),package_home,args.label,*args.tests]
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    with (result/'launcher_stdout.txt').open('w',encoding='utf-8') as handle:
        process=subprocess.run(command,stdout=handle,stderr=subprocess.STDOUT,timeout=480)
    (result/'launcher.json').write_text(json.dumps({'started_utc':started,'completed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'launcher_exit_code':process.returncode,'launcher_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},indent=2)+'\n',encoding='utf-8')
    print((result/'launcher_stdout.txt').read_text(encoding='utf-8')[-2500:])
    if (result/'stdout.txt').exists():print((result/'stdout.txt').read_text(encoding='utf-8')[-10000:])

if __name__=='__main__':main()
