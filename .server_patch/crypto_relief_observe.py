"""Bounded live evidence, including writes to both physical disks."""
import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path('/opt/vibetrading/releases/crypto-relief-final-20261002')
DATA = Path('/srv/vibetrading-data/runtime_data')
STATE = Path('/srv/vibetrading-crypto-state')
NAMES = ['nats', 'storage', 'crypto-storage', 'maintenance', 'crypto-maintenance',
         'crypto-collector-binance', 'crypto-collector-bitget', 'crypto-collector-bybit', 'collectors', 'telegram']


def snapshot():
    now = time.time_ns()
    inspected = json.loads(subprocess.check_output(['docker', 'inspect', *['vibetrading-' + n + '-1' for n in NAMES]]))
    containers = {}
    for c in inspected:
        name = c['Name'].removeprefix('/vibetrading-').removesuffix('-1')
        pid = c['State']['Pid']
        row = {'status': c['State']['Status'], 'health': c['State'].get('Health', {}).get('Status'),
               'pid': pid, 'restarts': c['RestartCount'], 'image': c['Image'], 'io': {},
               'mounts': {m['Destination']: m['Source'] for m in c['Mounts']}}
        if pid:
            rel = next(line.split(':', 2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0:'))
            group = Path('/sys/fs/cgroup') / rel.lstrip('/')
            for line in (group / 'io.stat').read_text().splitlines():
                device, *entries = line.split()
                row['io'][device] = {k: int(v) for k, v in (entry.split('=') for entry in entries)}
            row['oom_kill'] = dict(line.split() for line in (group / 'memory.events').read_text().splitlines())['oom_kill']
            row['memory_bytes'] = int((group / 'memory.current').read_text())
        containers[name] = row
    health = {}
    for directory in ('state', 'crypto_state'):
        health_root=STATE if directory=='crypto_state' else DATA/directory
        for p in (health_root / 'health').glob('*.json'):
            if p.stem not in ('crypto-storage', 'storage', 'maintenance', 'collectors-binance-perp', 'collectors-bybit-perp', 'collectors-bitget-perp'):
                continue
            v = json.loads(p.read_text())
            v['age_seconds'] = round((now - v['ts_ns']) / 1e9, 2)
            health[directory + '/' + p.stem] = v
    with urllib.request.urlopen('http://127.0.0.1:8222/jsz?streams=true&consumers=true&config=true', timeout=5) as f:
        nats = json.load(f)
    db = sqlite3.connect(f'file:{STATE}/vibetrading.sqlite3?mode=ro', uri=True, timeout=3)
    db.execute('PRAGMA query_only=ON')
    catalogue = {'page_count': db.execute('PRAGMA page_count').fetchone()[0],
                 'free_pages': db.execute('PRAGMA freelist_count').fetchone()[0],
                 'page_size': db.execute('PRAGMA page_size').fetchone()[0],
                 'manifest': db.execute('SELECT upload_status,local_present,COUNT(*),SUM(byte_size) FROM parquet_manifest GROUP BY 1,2').fetchall(),
                 'live_runs': db.execute('SELECT COUNT(*),SUM(entry_count) FROM event_index_runs').fetchone(),
                 'retirement_state': db.execute('SELECT * FROM event_index_retention_state').fetchone(),
                 'recovery_unverified': db.execute('SELECT COUNT(*) FROM crypto_delivery_recovery INDEXED BY idx_crypto_recovery_upload WHERE verified_ts_ns IS NULL').fetchone()[0],
                 'recovery_unpruned': db.execute('SELECT COUNT(*) FROM crypto_delivery_recovery INDEXED BY idx_crypto_recovery_prune WHERE pruned_ts_ns IS NULL').fetchone()[0]}
    db.close()
    disks = {parts[2]: [int(v) for v in parts[3:]] for line in Path('/proc/diskstats').read_text().splitlines()
             if (parts := line.split())[2] in ('vda', 'vdb')}
    return {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'monotonic': time.monotonic(),
            'containers': containers, 'health': health, 'jsz': nats, 'catalogue': catalogue, 'diskstats': disks,
            'io_pressure': Path('/proc/pressure/io').read_text(),
            'data_disk_free': shutil.disk_usage(DATA).free, 'root_disk_free': shutil.disk_usage('/').free,
            'memory_available_kib': int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))),
            'db_bytes': {p.name: p.stat().st_size for p in STATE.glob('vibetrading.sqlite3*')},
            'storage_errors': [json.loads(line) for line in (STATE/'storage_errors.jsonl').read_text().splitlines()[-10:]]
                              if (STATE/'storage_errors.jsonl').exists() else []}


label = sys.argv[1]
assert label.replace('-', '').isalnum()
seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 0
assert 0 <= seconds <= 30
a = snapshot()
if seconds:
    time.sleep(seconds)
    b = snapshot()
    dt = b['monotonic'] - a['monotonic']
    b['rates_seconds'] = dt
    b['disk_rates'] = {}
    for name, x in a['diskstats'].items():
        y = b['diskstats'][name]
        reads, writes = y[0] - x[0], y[4] - x[4]
        b['disk_rates'][name] = {'read_iops': round(reads / dt, 2), 'write_iops': round(writes / dt, 2),
            'util_percent': round((y[9] - x[9]) / dt / 10, 2),
            'read_await_ms': round((y[3] - x[3]) / max(1, reads), 2), 'write_await_ms': round((y[7] - x[7]) / max(1, writes), 2)}
    b['container_io_rates'] = {}
    for name, c in b['containers'].items():
        old = a['containers'][name]
        if c['pid'] != old['pid']:
            continue
        b['container_io_rates'][name] = {dev: {k: round((v - old['io'].get(dev, {}).get(k, 0)) / dt, 2) for k, v in entries.items()}
            for dev, entries in c['io'].items()}
    a = b
(ROOT / (label + '.json')).write_text(json.dumps(a, indent=2))
summary = {'utc': a['utc'], 'state': {k: {f: v[f] for f in ('status', 'health', 'restarts', 'oom_kill') if f in v} for k, v in a['containers'].items()},
           'health': {k: {f: v[f] for f in ('age_seconds', 'phase', 'reason', 'ready', 'messages', 'acked', 'pending', 'published_count', 'last_commit_ms', 'archive') if f in v}
                      for k, v in a['health'].items()}, 'catalogue': a['catalogue'], 'io_pressure': a['io_pressure'],
           'storage_errors': a['storage_errors'], 'disk_rates': a.get('disk_rates'),
           'disk_free': {'data': a['data_disk_free'], 'root': a['root_disk_free']}}
summary['streams']={s['name']:{'messages':s['state']['messages'],'bytes':s['state']['bytes'],
    'max_bytes':s['config']['max_bytes'],'consumers':[{k:c.get(k) for k in ('name','num_pending','num_ack_pending','num_redelivered','ack_floor')}
        for c in s.get('consumer_detail',[])]}
    for account in a['jsz']['account_details'] for s in account.get('stream_detail',[])}
summary['memory_available_mib']=round(a['memory_available_kib']/1024,1)
print(json.dumps(summary), flush=True)
