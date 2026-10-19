import collections,hashlib,json,os,pathlib,subprocess,zipfile
root=pathlib.Path('/srv/vibetrading-capacity/incident-20261006')
wheel=root/'py_spy.whl'; z=zipfile.ZipFile(wheel)
name=next(n for n in z.namelist() if n.endswith('/py-spy'))
binary=root/'py-spy'; binary.write_bytes(z.read(name));binary.chmod(0o700)
pid=subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}','vibetrading-crypto-storage-1'],text=True).strip()
subprocess.run([str(binary),'record','--pid',pid,'--duration','15','--rate','49','--format','raw','--output',str(root/'storage-before.prof')],check=True)
counts=collections.Counter(); total=0
for line in (root/'storage-before.prof').read_text().splitlines():
    stack,n=line.rsplit(' ',1);n=int(n);total+=n
    for frame in set(stack.split(';')): counts[frame]+=n
print(json.dumps({'samples':total,'top_frames':counts.most_common(18)},indent=2))
metadata={'state_files':[{'name':p.name,'bytes':p.stat().st_size} for p in pathlib.Path('/srv/vibetrading-data/runtime_data/state').iterdir() if p.is_file()],
    'mounts':[{k:c[k] for k in ('Name','Mounts')} for c in json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-q'],text=True).split()],text=True))]}
(root/'metadata.json').write_text(json.dumps(metadata,indent=2));print(json.dumps({'state_files':metadata['state_files']},indent=2))
