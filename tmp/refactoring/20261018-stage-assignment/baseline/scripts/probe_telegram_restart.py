import json
import re
import subprocess
row=json.loads(subprocess.check_output(['docker','inspect','vibetrading-telegram-1'],text=True))[0]
result=subprocess.run(['docker','logs','--tail','45','vibetrading-telegram-1'],text=True,capture_output=True)
log=result.stdout+result.stderr
for value in row['Config']['Env']:
    key,_,secret=value.partition('=')
    if any(term in key for term in ['TOKEN','SECRET','APP_KEY','ACCOUNT_NUMBER']) and len(secret)>6:
        log=log.replace(secret,'<redacted>')
log=re.sub(r'(https?://api\.telegram\.org/bot)[^/\s]+',r'\1<redacted>',log)
print(json.dumps({'restart_count':row['RestartCount'],'oom_killed':row['State']['OOMKilled'],
    'memory_limit':row['HostConfig']['Memory'],'log':log}))
