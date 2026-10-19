"""Host-only setup for a disposable full-size candidate and real NATS replay.

Production containers/DB/Parquet/NATS are not recreated or mounted. Shares only
the maintenance lock inode; archive credentials go only to the trial archive.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path('/srv/vibetrading-data/r2-load-gate/fullscale-20261001')
RELEASE = Path('/opt/vibetrading/releases/r2-crypto-prune-20261001')
SCRIPTS = Path('/opt/vibetrading/r2-cutover-20260930/scripts')
TRACE = Path('/opt/vibetrading/r2-cutover-20260930/backup')
IMAGE = 'sha256:5dce97a767197d4ffd9cae0e8024aeca4ba5574369a287942f4f2a8ed3406e8c'
NETWORK = 'r2-load-gate-20261001'
NATS_IMAGE = 'sha256:b83efabe3e7def1e0a4a31ec6e078999bb17c80363f881df35edc70fcb6bb927'


def run(args):
    return subprocess.check_output(args, text=True).strip()


def env_args(env):
    return [arg for key, value in env.items() for arg in ('-e', f'{key}={value}')]


def prepare():
    proof_path = ROOT / 'evidence/prepared.json'
    if ROOT.resolve() != ROOT or proof_path.exists():
        raise RuntimeError('fixed trial root/pristine setup gate failed')
    database = ROOT / 'state/vibetrading.sqlite3'
    if database.stat().st_size != 1755222016 or database.samefile('/srv/vibetrading-data/runtime_data/crypto_state/vibetrading.sqlite3'):
        raise RuntimeError('pristine candidate identity mismatch')
    started, count, last_report = time.monotonic(), 0, 0
    digest = hashlib.sha256()
    with database.open('rb') as handle:
        while chunk := handle.read(256 * 1024):
            digest.update(chunk)
            count += len(chunk)
            delay = started + count / (8 * 1024**2) - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if shutil.disk_usage(ROOT).free < 4 * 1024**3:
                raise RuntimeError('protected reserve reached during transfer verification')
            full = next(s for s in Path('/proc/pressure/io').read_text().splitlines() if s.startswith('full '))
            pressure = float(dict(p.split('=') for p in full.split()[1:])['avg10'])
            while pressure > 5:
                if time.monotonic() - started > 900:
                    raise RuntimeError('transfer verification pressure deadline exceeded')
                time.sleep(1)
                full = next(s for s in Path('/proc/pressure/io').read_text().splitlines() if s.startswith('full '))
                pressure = float(dict(p.split('=') for p in full.split()[1:])['avg10'])
            if time.monotonic() - last_report > 20:
                print(json.dumps({'phase': 'verify_new_candidate_transfer', 'bytes': count}), flush=True)
                last_report = time.monotonic()
    if digest.hexdigest() != '02d3f0f96535ad5eef8bc3f4dc7000f538dd7ef2a813e45eff50b9d19098f016':
        raise RuntimeError('transferred candidate full checksum mismatch')
    connection = sqlite3.connect(database)
    try:
        metadata = dict(connection.execute("SELECT key,value FROM runtime_metadata WHERE key IN ('event_index_format','event_index_migration_delivery_sha256')"))
        if metadata != {'event_index_format': 'immutable-runs-v1', 'event_index_migration_delivery_sha256':
                        'd73c5ce16a312d9cbcb69e023c4b7f52a93716c2b39c805787b6e3b8c3b4d7e8'}:
            raise RuntimeError('candidate migration contract mismatch')
        baseline = connection.execute('SELECT max(rowid) FROM parquet_manifest').fetchone()[0]
        # Historical files are absent from this isolated mount. Do not pretend
        # they were remotely verified; preserve status/ACK/event coverage.
        connection.execute('UPDATE parquet_manifest SET local_present=0')
        backup_path = Path('/srv/vibetrading-data/r2-cutover-local-backup/current-crypto-pre-r2.receipt.json')
        backup = json.loads(backup_path.read_text())
        if backup.get('remote_full_sha256_verified') is not True or backup.get('consistent_current_db_backup') is not True:
            raise RuntimeError('actual verified pre-migration source backup required')
        connection.execute('INSERT INTO archive_backup VALUES(?,?,?,?)',
                           ('verified-premigration-source-20261001', backup['remote_key'], backup['sha256'], backup_path.stat().st_mtime_ns))
        connection.commit()
    finally:
        connection.close()
    os.chown(database, 10001, 10001)
    lock = ROOT / '.vibetrading-maintenance.lock'
    os.link('/srv/vibetrading-data/runtime_data/.vibetrading-maintenance.lock', lock)
    parse = "import json,yaml; x=yaml.safe_load(open('/release/crypto-r2.yml')); print(json.dumps({k:v['environment'] for k,v in x['services'].items() if k in ('crypto-storage','crypto-maintenance')}))"
    profiles = json.loads(run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{RELEASE}/deploy:/release:ro',
                              '--entrypoint', 'python', IMAGE, '-c', parse]))
    for env in profiles.values():
        env.update(NATS_URL='nats://r2-load-nats:4222', VIBETRADING_DATA_ROOT=str(ROOT),
                   VIBETRADING_STATE_DIR=str(ROOT / 'state'), PARQUET_DATA_ROOT=str(ROOT / 'crypto_market_data'),
                   VIBETRADING_LOG_DIR=str(ROOT / 'logs'), ARCHIVE_PREFIX='vibetrading/crypto/qualification/fullscale-20261001',
                   VIBETRADING_IMAGE_ID=IMAGE)
    (ROOT / 'evidence/profiles.json').write_text(json.dumps(profiles, indent=2))
    (ROOT / 'evidence/trial.env').write_text(''.join(f'{k}={v}\n' for k, v in profiles['crypto-storage'].items()))
    proof = {'source_full_sha256': digest.hexdigest(), 'seed_deliveries': 9240414, 'baseline_manifest_rowid': baseline,
             'history_files_absent_from_trial': True, 'history_verification_flags_not_forged': True,
             'backup': 'actual verified pre-migration source backup; full migrated backup not exercised in replay',
             'image': IMAGE, 'network': NETWORK, 'production_data_modified': False}
    proof_path.write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof), flush=True)


def start():
    raise RuntimeError('obsolete initial setup disabled: use explicitly verified production resource profile')


def feed():
    health = json.loads((ROOT / 'state/health/crypto-storage.json').read_text())
    if (health.get('ready') is not True or health.get('state') != 'running'
            or time.time_ns() - health['ts_ns'] > 60 * 10**9
            or health.get('event_index', {}).get('validated_entries', 0) < 9240414):
        raise RuntimeError('full-size storage warmup/readiness gate not satisfied')
    profiles = json.loads((ROOT / 'evidence/profiles.json').read_text())
    args = ['docker', 'run', '-d', '--name', 'r2-load-feeder', '--network', NETWORK, '--memory', '384m',
            '-v', f'{ROOT}:{ROOT}', '-v', f'{SCRIPTS}:/app/scripts:ro', '-v', f'{TRACE}:/trace:ro',
            '--env-file', '/etc/vibetrading/app.env', '-e', 'PYTHONPATH=/app']
    print(run(args + env_args(profiles['crypto-storage']) + [IMAGE, 'python', '/app/scripts/replay_crypto_nats.py',
              '--input', '/trace/crypto-replay-burst.jsonl', '--report', '/trace/crypto-replay-burst.report.json',
              '--env', str(ROOT / 'evidence/trial.env'), '--result', str(ROOT / 'evidence/replay.json'),
              '--duration-seconds', '1800', '--multiplier', '2']), flush=True)


def initialize_existing_trial():
    # Repairs only the missing disposable streams after an interrupted setup.
    # Never recreates the network/containers or uses production NATS.
    profiles = json.loads((ROOT / 'evidence/profiles.json').read_text())
    code = "import asyncio; from vibetrading.bus import NatsJetStreamBus; from vibetrading.settings import get_settings\n"
    code += "async def go():\n b=NatsJetStreamBus(get_settings())\n await b.connect(initialize=True)\n await b.close()\nasyncio.run(go())"
    print(run(['docker', 'run', '--rm', '--name', 'r2-load-init', '--network', NETWORK, '--memory', '256m',
               '--cpus', '0.5', '-v', f'{ROOT}:{ROOT}', '--env-file', '/etc/vibetrading/app.env']
              + env_args(profiles['crypto-storage']) + [IMAGE, 'python', '-c', code]), flush=True)


def match_production_after_failed_trial():
    failure = json.loads((ROOT / 'evidence/monitor-failure.json').read_text())
    if not failure.get('error'):
        raise RuntimeError('failed-trial evidence required')
    for name in ('r2-load-nats', 'r2-load-feeder', 'r2-load-storage', 'r2-load-maintenance'):
        state = json.loads(run(['docker', 'inspect', '--format', '{{json .State}}', name]))
        if state['Running']:
            raise RuntimeError('failed trial must be stopped before matching resources')
    with urllib.request.urlopen('http://127.0.0.1:8222/varz', timeout=3) as response:
        config = json.load(response)['jetstream']['config']
    if config['sync_interval'] != 1_000_000_000:
        raise RuntimeError('unexpected production sync policy; inspect before deriving trial')
    directory = ROOT / 'nats-matched'
    directory.mkdir()
    nats_config = {'port': 4222, 'http_port': 8222, 'jetstream': {'store_dir': str(directory),
                   'max_memory_store': config['max_memory'], 'max_file_store': config['max_storage'],
                   'sync_interval': '1s'}}
    path = ROOT / 'evidence/nats-matched.conf'
    path.write_text(json.dumps(nats_config))
    # Validate syntax without opening a server or changing a data store.
    run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{path}:/etc/nats/trial.conf:ro',
         NATS_IMAGE, '-t', '-c', '/etc/nats/trial.conf'])
    run(['docker', 'rename', 'r2-load-nats', 'r2-load-nats-failed-256m'])
    run(['docker', 'rename', 'r2-load-feeder', 'r2-load-feeder-failed-256m'])
    for name in ('pressure.jsonl', 'monitor-failure.json', 'market-container-baseline.json'):
        source = ROOT / 'evidence' / name
        source.rename(source.with_name(source.stem + '-256m' + source.suffix))
    connection = sqlite3.connect(ROOT / 'state/vibetrading.sqlite3')
    try:
        baseline = connection.execute('SELECT max(rowid) FROM parquet_manifest').fetchone()[0]
    finally:
        connection.close()
    proof_path = ROOT / 'evidence/prepared.json'
    proof = json.loads(proof_path.read_text())
    proof_path.rename(proof_path.with_name('prepared-256m.json'))
    proof.update(baseline_manifest_rowid=baseline, earlier_failed_test_data_retained=True,
                 resource_correction='512MiB NATS, unlimited CPU as production; same JetStream limits and 1s sync',
                 production_jetstream_config=config)
    proof_path.write_text(json.dumps(proof, indent=2))
    print(run(['docker', 'run', '-d', '--name', 'r2-load-nats', '--network', NETWORK, '--memory', '512m',
               '-v', f'{directory}:{directory}', '-v', f'{path}:/etc/nats/trial.conf:ro',
               NATS_IMAGE, '-c', '/etc/nats/trial.conf']), flush=True)
    initialize_existing_trial()
    for name in ('r2-load-storage', 'r2-load-maintenance'):
        run(['docker', 'update', '--cpus', '0', name])
        print(run(['docker', 'start', name]), flush=True)
    print(json.dumps(proof), flush=True)


def match_worker_cpu_resources():
    """Recreate disposable workers: Docker --cpus=0 does not clear NanoCpus."""
    if (ROOT / 'evidence/replay.json').exists():
        raise RuntimeError('completed replay must not be restarted')
    try:
        run(['docker', 'inspect', 'r2-load-feeder'])
    except subprocess.CalledProcessError:
        pass
    else:
        raise RuntimeError('feeder must not exist while correcting resources')
    profiles = json.loads((ROOT / 'evidence/profiles.json').read_text())
    for name in ('r2-load-storage', 'r2-load-maintenance'):
        production = 'vibetrading-crypto-' + name.removeprefix('r2-load-') + '-1'
        config = json.loads(run(['docker', 'inspect', '--format', '{{json .HostConfig}}', production]))
        if any(config[key] != 0 for key in ('NanoCpus', 'CpuPeriod', 'CpuQuota')):
            raise RuntimeError('production CPU profile changed; inspect first')
        current = json.loads(run(['docker', 'inspect', '--format', '{{json .HostConfig}}', name]))
        if current['NanoCpus'] == 0:
            raise RuntimeError('resource correction already performed; do not recreate')
    run(['docker', 'stop', '--time', '90', 'r2-load-storage', 'r2-load-maintenance'])
    for name in ('r2-load-storage', 'r2-load-maintenance'):
        run(['docker', 'rename', name, name + '-failed-cpu-cap'])
    common = ['docker', 'run', '-d', '--network', NETWORK, '-v', f'{ROOT}:{ROOT}',
              '--env-file', '/etc/vibetrading/app.env', '--env-file', str(RELEASE / 'deploy/crypto-release.env')]
    print(run(common + ['--name', 'r2-load-storage', '--memory', '768m']
              + env_args(profiles['crypto-storage']) + [IMAGE, 'python', 'main.py', 'run', '--role', 'crypto-storage']), flush=True)
    print(run(common + ['--name', 'r2-load-maintenance', '--memory', '512m',
                        '-v', '/etc/vibetrading/secrets/archive_access_key:/run/secrets/archive_access_key:ro',
                        '-v', '/etc/vibetrading/secrets/archive_secret_key:/run/secrets/archive_secret_key:ro']
              + env_args(profiles['crypto-maintenance']) + [IMAGE, 'ionice', '-c3', 'nice', '-n', '10',
                                                         'python', 'main.py', 'run', '--role', 'crypto-maintenance']), flush=True)
    for name in ('r2-load-nats', 'r2-load-storage', 'r2-load-maintenance'):
        config = json.loads(run(['docker', 'inspect', '--format', '{{json .HostConfig}}', name]))
        if any(config[key] != 0 for key in ('NanoCpus', 'CpuPeriod', 'CpuQuota')):
            raise RuntimeError('trial CPU profile does not match production')
    print(json.dumps({'worker_cpu_limits_removed': True, 'production_modified': False}), flush=True)


if __name__ == '__main__':
    {'prepare': prepare, 'start': start, 'feed': feed, 'initialize': initialize_existing_trial,
     'match-production': match_production_after_failed_trial,
     'match-worker-cpu': match_worker_cpu_resources}[sys.argv[1]]()
