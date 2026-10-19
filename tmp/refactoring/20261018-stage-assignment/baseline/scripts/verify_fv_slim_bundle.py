import argparse,hashlib,json,tarfile
from pathlib import Path
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
p=argparse.ArgumentParser();p.add_argument('bundle',type=Path);p.add_argument('expected_sha256');a=p.parse_args()
root=a.bundle.parent.resolve()
if sha(a.bundle)!=a.expected_sha256:raise RuntimeError('Bundle checksum mismatch')
with tarfile.open(a.bundle) as archive:
 members=archive.getmembers()
 for m in members:
  dest=(root/m.name).resolve()
  if root not in dest.parents or not (m.isfile() or m.isdir()):raise RuntimeError('Unsafe archive member')
 archive.extractall(root,members=members)
venue=a.bundle.name.split('_')[0];audit=json.loads((root/('slim_'+venue+'_audit.json')).read_text())
for r in audit['files']:
 if sha(root/(venue+'_native')/r['name'])!=r['sha256']:raise RuntimeError('Derived file checksum mismatch')
print(json.dumps({'verified':venue,'files':len(audit['files']),'rows':audit['rows'],'bundle_sha256':a.expected_sha256}))
