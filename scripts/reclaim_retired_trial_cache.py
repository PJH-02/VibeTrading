"""Remove only the private, reclaimable cache subtree of a retired trial."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT_ID = '82xsps2yjmen5c5bh5dl45hkn'
RECEIPT = Path('/opt/vibetrading/r2-prune-test-20261001/retired-trial-cache-receipt.json')


def main():
    raw = subprocess.check_output(['docker', 'buildx', 'du', '--format', 'json'], text=True)
    records = {row['ID']: row for row in (json.loads(line) for line in raw.splitlines() if line.strip())}
    if ROOT_ID not in records or records[ROOT_ID]['Description'] != 'mount / from exec /bin/sh -c pip install --no-cache-dir -r requirements.txt':
        raise RuntimeError('retired cache identity changed')
    selected = {ROOT_ID}
    while True:
        added = {key for key, row in records.items() if selected.intersection(row.get('Parents') or [])} - selected
        if not added:
            break
        selected.update(added)
    # Descendants hold the root snapshot; deleting the root record alone freed
    # zero bytes. Refuse any descendant that is in use or shared with an image.
    if any(not records[key]['Reclaimable'] or records[key]['Shared'] for key in selected):
        raise RuntimeError('retired subtree contains a protected/shared cache record')
    expected_ids = sorted(selected)
    selector = 'id~=^(' + '|'.join(re.escape(key) for key in expected_ids) + ')$'
    verified = subprocess.check_output(['docker', 'buildx', 'du', '--filter', selector, '--format', 'json'], text=True)
    actual_ids = sorted(json.loads(line)['ID'] for line in verified.splitlines() if line.strip())
    if actual_ids != expected_ids:
        raise RuntimeError('exact cache filter selection mismatch')
    before = shutil.disk_usage('/').free
    receipt = {'root_cache_id': ROOT_ID, 'selected': [records[key] for key in expected_ids],
               'free_bytes_before': before, 'image_or_volume_deletion': False}
    RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n')
    result = subprocess.check_output(['docker', 'buildx', 'prune', '--filter', selector, '--force'], text=True)
    receipt.update(prune_output=result.strip(), free_bytes_after=shutil.disk_usage('/').free,
                   reclaimed_bytes=shutil.disk_usage('/').free - before)
    RECEIPT.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'selected'}))
    print(json.dumps({'selected_records': len(expected_ids)}))


if __name__ == '__main__':
    main()
