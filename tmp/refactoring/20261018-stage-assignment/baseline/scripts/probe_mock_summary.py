"""Read only mock account summary fields; never submit an order."""
import asyncio
import json
import sqlite3
import sys
sys.path.insert(0,'/app')
from vibetrading.kis_execution import KisCashBroker
from vibetrading.settings import get_settings

async def main():
    broker=KisCashBroker(get_settings(),mode='mock')
    await broker.connect()
    state=await broker.account_state()
    db=sqlite3.connect('file:/data/state/etf-mock/journal.sqlite3?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    orders=[dict(r) for r in db.execute('SELECT strategy,symbol,side,quantity,status,filled,order_number,day FROM orders')]
    db.close()
    print(json.dumps({'mock':state['mock'],'summary':state['summary'],'orders':orders,
        'positions':[{k:r.get(k) for k in ['pdno','hldg_qty','evlu_amt']} for r in state['positions']]}))

asyncio.run(main())
