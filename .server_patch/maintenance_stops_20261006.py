import json,pathlib,subprocess,time
r=pathlib.Path('/srv/vibetrading-capacity/incident-20261006');names=json.loads((r/'market-migration/affected.json').read_text());raw=json.loads(subprocess.check_output(['docker','inspect',*names],text=True));rows=[{'name':c['Name'],'image':c['Image'],'status':c['State']['Status'],'started_at':c['State']['StartedAt'],'finished_at':c['State']['FinishedAt'],'exit_code':c['State']['ExitCode'],'oom_killed':c['State']['OOMKilled']} for c in raw];(r/'maintenance-stops.json').write_text(json.dumps({'ts_ns':time.time_ns(),'containers':rows},indent=2));print(json.dumps(rows,indent=2))
# Sanitize the first read-only snapshot's unused default NATS example config.
for p in r.glob('snapshot-*.json'):
 d=json.loads(p.read_text())
 if 'nats_config' in d:d.pop('nats_config');p.write_text(json.dumps(d,indent=2))
