"""Shared append lock for live capture and bounded historical repair."""
import json
import os
from pathlib import Path
from .etf_csv_history import csv_lock


def merge_session_rows(root: Path, day: str, symbol: str, rows: list[dict], *, replace=False):
    root.mkdir(parents=True, exist_ok=True)
    path = root/(day+'-'+symbol+'.jsonl')
    with csv_lock(root):
        existing = {}
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    row = json.loads(line); existing[row['timestamp']] = row
                except (ValueError,KeyError):
                    continue
        added = []
        fields = ('open','high','low','close','volume')
        for row in sorted(rows,key=lambda r:r['timestamp']):
            if row['date'] != day or row['symbol'] != symbol:
                raise ValueError('Session row identity mismatch')
            old = existing.get(row['timestamp'])
            same = old is not None and all(float(old[k]) == float(row[k]) for k in fields)
            if old is not None and (same or not replace):
                continue
            added.append(row); existing[row['timestamp']] = row
        if added:
            with path.open('a',encoding='utf-8') as handle:
                handle.write(''.join(json.dumps(r,allow_nan=False)+'\n' for r in added))
                handle.flush(); os.fsync(handle.fileno())
    return added
