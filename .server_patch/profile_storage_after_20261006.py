import collections,json,pathlib,subprocess
r=pathlib.Path('/srv/vibetrading-capacity/incident-20261006');pid=subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}','vibetrading-crypto-storage-1'],text=True).strip()
subprocess.run([str(r/'py-spy'),'record','--pid',pid,'--duration','15','--rate','49','--format','raw','--output',str(r/'storage-after.prof')],check=True)
counts=collections.Counter();total=0;active=0;lookup=0
for line in (r/'storage-after.prof').read_text().splitlines():
 stack,n=line.rsplit(' ',1);n=int(n);total+=n;frames=stack.split(';')
 if not frames[-1].startswith(('_worker','wait','select','run_forever','_run_once')):active+=n
 if 'committed_event_ids' in stack:lookup+=n
 for f in set(frames):counts[f]+=n
result={'samples':total,'approx_active_samples':active,'committed_event_ids_samples':lookup,'top_frames':counts.most_common(25)};(r/'profile-after-summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
