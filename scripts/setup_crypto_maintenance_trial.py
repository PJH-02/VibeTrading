"""Qualify the new maintenance service on the existing disposable full DB.

No second DB copy, no production mounts, no production setting changes. The
one-hour test retention admits earlier verified replay data while preserving
every row of the new 30-minute replay for the independent terminal audit.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

from setup_crypto_load_trial import ROOT, SCRIPTS, TRACE, NETWORK, NATS_IMAGE, env_args, run

RELEASE = Path('/opt/vibetrading/releases/r2-maintenance-service-final-20261001')
EVIDENCE = ROOT / 'evidence/service-20261001'


def image_id():
    return json.loads((RELEASE / '.deploy-release.json').read_text())['images']['crypto']['id']


def setup():
    if EVIDENCE.exists():
        raise RuntimeError('service trial already prepared; inspect rather than repeat')
    parent = json.loads((ROOT / 'evidence/integrity.json').read_text())
    if parent.get('storage_ack_archive_integrity_verified') is not True:
        raise RuntimeError('parent full-size exact audit required')
    for name in ('r2-load-feeder', 'r2-load-storage', 'r2-load-maintenance'):
        state = json.loads(run(['docker', 'inspect', '--format', '{{json .State}}', name]))
        if state['Running'] or state['ExitCode'] != 0:
            raise RuntimeError('parent trial writers must be cleanly stopped')
    if shutil.disk_usage(ROOT).free < 4 * 1024**3 + 128 * 1024**2:
        raise RuntimeError('service qualification scratch reserve insufficient')
    database = ROOT / 'state/vibetrading.sqlite3'
    if database.samefile('/srv/vibetrading-data/runtime_data/crypto_state/vibetrading.sqlite3'):
        raise RuntimeError('production catalogue must never be used')
    conn = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        baseline = conn.execute('SELECT MAX(rowid) FROM parquet_manifest').fetchone()[0]
        retired = conn.execute('SELECT retired_entries FROM event_index_retention_state WHERE singleton=1').fetchone()[0]
    finally:
        conn.close()
    EVIDENCE.mkdir()
    parse = "import json,yaml; x=yaml.safe_load(open('/release/crypto-r2.yml')); print(json.dumps({k:v['environment'] for k,v in x['services'].items() if k in ('crypto-storage','crypto-maintenance')}))"
    image = image_id()
    profiles = json.loads(run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{RELEASE}/deploy:/release:ro',
                              '--entrypoint', 'python', image, '-c', parse]))
    for env in profiles.values():
        env.update(NATS_URL='nats://r2-load-nats:4222', VIBETRADING_DATA_ROOT=str(ROOT),
                   VIBETRADING_STATE_DIR=str(ROOT / 'state'), PARQUET_DATA_ROOT=str(ROOT / 'crypto_market_data'),
                   VIBETRADING_LOG_DIR=str(ROOT / 'logs'), VIBETRADING_IMAGE_ID=image,
                   ARCHIVE_PREFIX='vibetrading/crypto/qualification/service-20261001',
                   ARCHIVE_DELIVERY_HISTORY_RETENTION_SECONDS='3600', MARKET_EVENT_MAX_AGE_SECONDS='3600')
    (EVIDENCE / 'profiles.json').write_text(json.dumps(profiles, indent=2))
    (EVIDENCE / 'trial.env').write_text(''.join(f'{key}={value}\n' for key, value in profiles['crypto-storage'].items()))
    proof = {'baseline_manifest_rowid': baseline, 'parent_integrity': parent,
             'baseline_retired_entries': retired, 'image': image, 'database_copy_added': False,
             'production_retention_changed': False, 'test_delivery_and_live_retention_seconds': 3600,
             'old_replay_remains_independently_archived': True, 'production_data_modified': False}
    (EVIDENCE / 'prepared.json').write_text(json.dumps(proof, indent=2))
    # Preserve previous containers, stream generation, data and evidence.
    run(['docker', 'stop', '--time', '10', 'r2-load-nats'])
    for name in ('r2-load-feeder', 'r2-load-storage', 'r2-load-maintenance', 'r2-load-nats'):
        run(['docker', 'rename', name, name + '-before-service'])
    directory = ROOT / 'nats-service'
    directory.mkdir()
    config = json.loads((ROOT / 'evidence/nats-matched.conf').read_text())
    config['jetstream']['store_dir'] = str(directory)
    path = EVIDENCE / 'nats.conf'
    path.write_text(json.dumps(config))
    run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{path}:/etc/nats/trial.conf:ro',
         NATS_IMAGE, '-t', '-c', '/etc/nats/trial.conf'])
    run(['docker', 'run', '-d', '--name', 'r2-load-nats', '--network', NETWORK, '--memory', '512m',
         '-v', f'{directory}:{directory}', '-v', f'{path}:/etc/nats/trial.conf:ro', NATS_IMAGE, '-c', '/etc/nats/trial.conf'])
    initialize()
    print(json.dumps(proof), flush=True)


def initialize():
    if subprocess.run(['docker', 'inspect', 'r2-load-storage'], capture_output=True).returncode == 0:
        raise RuntimeError('new storage already exists; refuse repeated initialization')
    profiles = json.loads((EVIDENCE / 'profiles.json').read_text())
    image = image_id()
    code = 'import asyncio; from vibetrading.bus import NatsJetStreamBus; from vibetrading.settings import get_settings\n'
    code += 'async def go():\n b=NatsJetStreamBus(get_settings())\n await b.connect(initialize=True)\n await b.close()\nasyncio.run(go())'
    run(['docker', 'run', '--rm', '--network', NETWORK, '--memory', '256m', '-v', f'{ROOT}:{ROOT}',
         '--env-file', '/etc/vibetrading/app.env'] + env_args(profiles['crypto-storage']) + [image, 'python', '-c', code])
    common = ['docker', 'run', '-d', '--network', NETWORK, '-v', f'{ROOT}:{ROOT}',
              '--env-file', '/etc/vibetrading/app.env', '--env-file', str(RELEASE / 'deploy/crypto-release.env')]
    run(common + ['--name', 'r2-load-storage', '--memory', '768m'] + env_args(profiles['crypto-storage'])
        + [image, 'python', 'main.py', 'run', '--role', 'crypto-storage'])
    print(json.dumps({'initialized_private_nats': True, 'new_storage_started': True}), flush=True)


def prepare_test_policy():
    state = json.loads(run(['docker', 'inspect', '--format', '{{json .State}}', 'r2-load-storage']))
    if state['Running'] or state['ExitCode'] != 1:
        raise RuntimeError('only the interrupted pre-feed policy setup can be recovered')
    for name in ('r2-load-feeder', 'r2-load-maintenance'):
        if subprocess.run(['docker', 'inspect', name], capture_output=True).returncode == 0:
            raise RuntimeError('test producer/archive must not exist before policy preparation')
    image = image_id()
    run(['docker', 'run', '--rm', '--network', 'none', '--user', '0:0', '--memory', '768m',
         '-v', f'{ROOT}:{ROOT}', '-v', f'{SCRIPTS}:/app/scripts:ro', '-e', 'PYTHONPATH=/app', image,
         'ionice', '-c3', 'nice', '-n', '10', 'python', '/app/scripts/prepare_trial_retention_policy.py'])
    profiles = json.loads((EVIDENCE / 'profiles.json').read_text())
    for env in profiles.values():
        env['VIBETRADING_IMAGE_ID'] = image
    (EVIDENCE / 'profiles.json').write_text(json.dumps(profiles, indent=2))
    (EVIDENCE / 'trial.env').write_text(''.join(f'{key}={value}\n' for key,value in profiles['crypto-storage'].items()))
    proof_path = EVIDENCE / 'prepared.json'
    proof = json.loads(proof_path.read_text())
    proof_path.rename(EVIDENCE / 'prepared-before-test-policy.json')
    proof.update(image=image, offline_test_policy_proof=str(EVIDENCE / 'offline-test-policy.json'),
                 setup_failures_before_any_input=['initializer omitted private mount', 'retained replay policy blocked narrowing'])
    proof_path.write_text(json.dumps(proof, indent=2))
    run(['docker', 'rename', 'r2-load-storage', 'r2-load-storage-before-test-policy'])
    initialize()


def feed():
    if (EVIDENCE / 'replay.json').exists() or subprocess.run(['docker', 'inspect', 'r2-load-feeder'], capture_output=True).returncode == 0:
        raise RuntimeError('service feeder exists/completed; refuse repeat')
    image = image_id()
    health = json.loads((ROOT / 'state/health/crypto-storage.json').read_text())
    if (health.get('ready') is not True or health.get('state') != 'running'
            or time.time_ns() - health['ts_ns'] > 60 * 10**9
            or health.get('event_index', {}).get('validated_entries', 0) < 9240414):
        raise RuntimeError('full-size storage warmup/readiness gate not satisfied')
    profiles = json.loads((EVIDENCE / 'profiles.json').read_text())
    common = ['docker', 'run', '-d', '--network', NETWORK, '-v', f'{ROOT}:{ROOT}',
              '--env-file', '/etc/vibetrading/app.env', '--env-file', str(RELEASE / 'deploy/crypto-release.env')]
    run(common + ['--name', 'r2-load-maintenance', '--memory', '512m',
                  '-v', '/etc/vibetrading/secrets/archive_access_key:/run/secrets/archive_access_key:ro',
                  '-v', '/etc/vibetrading/secrets/archive_secret_key:/run/secrets/archive_secret_key:ro']
        + env_args(profiles['crypto-maintenance']) + [image, 'ionice', '-c3', 'nice', '-n', '10',
                                                     'python', 'main.py', 'run', '--role', 'crypto-maintenance'])
    run(common + ['--name', 'r2-load-feeder', '--memory', '384m', '-v', f'{SCRIPTS}:/app/scripts:ro',
                  '-v', f'{TRACE}:/trace:ro', '-e', 'PYTHONPATH=/app'] + env_args(profiles['crypto-storage'])
        + [image, 'python', '/app/scripts/replay_crypto_nats.py', '--input', '/trace/crypto-replay-burst.jsonl',
           '--report', '/trace/crypto-replay-burst.report.json', '--env', str(EVIDENCE / 'trial.env'),
           '--result', str(EVIDENCE / 'replay.json'), '--duration-seconds', '1800', '--multiplier', '2'])
    print(json.dumps({'service_replay_started': True, 'image': image, 'production_modified': False}), flush=True)


def watch():
    import watch_crypto_load_trial as observer
    observer.EVIDENCE = EVIDENCE
    observer.main()


def finish(phase):
    import finalize_crypto_load_trial as finalizer
    finalizer.EVIDENCE = EVIDENCE
    finalizer.IMAGE = image_id()
    getattr(finalizer, phase)()


if __name__ == '__main__':
    phase = sys.argv[1]
    if phase in {'drain', 'audit'}:
        finish(phase)
    else:
        {'setup': setup, 'initialize': initialize, 'prepare-test-policy': prepare_test_policy,
         'feed': feed, 'watch': watch}[phase]()
