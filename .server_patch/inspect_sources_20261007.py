import hashlib,json,pathlib,subprocess
names=['vibetrading/bus.py','vibetrading/runtime.py','vibetrading/providers/toss.py','vibetrading/compactor.py']
code='import hashlib,json;names='+repr(names)+';print(json.dumps({p:hashlib.sha256(open("/app/"+p,"rb").read()).hexdigest() for p in names}))'
result={}
for container in ('vibetrading-collectors-1','vibetrading-maintenance-1'):
    result[container]=json.loads(subprocess.check_output(['docker','exec',container,'python','-c',code],text=True))
pathlib.Path('/srv/vibetrading-capacity/incident-20261007/source-baseline.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
