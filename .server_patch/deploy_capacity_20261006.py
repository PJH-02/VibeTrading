import argparse,copy,hashlib,json,pathlib,shutil,subprocess,tarfile,time
ROOT=pathlib.Path('/opt/vibetrading/releases/capacity-20261006')
OLD=pathlib.Path('/opt/vibetrading/releases/exchange-expansion-20261003')
BASE='sha256:3724b7738064ee488a2fd1a683c369845fa5f2db4f619ef091179135d15805d3'
def run(*a):return subprocess.check_output(a,text=True).strip()
def compose(file):return ['docker','compose','--project-name','vibetrading','--env-file','/etc/vibetrading/compose.env','--env-file',str(ROOT/'release.env'),'-f',str(ROOT/file)]
def build():
    ROOT.mkdir(exist_ok=True)
    with tarfile.open('/tmp/capacity-20261006.tar.gz') as t:
        assert all(m.isfile() and (ROOT/m.name).resolve().is_relative_to(ROOT) for m in t.getmembers());t.extractall(ROOT,filter='data')
    m=json.loads((ROOT/'manifest.json').read_text())
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==s for p,s in m['files'].items())
    run('docker','tag',BASE,'vibetrading-capacity-base:20261006')
    run('docker','build','--network=none','--pull=false','--memory=256m','--cpu-quota=50000','--build-arg','BASE=vibetrading-capacity-base:20261006','--build-arg','REVISION='+m['revision'],'-t','vibetrading:'+m['revision'],str(ROOT))
    image=run('docker','image','inspect','--format','{{.Id}}','vibetrading:'+m['revision'])
    for p in ('vibetrading/sqlite_store.py','vibetrading/event_index_runs.py'):
        code='import hashlib; print(hashlib.sha256(open("/app/'+p+'","rb").read()).hexdigest())'
        assert run('docker','run','--rm','--network=none','--memory=64m',image,'python','-c',code)==m['files'][p]
    tests=[p for p in m['files'] if p.startswith('tests/')]
    output=run('docker','run','--rm','--network=none','--memory=512m','--cpus=0.5','-v',str(ROOT/'tests')+':/app/tests:ro','-v','/tmp/capacity-test-scripts:/app/scripts:ro',image,'python','-m','pytest','-p','no:cacheprovider',*[ '/app/'+p for p in tests],'-q')
    receipt={'image':image,'base':BASE,'revision':m['revision'],'tests':output}
    (ROOT/'release.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)
def configure():
    release=json.loads((ROOT/'release.json').read_text());backup=ROOT/'rollback';backup.mkdir(exist_ok=True)
    shutil.copy2('/etc/systemd/system/vibetrading.service',backup/'unit');shutil.copy2('/etc/vibetrading/app.env',backup/'app.env');(backup/'app.env').chmod(0o600)
    crypto=json.loads((OLD/'deploy/crypto-expansion.yml').read_text())
    for name,s in crypto['services'].items():
        s['image']=release['image'];s['env_file']=['/etc/vibetrading/app.env',str(ROOT/'image.env')]
        e=s['environment'];e.update({'CRYPTO_NATS_MAX_BYTES':str(512*1024**2),'NATS_FILE_STORE_MAX_BYTES':str(2560*1024**2)})
        if name=='crypto-storage':
            e['CRYPTO_EVENT_GLOBAL_FILTER_BYTES']=str(128*1024**2)
            s['mem_limit']='1280m';s['memswap_limit']='1536m'
        if name=='crypto-maintenance':
            e.update({'ARCHIVE_UPLOAD_BATCH':'32','CRYPTO_RECOVERY_ARCHIVE_BATCH':'32','ARCHIVE_DELIVERY_HISTORY_PRUNE_LIMIT':'65536','CRYPTO_EVENT_RUN_RETIRE_BATCH':'32'})
    market=json.loads((OLD/'deploy/market-collectors.yml').read_text())
    for s in market['services'].values():
        s['image']=release['image'];s['env_file']=['/etc/vibetrading/app.env',str(ROOT/'image.env')]
        s['environment'].update({'STORAGE_DATA_DISK_ROOT':'/data/market_data','STORAGE_STATE_DISK_ROOT':'/data/state','STORAGE_NATS_DISK_ROOT':'/data','NATS_FILE_STORE_MAX_BYTES':str(2560*1024**2),'CRYPTO_NATS_MAX_BYTES':str(512*1024**2)})
    config_hash=hashlib.sha256(json.dumps({'crypto':crypto,'market':market},sort_keys=True).encode()).hexdigest()
    for document in (crypto,market):
        for s in document['services'].values():s['environment'].update({'VIBETRADING_BUILD_REVISION':release['revision'],'VIBETRADING_BUILD_CONFIG_HASH':config_hash,'VIBETRADING_IMAGE_ID':release['image']})
    (ROOT/'crypto.yml').write_text(json.dumps(crypto,indent=2));(ROOT/'market.yml').write_text(json.dumps(market,indent=2))
    (ROOT/'image.env').write_text(f'VIBETRADING_BUILD_REVISION={release["revision"]}\nVIBETRADING_IMAGE_ID={release["image"]}\nVIBETRADING_BUILD_CONFIG_HASH={config_hash}\n')
    (ROOT/'release.env').write_text(f'VIBETRADING_CRYPTO_IMAGE={release["image"]}\nVIBETRADING_CRYPTO_RELEASE_ENV_FILE={ROOT}/image.env\n')
    # Existing stream identities and consumer policies are retained.
    nats_path=pathlib.Path('/opt/vibetrading/releases/crypto-relief-final-20261002/deploy/nats.conf')
    shutil.copy2(nats_path,backup/'nats.conf')
    text=nats_path.read_text();import re
    text,n=re.subn(r'max_file_store\s*:\s*[^\n]+','max_file_store: 2560MB',text)
    assert n==1
    (ROOT/'nats.conf').write_text(text)
    old_nats=json.loads(pathlib.Path('/opt/vibetrading/releases/crypto-relief-final-20261002/deploy/nats-relief.yml').read_text())
    replaced=False
    for i,mount in enumerate(old_nats['services']['nats']['volumes']):
        if isinstance(mount,dict) and mount['target']=='/etc/nats/nats.conf':
            mount['source']=str(ROOT/'nats.conf');replaced=True
        elif isinstance(mount,str) and ':/etc/nats/nats.conf' in mount:
            suffix=mount.split(':/etc/nats/nats.conf',1)[1]
            old_nats['services']['nats']['volumes'][i]=str(ROOT/'nats.conf')+':/etc/nats/nats.conf'+suffix;replaced=True
    assert replaced,'NATS configuration mount not found'
    (ROOT/'nats.yml').write_text(json.dumps(old_nats,indent=2))
    for f in ('crypto.yml','market.yml','nats.yml'):run(*compose(f),'config','--quiet')
    (ROOT/'configuration.json').write_text(json.dumps({'config_hash':config_hash,'filter_bytes':128*1024**2,'crypto_nats_bytes':512*1024**2,'nats_file_store_bytes':2560*1024**2}))
    print(json.dumps({'configured':True,'config_hash':config_hash}),flush=True)
def activate_crypto():
    release=json.loads((ROOT/'release.json').read_text());assert 'passed' in release['tests']
    assert pathlib.Path('/srv/vibetrading-data/runtime_data/state').is_mount()
    # Crypto storage and publishers do not write Market Parquet or exports.
    # Keep maintenance stopped until exports has completed its verified cutover.
    assert not json.loads(run('docker','inspect','vibetrading-maintenance-1'))[0]['State']['Running']
    run(*compose('nats.yml'),'up','-d','--no-deps','--no-build','nats')
    run(*compose('crypto.yml'),'up','-d','--no-deps','--no-build','crypto-storage')
    code='import asyncio\nfrom vibetrading.bus import NatsJetStreamBus\nasync def main():\n b=NatsJetStreamBus(); await b.connect(initialize=True); await b.close()\nasyncio.run(main())'
    run('docker','exec','vibetrading-crypto-storage-1','python','-c',code)
    run(*compose('crypto.yml'),'up','-d','--no-deps','--no-build',*[n for n in json.loads((ROOT/'crypto.yml').read_text())['services'] if n.startswith('crypto-collector-')])
    (ROOT/'crypto-activation.json').write_text(json.dumps({'ts_ns':time.time_ns(),'image':release['image']}))
    print('crypto storage and publishers activated; maintenance remains stopped',flush=True)

def activate():
    release=json.loads((ROOT/'release.json').read_text());assert 'passed' in release['tests']
    assert pathlib.Path('/srv/vibetrading-capacity/incident-20261006/market-migration/complete.json').exists()
    names=['vibetrading-crypto-collector-'+p+'-1' for p in ('binance','bybit','bitget','arcus','grvt')]
    partial=(ROOT/'crypto-activation.json').exists()
    if not partial:
        run('docker','stop','--time','30',*names)
        run('docker','stop','--time','60','vibetrading-crypto-maintenance-1','vibetrading-crypto-storage-1','vibetrading-collectors-1')
    # Change the persistent file-store quota while every publisher is stopped.
    if not partial:run(*compose('nats.yml'),'up','-d','--no-deps','--no-build','nats')
    run(*compose('crypto.yml'),'up','-d','--no-deps','--no-build','crypto-storage','crypto-maintenance')
    code='import asyncio\nfrom vibetrading.bus import NatsJetStreamBus\nasync def main():\n b=NatsJetStreamBus(); await b.connect(initialize=True); await b.close()\nasyncio.run(main())'
    if not partial:run('docker','exec','vibetrading-crypto-storage-1','python','-c',code)
    run(*compose('market.yml'),'up','-d','--no-deps','--no-build','collectors')
    run(*compose('crypto.yml'),'up','-d','--no-deps','--no-build',*[n for n in json.loads((ROOT/'crypto.yml').read_text())['services'] if n.startswith('crypto-collector-')])
    unit=pathlib.Path('/etc/systemd/system/vibetrading.service');text=unit.read_text()
    old_nats=next(l for l in text.splitlines() if l.startswith('ExecStart=') and 'nats-relief.yml' in l)
    old_crypto=next(l for l in text.splitlines() if l.startswith('ExecStart=') and 'crypto-expansion.yml' in l)
    old_market=next(l for l in text.splitlines() if l.startswith('ExecStart=') and 'market-collectors.yml' in l)
    for old,f,extra in ((old_nats,'nats.yml',['nats']),(old_market,'market.yml',['collectors']),(old_crypto,'crypto.yml',[])):
        text=text.replace(old,'ExecStart=/usr/bin/'+' '.join(compose(f))+' up -d --no-deps --no-build'+(' '+' '.join(extra) if extra else ''))
    unit.write_text(text);run('systemctl','daemon-reload')
    (ROOT/'activation.json').write_text(json.dumps({'ts_ns':time.time_ns(),'image':release['image']}));print('activated',flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=('build','configure','activate_crypto','activate'));a=p.parse_args();{'build':build,'configure':configure,'activate_crypto':activate_crypto,'activate':activate}[a.phase]()
