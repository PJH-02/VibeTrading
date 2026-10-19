"""Observe and guard only the disposable trial, without SQLite heartbeat scans."""
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

ROOT = Path('/srv/vibetrading-data/r2-load-gate/fullscale-20261001')
EVIDENCE = ROOT / 'evidence'
TRIAL = ['r2-load-feeder', 'r2-load-storage', 'r2-load-maintenance', 'r2-load-nats']
MARKET = ['vibetrading-storage-1', 'vibetrading-maintenance-1', 'vibetrading-collectors-1', 'vibetrading-nats-1']


def states(names):
    result = {}
    for name in names:
        payload = subprocess.check_output(['docker', 'inspect', '--format', '{{json .State}}', name], text=True)
        state = json.loads(payload)
        result[name] = {k: state.get(k) for k in ('Status', 'Pid', 'ExitCode', 'OOMKilled')}
        result[name]['health'] = state.get('Health', {}).get('Status')
        if state.get('Pid'):
            try:
                relative = next(line.split(':', 2)[2] for line in Path(f"/proc/{state['Pid']}/cgroup").read_text().splitlines()
                                if line.startswith('0:'))
                group = Path('/sys/fs/cgroup') / relative.lstrip('/')
                result[name]['memory_current'] = int((group / 'memory.current').read_text())
                result[name]['memory_events'] = {k: int(v) for k, v in
                                                (line.split() for line in (group / 'memory.events').read_text().splitlines())}
                result[name]['io_stat'] = (group / 'io.stat').read_text().splitlines()
            except (OSError, StopIteration):
                result[name]['cgroup_metrics_unavailable'] = True
    return result


def stream(address):
    with urllib.request.urlopen(address + '/jsz?streams=true&consumers=true', timeout=3) as handle:
        data = json.load(handle)
    return [{k: row.get(k) for k in ('name', 'state', 'consumer_detail')}
            for account in data.get('account_details', []) for row in account.get('stream_detail', [])]


def stop_trial():
    # Never stop a production container; preserve NATS/test data for diagnosis.
    for name in TRIAL[:3]:
        subprocess.run(['docker', 'stop', '--time', '90', name], capture_output=True, timeout=105, check=False)


def main():
    destination = EVIDENCE / 'pressure.jsonl'
    if destination.exists():
        raise RuntimeError('monitor evidence exists; resume/read existing job, do not repeat')
    baseline = states(MARKET)
    (EVIDENCE / 'market-container-baseline.json').write_text(json.dumps(baseline, indent=2))
    ip = subprocess.check_output(['docker', 'inspect', '--format', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',
                                  'r2-load-nats'], text=True).strip()
    started = last_report = time.monotonic()
    high_pressure = errors = 0
    samples = 0
    try:
        with destination.open('x') as output:
            while True:
                samples += 1
                trial = states(TRIAL)
                market = states(MARKET)
                full = next(s for s in Path('/proc/pressure/io').read_text().splitlines() if s.startswith('full '))
                pressure = float(dict(p.split('=') for p in full.split()[1:])['avg10'])
                memory = dict((line.split(':')[0], int(line.split()[1]) * 1024)
                              for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
                free = shutil.disk_usage(ROOT).free
                health = json.loads((ROOT / 'state/health/crypto-storage.json').read_text())
                maintenance_path = ROOT / 'state/health/maintenance.json'
                maintenance = json.loads(maintenance_path.read_text()) if maintenance_path.exists() else {}
                try:
                    nats = stream('http://' + ip + ':8222')
                    market_nats = stream('http://127.0.0.1:8222')
                    errors = 0
                except Exception as exc:
                    errors += 1
                    nats = {'unavailable': type(exc).__name__}
                    market_nats = None
                record = {'ts_ns': time.time_ns(), 'elapsed_seconds': round(time.monotonic() - started, 1),
                          'disk_free_bytes': free, 'mem_available_bytes': memory['MemAvailable'],
                          'io_full_avg10': pressure, 'trial': trial, 'market': market,
                          'trial_streams': nats, 'production_streams': market_nats,
                          'storage': {k: health.get(k) for k in ('state', 'ready', 'ts_ns', 'pending', 'buffer_bytes',
                                     'rss_bytes', 'cgroup_used_bytes', 'cgroup_limit_bytes', 'messages', 'files', 'flushes')},
                          'maintenance': {k: maintenance.get(k) for k in ('state', 'ts_ns', 'stage', 'archive')}}
                output.write(json.dumps(record) + '\n')
                output.flush()
                high_pressure = high_pressure + 1 if pressure > 30 else 0
                reasons = []
                if free < 4 * 1024**3:
                    reasons.append('protected_disk_reserve')
                if memory['MemAvailable'] < 512 * 1024**2:
                    reasons.append('host_memory_reserve')
                if high_pressure >= 6:
                    reasons.append('sustained_host_io_pressure')
                if errors >= 3:
                    reasons.append('monitor_control_plane_unavailable')
                if any(value['OOMKilled'] or value.get('memory_events', {}).get('oom_kill', 0) for value in trial.values()):
                    reasons.append('trial_oom')
                if any(market[name]['Pid'] != baseline[name]['Pid'] or market[name]['Status'] != baseline[name]['Status']
                       for name in MARKET):
                    reasons.append('production_process_changed')
                if any(trial[name]['Status'] != 'running' for name in TRIAL[1:]):
                    reasons.append('storage_archive_nats_stopped')
                if trial['r2-load-feeder']['Status'] != 'running' and trial['r2-load-feeder']['ExitCode']:
                    reasons.append('feeder_failed')
                if reasons:
                    raise RuntimeError('trial failed: ' + ','.join(reasons))
                if time.monotonic() - last_report >= 30:
                    print(json.dumps({k: record[k] for k in ('elapsed_seconds', 'disk_free_bytes', 'mem_available_bytes',
                                                            'io_full_avg10', 'storage')}), flush=True)
                    last_report = time.monotonic()
                if trial['r2-load-feeder']['Status'] != 'running':
                    if not (EVIDENCE / 'replay.json').exists():
                        raise RuntimeError('feeder exited without complete replay receipt')
                    result = {'feeder_complete': True, 'samples': samples, 'producer_receipt': str(EVIDENCE / 'replay.json'),
                              'whole_load_qualification_passed': False, 'next': 'drain, archive, terminal exact audit'}
                    (EVIDENCE / 'monitor-complete.json').write_text(json.dumps(result, indent=2))
                    print(json.dumps(result), flush=True)
                    return
                if time.monotonic() - started > 1860:
                    raise RuntimeError('feeder did not complete within bounded duration')
                time.sleep(10)
    except BaseException as exc:
        (EVIDENCE / 'monitor-failure.json').write_text(json.dumps({'error': str(exc), 'samples': samples}, indent=2))
        stop_trial()
        raise


if __name__ == '__main__':
    main()
