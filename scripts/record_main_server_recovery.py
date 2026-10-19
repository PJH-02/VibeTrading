"""Document observable event-time gaps left by the unintended shutdown."""
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from vibetrading.settings import get_settings
from vibetrading.gap_ledger import CollectionGapLedger

settings=get_settings()
boundary=int(datetime(2026,10,14,20,30,tzinfo=ZoneInfo('Asia/Seoul')).timestamp()*1e9)
database=settings.data_root/'crypto_state/vibetrading.sqlite3'
result=[]
with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=3) as db:
    for provider in ['binance-perp','arcus-perp','grvt-perp']:
        before=db.execute("SELECT MAX(max_ts_ns) FROM parquet_manifest WHERE provider=? AND max_ts_ns<? AND lifecycle_state='active' AND event_type IN ('trade','order_book')",(provider,boundary)).fetchone()[0]
        after=db.execute("SELECT MIN(min_ts_ns) FROM parquet_manifest WHERE provider=? AND min_ts_ns>? AND lifecycle_state='active' AND event_type IN ('trade','order_book')",(provider,boundary)).fetchone()[0]
        if before and after and 0<after-before<3_600_000_000_000:
            row={'provider':provider,'last_retained_event_ns':before,'first_retained_event_ns':after,
                'gap_seconds':round((after-before)/1e9,3),'reason':'unintended_server_decommission',
                'basis':'retained active trade/book manifest timestamp bounds; no ticks reconstructed'}
            ledger=CollectionGapLedger(settings.state_dir/'collection_gaps.sqlite3')
            with ledger._connect() as journal:
                if not journal.execute('SELECT 1 FROM gaps WHERE provider=? AND reason=? AND started_ns=?',
                    (provider,row['reason'],before+1)).fetchone():
                    journal.execute('INSERT INTO gaps(provider,symbols,started_ns,ended_ns,reason,details_json) VALUES(?,?,?,?,?,?)',
                        (provider,'BTCUSDT,ETHUSDT,SOLUSDT',before+1,after,row['reason'],json.dumps(row)))
            result.append(row)
path=settings.state_dir/'main-server-recovery-gaps.json'
path.write_text(json.dumps(result,indent=2))
print(json.dumps(result))
