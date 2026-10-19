"""Record recovery status with credentials redacted from diagnostic logs."""
import json
import re
import subprocess
import time
from pathlib import Path

names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
rows=json.loads(subprocess.check_output(['docker','inspect',*[n for n in names if n.startswith('vibetrading-')]],text=True))
result={'checked_ns':time.time_ns(),'containers':[],'logs':{},'health':{}}
for row in rows:
    name=row['Name'].lstrip('/')
    result['containers'].append({'name':name,'running':row['State']['Running'],'health':row['State'].get('Health',{}).get('Status'),
        'restarts':row['RestartCount'],'oom_killed':row['State']['OOMKilled'],'image':row['Config']['Image']})
    output=subprocess.run(['docker','logs','--tail','15',name],text=True,capture_output=True)
    log=output.stdout+output.stderr
    for item in row['Config'].get('Env',[]):
        key,_,value=item.partition('=')
        if len(value)>5 and any(term in key for term in ['TOKEN','SECRET','APP_KEY','API_KEY','ACCOUNT_NUMBER','PASSWORD']):
            log=log.replace(value,'<redacted>')
    log=re.sub(r'(https?://api\.telegram\.org/bot)[^/\s]+',r'\1<redacted>',log)
    result['logs'][name]=log[-1800:]
root=Path('/srv/vibetrading-data/runtime_data')
for file in (root/'state/health').glob('*.json'):
    if file.stem in ['collectors','storage','maintenance','etf-mock','etf-csv-sync'] or file.stem.startswith('collectors-'):
        data=json.loads(file.read_text())
        result['health'][file.stem]={k:data.get(k) for k in ['state','phase','reason','ts_ns','symbol_last_seen_ns','pending_orders','account_routes','error','vehicle_bars']}
for file in (Path('/srv/vibetrading-crypto-state')/'health').glob('*.json'):
    data=json.loads(file.read_text()); result['health']['crypto-'+file.stem]={k:data.get(k) for k in ['state','phase','ready','reason','ts_ns','error']}
path=Path('/opt/vibetrading/releases/telegram-recovery-20261014/verification.json')
path.write_text(json.dumps(result,indent=2,ensure_ascii=False))
print(json.dumps(result,ensure_ascii=False))
