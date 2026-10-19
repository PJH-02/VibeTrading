"""Pinned, source-verified deployment; never touches Market storage or NATS data."""
import argparse, copy, hashlib, json, os, pathlib, shutil, subprocess, tarfile

ROOT=pathlib.Path('/opt/vibetrading/releases/exchange-expansion-20261003')
OLD=pathlib.Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
BASE='sha256:218f2784bfec94e57e97bf3ecb2ee9bdb72b02f59d5e05a02fafc65f1530010c'
NAMES=['crypto-storage','crypto-maintenance','crypto-collector-binance','crypto-collector-bitget','crypto-collector-bybit','crypto-collector-arcus','crypto-collector-grvt']

def run(*args): return subprocess.check_output(args,text=True).strip()
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def compose_args(filename):
    return ['docker','compose','--project-name','vibetrading','--env-file','/etc/vibetrading/compose.env',
        '--env-file',str(ROOT/'deploy/release.env'),'-f',str(ROOT/'deploy'/filename)]

def build(bundle):
    if ROOT.exists():
        revision=json.loads((ROOT/'release.json').read_text())['revision']
        shutil.copy2(ROOT/'release.json',ROOT/('release-before-'+revision+'.json'))
    ROOT.mkdir(parents=True,exist_ok=True)
    with tarfile.open(bundle,'r:gz') as tar:
        for m in tar.getmembers():
            assert m.isfile() and (ROOT/m.name).resolve().is_relative_to(ROOT)
        tar.extractall(ROOT,filter='data')
    manifest=json.loads((ROOT/'bundle-manifest.json').read_text())
    assert all(sha(ROOT/p)==v for p,v in manifest['files'].items())
    shutil.copytree('/srv/vibetrading-capacity/build-wheels-20261003',ROOT/'wheels',dirs_exist_ok=True)
    (ROOT/'wheels/sha256.json').write_text(json.dumps({p.name:sha(p) for p in (ROOT/'wheels').glob('*.whl')},indent=2))
    revision=manifest['revision']; config_hash=manifest['config_hash']; tag='vibetrading-crypto:'+revision
    inspected=json.loads(run('docker','image','inspect',BASE))[0]
    base_tag='vibetrading-expansion-base:'+inspected['Id'].split(':')[1][:20]
    run('docker','tag',BASE,base_tag)
    with (ROOT/'build.log').open('w') as log:
        result=subprocess.run(['docker','build','--network=none','--pull=false','--memory=512m',
            '--cpu-quota=75000','-f',str(ROOT/'Dockerfile.expansion'),'-t',tag,
            '--build-arg','BASE='+base_tag,'--build-arg','REVISION='+revision,
            '--build-arg','CONFIG_HASH='+config_hash,str(ROOT)],stdout=log,stderr=subprocess.STDOUT)
    assert result.returncode==0, 'build failed: inspect build.log'
    image=json.loads(run('docker','image','inspect',tag))[0]
    assert image['RootFS']['Layers'][:len(inspected['RootFS']['Layers'])]==inspected['RootFS']['Layers']
    expected={p:v for p,v in manifest['files'].items() if p.startswith(('vibetrading/','config/','strategies/')) or p=='main.py'}
    check='import hashlib,pathlib,json; expected='+repr(expected)+'; assert all(hashlib.sha256((pathlib.Path("/app")/p).read_bytes()).hexdigest()==h for p,h in expected.items()); print("runtime_source_verified")'
    run('docker','run','--rm','--network=none','--memory=192m','--entrypoint','python',image['Id'],'-c',check)
    release={'image':image['Id'],'base':BASE,'revision':revision,'config_hash':config_hash,'source_hash':manifest['source_hash']}
    (ROOT/'release.json').write_text(json.dumps(release,indent=2))
    (ROOT/'deploy').mkdir(exist_ok=True)
    (ROOT/'deploy/image.env').write_text(f'VIBETRADING_BUILD_REVISION={revision}\nVIBETRADING_BUILD_CONFIG_HASH={config_hash}\nVIBETRADING_IMAGE_ID={image["Id"]}\n')
    (ROOT/'deploy/release.env').write_text(f'VIBETRADING_CRYPTO_IMAGE={image["Id"]}\nVIBETRADING_CRYPTO_RELEASE_ENV_FILE={ROOT}/deploy/image.env\n')
    print(json.dumps(release),flush=True)

def configure():
    release=json.loads((ROOT/'release.json').read_text())
    (ROOT/'rollback').mkdir(exist_ok=True)
    for path in [OLD/'deploy/crypto-r2.yml',pathlib.Path('/etc/systemd/system/vibetrading.service')]:
        if not (ROOT/'rollback'/path.name).exists(): shutil.copy2(path,ROOT/'rollback'/path.name)
    original=json.loads((OLD/'deploy/crypto-r2.yml').read_text())
    new=copy.deepcopy(original)
    template=new['services']['crypto-collector-binance']
    for provider in ('arcus','grvt'):
        service=copy.deepcopy(template)
        service['command'][-1]=provider+'-perp'
        service['healthcheck']['test'][5]='collectors-'+provider+'-perp'
        new['services']['crypto-collector-'+provider]=service
    for name,s in new['services'].items():
        s['image']=release['image']
        s['env_file']=['${VIBETRADING_APP_ENV_FILE:?set in compose.env}',str(ROOT/'deploy/image.env')]
        env=s['environment']
        env.update({'VIBETRADING_BUILD_REVISION':release['revision'],'VIBETRADING_BUILD_CONFIG_HASH':release['config_hash'],
            'VIBETRADING_IMAGE_ID':release['image'],'STORAGE_DATA_DISK_ROOT':'/data/crypto_market_data',
            'STORAGE_NATS_DISK_ROOT':'/data','CRYPTO_COLLECTOR_PROVIDERS':'binance-perp,bitget-perp,bybit-perp,arcus-perp,grvt-perp',
            'ARROW_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
        if name.startswith('crypto-collector-'):
            s['mem_limit']='256m'; s['mem_reservation']='96m'; s['memswap_limit']='384m'
            env['COLLECTOR_PUBLISH_BUFFER_BYTES']=str(16*1024*1024)
            s['stop_grace_period']='30s'
        if name=='crypto-storage': env['SQLITE_CACHE_KIB']='65536'
        if name=='crypto-maintenance':
            s['mem_limit']='384m'; s['memswap_limit']='512m'; env['SQLITE_CACHE_KIB']='32768'
    (ROOT/'deploy/crypto-expansion.yml').write_text(json.dumps(new,indent=2))
    old=json.loads(run('docker','inspect','vibetrading-collectors-1'))[0]
    old_env=dict(e.split('=',1) for e in old['Config']['Env'] if '=' in e)
    safe={k:v for k,v in old_env.items() if k.startswith(('VIBETRADING_','NATS_','STORAGE_','PARQUET_','ARCHIVE_','CRYPTO_','COLLECTOR_')) and not any(w in k.upper() for w in ('KEY','SECRET','TOKEN','PASSWORD','CREDENTIAL','AUTH'))}
    safe.update({'VIBETRADING_BUILD_REVISION':release['revision'],'VIBETRADING_BUILD_CONFIG_HASH':release['config_hash'],
        'VIBETRADING_IMAGE_ID':release['image'],'COLLECTOR_PUBLISH_BUFFER_BYTES':str(16*1024*1024)})
    mounts=[{'type':'bind','source':m['Source'],'target':m['Destination'],'read_only':not m['RW']} for m in old['Mounts']]
    hc=old['Config'].get('Healthcheck',{})
    health={'test':hc['Test'],'interval':str(hc.get('Interval',30*10**9))+'ns',
        'timeout':str(hc.get('Timeout',10*10**9))+'ns','retries':hc.get('Retries',4)}
    market={'services':{'collectors':{'image':release['image'],'env_file':['/etc/vibetrading/app.env',str(ROOT/'deploy/image.env')],
        'environment':safe,'command':old['Config']['Cmd'],'volumes':mounts,'mem_limit':'768m','restart':'unless-stopped',
        'stop_grace_period':'60s','healthcheck':health}},'networks':new['networks']}
    (ROOT/'deploy/market-collectors.yml').write_text(json.dumps(market,indent=2))
    # Keep secrets on the server; synchronize only Arcus keys from the user's
    # original env to the runtime env and record presence, never their values.
    source=pathlib.Path('/root/vibetrading/.env')
    app=pathlib.Path('/etc/vibetrading/app.env')
    if not (ROOT/'rollback/app.env').exists(): shutil.copy2(app,ROOT/'rollback/app.env')
    os.chmod(ROOT/'rollback/app.env',0o600)
    source_lines=source.read_text().splitlines()
    arcus_lines=[l for l in source_lines if l.startswith(('ARCUS_API_KEY=','ARCUS_API_SECRET='))]
    existing=app.read_text().splitlines()
    filtered=[l for l in existing if not l.startswith(('ARCUS_API_KEY=','ARCUS_API_SECRET=','ARCUS_ADDRESS='))]
    filtered.extend(arcus_lines); filtered.append('ARCUS_ADDRESS=0xa77082cf92A22Ed3bCC30C4aD2c08CF2F35026E1')
    app.write_text('\n'.join(filtered)+'\n'); os.chmod(app,0o600)
    if not any(l.startswith('ARCUS_ADDRESS=') for l in source_lines):
        with source.open('a') as f: f.write('\nARCUS_ADDRESS=0xa77082cf92A22Ed3bCC30C4aD2c08CF2F35026E1\n')
    for filename in ('crypto-expansion.yml','market-collectors.yml'):
        run(*compose_args(filename),'config','--quiet')
    print(json.dumps({'phase':'configured','services':NAMES,'market_collector_image':release['image']}),flush=True)

def test():
    release=json.loads((ROOT/'release.json').read_text())
    selected=['test_exchange_expansion.py','test_split_state_disk_guard.py','test_durable_pipeline_guards.py',
        'test_crypto_storage_readiness.py','test_binance_snapshot_bridge.py','test_aligned_derivatives.py','test_crypto_perp_pipeline.py']
    command=['docker','run','--rm','--network=none','--memory=768m','--memory-swap=1024m','--cpus=1',
        '-v',str(ROOT/'tests')+':/app/tests:ro',release['image'],'python','-m','pytest','-p','no:cacheprovider',
        *['/app/tests/'+n for n in selected],'-q']
    output=run(*command)
    (ROOT/'tests-passed.json').write_text(json.dumps({'image':release['image'],'output':output},indent=2))
    print(output,flush=True)

def activate():
    assert json.loads((ROOT/'tests-passed.json').read_text())['image']==json.loads((ROOT/'release.json').read_text())['image']
    # Stop crypto producers first. Existing JetStream queue survives while
    # storage codecs are updated; Market storage and NATS keep running.
    present=[f'vibetrading-crypto-collector-{p}-1' for p in ('binance','bitget','bybit','arcus','grvt')
        if subprocess.run(['docker','inspect',f'vibetrading-crypto-collector-{p}-1'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0]
    run('docker','stop','--time','30',*present)
    run('docker','stop','--time','60','vibetrading-crypto-maintenance-1','vibetrading-crypto-storage-1')
    run(*compose_args('crypto-expansion.yml'),'up','-d','--no-deps','--no-build','crypto-storage','crypto-maintenance')
    run(*compose_args('market-collectors.yml'),'up','-d','--no-deps','--no-build','collectors')
    # All five collectors wait for crypto-storage's genuine readiness signal.
    run(*compose_args('crypto-expansion.yml'),'up','-d','--no-deps','--no-build',*[n for n in NAMES if n.startswith('crypto-collector-')])
    finalize()

def finalize():
    release=json.loads((ROOT/'release.json').read_text())
    assert json.loads((ROOT/'tests-passed.json').read_text())['image']==release['image']
    for name in NAMES+['collectors']:
        current=json.loads(run('docker','inspect','vibetrading-'+name+'-1'))[0]
        assert current['Image']==release['image'] and current['State']['Running']
    unit=pathlib.Path('/etc/systemd/system/vibetrading.service')
    text=unit.read_text().replace('vibetrading-maintenance-1 vibetrading-collectors-1 vibetrading-telegram-1','vibetrading-maintenance-1 vibetrading-telegram-1')
    old_line=next((l for l in text.splitlines() if l.startswith('ExecStart=') and 'crypto-r2.yml' in l),None)
    new_line='ExecStart=/usr/bin/'+' '.join(compose_args('crypto-expansion.yml'))+' up -d --no-deps --no-build'
    market_line='ExecStart=/usr/bin/'+' '.join(compose_args('market-collectors.yml'))+' up -d --no-deps --no-build collectors'
    if old_line:
        text=text.replace(old_line,market_line+'\n'+new_line)
    else:
        assert market_line in text and new_line in text, 'unexpected systemd deployment configuration'
    text=text.replace('vibetrading-crypto-collector-bybit-1 vibetrading-collectors-1',
        'vibetrading-crypto-collector-bybit-1 vibetrading-crypto-collector-arcus-1 vibetrading-crypto-collector-grvt-1 vibetrading-collectors-1')
    unit.write_text(text); run('systemctl','daemon-reload')
    (ROOT/'activation.json').write_text(json.dumps({'phase':'activated','time':run('date','-Is')},indent=2))
    print(json.dumps({'phase':'activated','time':run('date','-Is')}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('phase',choices=('build','test','configure','activate','finalize')); p.add_argument('--bundle')
    a=p.parse_args()
    {'build':lambda:build(a.bundle),'test':test,'configure':configure,'activate':activate,'finalize':finalize}[a.phase]()
