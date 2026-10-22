"""Signed WebSocket order submission for Arcus and GRVT.

Submission requires both LIVE_ORDER_ENABLED and a venue-specific arm.
Transport timeouts are ambiguous outcomes and are never retried here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from decimal import Decimal
from typing import Any

import aiohttp
import websockets

from .models import AssetClass, OrderIntent, Side, Venue
from .providers.crypto_perp import SYMBOLS, _instrument
from .settings import RuntimeSettings, get_settings


class OrderOutcomeUnknown(RuntimeError):
    """Reconcile the client order ID before deciding whether to submit again."""


def _enabled(value: Any) -> bool:
    return str(value).lower() in {'true','1','yes','on'}


def _integer_units(value: Decimal, unit: Decimal, label: str) -> int:
    if not value.is_finite() or value<=0 or unit<=0:
        raise ValueError(f'{label} must be positive and finite')
    scaled=value/unit
    if scaled!=scaled.to_integral_value():
        raise ValueError(f'{label} does not align with the market increment')
    return int(scaled)


def _validate(intent: OrderIntent, venue: Venue) -> str:
    if intent.instrument.venue!=venue or intent.instrument.asset_class!=AssetClass.CRYPTO_PERP:
        raise ValueError('order venue/product does not match adapter')
    symbol=intent.instrument.std_symbol
    if symbol not in SYMBOLS or intent.side not in {Side.BUY,Side.SELL}:
        raise ValueError('order is outside BTC/ETH/SOL perpetual allowlist')
    if intent.quantity is None or not intent.quantity.is_finite() or intent.quantity<=0:
        raise ValueError('order requires a positive finite quantity')
    return _instrument(venue,symbol).raw_symbol


class _WebSocketBroker:
    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings=settings or get_settings()
        self.env=self.settings.env
        self.use_testnet=_enabled(self.env.get(self.name.upper()+'_USE_TESTNET','true'))
        self._ws: Any=None
        self._rpc_lock=asyncio.Lock()
        self._request_id=0

    def _assert_submit_enabled(self) -> None:
        if not self.settings.live_order_enabled or not _enabled(self.env.get(self.name.upper()+'_ORDER_SUBMIT_ENABLED','false')):
            raise RuntimeError(f'{self.name} order submission is disabled')

    async def close(self) -> None:
        ws,self._ws=self._ws,None
        if ws is not None:
            await ws.close()

    async def _rpc(self, message: dict[str,Any], *, headers: dict[str,str] | None = None, mutation: bool = False) -> dict[str,Any]:
        async with self._rpc_lock:
            if self._ws is None:
                options={'ping_interval':20,'ping_timeout':20,'max_queue':8,'max_size':2*1024*1024}
                if headers:
                    # websockets >=14 renamed extra_headers.
                    keyword='additional_headers' if int(websockets.__version__.split('.')[0])>=14 else 'extra_headers'
                    options[keyword]=headers
                self._ws=await websockets.connect(self.ws_url,**options)
            self._request_id+=1
            message={**message,'id':self._request_id}
            sent=False
            async def exchange():
                nonlocal sent
                # Set before send: a send exception can still mean bytes left.
                sent=True
                await self._ws.send(json.dumps(message,separators=(',',':')))
                while True:
                    response=json.loads(await self._ws.recv())
                    if response.get('id')==message['id']:
                        return response
            try:
                response=await asyncio.wait_for(exchange(),15)
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception as exc:
                await self.close()
                if mutation and sent:
                    raise OrderOutcomeUnknown(f'{self.name} submission outcome unknown; query client order ID; do not retry automatically') from exc
                raise RuntimeError(f'{self.name} WebSocket RPC unavailable') from exc
            if response.get('error') or int(response.get('status',200))>=400:
                # Do not echo signed requests, auth values, or arbitrary vendor
                # response bodies into logs.
                raise RuntimeError(f'{self.name} RPC rejected by exchange')
            result=response.get('result',{})
            return result if isinstance(result,dict) else {'result':result}


class ArcusBrokerAdapter(_WebSocketBroker):
    name='arcus'

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        super().__init__(settings)
        prefix='ARCUS_TESTNET_' if self.use_testnet else 'ARCUS_'
        self.api_key=self.env.get(prefix+'API_KEY')
        self.api_secret=self.env.get(prefix+'API_SECRET')
        self.address=self.env.get(prefix+'ADDRESS') or self.env.get('ARCUS_ADDRESS')
        host='api.testnet.arcus.xyz' if self.use_testnet else 'api.arcus.xyz'
        self.rest_url='https://'+host
        self.ws_url='wss://'+host+'/v1/ws'
        self.markets: dict[str,dict[str,Any]]={}

    async def connect(self) -> None:
        self._signer()
        if not self.address or not re.fullmatch(r'0x[0-9a-fA-F]{40}',self.address):
            raise RuntimeError('Arcus registered wallet address is not configured')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(self.rest_url+'/v1/markets') as response:
                response.raise_for_status(); rows=(await response.json())['markets']
        self.markets={r['marketDisplayName']:r for r in rows if r['marketDisplayName'] in {_instrument(Venue.ARCUS,s).raw_symbol for s in SYMBOLS}}

    def _signer(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        if not self.api_key or not self.api_secret:
            raise RuntimeError('Arcus Ed25519 API key/secret are not configured for selected environment')
        try:
            secret=bytes.fromhex(self.api_secret.removeprefix('0x'))
            if len(secret)==64: secret=secret[:32]
            signer=Ed25519PrivateKey.from_private_bytes(secret)
            public=signer.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex()
            if public!=self.api_key.removeprefix('0x').lower():
                raise ValueError('public/private mismatch')
        except (ValueError,TypeError) as exc:
            raise RuntimeError('Arcus key must be a matching Ed25519 public key and 32-byte seed (hex)') from exc
        return signer

    def prepare_order(self, intent: OrderIntent, *, now_ns: int | None = None) -> dict[str,Any]:
        native=_validate(intent,Venue.ARCUS)
        if not self.address or not re.fullmatch(r'0x[0-9a-fA-F]{40}',self.address):
            raise RuntimeError('Arcus registered wallet address is not configured')
        market=self.markets[native]
        price=intent.limit_price
        is_market=price is None
        if is_market:
            price=Decimal(str(intent.metadata.get('slippage_limit_price','0')))
            mark=Decimal(str(market.get('markPrice','0')))
            if mark<=0 or abs(price/mark-1)>Decimal('.10'):
                raise ValueError('Arcus market order requires a protective price within 10% of mark')
        now=time.time_ns() if now_ns is None else now_ns
        expiry=(now+40*86400*10**9)//1000
        tif=str(intent.metadata.get('time_in_force','IOC' if is_market or intent.metadata.get('reduce_only') else 'GTT')).upper()
        tif_codes={'GTT':0,'FOK':1,'IOC':2,'ALO':3}
        if tif not in tif_codes or (intent.metadata.get('reduce_only') and tif not in {'IOC','FOK'}):
            raise ValueError('unsupported Arcus time in force')
        quantity=intent.quantity
        if quantity<Decimal(str(market.get('minOrderSize','0'))) or quantity>Decimal(str(market.get('maxOrderSize','Infinity'))):
            raise ValueError('Arcus quantity outside market size limits')
        if quantity*price<Decimal(str(market.get('minOrderNotional','0'))):
            raise ValueError('Arcus order below market minimum notional')
        payload={'address':self.address,'accountIndex':int(self.env.get('ARCUS_ACCOUNT_INDEX','0')),
            'marketId':int(market['marketId']),'orderSide':intent.side.value.upper(),
            'orderType':'MARKET' if is_market else 'LIMIT','timeInForce':tif,
            'goodTilTime':str(expiry),'quantity':format(quantity,'f'),'price':format(price,'f'),
            'clientId':intent.client_order_id,'clientTime':str(now),'reduceOnly':bool(intent.metadata.get('reduce_only',False))}
        if not 0<=payload['accountIndex']<=9:
            raise ValueError('Arcus account index must be between 0 and 9')
        canonical={'ad':self.address.lower(),'ai':payload['accountIndex'],'ct':now,'g':expiry*1000,
            'm':payload['marketId'],'op':1,'p':_integer_units(price,Decimal(market['tickSize']),'price'),
            'q':_integer_units(quantity,Decimal(market['stepSize']),'quantity'),
            'r':int(payload['reduceOnly']),'s':0 if intent.side==Side.BUY else 1,'t':tif_codes[tif],'v':1}
        if intent.client_order_id: canonical['c']=intent.client_order_id
        signature=self._signer().sign(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()).hex()
        return {'type':'post','request':{'type':'placeOrder','payload':payload,
            'apiKey':self.api_key,'timestamp':str(now),'signature':signature}}

    async def submit_order(self, intent: OrderIntent) -> dict:
        self._assert_submit_enabled()
        if not self.markets: await self.connect()
        return await self._rpc(self.prepare_order(intent),mutation=True)

    async def account_state(self) -> dict:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(self.rest_url+'/v1/account',params={'address':self.address,'accountIndex':self.env.get('ARCUS_ACCOUNT_INDEX','0')}) as response:
                response.raise_for_status(); return await response.json()

    async def order_status(self, client_order_id: str) -> dict:
        # Resolve from the public, account-scoped order history, including
        # filled/canceled orders. Absence from this page is not proof of failure.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(self.rest_url+'/v1/orders',params={'address':self.address,'accountIndex':self.env.get('ARCUS_ACCOUNT_INDEX','0'),'limit':100}) as response:
                response.raise_for_status(); result=await response.json()
        return {'provider':self.name,'client_order_id':client_order_id,
            'matches':[r for r in result.get('orders',[]) if r.get('clientId')==client_order_id],
            'complete_history':False}


GRVT_ORDER_TYPES={
    'Order':[{'name':n,'type':t} for n,t in (('subAccountID','uint64'),('isMarket','bool'),('timeInForce','uint8'),('postOnly','bool'),('reduceOnly','bool'),('legs','OrderLeg[]'),('nonce','uint32'),('expiration','int64'))],
    'OrderLeg':[{'name':n,'type':t} for n,t in (('assetID','uint256'),('contractSize','uint64'),('limitPrice','uint64'),('isBuyingContract','bool'))]}


class GrvtBrokerAdapter(_WebSocketBroker):
    name='grvt'

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        super().__init__(settings)
        prefix='GRVT_TESTNET_' if self.use_testnet else 'GRVT_'
        self.api_key=self.env.get(prefix+'API_KEY')
        self.private_key=self.env.get(prefix+'PRIVATE_KEY') or self.env.get(prefix+'API_SECRET')
        self.sub_account=self.env.get(prefix+'SUB_ACCOUNT_ID') or self.env.get(prefix+'ACCOUNT_ID')
        self.address=self.env.get(prefix+'ADDRESS')
        domain='testnet.grvt.io' if self.use_testnet else 'grvt.io'
        self.market_url='https://market-data.'+domain
        self.auth_url='https://edge.'+domain+'/auth/api_key/login'
        self.rest_url='https://trades.'+domain
        self.ws_url='wss://trades.'+domain+'/ws/full'
        self.chain_id=326 if self.use_testnet else 325
        self.instruments: dict[str,dict[str,Any]]={}
        self._headers: dict[str,str]={}
        self._auth_deadline=0.0

    @staticmethod
    def native_client_id(client_order_id: str) -> str:
        return str((int(hashlib.sha256(client_order_id.encode()).hexdigest()[:16],16)&((1<<63)-1))|(1<<63))

    async def connect(self) -> None:
        from eth_account import Account
        if not self.api_key or not self.private_key or not self.sub_account:
            raise RuntimeError('GRVT API key, signing private key, and subaccount are required')
        signer=Account.from_key(self.private_key)
        if self.address and signer.address.lower()!=self.address.lower():
            raise RuntimeError('GRVT signing key does not match configured address')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(self.auth_url,json={'api_key':self.api_key}) as response:
                if response.status!=200:
                    raise RuntimeError(f'GRVT API-key login rejected status={response.status}')
                cookie=response.cookies.get('gravity')
                account=response.headers.get('X-Grvt-Account-Id')
                if cookie is None or not account:
                    raise RuntimeError('GRVT login did not return session cookie/account header')
                self._headers={'Cookie':'gravity='+cookie.value,'X-Grvt-Account-Id':account}
                result=await response.json()
                if result.get('sub_account_id') and str(result['sub_account_id'])!=str(self.sub_account):
                    raise RuntimeError('GRVT API key is registered to another subaccount')
            for symbol in SYMBOLS:
                native=_instrument(Venue.GRVT,symbol).raw_symbol
                async with session.post(self.market_url+'/full/v1/instrument',json={'instrument':native}) as response:
                    response.raise_for_status(); self.instruments[native]=(await response.json())['result']
        self._auth_deadline=time.monotonic()+300

    def prepare_order(self, intent: OrderIntent, *, now_ns: int | None = None) -> dict[str,Any]:
        from eth_account import Account
        from eth_account.messages import encode_typed_data
        native=_validate(intent,Venue.GRVT)
        instrument=self.instruments[native]
        now=time.time_ns() if now_ns is None else now_ns
        quantity=intent.quantity
        size=_integer_units(quantity,Decimal(10)**-int(instrument['base_decimals']),'quantity')
        if quantity<Decimal(instrument['min_size']):
            raise ValueError('GRVT quantity below minimum size')
        price=intent.limit_price
        is_market=price is None
        if price is not None:
            _integer_units(price,Decimal(instrument['tick_size']),'price')
            if quantity*price<Decimal(str(instrument.get('min_notional','0'))):
                raise ValueError('GRVT order below minimum notional')
        scaled_price=_integer_units(price,Decimal('0.000000001'),'price') if price is not None else 0
        tif=str(intent.metadata.get('time_in_force','IMMEDIATE_OR_CANCEL' if is_market else 'GOOD_TILL_TIME'))
        tif_codes={'GOOD_TILL_TIME':1,'ALL_OR_NONE':2,'IMMEDIATE_OR_CANCEL':3,'FILL_OR_KILL':4}
        if tif not in tif_codes: raise ValueError('unsupported GRVT time in force')
        post_only=bool(intent.metadata.get('post_only',False))
        if post_only and (is_market or tif!='GOOD_TILL_TIME'):
            raise ValueError('GRVT post-only requires a resting limit order')
        nonce=int(hashlib.sha256((intent.intent_id+str(intent.created_ts_ns)).encode()).hexdigest()[:8],16)
        expiry=now+60*10**9
        signing={'subAccountID':int(self.sub_account),'isMarket':is_market,'timeInForce':tif_codes[tif],
            'postOnly':post_only,'reduceOnly':bool(intent.metadata.get('reduce_only',False)),
            'legs':[{'assetID':int(instrument['instrument_hash'],16),'contractSize':size,
                'limitPrice':scaled_price,'isBuyingContract':intent.side==Side.BUY}],
            'nonce':nonce,'expiration':expiry}
        domain={'name':'GRVT Exchange','version':'0','chainId':self.chain_id}
        signable=encode_typed_data(domain,GRVT_ORDER_TYPES,signing)
        signer=Account.from_key(self.private_key)
        signature=signer.sign_message(signable)
        order={'sub_account_id':str(self.sub_account),'is_market':is_market,'time_in_force':tif,
            'post_only':post_only,'reduce_only':signing['reduceOnly'],
            'legs':[{'instrument':native,'size':format(quantity,'f'),'limit_price':format(price,'f') if price is not None else '0',
                'is_buying_asset':intent.side==Side.BUY}],
            'signature':{'signer':signer.address,'r':'0x'+signature.r.to_bytes(32,'big').hex(),
                's':'0x'+signature.s.to_bytes(32,'big').hex(),'v':signature.v,'nonce':nonce,
                'expiration':str(expiry),'chain_id':str(self.chain_id)},
            'metadata':{'client_order_id':self.native_client_id(intent.client_order_id)}}
        return {'jsonrpc':'2.0','method':'v1/create_order','params':{'order':order}}

    async def submit_order(self, intent: OrderIntent) -> dict:
        self._assert_submit_enabled()
        if time.monotonic()>=self._auth_deadline:
            await self.close(); await self.connect()
        return await self._rpc(self.prepare_order(intent),headers=self._headers,mutation=True)

    async def account_state(self) -> dict:
        if time.monotonic()>=self._auth_deadline: await self.close(); await self.connect()
        return await self._rpc({'jsonrpc':'2.0','method':'v1/positions','params':{'sub_account_id':str(self.sub_account)}},headers=self._headers)

    async def order_status(self, client_order_id: str) -> dict:
        if time.monotonic()>=self._auth_deadline: await self.close(); await self.connect()
        return await self._rpc({'jsonrpc':'2.0','method':'v1/order','params':{
            'sub_account_id':str(self.sub_account),'client_order_id':self.native_client_id(client_order_id)}},headers=self._headers)
