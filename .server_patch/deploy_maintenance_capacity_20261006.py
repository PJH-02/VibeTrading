import hashlib,json,pathlib,shutil,subprocess,tarfile,time
R=pathlib.Path('/opt/vibetrading/releases/capacity-maintenance-final-20261006');C=pathlib.Path('/opt/vibetrading/releases/capacity-20261006');BASE='sha256:fcfe1a36a1efce6e7bbe843f4dab59df48e230bbda545b3ad9d94bd77758b026'
def run(*a):return subprocess.check_output(a,text=True).strip()
R.mkdir(exist_ok=True)
with tarfile.open('/tmp/maintenance-capacity-20261006.tar.gz') as t:
 assert all(m.isfile() and (R/m.name).resolve().is_relative_to(R) for m in t.getmembers());t.extractall(R,filter='data')
m=json.loads((R/'manifest.json').read_text());assert all(hashlib.sha256((R/n).read_bytes()).hexdigest()==h for n,h in m['files'].items())
run('docker','tag',BASE,'vibetrading-capacity-maintenance-base:20261006');run('docker','build','--network=none','--pull=false','--memory=256m','--cpu-quota=50000','--build-arg','BASE=vibetrading-capacity-maintenance-base:20261006','--build-arg','REVISION='+m['revision'],'-t','vibetrading:'+m['revision'],str(R));image=run('docker','image','inspect','--format','{{.Id}}','vibetrading:'+m['revision'])
actual=run('docker','run','--rm','--network=none','--memory=64m',image,'python','-c','import hashlib;print(hashlib.sha256(open("/app/vibetrading/runtime.py","rb").read()).hexdigest())');assert actual==m['files']['vibetrading/runtime.py']
tests=run('docker','run','--rm','--network=none','--memory=512m','--cpus=0.5','-v',str(R/'tests')+':/app/tests:ro','-v',str(R/'scripts')+':/app/scripts:ro',image,'python','-m','pytest','-p','no:cacheprovider',*[ '/app/'+n for n in m['files'] if n.startswith('tests/')],'-q');assert 'passed' in tests
config=C/'crypto.yml';shutil.copy2(config,R/'crypto-before.yml');d=json.loads(config.read_text());s=d['services']['crypto-maintenance'];s['image']=image;s['environment']['ARCHIVE_LOCK_RETRY_SECONDS']='10';h=hashlib.sha256(json.dumps(s,sort_keys=True).encode()).hexdigest();s['environment'].update({'VIBETRADING_BUILD_REVISION':m['revision'],'VIBETRADING_BUILD_CONFIG_HASH':h,'VIBETRADING_IMAGE_ID':image});config.write_text(json.dumps(d,indent=2))
cmd=['docker','compose','--project-name','vibetrading','--env-file','/etc/vibetrading/compose.env','--env-file',str(C/'release.env'),'-f',str(config)]
run(*cmd,'config','--quiet');run(*cmd,'up','-d','--no-deps','--no-build','crypto-maintenance')
# Reproduce the two existing Market services without exporting credentials.
# Credentials remain in app.env and the unchanged secret-file mounts.
raw=json.loads(run('docker','inspect','vibetrading-storage-1','vibetrading-maintenance-1'));services={}
for c in raw:
 name='storage' if c['Name'].endswith('-storage-1') else 'maintenance'
 env=dict(v.split('=',1) for v in c['Config']['Env'] if '=' in v)
 safe={k:v for k,v in env.items() if k.startswith(('VIBETRADING_','STORAGE_','NATS_','ARCHIVE_','SQLITE_','PARQUET_','COMPACTOR_','ARROW_','OMP_','DELIVERY_')) and (not any(w in k for w in ('SECRET','TOKEN','PASSWORD','KEY','ACCOUNT')) or k.endswith('_FILE'))}
 safe.update({'STORAGE_DATA_DISK_ROOT':'/data/market_data','STORAGE_STATE_DISK_ROOT':'/data/state','STORAGE_NATS_DISK_ROOT':'/data','NATS_FILE_STORE_MAX_BYTES':'2684354560','CRYPTO_NATS_MAX_BYTES':'536870912','ARCHIVE_LOCK_RETRY_SECONDS':'10'})
 assert not any(v['Destination'] in ('/app/vibetrading/runtime.py','/app/vibetrading/archive.py','/app/vibetrading/sqlite_store.py') for v in c['Mounts'])
 svc={'image':image,'env_file':['/etc/vibetrading/app.env'],'environment':safe,'command':c['Config']['Cmd'],'user':c['Config']['User'],'working_dir':c['Config']['WorkingDir'],'restart':c['HostConfig']['RestartPolicy']['Name'],'mem_limit':c['HostConfig']['Memory'],'stop_grace_period':'90s','volumes':[{'type':'bind','source':v['Source'],'target':v['Destination'],'read_only':not v['RW']} for v in c['Mounts']]}
 if c['HostConfig']['MemorySwap']>0:svc['memswap_limit']=c['HostConfig']['MemorySwap']
 hc=c['Config'].get('Healthcheck')
 if hc:
  svc['healthcheck']={'test':hc['Test'],'interval':str(hc['Interval'])+'ns','timeout':str(hc['Timeout'])+'ns','retries':hc['Retries']}
 ch=hashlib.sha256(json.dumps(svc,sort_keys=True).encode()).hexdigest();svc['environment'].update({'VIBETRADING_BUILD_REVISION':m['revision'],'VIBETRADING_BUILD_CONFIG_HASH':ch,'VIBETRADING_IMAGE_ID':image});services[name]=svc
market_file=R/'market-storage.yml';market_file.write_text(json.dumps({'services':services,'networks':{'default':{'external':True,'name':'vibetrading_default'}}},indent=2))
market_cmd=['docker','compose','--project-name','vibetrading','-f',str(market_file)]
run(*market_cmd,'config','--quiet');run(*market_cmd,'up','-d','--no-deps','--no-build','storage','maintenance')
unit=pathlib.Path('/etc/systemd/system/vibetrading.service');shutil.copy2(unit,R/'unit-before');text=unit.read_text();old='ExecStart=/usr/bin/docker start vibetrading-storage-1 vibetrading-maintenance-1 vibetrading-telegram-1';assert old in text
text=text.replace(old,'ExecStart=/usr/bin/'+' '.join(market_cmd)+' up -d --no-deps --no-build storage maintenance\nExecStart=/usr/bin/docker start vibetrading-telegram-1');unit.write_text(text);run('systemctl','daemon-reload')
receipt={'ts_ns':time.time_ns(),'image':image,'base':BASE,'revision':m['revision'],'config_hash':h,'runtime_sha256':actual,'tests':tests,'recreated_services':['crypto-maintenance','storage','maintenance'],'unchanged_crypto_storage_image':BASE};(R/'release.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)
