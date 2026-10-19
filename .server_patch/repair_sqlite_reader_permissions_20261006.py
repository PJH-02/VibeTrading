import json,os,pathlib,stat,subprocess,time
out=pathlib.Path('/srv/vibetrading-capacity/incident-20261006');names=['vibetrading-storage-1','vibetrading-maintenance-1']
subprocess.run(['docker','stop','--time','30',*names],check=True)
rows=[]
for root in (pathlib.Path('/srv/vibetrading-data/runtime_data/state'),pathlib.Path('/srv/vibetrading-crypto-state')):
 for db in root.glob('*.sqlite3'):
  owner=db.stat()
  for suffix in ('-wal','-shm'):
   p=db.with_name(db.name+suffix)
   if not p.exists():continue
   old=p.stat()
   if (old.st_uid,old.st_gid)!=(owner.st_uid,owner.st_gid):
    rows.append({'path':str(p),'before_uid':old.st_uid,'after_uid':owner.st_uid,'bytes':old.st_size})
    os.chown(p,owner.st_uid,owner.st_gid);os.chmod(p,stat.S_IMODE(owner.st_mode)|0o600)
(out/'sqlite-reader-permission-repair.json').write_text(json.dumps({'ts_ns':time.time_ns(),'changes':rows},indent=2));print(json.dumps(rows),flush=True)
subprocess.run(['docker','start',*names],check=True)
