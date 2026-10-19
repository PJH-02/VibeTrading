"""Read-only production recovery and feed verification; no broker requests."""
import asyncio
import json
import sqlite3
import time
from pathlib import Path

import nats
from vibetrading.settings import get_settings
from vibetrading.telegram_trade import trade_report
from vibetrading.etf_journal import EtfJournal

async def main():
    settings=get_settings(); root=settings.state_dir
    output={'verified_ns':time.time_ns()}
    for role in ['etf-mock','collectors-kis','collectors-kiwoom','collectors-toss','etf-csv-sync']:
        path=root/'health'/(role+'.json')
        if path.exists():
            data=json.loads(path.read_text()); output[role]={k:v for k,v in data.items() if k!='build'}
    journal=EtfJournal(root/'etf-mock/journal.sqlite3')
    output['logical_books']={s:journal.logical_book(s)for s in ('C3','KP','KD')}
    output['reconstructed_orders']=[dict(r)for r in journal.db.execute('SELECT * FROM reconstructed_orders')]
    output['excluded_orders']=[dict(r)for r in journal.db.execute('SELECT intent_id,mirror,reason FROM strategy_order_exclusions')]
    output['report']=trade_report(settings,'2026-10-15')
    output['notifications']=[dict(r)for r in journal.db.execute("SELECT kind,sent_ns FROM notifications WHERE kind='mock_reconstructed'")]
    journal.db.close()
    client=await nats.connect(settings.nats.url)
    sub=await client.subscribe('live.md.index_quote.kis.index.>')
    try:
        msg=await sub.next_msg(timeout=12); event=json.loads(msg.data)
        output['live_index']={k:event.get(k)for k in ('event_type','provider','value','open','source_ts_ns','receive_ts_ns','instrument')}
    except Exception as exc: output['live_index_error']=type(exc).__name__
    await sub.unsubscribe()
    info=await client.jetstream().stream_info('MARKET_DATA')
    output['market_data']={'messages':info.state.messages,'consumer_count':info.state.consumer_count,'subjects':info.config.subjects}
    await client.close()
    print(json.dumps(output,default=str,ensure_ascii=False))

asyncio.run(main())
