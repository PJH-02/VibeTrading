"""Move exact crypto trees to a verified new disk; retain originals for rollback."""
import argparse, hashlib, json, os, pathlib, sqlite3, subprocess, time

NEW = pathlib.Path('/srv/vibetrading-capacity')
RECORD = pathlib.Path('/opt/vibetrading/releases/storage-300gb-20261003')
PAIRS = [(pathlib.Path('/srv/vibetrading-crypto-state'), NEW/'crypto-state'),
         (pathlib.Path('/srv/vibetrading-data/runtime_data/crypto_market_data'), NEW/'crypto-market-data')]

def run(*args):
    return subprocess.check_output(args, text=True).strip()

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()

def manifest(root):
    out = {}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise RuntimeError(f'unexpected symlink: {p}')
        if p.is_file():
            out[str(p.relative_to(root))] = {'bytes':p.stat().st_size,'sha256':sha(p)}
    return out

def prepare():
    RECORD.mkdir(parents=True, exist_ok=False)
    os.chmod(RECORD, 0o700)
    block = json.loads(run('lsblk','-J','-b','-o','NAME,SIZE,FSTYPE,MOUNTPOINTS','/dev/vdc'))['blockdevices'][0]
    assert block['name']=='vdc' and block['size']==300*1024**3 and not block.get('fstype')
    assert not any(block.get('mountpoints') or []) and not block.get('children')
    assert not run('wipefs','--no-act','/dev/vdc'), 'disk contains an existing signature'
    (RECORD/'disk-before.json').write_text(json.dumps(block,indent=2))
    for p in ('/etc/fstab','/etc/systemd/system/vibetrading.service'):
        (RECORD/pathlib.Path(p).name).write_bytes(pathlib.Path(p).read_bytes())
    run('mkfs.ext4','-m','1','-L','vibetrading-capacity','/dev/vdc')
    uuid=run('blkid','-s','UUID','-o','value','/dev/vdc')
    NEW.mkdir(exist_ok=True)
    with open('/etc/fstab','a') as f:
        f.write(f'\n# VibeTrading 300GB expansion 2026-10-03\nUUID={uuid} {NEW} ext4 defaults,noatime 0 2\n')
    run('mount',str(NEW))
    (RECORD/'new-uuid.txt').write_text(uuid+'\n')
    print(json.dumps({'phase':'prepared','uuid':uuid,'mount':str(NEW)}),flush=True)

def cutover():
    assert os.path.ismount(NEW) and run('findmnt','-n','-o','SOURCE',str(NEW))=='/dev/vdc'
    assert not (RECORD/'verified-trees.json').exists()
    run('systemctl','stop','vibetrading')
    # Includes Telegram and Market containers: existing recursive /data binds
    # must reopen after adding the nested crypto-data bind mount.
    assert not run('docker','ps','-q','--filter','name=vibetrading-')
    evidence=[]
    for source, target in PAIRS:
        assert source.is_dir() and not source.is_symlink() and not os.path.ismount(source)
        assert not target.exists()
        target.mkdir()
        run('rsync','-aHAX','--numeric-ids',str(source)+'/',str(target)+'/')
        before=manifest(source)
        assert before==manifest(target), 'copy checksum mismatch'
        if target.name=='crypto-state':
            db=sqlite3.connect(f'file:{target}/vibetrading.sqlite3?mode=ro',uri=True)
            assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
            db.close()
            # Read-only quick_check can recreate WAL/SHM files; compare durable
            # precheck bytes before any service is restarted.
        backup=source.with_name(source.name+'.before-300gb-20261003')
        assert not backup.exists()
        source.rename(backup)
        source.mkdir()
        with open('/etc/fstab','a') as f:
            f.write(f'{target} {source} none bind,x-systemd.requires-mounts-for={NEW} 0 0\n')
        run('mount',str(source))
        evidence.append({'source':str(source),'target':str(target),'rollback':str(backup),'files':before})
        print(json.dumps({'copied':str(source),'files':len(before),'bytes':sum(v['bytes'] for v in before.values())}),flush=True)
    (RECORD/'verified-trees.json').write_text(json.dumps(evidence,indent=2))
    unit=pathlib.Path('/etc/systemd/system/vibetrading.service')
    text=unit.read_text().replace('RequiresMountsFor=/srv/vibetrading-data /srv/vibetrading-crypto-state',
        f'RequiresMountsFor=/srv/vibetrading-data {NEW} /srv/vibetrading-crypto-state /srv/vibetrading-data/runtime_data/crypto_market_data')
    text=text.replace('ConditionPathIsMountPoint=/srv/vibetrading-data',
        f'ConditionPathIsMountPoint=/srv/vibetrading-data\nConditionPathIsMountPoint={NEW}\nConditionPathIsMountPoint=/srv/vibetrading-crypto-state\nConditionPathIsMountPoint=/srv/vibetrading-data/runtime_data/crypto_market_data')
    unit.write_text(text)
    run('systemctl','daemon-reload')
    run('systemctl','start','vibetrading')
    print(json.dumps({'phase':'services_restarted','record':str(RECORD)}),flush=True)

def retain_and_swap():
    assert os.path.ismount(NEW) and run('systemctl','is-active','vibetrading')=='active'
    evidence=json.loads((RECORD/'verified-trees.json').read_text())
    retained=NEW/'migration-retained-20261003'
    retained.mkdir(exist_ok=True); os.chmod(retained,0o700)
    for e in evidence:
        backup=pathlib.Path(e['rollback']); target=retained/backup.name
        assert backup in [s.with_name(s.name+'.before-300gb-20261003') for s,t in PAIRS]
        assert backup.is_dir() and not backup.is_symlink() and not os.path.ismount(backup)
        assert manifest(backup)==e['files']
        target.mkdir(exist_ok=True)
        run('rsync','-aHAX','--numeric-ids',str(backup)+'/',str(target)+'/')
        assert manifest(target)==e['files']
        # Remove only individually verified original bytes after relocation;
        # every original remains in the retained copy on 300GB.
        for rel,info in e['files'].items():
            p=backup/rel
            assert p.resolve().is_relative_to(backup.resolve()) and sha(p)==info['sha256']
            p.unlink()
        for p in sorted(backup.rglob('*'),key=lambda p:len(p.parts),reverse=True):
            if p.is_dir(): p.rmdir()
        backup.rmdir()
        e['retained']=str(target)
    (RECORD/'retained-trees.json').write_text(json.dumps(evidence,indent=2))
    swap=NEW/'emergency.swap'
    assert not swap.exists()
    run('fallocate','-l','4G',str(swap)); os.chmod(swap,0o600)
    run('mkswap',str(swap)); run('swapon',str(swap))
    with open('/etc/fstab','a') as f: f.write(f'{swap} none swap sw 0 0\n')
    pathlib.Path('/etc/sysctl.d/90-vibetrading-memory.conf').write_text('vm.swappiness=10\n')
    run('sysctl','-w','vm.swappiness=10')
    print(json.dumps({'phase':'retained_verified_and_swap_enabled','swap_bytes':4*1024**3}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('phase',choices=('prepare','cutover','retain-and-swap'))
    args=parser.parse_args()
    {'prepare':prepare,'cutover':cutover,'retain-and-swap':retain_and_swap}[args.phase]()
