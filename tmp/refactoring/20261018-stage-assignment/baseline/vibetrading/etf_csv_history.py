"""Shared ticker CSV history, with atomic close-only appends and receipts."""
import csv
import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from .maintenance_gate import _try_lock, _unlock

ETF_HISTORY_ROLES = {s:(s+'_daily',s+'_minute') for s in ('069500','122630','229200','251340')}
CSV_FILES = {key:'raw/'+key+'.csv' for pair in ETF_HISTORY_ROLES.values() for key in pair}
CSV_FILES.update(KOSPI200_daily='raw/KOSPI200_daily.csv', SP500_daily='raw/SP500_daily.csv')


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        while chunk:=handle.read(1024*1024): digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as handle:
        json.dump(data,handle,indent=2,ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary,path)
    if os.name != 'nt':
        fd = os.open(path.parent,os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)


@contextmanager
def csv_lock(root):
    with (root/'.csv.lock').open('a+') as handle:
        deadline = time.monotonic()+30
        while not _try_lock(handle):
            if time.monotonic()>deadline: raise RuntimeError('Ticker CSV writer is busy')
            time.sleep(.05)
        try: yield
        finally: _unlock(handle)


def recover_append(root):
    pending = root/'.append-transaction.json'
    if not pending.exists(): return
    tx = json.loads(pending.read_text(encoding='utf-8'))
    target = root/CSV_FILES[tx['key']]
    actual = digest_file(target)
    if actual==tx['entry']['sha256']:
        manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        manifest['files'][tx['key']] = tx['entry']
        atomic_json(root/'manifest.json',manifest)
    elif actual!=tx['old_sha256']:
        raise RuntimeError('CSV interrupted append has an unexpected checksum')
    target.with_suffix('.append.tmp').unlink(missing_ok=True)
    pending.unlink()


class CsvStrategyHistory:
    def __init__(self, root: Path):
        self.root = root
        with csv_lock(root):
            recover_append(root)
            self.manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
            if set(self.manifest.get('files',{})) != set(CSV_FILES):
                raise RuntimeError('Ticker CSV bundle is incomplete')
            for key,row in self.manifest['files'].items():
                if row['path'] != CSV_FILES[key]: raise ValueError('CSV ticker path does not match its dataset')
                if digest_file(root/row['path']) != row['sha256']: raise RuntimeError('Ticker CSV checksum mismatch')

    def daily(self, key: str, symbol: str) -> pd.DataFrame:
        row = self.manifest['files'][key]
        data = pd.read_csv(self.root/row['path'],dtype={'symbol':str})
        data['timestamp'] = pd.to_datetime(data['date'])
        data['date'] = data.timestamp.dt.strftime('%Y-%m-%d')
        if 'symbol' in data and symbol.isdigit() and not data.symbol.str.zfill(6).eq(symbol).all():
            raise RuntimeError('Daily CSV ticker mismatch')
        data['symbol'] = symbol
        if 'source' not in data: data['source'] = row['provider']
        if 'trading_value' not in data: data['trading_value'] = float('nan')
        return data.sort_values('timestamp').drop_duplicates('timestamp')

    def minute(self, key: str, dates: set[str]) -> pd.DataFrame:
        row = self.manifest['files'][key]
        symbol = next(s for s,pair in ETF_HISTORY_ROLES.items() if key==pair[1])
        parts = []
        for chunk in pd.read_csv(self.root/row['path'],dtype={'date':str,'symbol':str},chunksize=8192):
            selected = chunk[chunk.date.isin(dates)].copy()
            if selected.empty: continue
            selected['symbol'] = selected.symbol.str.zfill(6)
            if not selected.symbol.eq(symbol).all(): raise RuntimeError(f'CSV execution vehicle does not match {symbol}')
            if not selected.timestamp_semantics.eq('bar_end').all(): raise RuntimeError('CSV minute data is not labelled by completed bar end')
            selected['timestamp'] = pd.to_datetime(selected.timestamp,format='mixed')
            if selected.timestamp.dt.tz is not None:
                selected['timestamp'] = selected.timestamp.dt.tz_convert('Asia/Seoul').dt.tz_localize(None)
            parts.append(selected)
        return pd.concat(parts,ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp') if parts else pd.DataFrame()

    def append_closed(self, key: str, rows: list[dict]) -> dict:
        """Append only new completed dates; keep user-supplied history intact."""
        with csv_lock(self.root):
            recover_append(self.root)
            manifest = json.loads((self.root/'manifest.json').read_text(encoding='utf-8'))
            old = manifest['files'][key]
            target = self.root/old['path']
            if digest_file(target)!=old['sha256']: raise RuntimeError('CSV changed outside the checked writer')
            rows = [r for r in rows if r['date']>old['last_date']]
            if not rows: return {'key':key,'appended_rows':0,'last_date':old['last_date']}
            rows = sorted(rows,key=lambda r:r.get('timestamp',r['date']))
            with target.open(encoding='utf-8-sig',newline='') as handle: columns = next(csv.reader(handle))
            temporary = target.with_suffix('.append.tmp')
            shutil.copyfile(target,temporary)
            with temporary.open('ab') as handle:
                with target.open('rb') as original:
                    original.seek(-1,2)
                    if original.read(1)!=b'\n': handle.write(b'\n')
            with temporary.open('a',encoding='utf-8',newline='') as handle:
                writer = csv.DictWriter(handle,fieldnames=columns,extrasaction='ignore')
                for row in rows: writer.writerow({c:row.get(c,'') for c in columns})
                handle.flush()
                os.fsync(handle.fileno())
            entry = old | {'sha256':digest_file(temporary),'bytes':temporary.stat().st_size,
                'rows':old['rows']+len(rows),'last_date':max(r['date'] for r in rows),'updated_ns':time.time_ns()}
            atomic_json(self.root/'.append-transaction.json',{'key':key,'old_sha256':old['sha256'],'entry':entry})
            os.replace(temporary,target)
            manifest['files'][key] = entry
            atomic_json(self.root/'manifest.json',manifest)
            (self.root/'.append-transaction.json').unlink()
            self.manifest = manifest
            return {'key':key,'appended_rows':len(rows),'last_date':entry['last_date']}

    @staticmethod
    def merge_daily(reference, refreshed):
        return pd.concat([reference,refreshed[refreshed.timestamp>reference.timestamp.max()]],ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp')

    @staticmethod
    def merge_minutes(reference, refreshed):
        if reference.empty: return refreshed
        return pd.concat([reference,refreshed[~refreshed.date.isin(reference.date.unique())]],ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp')
