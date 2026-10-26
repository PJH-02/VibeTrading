import asyncio
import json
import time
from decimal import Decimal
from types import SimpleNamespace

import aiohttp
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from eth_account import Account
from eth_account.messages import encode_typed_data

from vibetrading.bus import InMemoryEventBus
from feeds.collection_timing import CollectionContext
from execution.extended_brokers import ArcusBrokerAdapter, GrvtBrokerAdapter, GRVT_ORDER_TYPES, OrderOutcomeUnknown
from market.hot_path import HotPathStore
from market.market_policy import MarketDataPolicyBus
from core.models import OrderIntent, OrderDestination, Side, Venue
from feeds.providers.crypto_perp import _instrument
from feeds.providers.extended_perp import ExtendedPerpCollector
from feeds.providers.hyperliquid import HyperliquidCollector
from vibetrading.runtime import _CollectorReadinessBus
from vibetrading.settings import RuntimeSettings


def settings(tmp_path, **env):
    path=tmp_path/'.env'; env={'VIBETRADING_DATA_ROOT':str(tmp_path),**env}
    path.write_text('\n'.join(k+'='+str(v) for k,v in env.items()),encoding='utf8')
    return RuntimeSettings.from_env(path)


def intent(venue, **kwargs):
    values=dict(intent_id='intent-1',job_id='test',signal_id='signal-1',destination=OrderDestination.REAL,
        instrument=_instrument(venue,'BTCUSDT'),side=Side.BUY,client_order_id='Mixed-Case-Client',
        quantity=Decimal('0.01'),limit_price=Decimal('50000'))
    return OrderIntent(**{**values,**kwargs})


def arcus(tmp_path):
    key=Ed25519PrivateKey.from_private_bytes(b'\x11'*32)
    public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex()
    broker=ArcusBrokerAdapter(settings(tmp_path,ARCUS_USE_TESTNET='false',ARCUS_API_KEY=public,
        ARCUS_API_SECRET=(b'\x11'*32).hex(),ARCUS_ADDRESS='0x'+'22'*20))
    broker.markets={'BTC-USD':{'marketId':1,'tickSize':'0.1','stepSize':'0.00000001',
        'minOrderSize':'0.0001','maxOrderSize':'10','minOrderNotional':'5','markPrice':'50000'}}
    return broker,key


def grvt(tmp_path):
    key='0x'+'11'*32
    broker=GrvtBrokerAdapter(settings(tmp_path,GRVT_TESTNET_API_KEY='dummy',GRVT_TESTNET_API_SECRET=key,
        GRVT_TESTNET_ACCOUNT_ID='123',GRVT_TESTNET_ADDRESS=Account.from_key(key).address))
    broker.instruments={'BTC_USDT_Perp':{'instrument_hash':'0x030501','base_decimals':9,
        'min_size':'0.001','tick_size':'0.1','min_notional':'100'}}
    return broker


def test_native_identifiers_and_quote_currency_are_not_fabricated():
    assert _instrument(Venue.GRVT,'BTCUSDT').raw_symbol=='BTC_USDT_Perp'
    assert _instrument(Venue.ARCUS,'BTCUSDT').raw_symbol=='BTC-USD'
    assert _instrument(Venue.ARCUS,'BTCUSDT').currency=='USD'
    with pytest.raises(ValueError): ExtendedPerpCollector(Venue.ARCUS,('SKHYUSDT',))


def test_arcus_snapshot_boundary_duplicate_and_real_gap():
    async def scenario():
        c=ExtendedPerpCollector(Venue.ARCUS,('BTCUSDT',)); b=InMemoryEventBus()
        async def send(seq,kind='channel_data',bids=None):
            m={'type':kind,'channel':'l2OrderbookUpdates','id':'BTC-USD',
                'contents':{'lastSequenceId':seq,'globalSequenceId':seq*100,'bids':bids or [['100','1']],'asks':[['101','2']]}}
            await c._arcus_message(b,m,json.dumps(m))
        await send(10,'subscribed'); await send(15); await send(16); await send(16)
        assert c.last_book['BTCUSDT']==16
        assert b.messages[-1].data['sync_generation']==0
        hot=HotPathStore()
        for m in b.messages: hot.apply(m.data)
        assert hot.latest_book(_instrument(Venue.ARCUS,'BTCUSDT').key) is not None
        with pytest.raises(RuntimeError,match='mid-stream'): await send(18)
        assert b.messages[-1].data['event_type']=='data_gap'
        assert 'BTCUSDT' not in c.last_book
    asyncio.run(scenario())


def test_grvt_ack_baseline_nanoseconds_and_gap():
    async def scenario():
        c=ExtendedPerpCollector(Venue.GRVT,('BTCUSDT',)); b=InMemoryEventBus()
        await c._grvt_message(b,{'result':{'stream':'v1.book.d','subs':['BTC_USDT_Perp@100'],
            'first_sequence_number':['123']}},'ack')
        async def send(seq,previous):
            m={'stream':'v1.book.d','sequence_number':str(seq),'prev_sequence_number':str(previous),
                'feed':{'instrument':'BTC_USDT_Perp','event_time':'1791035443627660123',
                    'bids':[{'price':'100','size':'1'}],'asks':[{'price':'101','size':'2'}]}}
            await c._grvt_message(b,m,json.dumps(m))
        await send(0,0); await send(123,122); await send(124,123)
        assert b.messages[0].data['final_update_id']==122
        assert b.messages[1].data['source_ts_ns']==1791035443627660123
        hot=HotPathStore()
        for m in b.messages: hot.apply(m.data)
        assert hot.latest_book(_instrument(Venue.GRVT,'BTCUSDT').key) is not None
        await send(124,123)
        assert c.last_book['BTCUSDT']==124
        with pytest.raises(RuntimeError,match='sequence gap'): await send(126,124)
    asyncio.run(scenario())


def test_arcus_missing_timestamp_stays_null_and_trade_side_unknown():
    async def scenario():
        c=ExtendedPerpCollector(Venue.ARCUS); b=InMemoryEventBus()
        await c._arcus_message(b,{'channel':'trades','id':'BTC-USD','contents':[
            {'tradeId':'fill-1','price':'100','size':'1','timestamp':1700000000000001}]},'raw')
        assert b.messages[0].data['source_ts_ns']==1700000000000001000
        assert b.messages[0].data['aggressor_side']=='unknown'
        assert c._common('BTCUSDT',None)['source_ts_ns'] is None
    asyncio.run(scenario())


@pytest.mark.parametrize('venue',[Venue.ARCUS,Venue.GRVT])
def test_new_venues_pass_explicit_policy(venue):
    c=ExtendedPerpCollector(venue)
    async def scenario():
        b=InMemoryEventBus()
        await c._book(b,'BTCUSDT',None,[['100','1']],[['101','1']],__import__("core.models",fromlist=['BookUpdateType']).BookUpdateType.SNAPSHOT,1)
        from core.models import event_from_dict
        assert MarketDataPolicyBus(b)._allowed(event_from_dict(b.messages[0].data))
    asyncio.run(scenario())


@pytest.mark.parametrize('status',[429,502,503])
def test_hyperliquid_rest_transient_error_isolated(status):
    async def failing(*args):
        raise aiohttp.ClientResponseError(None,(),status=status)
    asyncio.run(HyperliquidCollector({})._rest_cycle(failing,None,None,CollectionContext('hyperliquid',0)))


def test_hyperliquid_rest_permanent_and_policy_failures_propagate():
    async def permanent(*args): raise aiohttp.ClientResponseError(None,(),status=400)
    async def publish_failure(*args): raise RuntimeError('storage paused')
    c=HyperliquidCollector({}); ctx=CollectionContext('hyperliquid',0)
    with pytest.raises(aiohttp.ClientResponseError): asyncio.run(c._rest_cycle(permanent,None,None,ctx))
    with pytest.raises(RuntimeError): asyncio.run(c._rest_cycle(publish_failure,None,None,ctx))


def test_market_readiness_uses_market_stream_not_crypto(tmp_path):
    async def scenario():
        selected=[]; ready=[]
        async def probe(name):
            selected.append(name)
            return SimpleNamespace(status='fresh',ratio=.001,config_mismatch=name=='CRYPTO_DATA')
        async def callback(): ready.append(True)
        inner=SimpleNamespace(settings=settings(tmp_path),market_stream_name='MARKET_DATA',stream_probe=probe)
        b=_CollectorReadinessBus(inner,callback,'hyperliquid')
        b.first_published_monotonic=time.monotonic()-120
        assert await b.confirm_recovered()
        assert selected==['MARKET_DATA'] and ready
    asyncio.run(scenario())


def test_arcus_typed_signature_and_exact_units(tmp_path):
    broker,key=arcus(tmp_path); now=1791035443627660123
    message=broker.prepare_order(intent(Venue.ARCUS),now_ns=now)['request']
    payload=message['payload']
    canonical={'ad':broker.address.lower(),'ai':0,'c':'Mixed-Case-Client','ct':now,
        'g':int(payload['goodTilTime'])*1000,'m':1,'op':1,'p':500000,'q':1000000,
        'r':0,'s':0,'t':0,'v':1}
    key.public_key().verify(bytes.fromhex(message['signature']),json.dumps(canonical,sort_keys=True,separators=(',',':')).encode())
    assert int(payload['goodTilTime'])*1000>now+31*86400*10**9
    with pytest.raises(ValueError,match='increment'): broker.prepare_order(intent(Venue.ARCUS,limit_price=Decimal('50000.01')))


def test_arcus_market_order_requires_price_protection(tmp_path):
    b,_=arcus(tmp_path)
    with pytest.raises(ValueError,match='protective'): b.prepare_order(intent(Venue.ARCUS,limit_price=None))
    m=b.prepare_order(intent(Venue.ARCUS,limit_price=None,metadata={'slippage_limit_price':'50100'}))
    assert m['request']['payload']['timeInForce']=='IOC'


def test_grvt_eip712_signature_and_client_id(tmp_path):
    b=grvt(tmp_path); now=1791035443627660123
    m=b.prepare_order(intent(Venue.GRVT),now_ns=now)['params']['order']; sig=m['signature']
    signing={'subAccountID':123,'isMarket':False,'timeInForce':1,'postOnly':False,'reduceOnly':False,
        'legs':[{'assetID':0x030501,'contractSize':10000000,'limitPrice':50000000000000,'isBuyingContract':True}],
        'nonce':sig['nonce'],'expiration':int(sig['expiration'])}
    encoded=encode_typed_data({'name':'GRVT Exchange','version':'0','chainId':326},GRVT_ORDER_TYPES,signing)
    recovered=Account.recover_message(encoded,vrs=(sig['v'],int(sig['r'],16),int(sig['s'],16)))
    assert recovered==Account.from_key(b.private_key).address
    assert int(m['metadata']['client_order_id'])>=2**63
    assert b.native_client_id('Mixed-Case-Client')==m['metadata']['client_order_id']


@pytest.mark.parametrize('factory',[arcus,grvt])
def test_order_submission_disabled_without_explicit_arm(tmp_path,factory):
    b=factory(tmp_path); b=b[0] if isinstance(b,tuple) else b
    with pytest.raises(RuntimeError,match='disabled'):
        asyncio.run(b.submit_order(intent(Venue.ARCUS if b.name=='arcus' else Venue.GRVT)))


def test_ambiguous_order_transport_failure_never_retried(tmp_path):
    async def scenario():
        b,_=arcus(tmp_path); calls=[]
        class Socket:
            async def send(self,payload): calls.append(payload); raise OSError('reset after write')
            async def close(self): pass
        b._ws=Socket()
        with pytest.raises(OrderOutcomeUnknown): await b._rpc({'type':'post'},mutation=True)
        assert len(calls)==1 and b._ws is None
    asyncio.run(scenario())


def test_split_disk_guard_checks_nats_even_with_free_300gb(tmp_path,monkeypatch):
    import storage.storage_guard as module
    env=settings(tmp_path,STORAGE_DATA_DISK_ROOT='/large',STORAGE_NATS_DISK_ROOT='/small',STORAGE_STATE_DISK_ROOT='/large')
    def usage(path): return SimpleNamespace(total=300*1024**3,free=(1 if str(path)=='/small' else 280)*1024**3)
    monkeypatch.setattr(module.shutil,'disk_usage',usage)
    decision=module.StorageGuard(env).evaluate()
    assert decision.fail_closed and decision.free_bytes>100*1024**3
    assert decision.nats_free_bytes==1024**3
    assert not module.StorageGuard(env).can_resume_collection()
