"""Package the user-maintained ticker CSVs without rewriting their bytes."""
import csv
import hashlib
import json
import tarfile
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from vibetrading.etf_csv_history import CSV_FILES
from vibetrading.etf_csv_sync import latest_completed_session
from datetime import datetime, timezone

root = Path(__file__).resolve().parents[1]
destination = root/'runtime_data/etf_reference_csv'
manifest = {'version':2,'storage':'shared_ticker_csv_close_only_append','files':{}}
for key,relative in CSV_FILES.items():
    path = destination/relative
    count,last = 0,''
    symbol = key.rsplit('_',1)[0]
    with path.open(encoding='utf-8-sig',newline='') as handle:
        for row in csv.DictReader(handle):
            if row['date']<last: raise ValueError(f'{path.name}: dates are not ordered')
            last = row['date']
            count += 1
            if key.endswith('_minute'):
                assert row['timestamp_semantics']=='bar_end'
                assert str(row['symbol']).zfill(6)==symbol
    assert count>0
    if key=='SP500_daily':
        assert last<=latest_completed_session('XNYS',datetime.now(timezone.utc)), 'SP500 contains a session that has not closed'
    manifest['files'][key] = {'path':relative,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'bytes':path.stat().st_size,'rows':count,'last_date':last,'symbol':symbol,
        'provider':'TOSS' if symbol.isdigit() else 'user_index_csv',
        'timestamp_semantics':'bar_end' if key.endswith('_minute') else 'completed_session'}
(destination/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
provenance_path = root/'config/etf_strategy_provenance.json'
provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
provenance['history'] = {'route':'shared ticker CSV + intraday live data; close-only append','files':manifest['files']}
provenance['shared_inputs'] = {'C3':['069500_daily','069500_minute','122630_daily','122630_minute','KOSPI200_daily','SP500_daily'],
    'KP':['069500_daily','069500_minute','122630_daily','122630_minute'],
    'KD':['229200_daily','229200_minute','251340_daily','251340_minute']}
provenance_path.write_text(json.dumps(provenance,indent=2,ensure_ascii=False),encoding='utf-8')
bundle = root/'.server_patch/etf-reference-csv-20261013.tar.gz'
with tarfile.open(bundle,'w:gz') as archive:
    for name in ['manifest.json',*CSV_FILES.values()]:
        archive.add(destination/name,arcname=name,recursive=False)
print(json.dumps({'bundle':str(bundle),'files':len(CSV_FILES),'bytes':sum(r['bytes'] for r in manifest['files'].values())}))
