"""Verify and deliver the corrected reply to the configured user."""
import asyncio
import json

import aiohttp
from operations.telegram import TelegramController
from operations.telegram_trade import trade_report

async def main():
    bot=TelegramController()
    text=trade_report(bot.settings,'2026-10-14')
    assert '실제 매수' not in text and '실제 매도' not in text
    assert '-882,574.47원' in text and '17,419.47원' in text
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        await bot._handle(session,{'message':{'from':{'id':int(bot.allowed_id)},'chat':{'id':int(bot.allowed_id)},'text':'trade'}})
    print(json.dumps({'trade_reply_delivered':True,'report':text},ensure_ascii=False))

asyncio.run(main())
