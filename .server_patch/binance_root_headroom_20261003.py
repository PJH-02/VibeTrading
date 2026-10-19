"""Preserve cold rollback bytes in verified compressed archives and bound OS logs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

DEST=Path('/srv/vibetrading-data/admin-retained-20261003')
ROOT=Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
SOURCES=[Path('/srv/vibetrading-nats'),Path('/srv/vibetrading-nats-attempt-1'),
         Path('/opt/vibetrading/r2-cutover-20260930/backup'),
         Path('/srv/vibetrading-data/nats-before-db-split-20261002')]

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024**2),b''):h.update(b)
    return h.hexdigest()

def entries(path):
    out={}
    for p in sorted(path.rglob('*')):
        assert not p.is_symlink(),str(p)
        if p.is_file():
            s=p.stat()
            out[str(p.relative_to(path))]={'bytes':s.st_size,'sha256':sha(p),'uid':s.st_uid,
                'gid':s.st_gid,'mode':s.st_mode&0o7777,'mtime_ns':s.st_mtime_ns}
    return out

def archive(path,items,name):
    target=DEST/(name+'.tar.gz')
    receipt=DEST/(name+'.json')
    assert not target.exists() and not receipt.exists()
    stage=target.with_suffix('.partial')
    with tarfile.open(stage,'w:gz',compresslevel=3) as t:
        for relative in sorted(items):t.add(path/relative,arcname=relative,recursive=False)
    checked={}
    with tarfile.open(stage,'r:gz') as t:
        for member in t:
            assert member.isfile() and member.name in items
            h=hashlib.sha256()
            with t.extractfile(member) as f:
                for b in iter(lambda:f.read(1024**2),b''):h.update(b)
            expected=items[member.name]
            assert member.size==expected['bytes'] and h.hexdigest()==expected['sha256']
            assert (member.uid,member.gid,member.mode)==(expected['uid'],expected['gid'],expected['mode'])
            checked[member.name]=expected
    assert checked==items
    with stage.open('rb') as f:os.fsync(f.fileno())
    stage.rename(target)
    target.chmod(0o600)
    record={'original':str(path),'archive':str(target),'archive_bytes':target.stat().st_size,
        'sha256':sha(target),'files':items,'all_members_verified':True}
    receipt.write_text(json.dumps(record,indent=2))
    receipt.chmod(0o600)
    return record

assert DEST.parent.resolve(strict=True)==Path('/srv/vibetrading-data')
assert os.path.ismount(str(DEST.parent))
assert DEST.parent.stat().st_dev!=Path('/').stat().st_dev
DEST.mkdir(mode=0o700,exist_ok=True)
names=subprocess.check_output(['docker','ps','-q'],text=True).split()
running=json.loads(subprocess.check_output(['docker','inspect',*names]))
for source in SOURCES:
    assert source.resolve(strict=True)==source and source.is_dir()
    assert not any(str(source)==m['Source'] or m['Source'].startswith(str(source)+'/')
                   for c in running for m in c['Mounts']),('active mount',str(source))
for p in Path('/proc').iterdir():
    if not p.name.isdigit():continue
    refs=[]
    try:
        refs.append((p/'cmdline').read_bytes().decode(errors='replace'))
        for link in [p/'exe',p/'cwd',*list((p/'fd').iterdir())]:
            try:refs.append(os.readlink(link))
            except OSError:pass
    except OSError:continue
    assert not any(str(s) in r for s in SOURCES for r in refs),('active process',p.name)
before={'root':shutil.disk_usage('/').free,'data':shutil.disk_usage(DEST).free}
result={'started_ns':time.time_ns(),'before':before,'cold_archives':[]}
for index,source in enumerate(SOURCES):
    items=entries(source)
    r=archive(source,items,'cold-'+str(index)+'-'+source.name)
    assert entries(source)==items,'Cold source changed during archive'
    assert source in SOURCES and source.resolve(strict=True)==source
    # All original bytes, owners and modes now exist in a verified archive.
    shutil.rmtree(source)
    source.mkdir(mode=0o700)
    (source/'ARCHIVED.json').write_text(json.dumps({'archive':r['archive'],'receipt':str(DEST/('cold-'+str(index)+'-'+source.name+'.json'))}))
    result['cold_archives'].append({k:r[k] for k in ('original','archive','archive_bytes','sha256','all_members_verified')})
    print(json.dumps({'archived':str(source),'root_free':shutil.disk_usage('/').free,'data_free':shutil.disk_usage(DEST).free}),flush=True)

# Rotate before selecting files, so every historical journal copied is closed.
subprocess.run(['journalctl','--rotate'],check=True)
journal_root=Path('/var/log/journal')
journals={str(p.relative_to(journal_root)):{'bytes':p.stat().st_size,'sha256':sha(p),
    'uid':p.stat().st_uid,'gid':p.stat().st_gid,'mode':p.stat().st_mode&0o7777,'mtime_ns':p.stat().st_mtime_ns}
    for p in journal_root.glob('*/*@*.journal')}
if journals:
    r=archive(journal_root,journals,'closed-system-journals')
    result['journal_archive']={k:r[k] for k in ('archive','archive_bytes','sha256','all_members_verified')}
config_dir=Path('/etc/systemd/journald.conf.d')
config_dir.mkdir(exist_ok=True)
(config_dir/'vibetrading-storage-budget.conf').write_text('[Journal]\nSystemMaxUse=96M\nRuntimeMaxUse=32M\nSystemKeepFree=1G\n')
subprocess.run(['systemctl','restart','systemd-journald'],check=True)
subprocess.run(['journalctl','--vacuum-size=96M'],check=True)

# These two files are regenerable package indexes, not installed packages.
active={Path(os.readlink(p/'exe')).name for p in Path('/proc').iterdir() if p.name.isdigit() and (p/'exe').exists()}
assert not any(n.startswith(('apt','dpkg')) for n in active),'Package manager active'
(Path('/etc/apt/apt.conf.d')/'99vibetrading-small-root').write_text('Dir::Cache::pkgcache "";\nDir::Cache::srcpkgcache "";\n')
removed=[]
for filename in ('pkgcache.bin','srcpkgcache.bin'):
    p=Path('/var/cache/apt')/filename
    if p.exists():
        assert p.resolve(strict=True).parent==Path('/var/cache/apt') and not p.is_symlink()
        removed.append({'path':str(p),'bytes':p.stat().st_size})
        p.unlink()
result['regenerable_apt_indexes_removed']=removed
result.update(completed_ns=time.time_ns(),after={'root':shutil.disk_usage('/').free,'data':shutil.disk_usage(DEST).free},
    active_data_untouched=True,root_journal_budget_bytes=96*1024**2)
(ROOT/'binance-headroom-20261003.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
