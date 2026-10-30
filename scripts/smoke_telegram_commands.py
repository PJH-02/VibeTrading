"""Exercise replies to the configured user's requested status/trade commands."""
import asyncio
import json
import time
from pathlib import Path

import aiohttp
from operations.telegram import TelegramController

async def main():
    bot=TelegramController(); assert bot.enabled
    result={'checked_ns':time.time_ns(),'commands':[]}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        me=await bot._api(session,'getMe',{})
        assert me['ok']
        webhook=await bot._api(session,'getWebhookInfo',{})
        result['webhook_configured']=bool(webhook['result'].get('url'))
        assert not result['webhook_configured']
        for command in ['status','trade']:
            await bot._handle(session,{'message':{'from':{'id':int(bot.allowed_id)},'chat':{'id':int(bot.allowed_id)},'text':command}})
            result['commands'].append({'command':command,'delivered':True})
    path=bot.settings.state_dir/'telegram-command-verification.json'
    path.write_text(json.dumps(result,indent=2))
    print(json.dumps(result))

asyncio.run(main())
