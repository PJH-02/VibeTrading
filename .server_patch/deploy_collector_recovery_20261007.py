import hashlib,json,pathlib,shutil,subprocess,time
R=pathlib.Path('/opt/vibetrading/releases/collector-recovery-20261007');C=pathlib.Path('/opt/vibetrading/releases/capacity-20261006');M=pathlib.Path('/opt/vibetrading/releases/capacity-maintenance-final-20261006')
m=json.loads((R/'build.json').read_text());image=m['image'];changes=[]
def run(*args):return subprocess.check_output(args,text=True).strip()
for path,services in ((C/'market.yml',['collectors']),(C/'crypto.yml',['crypto-collector-'+p for p in ('binance','bitget','bybit','arcus','grvt')]),(M/'market-storage.yml',['maintenance'])):
    shutil.copy2(path,R/(path.parent.name+'-'+path.name+'.before'))
    d=json.loads(path.read_text())
    for name in services:
        s=d['services'][name];old=s['image'];s['image']=image
        if name=='maintenance':
            s['environment'].update({'COMPACTOR_BATCH_ROWS':'64','PARQUET_TARGET_COMPACT_BYTES':str(16*1024*1024),'COMPACTOR_MAX_INPUT_BYTES':str(32*1024*1024)})
        h=hashlib.sha256(json.dumps(s,sort_keys=True).encode()).hexdigest();s['environment'].update({'VIBETRADING_BUILD_REVISION':m['revision'],'VIBETRADING_BUILD_CONFIG_HASH':h,'VIBETRADING_IMAGE_ID':image})
        changes.append({'config':str(path),'service':name,'previous_image':old,'image':image,'config_hash':h})
    path.write_text(json.dumps(d,indent=2))
    cmd=['docker','compose','--project-name','vibetrading']
    if path.parent==C:cmd+=['--env-file','/etc/vibetrading/compose.env','--env-file',str(C/'release.env')]
    cmd+=['-f',str(path)];run(*cmd,'config','--quiet')
    for service in services:
        run(*cmd,'up','-d','--no-deps','--no-build',service)
        if service.startswith('crypto-collector-'):time.sleep(3)
receipt={'activated_ts_ns':time.time_ns(),'revision':m['revision'],'image':image,'changes':changes,'retained_services':['crypto-storage','crypto-maintenance','storage','nats'],'persistent_unit':run('systemctl','is-active','vibetrading.service')}
(R/'activation.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)
