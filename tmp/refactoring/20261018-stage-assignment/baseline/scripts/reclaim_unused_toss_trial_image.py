"""Reclaim exactly one retired trial image; no force, volumes, or containers."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

TAG = 'vibetrading-toss-daymarket:20260928'
EXPECTED_PREFIX = 'sha256:d9453df9753c'
RECEIPT = Path('/opt/vibetrading/r2-prune-test-20261001/retired-trial-image-receipt.json')


def command(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def main():
    image = json.loads(command('image', 'inspect', TAG))[0]
    if not image['Id'].startswith(EXPECTED_PREFIX) or image['RepoTags'] != [TAG]:
        raise RuntimeError('retired trial image identity changed')
    # Includes stopped containers. Docker rm without --force also enforces this
    # if another process creates a reference between the check and removal.
    if command('ps', '-aq', '--filter', f'ancestor={image["Id"]}'):
        raise RuntimeError('trial image is referenced by a container')
    before = shutil.disk_usage('/').free
    receipt = {'tag': TAG, 'image_id': image['Id'], 'image_bytes': image['Size'],
               'referencing_containers': 0, 'free_bytes_before': before,
               'volumes_deleted': False, 'containers_deleted': False}
    RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n')
    command('image', 'rm', '--no-prune', TAG)
    receipt.update(free_bytes_after=shutil.disk_usage('/').free,
                   reclaimed_bytes=shutil.disk_usage('/').free - before)
    RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
