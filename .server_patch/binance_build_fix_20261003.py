"""Build/test a one-file Binance fix on the verified current crypto image."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

OLD=Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
ROOT=Path('/opt/vibetrading/releases/binance-bridge-fix-20261003')
PATCH=Path('/tmp/binance-fix-20261003')
baseline=json.loads((OLD/'.deploy-release.json').read_text())
base=baseline['images']['crypto']['id']
assert base=='sha256:a0832388c6645b63c2a551001434671c809000e282da85a09031a24142fdcab8'
expected=baseline['runtime_files']['vibetrading/providers/crypto_perp.py']
original=hashlib.sha256((PATCH/'before.py').read_bytes()).hexdigest()
assert original==expected
for name in ('crypto-storage','crypto-maintenance','crypto-collector-binance','crypto-collector-bitget','crypto-collector-bybit'):
    current=json.loads(subprocess.check_output(['docker','inspect','vibetrading-'+name+'-1']))[0]
    assert current['Image']==base and current['State']['Running'],name
ROOT.mkdir()
(ROOT/'vibetrading/providers').mkdir(parents=True)
(ROOT/'tests').mkdir()
shutil.copy2(PATCH/'crypto_perp.py',ROOT/'vibetrading/providers/crypto_perp.py')
tests=('test_binance_snapshot_bridge.py','test_crypto_perp_pipeline.py','test_split_state_disk_guard.py')
for name in tests:shutil.copy2(PATCH/name,ROOT/'tests'/name)
source=hashlib.sha256((ROOT/'vibetrading/providers/crypto_perp.py').read_bytes()).hexdigest()
revision='binance-bridge-'+source[:16]
compose=json.loads((OLD/'deploy/crypto-r2.yml').read_text())
service=compose['services']['crypto-collector-binance']
config_hash=hashlib.sha256(json.dumps(service,sort_keys=True).encode()).hexdigest()
base_tag='vibetrading-crypto-base:'+base.split(':')[1][:20]
subprocess.run(['docker','tag',base,base_tag],check=True)
(ROOT/'Dockerfile').write_text(f'FROM {base_tag}\nUSER root\nCOPY --chown=10001:10001 vibetrading/providers/crypto_perp.py /app/vibetrading/providers/crypto_perp.py\nLABEL org.opencontainers.image.revision={revision} org.vibetrading.config-hash={config_hash}\nENV VIBETRADING_BUILD_REVISION={revision} VIBETRADING_BUILD_CONFIG_HASH={config_hash} PYTHONDONTWRITEBYTECODE=1\nUSER vibetrading\n')
tag='vibetrading-crypto:'+revision
subprocess.run(['docker','build','--network=none','--pull=false','-t',tag,str(ROOT)],check=True)
image=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0]['Id']
runtime=dict(baseline['runtime_files'])
runtime['vibetrading/providers/crypto_perp.py']=source
verify='import hashlib,json,pathlib;e=json.loads('+repr(json.dumps(runtime))+');assert all(hashlib.sha256((pathlib.Path("/app")/p).read_bytes()).hexdigest()==h for p,h in e.items());print("all_runtime_files_verified")'
subprocess.run(['docker','run','--rm','--network=none','--read-only','--entrypoint','python',image,'-c',verify],check=True)
cmd=['docker','run','--rm','--name','binance-bridge-fix-tests','--network=none','--read-only','--memory','384m',
     '--tmpfs','/tmp:rw,size=268435456','--tmpfs','/app/runtime_data:rw,size=16777216',
     '-v',str(ROOT/'tests')+':/tests:ro','-e','PYTHONPATH=/tests:/app','--entrypoint','python',image,
     '-m','pytest','-q','-p','no:cacheprovider','--override-ini=addopts=']+['/tests/'+n for n in tests]
tested=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
(ROOT/'tests-result.txt').write_text(tested.stdout)
print(tested.stdout,flush=True)
assert tested.returncode==0,'Candidate image tests failed; do not deploy'
manifest={'base_image':base,'image':image,'revision':revision,'config_hash':config_hash,
          'changed_files':['vibetrading/providers/crypto_perp.py'],'runtime_files':runtime,'tests':list(tests),'test_passed':True}
(ROOT/'.deploy-release.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps({k:v for k,v in manifest.items() if k!='runtime_files'}),flush=True)
