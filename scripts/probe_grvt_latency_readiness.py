"""GRVT testnet-only read-only readiness, without printing auth material."""
import asyncio
import json
from dataclasses import replace

import aiohttp
from vibetrading.extended_brokers import GrvtBrokerAdapter
from vibetrading.settings import get_settings

async def main():
    settings=get_settings()
    settings=replace(settings,env=dict(settings.env,GRVT_USE_TESTNET='true'))
    broker=GrvtBrokerAdapter(settings)
    assert broker.use_testnet and broker.chain_id==326 and '.testnet.grvt.io' in broker.rest_url
    result={'authenticated':False,'testnet':True}
    try:
        await broker.connect()
        result['authenticated']=True
        result['instruments']={symbol:{k:row.get(k) for k in ['instrument','tick_size','min_size','min_notional','base_decimals']} for symbol,row in broker.instruments.items()}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            for endpoint in ['positions','open_orders','account_summary']:
                async with session.post(broker.rest_url+'/full/v1/'+endpoint,json={'sub_account_id':str(broker.sub_account)},headers=broker._headers) as response:
                    data=await response.json()
                    if response.status!=200 or 'result' not in data:
                        raise RuntimeError(f'{endpoint} rejected status={response.status} code={data.get("code")}')
                    rows=data['result']
                    if endpoint=='positions': result['positions']=[{k:row.get(k) for k in ['instrument','size']} for row in rows if str(row.get('size','0')) not in {'0','0.0'}]
                    elif endpoint=='open_orders': result['open_orders_count']=len(rows)
                    else: result['equity']=rows.get('total_equity'); result['available_balance']=rows.get('available_balance')
    except Exception as exc:
        result['error']=str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
    finally:
        await broker.close()
    print(json.dumps(result))

asyncio.run(main())
