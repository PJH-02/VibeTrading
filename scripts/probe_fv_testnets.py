"""Read-only readiness probe; never prints keys, cookies or signatures."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aiohttp
from vibetrading.extended_brokers import GrvtBrokerAdapter
from vibetrading.liquidity_mm.exchange import TestnetClient


async def main():
    result = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        bybit = TestnetClient(session)
        await bybit.sync_clock()
        try:
            result['bybit'] = {'wallet': {k:str(v) for k,v in (await bybit.wallet()).items()},
                               'positions': [{k:p.get(k) for k in ('symbol','side','size','positionIdx')} for p in await bybit.positions()],
                               'open_orders': len(await bybit.open_orders())}
        except RuntimeError as exc:
            result['bybit'] = {'authenticated':False,'error':str(exc)}
        broker = GrvtBrokerAdapter()
        if not broker.use_testnet:
            raise RuntimeError('GRVT testnet mode required')
        try:
            await broker.connect()
            result['grvt'] = {'authenticated':True, 'instruments': {k:{f:v.get(f) for f in ('instrument','tick_size','min_size','base_decimals','min_notional')} for k,v in broker.instruments.items()}}
            for endpoint, payload in [('account_summary',{}),('positions',{}),('open_orders',{}),('get_sub_accounts',{})]:
                params = {'sub_account_id': str(broker.sub_account), **payload} if endpoint != 'get_sub_accounts' else {}
                async with session.post(broker.rest_url+'/full/v1/'+endpoint, json=params, headers=broker._headers) as response:
                    data = await response.json()
                    if endpoint == 'get_sub_accounts':
                        data = {'http_status': response.status, 'count':len(data.get('result',[])) if isinstance(data.get('result'),list) else None, 'error_code':data.get('code')}
                    result['grvt'][endpoint] = data
            async with session.post(broker.market_url+'/full/v1/instruments',json={'kind':['PERPETUAL'],'is_active':True}) as response:
                data = await response.json()
                result['grvt']['available_instruments'] = [{k:r.get(k) for k in ('instrument','min_size','tick_size','base_decimals','min_notional')} for r in data.get('result',[]) if r.get('instrument') in ('BTC_USDT_Perp','ETH_USDT_Perp','SOL_USDT_Perp','XRP_USDT_Perp','DOGE_USDT_Perp')]
        except Exception as exc:
            result['grvt'] = {'authenticated':False,'error':str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__}
        finally:
            await broker.close()
    path = ROOT/'runtime_data'/'fv_mm'/'readiness.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    print(json.dumps(result,indent=2,default=str))


if __name__=='__main__':
    asyncio.run(main())
