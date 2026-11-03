from pathlib import Path
import subprocess,os,json,datetime
R=Path('/srv/vibetrading-crypto-state/cut01-20261102');DATA=R/'data-v7';IMAGE='sha256:0876cf2f41bcd75e953a5bff41bc49d01ffb5897681bbc295c62ffd3b5ad37ab';REVISION='111fa0a9ec4e71b311ff1c3e38f790f8611e82ae';TRIAL='cut01-v7-'+REVISION[:12]
results=[];failed=False
for role in('binance-perp','arcus-perp','grvt-perp','engine','engine-restart','storage'):
 name='cut01-shadow-v7-'+role
 p=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=15)
 if p.returncode:results.append({'name':name,'not_created':True});continue
 x=json.loads(p.stdout)[0]
 if x['Image']!=IMAGE or x['Config'].get('Labels',{}).get('org.vibetrading.cut-trial')!=TRIAL or not any(v.get('Source')==str(DATA)and v.get('Destination')=='/data'for v in x['Mounts']):
  results.append({'name':name,'result':'REFUSE_UNOWNED_STOP'});failed=True;continue
 p=subprocess.run(['docker','stop','--signal=SIGINT','--timeout=45',name],capture_output=True,text=True,timeout=60)
 y=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0];ok=p.returncode==0 and y['State']['Status']=='exited'
 results.append({'name':name,'stop_exit':p.returncode,'container_exit':y['State']['ExitCode'],'oom':y['State']['OOMKilled'],'stopped':ok});failed=failed or not ok
receipt={'result':'PASS_OWNED_STOP_POST'if not failed else'FAIL_OWNED_STOP_POST','observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'service_result':os.environ.get('SERVICE_RESULT'),'main_exit_code':os.environ.get('EXIT_CODE'),'main_exit_status':os.environ.get('EXIT_STATUS'),'source_head':REVISION,'roles':results,'original_roles_stopped':0,'container_deletions':0}
(R/'evidence/shadow-v7-stop-post.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
raise SystemExit(1 if failed else 0)
