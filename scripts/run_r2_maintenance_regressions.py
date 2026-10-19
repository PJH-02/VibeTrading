"""Actual NATS regression with private ephemeral services and no credentials."""
import json
import os
from pathlib import Path
import subprocess

RELEASE = Path(os.environ.get('CRYPTO_RELEASE_ROOT', '/opt/vibetrading/releases/r2-maintenance-service-final-20261001'))
SCRIPTS = Path('/opt/vibetrading/r2-cutover-20260930/scripts')
NETWORK = 'r2-load-gate-20261001'
NATS_IMAGE = 'sha256:b83efabe3e7def1e0a4a31ec6e078999bb17c80363f881df35edc70fcb6bb927'


def run(args):
    return subprocess.check_output(args, text=True).strip()


def main():
    image = json.loads((RELEASE / '.deploy-release.json').read_text())['images']['crypto']['id']
    destination = RELEASE / 'nats-regression.json'
    if destination.exists():
        raise RuntimeError('completed regression receipt already exists')
    results = []
    for name, script in [('r2-prune-nats', 'verify_crypto_prune_generation_nats.py'),
                         ('r2-test-nats', 'verify_event_runs_nats_e2e.py')]:
        if subprocess.run(['docker', 'inspect', name], capture_output=True).returncode == 0:
            raise RuntimeError('test NATS name is already owned; refuse to replace it')
        run(['docker', 'run', '--rm', '-d', '--name', name, '--network', NETWORK, '--memory', '256m',
             NATS_IMAGE, '-js', '-m', '8222'])
        try:
            output = run(['docker', 'run', '--rm', '--network', NETWORK, '--memory', '512m',
                          '-v', f'{SCRIPTS}:/app/scripts:ro', '-e', 'PYTHONPATH=/app',
                          '-e', f'R2_TEST_NATS_URL=nats://{name}:4222', image, 'python', f'/app/scripts/{script}'])
            result = {'script': script, 'output': output}
            results.append(result)
            print(json.dumps(result), flush=True)
        finally:
            run(['docker', 'stop', '--time', '10', name])
    with destination.open('x') as handle:
        json.dump({'image': image, 'results': results, 'production_mounted': False}, handle, indent=2)


if __name__ == '__main__':
    main()
