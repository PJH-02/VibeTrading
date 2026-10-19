"""Check deployed mount/image/guard provenance without exposing app secrets."""
import json
from pathlib import Path
import subprocess
import time

ROOT=Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
OLD=Path('/opt/vibetrading/releases/crypto-relief-20261002')
DATA=Path('/srv/vibetrading-data/runtime_data')
STATE=Path('/srv/vibetrading-crypto-state')
release=json.loads((ROOT/'.deploy-release.json').read_text())
before=json.loads((OLD/'before.json').read_text())
names=['crypto-storage','crypto-maintenance','crypto-collector-binance','crypto-collector-bitget','crypto-collector-bybit']
containers=json.loads(subprocess.check_output(['docker','inspect',*['vibetrading-'+n+'-1' for n in names]]))
whitelist={'VIBETRADING_STATE_DIR','STORAGE_STATE_DISK_ROOT','STORAGE_STATE_RESERVE_BYTES',
           'STORAGE_STATE_RESUME_MARGIN_BYTES','STORAGE_RESERVE_GB','SQLITE_CACHE_KIB',
           'MARKET_EVENT_MAX_AGE_SECONDS','ARCHIVE_DELIVERY_HISTORY_RETENTION_SECONDS',
           'ARCHIVE_RELIEF_MAX_IO_PRESSURE','ARCHIVE_DELIVERY_PRUNE_MAX_IO_PRESSURE',
           'ARCHIVE_BACKLOG_IDLE_SECONDS','ARCHIVE_DELIVERY_HISTORY_PRUNE_LIMIT','NATS_FILE_STORE_MAX_BYTES'}
result={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'image':release['images']['crypto']['id'],
        'revision':release['build_revision'],'containers':{}}
for c in containers:
    assert c['Image']==result['image']
    assert c['State']['Running'] and c['RestartCount']==0 and not c['State']['OOMKilled']
    mounts={m['Destination']:m['Source'] for m in c['Mounts']}
    assert mounts['/data']==str(DATA) and mounts['/data/crypto_state']==str(STATE)
    env=dict(e.split('=',1) for e in c['Config']['Env'])
    assert env['STORAGE_STATE_DISK_ROOT']=='/data/crypto_state'
    assert int(env['STORAGE_STATE_RESERVE_BYTES'])>=1024**3
    assert float(env['STORAGE_RESERVE_GB'])>=4
    assert c['HostConfig']['RestartPolicy']['Name']=='unless-stopped'
    result['containers'][c['Name']]={'mounts':mounts,'memory_limit':c['HostConfig']['Memory'],
        'restart_policy':c['HostConfig']['RestartPolicy'], 'configuration':{k:env[k] for k in sorted(whitelist) if k in env}}
assert DATA.stat().st_dev!=STATE.stat().st_dev
for name,expected in before['protected_exports'].items():
    s=Path(name).stat()
    assert {'inode':s.st_ino,'size':s.st_size,'mtime_ns':s.st_mtime_ns}==expected
assert (DATA/'state/vibetrading.sqlite3').stat().st_ino==before['market_db_inode']
assert not (DATA/'crypto_state/vibetrading.sqlite3').exists()
result.update(separate_devices=True,protected_exports_preserved=True,market_db_inode_preserved=True,
              source_duplicate_removed=True)
unit=Path('/etc/systemd/system/vibetrading.service').read_text()
assert str(ROOT) in unit and str(STATE) in unit and '--remove-orphans' not in unit
result['boot_enabled']=subprocess.check_output(['systemctl','is-enabled','vibetrading.service'],text=True).strip()
result['boot_active']=subprocess.check_output(['systemctl','is-active','vibetrading.service'],text=True).strip()
assert result['boot_enabled']=='enabled' and result['boot_active']=='active'
errors=STATE/'storage_errors.jsonl'
result['storage_error_count']=len(errors.read_text().splitlines()) if errors.exists() else 0
assert result['storage_error_count']==0
result['crypto_logs']={}
for name in names:
    logs=subprocess.check_output(['docker','logs','--since','2026-10-02T05:55:12Z','vibetrading-'+name+'-1'],
                                 stderr=subprocess.STDOUT,text=True)
    lines=logs.splitlines()
    result['crypto_logs'][name]={'error_lines':sum(' ERROR ' in line for line in lines),
        'tracebacks':sum(line.startswith('Traceback') for line in lines),
        'pressure_pauses':sum('collection pressure paused' in line for line in lines),
        'warnings':sum(' WARNING ' in line for line in lines)}
result['protected_export_count']=len(before['protected_exports'])
telegram=json.loads(subprocess.check_output(['docker','inspect','vibetrading-telegram-1']))[0]
state_mount=next(m for m in telegram['Mounts'] if m['Destination']=='/data/crypto_state')
assert state_mount['Source']==str(STATE) and state_mount['RW'] is False
assert telegram['State']['Running']
result['telegram_state_mount']={'source':state_mount['Source'],'readonly':True}
(ROOT/'deployment-audit.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
