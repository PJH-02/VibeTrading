"""Host orchestrator for the existing completed trial, never production data."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

from setup_crypto_load_trial import ROOT, RELEASE, SCRIPTS, TRACE, IMAGE, NETWORK, env_args, run
EVIDENCE = ROOT / 'evidence'


def drain():
    complete = json.loads((EVIDENCE / 'monitor-complete.json').read_text())
    replay = json.loads((EVIDENCE / 'replay.json').read_text())
    if not complete.get('feeder_complete') or replay.get('publish_errors') != 0:
        raise RuntimeError('successful completed producer/observer required')
    proof = json.loads((EVIDENCE / 'prepared.json').read_text())
    ip = run(['docker', 'inspect', '--format', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}', 'r2-load-nats'])
    started = time.monotonic()
    while time.monotonic() - started < 1200:
        if shutil.disk_usage(ROOT).free < 4 * 1024**3:
            raise RuntimeError('protected disk reserve reached; preserve evidence and stop')
        with urllib.request.urlopen('http://' + ip + ':8222/jsz?streams=true&consumers=true', timeout=3) as response:
            data = json.load(response)
        stream = next(row for account in data['account_details'] for row in account['stream_detail']
                      if row['name'] == 'CRYPTO_DATA')
        consumer = next(row for row in stream['consumer_detail'] if row['name'] == 'crypto-parquet-storage')
        # Only inspect the bounded new manifest range, never scan deliveries.
        conn = sqlite3.connect((ROOT / 'state/vibetrading.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            conn.execute('PRAGMA query_only=ON')
            missing = conn.execute("SELECT COUNT(*) FROM parquet_manifest WHERE rowid>? AND lifecycle_state='active' "
                                   "AND (upload_status!='remote_verified' OR verified_ts_ns IS NULL)",
                                   (proof['baseline_manifest_rowid'],)).fetchone()[0]
        finally:
            conn.close()
        status = {'phase': 'drain_and_archive', 'elapsed_seconds': round(time.monotonic() - started, 1),
                  'messages': stream['state']['messages'], 'pending': consumer['num_pending'],
                  'ack_pending': consumer['num_ack_pending'], 'server_ack_floor': consumer['ack_floor']['stream_seq'],
                  'unverified_new_canonical_files': missing, 'free_bytes': shutil.disk_usage(ROOT).free}
        print(json.dumps(status), flush=True)
        if (not missing and not status['messages'] and not status['pending'] and not status['ack_pending']
                and status['server_ack_floor'] == replay['sent'] and stream['state']['last_seq'] == replay['sent']):
            run(['docker', 'stop', '--time', '90', 'r2-load-storage', 'r2-load-maintenance'])
            for name in ('r2-load-storage', 'r2-load-maintenance'):
                state = json.loads(run(['docker', 'inspect', '--format', '{{json .State}}', name]))
                if state['Running'] or state['ExitCode'] != 0:
                    raise RuntimeError('test writer failed to stop cleanly; audit not authorized')
            with (EVIDENCE / 'drained.json').open('x') as handle:
                json.dump(status, handle, indent=2)
            return
        time.sleep(30)
    raise RuntimeError('archive/drain deadline exceeded; preserve trial evidence')


def audit():
    json.loads((EVIDENCE / 'drained.json').read_text())
    proof = json.loads((EVIDENCE / 'prepared.json').read_text())
    for name in ('r2-load-storage', 'r2-load-maintenance'):
        state = json.loads(run(['docker', 'inspect', '--format', '{{json .State}}', name]))
        if state['Running'] or state['ExitCode'] != 0:
            raise RuntimeError('test writers must be stopped before read-only terminal audit')
    profiles = json.loads((EVIDENCE / 'profiles.json').read_text())
    # Trial root is host-owned; root only creates/removes private audit scratch.
    # SQLite remains URI mode=ro/query_only and production is never mounted.
    args = ['docker', 'run', '--rm', '--name', 'r2-load-integrity', '--user', '0:0', '--network', NETWORK, '--memory', '512m',
            '-v', f'{ROOT}:{ROOT}', '-v', f'{SCRIPTS}:/app/scripts:ro', '-v', f'{TRACE}:/trace:ro',
            '-v', '/etc/vibetrading/secrets/archive_access_key:/run/secrets/archive_access_key:ro',
            '-v', '/etc/vibetrading/secrets/archive_secret_key:/run/secrets/archive_secret_key:ro',
            '--env-file', '/etc/vibetrading/app.env', '-e', 'PYTHONPATH=/app']
    subprocess.run(args + env_args(profiles['crypto-maintenance']) + [IMAGE, 'ionice', '-c3', 'nice', '-n', '10',
                   'python', '/app/scripts/verify_crypto_replay_integrity.py',
                   '--env', str(EVIDENCE / 'trial.env'), '--root', str(ROOT),
                   '--input', '/trace/crypto-replay-burst.jsonl', '--report', '/trace/crypto-replay-burst.report.json',
                   '--feeder-result', str(EVIDENCE / 'replay.json'), '--result', str(EVIDENCE / 'integrity.json'),
                   '--baseline-manifest-rowid', str(proof['baseline_manifest_rowid'])], check=True)


def probe_prune():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--limit', type=int, choices=(8192, 32768, 65536), default=8192)
    parser.add_argument('--retention-seconds', type=int, choices=(1, 86400), default=1)
    parser.add_argument('--batches', type=int, choices=range(1, 49), default=16)
    options = parser.parse_args(sys.argv[2:])
    for name in ('r2-load-storage', 'r2-load-maintenance'):
        state = json.loads(run(['docker', 'inspect', '--format', '{{json .State}}', name]))
        if state['Running'] or state['ExitCode'] != 0:
            raise RuntimeError('test writers must remain stopped for the bounded prune probe')
    proof = json.loads((ROOT / 'evidence/integrity.json').read_text())
    if proof.get('storage_ack_archive_integrity_verified') is not True:
        raise RuntimeError('completed exact audit required before trial mutations')
    release = options.release.resolve(strict=True)
    if release.parent != Path('/opt/vibetrading/releases'):
        raise RuntimeError('staged crypto release required')
    image = json.loads((release / '.deploy-release.json').read_text())['images']['crypto']['id']
    env = json.loads((ROOT / 'evidence/profiles.json').read_text())['crypto-maintenance']
    env['VIBETRADING_IMAGE_ID'] = image
    # No object-store credential mounts: the probe consumes verified receipts
    # already in the disposable catalogue, plus the actual private NATS floor.
    subprocess.run(['docker', 'run', '--rm', '--name', 'r2-prune-capacity', '--user', '0:0',
                    '--network', NETWORK, '--memory', '512m', '-v', f'{ROOT}:{ROOT}',
                    '-v', f'{SCRIPTS}:/app/scripts:ro', '--env-file', '/etc/vibetrading/app.env',
                    '--env-file', str(release / 'deploy/crypto-release.env'), '-e', 'PYTHONPATH=/app']
                   + env_args(env) + [image, 'ionice', '-c3', 'nice', '-n', '10', 'python',
                                     '/app/scripts/probe_crypto_prune_capacity.py', '--root', str(ROOT),
                                     '--label', options.label, '--limit', str(options.limit),
                                     '--retention-seconds', str(options.retention_seconds),
                                     '--batches', str(options.batches)], check=True)


if __name__ == '__main__':
    {'drain': drain, 'audit': audit, 'probe-prune': probe_prune}[sys.argv[1]]()
