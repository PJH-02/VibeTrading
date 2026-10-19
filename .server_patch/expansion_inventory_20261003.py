import json, subprocess, pathlib, urllib.request

def run(*args):
    return subprocess.check_output(args, text=True)

containers = json.loads(run('docker', 'inspect', *run('docker', 'ps', '-q').split()))
out = {'containers': []}
for c in containers:
    env = dict(s.split('=', 1) for s in c['Config']['Env'] if '=' in s)
    out['containers'].append({'name': c['Name'], 'image': c['Image'],
        'mounts': [{k:m.get(k) for k in ('Source','Destination','RW')} for m in c['Mounts']],
        'memory_limit': c['HostConfig']['Memory'],
        'config_env': {k:v for k,v in env.items() if k in ('VIBETRADING_DATA_ROOT','VIBETRADING_STATE_DIR','PARQUET_DATA_ROOT','SQLITE_CACHE_KIB','NATS_FILE_STORE_MAX_BYTES','CRYPTO_NATS_MAX_BYTES')},
        'grvt_credentials_present': {k:bool(v) for k,v in env.items() if k.startswith('GRVT')},
        'arcus_credentials_present': {k:bool(v) for k,v in env.items() if k.startswith('ARCUS')}})
for p in ('/root/vibetrading/.env','/etc/vibetrading/app.env'):
    f = pathlib.Path(p)
    if f.exists():
        out[p] = {line.split('=',1)[0]:bool(line.split('=',1)[1].strip()) for line in f.read_text().splitlines() if '=' in line and line.startswith(('GRVT','ARCUS'))}
out['disk_sizes'] = run('du','-sh','/srv/vibetrading-crypto-state','/srv/vibetrading-data/runtime_data/crypto_market_data','/srv/vibetrading-data/nats','/srv/vibetrading-data/docker')
out['docker_stats'] = run('docker','stats','--no-stream','--format','{{.Name}} {{.MemUsage}} {{.CPUPerc}}')
for endpoint in ('/varz','/jsz?streams=true&consumers=true&config=true'):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8222'+endpoint, timeout=10) as response:
            out[endpoint] = json.load(response)
    except Exception as e:
        out[endpoint] = type(e).__name__
print(json.dumps(out, indent=2))
