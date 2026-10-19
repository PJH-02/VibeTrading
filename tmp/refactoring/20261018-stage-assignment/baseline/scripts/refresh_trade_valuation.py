"""Read two mock accounts and retain their daily executions; submit no orders."""
import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from vibetrading.etf_trading import EtfTradingLoop

async def main():
    loop=EtfTradingLoop()
    day=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d')
    data=await loop.account_report(day)
    print(json.dumps({'day':day,'actual_net_pnl':data['net_pnl'],'actual_fees':data['fees'],
        'adjusted_net_pnl':data['adjusted_net_pnl'],'adjusted_fees':data['adjusted_fees'],
        'account_scopes':list(data['accounts']),'broker_fills_complete':data.get('broker_fills_complete'),
        'fill_rows':loop.journal.db.execute('SELECT COUNT(*) FROM broker_trades WHERE day=?',(day,)).fetchone()[0]}))
    loop.journal.db.close()

asyncio.run(main())
