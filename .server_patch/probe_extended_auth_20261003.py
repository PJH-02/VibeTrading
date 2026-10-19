"""Read-only authentication and signing-key validation; never submits orders."""
import asyncio, dataclasses, json
import aiohttp
from decimal import Decimal, ROUND_CEILING
from vibetrading.models import OrderIntent,OrderDestination,Side,Venue
from vibetrading.providers.crypto_perp import _instrument,SYMBOLS
from vibetrading.settings import get_settings
from vibetrading.extended_brokers import ArcusBrokerAdapter, GrvtBrokerAdapter

async def main():
    s=get_settings()
    # `docker run --env-file` preserves quotes that Docker Compose strips.
    env={k:v[1:-1] if len(v)>=2 and v[0]==v[-1] and v[0] in ('"',"' ".strip()) else v for k,v in s.env.items()}
    s=dataclasses.replace(s,env=env)
    output=[]
    for factory,overrides in [(ArcusBrokerAdapter,{'ARCUS_USE_TESTNET':'false'}),
        (GrvtBrokerAdapter,{'GRVT_USE_TESTNET':'true'}),
        (GrvtBrokerAdapter,{'GRVT_USE_TESTNET':'false'})]:
        broker=factory(dataclasses.replace(s,env={**s.env,**overrides}))
        result={'venue':broker.name,'testnet':broker.use_testnet,'order_submission_attempted':False}
        result['live_orders_enabled']=s.live_order_enabled
        result['venue_orders_enabled']=str(broker.env.get(broker.name.upper()+'_ORDER_SUBMIT_ENABLED','false')).lower() in ('true','1','yes','on')
        try:
            await broker.connect()
            result['connect']='ok'
            prepared=[]
            for symbol in SYMBOLS:
                venue=Venue.ARCUS if broker.name=='arcus' else Venue.GRVT
                native=_instrument(venue,symbol).raw_symbol
                market=broker.markets[native] if broker.name=='arcus' else broker.instruments[native]
                tick=Decimal(market['tickSize'] if broker.name=='arcus' else market['tick_size'])
                reference=Decimal(str(market.get('markPrice',{'BTCUSDT':50000,'ETHUSDT':3000,'SOLUSDT':100}[symbol])))
                price=(reference/tick).to_integral_value()*tick
                step=Decimal(market['stepSize']) if broker.name=='arcus' else Decimal(10)**-int(market['base_decimals'])
                minimum=Decimal(market.get('minOrderSize','0') if broker.name=='arcus' else market['min_size'])
                notional=Decimal(str(market.get('minOrderNotional','0') if broker.name=='arcus' else market.get('min_notional','0')))
                quantity=(max(minimum,notional/price,step)/step).to_integral_value(rounding=ROUND_CEILING)*step
                broker.prepare_order(OrderIntent(intent_id='offline-validation-'+symbol,job_id='validation',signal_id='validation',
                    destination=OrderDestination.REAL,instrument=_instrument(venue,symbol),side=Side.BUY,
                    client_order_id='offline-validation-'+symbol,quantity=quantity,limit_price=price))
                prepared.append(symbol)
            result['signed_with_live_market_metadata']=prepared
            if broker.name=='arcus':
                async with aiohttp.ClientSession() as session:
                    async with session.get(broker.rest_url+'/v1/apiKeys',params={'address':broker.address}) as response:
                        result['api_key_registry_status']=response.status
                        if response.status==200:
                            registry=await response.json()
                            result['api_key_registered']=any(r.get('apiKey','').removeprefix('0x').lower()==broker.api_key.removeprefix('0x').lower() for r in registry.get('apiKeys',[]))
            state=await broker.account_state()
            result['account_read']='ok'
            result['account_response_keys']=list(state)
        except Exception as exc:
            # Error bodies can contain account information. Record only the
            # error class and our own known configuration errors.
            result['error_class']=type(exc).__name__
            text=str(exc)
            safe=('Arcus key must','Arcus registered wallet','Arcus Ed25519 API key',
                'GRVT signing key does not','GRVT API-key login rejected','GRVT API key is registered','GRVT login did not')
            if text.startswith(safe): result['configuration_error']=text
            status=getattr(exc,'status',None)
            if status: result['http_status']=status
            if broker.name=='grvt':
                async with aiohttp.ClientSession() as session:
                    async with session.post(broker.auth_url,json={'api_key':broker.api_key}) as response:
                        result['auth_content_type']=response.headers.get('Content-Type')
                        result['auth_challenge']=response.headers.get('cf-mitigated')
                        if response.headers.get('Content-Type','').startswith('application/json'):
                            body=await response.json()
                            clean=json.dumps(body)
                            for k,v in s.env.items():
                                if v and len(v)>4 and any(w in k for w in ('KEY','SECRET','TOKEN','PASSWORD','ADDRESS','ACCOUNT')):
                                    clean=clean.replace(v,'[redacted]')
                            result['auth_response']=json.loads(clean)
        finally:
            await broker.close()
        output.append(result)
    print(json.dumps(output,indent=2))
    import pathlib
    pathlib.Path('/tmp/expansion-auth-probe.json').write_text(json.dumps(output,indent=2))

asyncio.run(main())
