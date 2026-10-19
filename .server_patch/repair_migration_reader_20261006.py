import hashlib,json,pathlib,shutil,os
r=pathlib.Path('/srv/vibetrading-capacity/incident-20261006/market-migration');p=r/'verified.json';e=json.loads(p.read_text());s=pathlib.Path(e[0]['source']);t=pathlib.Path(e[0]['target'])
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  while b:=f.read(1024*1024):h.update(b)
 return h.hexdigest()
extra=[p.relative_to(s).as_posix() for p in s.rglob('*') if p.is_file() and p.relative_to(s).as_posix() not in e[0]['files']]
print(json.dumps({'extra':extra}),flush=True)
assert set(extra).issubset({'vibetrading.sqlite3-shm','vibetrading.sqlite3-wal'}) and extra
shutil.copy2(p,r/'verified-before-reader.json')
for n in extra:
 if n.endswith('-wal'):assert (s/n).stat().st_size==0
 shutil.copy2(s/n,t/n)
 owner=(s/n).stat();os.chown(t/n,owner.st_uid,owner.st_gid)
 e[0]['files'][n]={'bytes':owner.st_size,'sha256':sha(s/n)};assert sha(t/n)==e[0]['files'][n]['sha256']
p.write_text(json.dumps(e,indent=2))
# The aborted cutover only created an empty retained directory.
c=pathlib.Path('/srv/vibetrading-capacity/migration-retained-20261006');assert not list(c.iterdir());c.rmdir()
