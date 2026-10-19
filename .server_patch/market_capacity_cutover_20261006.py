"""Move Market data to the existing 300GB volume, preserving every byte."""
import argparse,hashlib,json,os,pathlib,shutil,sqlite3,subprocess,time
DATA=pathlib.Path('/srv/vibetrading-data/runtime_data')
NEW=pathlib.Path('/srv/vibetrading-capacity')
OUT=NEW/'incident-20261006'; RECORD=OUT/'market-migration'
PAIRS=[(DATA/'state',NEW/'market-state'),(DATA/'market_data',NEW/'market-data'),(DATA/'exports',NEW/'market-exports')]
def run(*a):return subprocess.check_output(a,text=True).strip()
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        while b:=f.read(1024*1024):h.update(b)
    return h.hexdigest()
def manifest(root):
    out={}
    for p in root.rglob('*'):
        assert not p.is_symlink()
        if p.is_file():out[p.relative_to(root).as_posix()]={'bytes':p.stat().st_size,'sha256':sha(p)}
    return out
def copy():
    assert os.path.ismount(NEW) and run('findmnt','-n','-o','SOURCE',str(NEW))=='/dev/vdc'
    RECORD.mkdir(exist_ok=True)
    if (RECORD/'affected.json').exists():
        affected=json.loads((RECORD/'affected.json').read_text())
    else:
        containers=json.loads(run('docker','inspect',*run('docker','ps','-q').split()))
        affected=[c['Name'].lstrip('/') for c in containers if any(m['Source']==str(DATA) for m in c['Mounts'])]
        (RECORD/'affected.json').write_text(json.dumps(affected))
    early=[n for n in affected if n in ('vibetrading-collectors-1','vibetrading-storage-1','vibetrading-maintenance-1','vibetrading-telegram-1','vibetrading-monitor-bridge-1')]
    (RECORD/'copy-started.json').write_text(json.dumps({'ts_ns':time.time_ns(),'early_stopped':early}))
    # Crypto collectors also write the shared Market gap ledger.
    run('docker','stop','--time','60',*affected)
    evidence=[]
    for source,target in PAIRS:
        assert source.is_dir() and not source.is_symlink() and not os.path.ismount(source)
        target.mkdir(exist_ok=True)
        run('rsync','-aHAX','--numeric-ids','--delete',str(source)+'/',str(target)+'/')
        before=manifest(source);assert before==manifest(target),'copy SHA256 mismatch'
        evidence.append({'source':str(source),'target':str(target),'files':before})
        print(json.dumps({'copied':str(source),'files':len(before),'bytes':sum(v['bytes'] for v in before.values())}),flush=True)
    (RECORD/'verified.json').write_text(json.dumps(evidence,indent=2))
def cutover():
    assert (RECORD/'verified.json').exists() and not (RECORD/'complete.json').exists()
    affected=json.loads((RECORD/'affected.json').read_text())
    crypto=[n for n in affected if n.startswith('vibetrading-crypto-collector-')]
    if crypto:run('docker','stop','--time','30',*crypto)
    remaining=[n for n in affected if n not in crypto]
    run('docker','stop','--time','60',*remaining)
    evidence=json.loads((RECORD/'verified.json').read_text());retained=NEW/'migration-retained-20261006';retained.mkdir()
    shutil.copy2('/etc/fstab',RECORD/'fstab-before');shutil.copy2('/etc/systemd/system/vibetrading.service',RECORD/'unit-before')
    for e in evidence:
        source=pathlib.Path(e['source']);target=pathlib.Path(e['target'])
        assert (source,target) in PAIRS and manifest(source)==e['files']
        cold=retained/target.name;cold.mkdir()
        run('rsync','-aHAX','--numeric-ids',str(source)+'/',str(cold)+'/')
        assert manifest(cold)==e['files']
        for rel,m in e['files'].items():
            p=source/rel
            assert p.resolve().is_relative_to(source.resolve()) and sha(p)==m['sha256']
            p.unlink()
        for p in sorted(source.rglob('*'),key=lambda p:len(p.parts),reverse=True):
            if p.is_dir():p.rmdir()
        assert not list(source.iterdir())
        with open('/etc/fstab','a') as f:f.write(f'{target} {source} none bind,x-systemd.requires-mounts-for={NEW} 0 0\n')
        run('mount',str(source));e['retained']=str(cold)
        print(json.dumps({'bound':str(source),'retained':str(cold)}),flush=True)
    unit=pathlib.Path('/etc/systemd/system/vibetrading.service');text=unit.read_text()
    old=next(l for l in text.splitlines() if l.startswith('RequiresMountsFor='))
    text=text.replace(old,old+' '+ ' '.join(str(s) for s,t in PAIRS))
    text=text.replace('[Service]','\n'.join('ConditionPathIsMountPoint='+str(s) for s,t in PAIRS)+'\n\n[Service]')
    unit.write_text(text);run('systemctl','daemon-reload')
    (RECORD/'complete.json').write_text(json.dumps({'ts_ns':time.time_ns(),'verified':evidence},indent=2))
    # Restart the existing containers so private /data binds see the new mounts.
    # Order protects consumers: storage first, then publishers and dashboards.
    ordered=sorted(affected,key=lambda n:0 if n.endswith('storage-1') else 2 if '-collector' in n else 1)
    run('docker','start',*ordered)
    print(json.dumps({'completed':True,'affected':affected,'disk':run('df','-h',str(DATA),str(NEW))}),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=('copy','cutover'));a=p.parse_args()
    {'copy':copy,'cutover':cutover}[a.phase]()
