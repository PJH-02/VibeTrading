"""Verify actual status delivery and journal readiness, without test orders."""
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0,'/app')
import aiohttp
from runtime.settings import get_settings
from operations.telegram import TelegramController


async def main():
    settings=get_settings(); bot=TelegramController(settings)
    message=bot._compact_status()
    assert all(label+':' in message for label in ['KIS','Kiwoom','TOSS','Binance','Arcus','Grvt'])
    assert 'Bybit' not in message and 'Bitget' not in message
    receipts=[]
    async def send(session,chat_id,text):
        result=await bot._api(session,'sendMessage',{'chat_id':chat_id,'text':text})
        receipts.append(result['result']['message_id'])
    bot._send=send
    async with aiohttp.ClientSession() as session:
        await bot._handle(session,{'message':{'from':{'id':settings.telegram_user_id},
            'chat':{'id':settings.telegram_user_id},'text':'/status'}})
    db=sqlite3.connect('file:'+str(settings.state_dir/'etf-mock/journal.sqlite3')+'?mode=ro',uri=True)
    notifications=[list(r) for r in db.execute('SELECT kind,COUNT(*),SUM(sent_ns IS NOT NULL) FROM notifications GROUP BY kind')]
    db.close()
    health={}
    for component in ('etf-mock','etf-csv-sync'):
        data=json.loads((settings.state_dir/'health'/(component+'.json')).read_text())
        health[component]={k:data.get(k) for k in ['phase','account_mode','pending_orders','vehicle_bars']}
        health[component]['heartbeat_age_seconds']=(time.time_ns()-data['ts_ns'])/1e9
    print(json.dumps({'telegram_message_ids':receipts,'status':message,'notifications':notifications,'health':health},ensure_ascii=False))

asyncio.run(main())
