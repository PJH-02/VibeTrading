import pathlib, shutil, os, json, subprocess, time
root=pathlib.Path('/opt/vibetrading/releases/exchange-expansion-20261003')
source=pathlib.Path('/root/vibetrading/.env'); target=pathlib.Path('/etc/vibetrading/app.env')
lines=[l for l in source.read_text().splitlines() if l.startswith(('GRVT_API_KEY=','GRVT_API_SECRET=','GRVT_PRIVATE_KEY=','GRVT_ACCOUNT_ID=','GRVT_SUB_ACCOUNT_ID=','GRVT_ADDRESS=','GRVT_TESTNET_API_KEY=','GRVT_TESTNET_API_SECRET=','GRVT_TESTNET_PRIVATE_KEY=','GRVT_TESTNET_ACCOUNT_ID=','GRVT_TESTNET_SUB_ACCOUNT_ID=','GRVT_TESTNET_ADDRESS='))]
keys={l.split('=',1)[0] for l in lines}
assert {'GRVT_API_KEY','GRVT_API_SECRET','GRVT_TESTNET_API_KEY','GRVT_TESTNET_API_SECRET'}.issubset(keys)
backup=root/'rollback'/('app-before-grvt-sync-'+str(time.time_ns())+'.env')
shutil.copy2(target,backup); os.chmod(backup,0o600)
existing=[l for l in target.read_text().splitlines() if l.split('=',1)[0] not in keys]
target.write_text('\n'.join(existing+lines)+'\n'); os.chmod(target,0o600)
receipt={'keys':sorted(keys),'values_logged':False,'source':'/root/vibetrading/.env','target':str(target)}
(root/'grvt-env-sync.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
subprocess.run(['docker','compose','--project-name','vibetrading','--env-file','/etc/vibetrading/compose.env',
    '--env-file',str(root/'deploy/release.env'),'-f',str(root/'deploy/crypto-expansion.yml'),
    'up','-d','--no-deps','--no-build','--force-recreate','crypto-collector-grvt','crypto-collector-arcus'],check=True)
