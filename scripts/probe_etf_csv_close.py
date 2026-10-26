"""Exercise native close updates in a separate sink; never alter live history."""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from storage.etf_csv_history import CsvStrategyHistory, CSV_FILES, ETF_HISTORY_ROLES, digest_file
from feeds.etf_csv_sync import EtfCsvSync
from feeds.providers.kiwoom import KiwoomAuthClient
from vibetrading.settings import get_settings


async def main():
    settings = get_settings()
    original = CsvStrategyHistory(settings.state_dir/'etf-mock/history')
    before = {k:digest_file(original.root/path) for k,path in CSV_FILES.items()}
    destination = settings.state_dir/('etf-csv-close-probe-'+str(time.time_ns()))
    history = destination/'history'
    (history/'raw').mkdir(parents=True)
    (destination/'etf-csv-sync'/'sessions').mkdir(parents=True)
    manifest = {'version':2,'files':{}}
    for key,path in CSV_FILES.items():
        symbol = key.rsplit('_',1)[0]
        if key.endswith('_daily'):
            frame = original.daily(key,symbol)
            frame = frame[frame.date<='2026-10-12'].tail(1)
            if not symbol.isdigit(): frame=frame[['date','open','high','low','close','volume']]
            else: frame['timestamp']=frame['date']
        else:
            frame = original.minute(key,{'2026-10-12'})
        assert not frame.empty
        frame.to_csv(history/path,index=False)
        manifest['files'][key] = original.manifest['files'][key] | {'rows':len(frame),
            'last_date':'2026-10-12','bytes':(history/path).stat().st_size,'sha256':digest_file(history/path)}
    (history/'manifest.json').write_text(json.dumps(manifest))
    service = EtfCsvSync.__new__(EtfCsvSync)
    service.settings = SimpleNamespace(state_dir=destination)
    service.store = CsvStrategyHistory(history)
    service.client = KiwoomAuthClient(settings)
    service.spool = destination/'etf-csv-sync'/'sessions'
    first = await service.sync_once()
    second = await service.sync_once()
    assert len(first['receipts'])==9 and all(r['appended_rows']>0 for r in first['receipts'])
    assert second['receipts']==[]
    assert before=={k:digest_file(original.root/path) for k,path in CSV_FILES.items()}
    result = {'first':first,'second':second,'canonical_files_unchanged':True,'sink':str(destination)}
    (settings.state_dir/'etf-csv-sync/native-close-probe.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


asyncio.run(main())
