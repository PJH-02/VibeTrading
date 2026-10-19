"""Bounded temporary collector instrumentation, always restored on completion."""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

RUN='crypto-e2e-'+str(time.time_ns())
release=Path('/opt/vibetrading/latency-audits')/RUN
release.mkdir(parents=True,mode=0o700)
trace=release/'trace'; trace.mkdir(mode=0o755)
shutil.copy2('/tmp/latency_trace_sitecustomize.py',trace/'sitecustomize.py')
data=Path('/srv/vibetrading-data/testnet_latency_audits')/RUN
data.mkdir(parents=True,mode=0o750); os.chown(data,10001,10001)
runtime=Path('/opt/vibetrading/releases/telegram-recovery-20261014/runtime.yml')
document=json.loads(runtime.read_text())
names=['crypto-collector-'+name for name in ['binance','arcus','grvt']]
original={name:copy.deepcopy(document['services'][name]) for name in names}
(release/'original-services.json').write_text(json.dumps(original,indent=2))
image=subprocess.check_output(['docker','inspect','--format','{{.Config.Image}}','vibetrading-telegram-1'],text=True).strip()
compose=['docker','compose','--project-name','vibetrading','-f',str(runtime)]
def run(*args): return subprocess.check_output(args,text=True).strip()
def snapshot():
    return {'time_ns':time.time_ns(),'cpu_count':os.cpu_count(),'load':os.getloadavg(),
        'memory':run('free','-m'),'stats':run('docker','stats','--no-stream','--format','{{.Name}} {{.CPUPerc}} {{.MemUsage}}')}
before=snapshot(); started=time.time_ns(); modified=False
receipt={'run_id':RUN,'release':str(release),'output':str(data),'clock_ntp':run('timedatectl','show','-p','NTPSynchronized'),
    'before':before,'mutated_production_data':False}
(release/'receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps({'run_id':RUN,'output':str(data),'phase':'instrumenting'}),flush=True)
try:
    for name in names:
        service=document['services'][name]
        service['volumes'].append(str(trace)+':/audit_trace:ro')
        env=service['environment']; env['PYTHONPATH']='/audit_trace:/app'; env['LATENCY_AUDIT_ID']=RUN
        env['VIBETRADING_BUILD_CONFIG_HASH']=hashlib.sha256(json.dumps(service,sort_keys=True).encode()).hexdigest()
    runtime.write_text(json.dumps(document,indent=2)); modified=True
    subprocess.check_call(compose+['config','--quiet'])
    subprocess.check_call(['docker','stop','--timeout','30',*['vibetrading-'+n+'-1' for n in names]])
    subprocess.check_call(compose+['up','-d','--no-deps','--no-build',*names])
    # Wait for genuine resumed collection, not the old health file.
    end=time.monotonic()+90
    while True:
        health=[]
        for name in ['binance','arcus','grvt']:
            file=Path('/srv/vibetrading-data/runtime_data/state/health')/('collectors-'+name+'-perp.json')
            row=json.loads(file.read_text()); health.append(row.get('phase')=='publishing' and row['ts_ns']>started)
        if all(health): break
        if time.monotonic()>end: raise RuntimeError('Instrumented collectors did not become ready')
        time.sleep(1)
    receipt['instrumented_ready_ns']=time.time_ns()
    cmd=['docker','run','--rm','--name',RUN,'--network','vibetrading_default','--memory','512m','--cpus','1',
        '--env-file','/etc/vibetrading/app.env','-e','NATS_URL=nats://nats:4222','-e','VIBETRADING_DATA_ROOT=/audit/data',
        '-e','VIBETRADING_STATE_DIR=/audit/state','-e','GRVT_USE_TESTNET=true','-e','ARROW_NUM_THREADS=1','-e','OMP_NUM_THREADS=1',
        '-v',str(data)+':/audit','-v','/tmp/measure_crypto_e2e.py:/app/measure.py:ro',image,
        'python','/app/measure.py','--run-id',RUN,'--seconds','120','--maker-repeats','5','--ioc-repeats','3']
    print(json.dumps({'phase':'measuring','duration_seconds':120}),flush=True)
    with (data/'probe.log').open('w') as log:
        process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,text=True)
        deadline=time.monotonic()+360
        while process.poll() is None:
            if time.monotonic()>deadline:
                subprocess.run(['docker','stop','--timeout','30',RUN],check=False)
                raise RuntimeError('Audit deadline reached; inspect final account cleanup')
            time.sleep(2)
        receipt['probe_exit_code']=process.returncode
    receipt['during_or_after']=snapshot()
    if (data/'result.json').exists():
        result=json.loads((data/'result.json').read_text())
        receipt['measurement']={k:v for k,v in result.items() if k not in {'pipeline','orders'}}
    else:
        raise RuntimeError('Probe did not create its result')
except Exception as exc:
    receipt['orchestrator_error']=str(exc)
finally:
    if modified:
        current=json.loads(runtime.read_text())
        for name,service in original.items(): current['services'][name]=service
        runtime.write_text(json.dumps(current,indent=2))
        subprocess.check_call(['docker','stop','--timeout','30',*['vibetrading-'+n+'-1' for n in names]])
        subprocess.check_call(compose+['up','-d','--no-deps','--no-build',*names])
        receipt['original_services_restored']=True
    receipt['finished_ns']=time.time_ns()
    receipt['after']=snapshot()
    (release/'receipt.json').write_text(json.dumps(receipt,indent=2))
    (data/'host-receipt.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps({'run_id':RUN,'output':str(data),'restored':receipt.get('original_services_restored'),
        'probe_exit_code':receipt.get('probe_exit_code'),'error':receipt.get('orchestrator_error')}),flush=True)
