"""Actual KIS adapter guarded cancel wire seam; the transport is scripted."""
import asyncio
from types import SimpleNamespace

import pytest

from execution.kis_execution import KisCashBroker


class _Response:
    status = 200
    headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _Session:
    def __init__(self, trace):
        self.trace = trace

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def post(self, url, *, json, headers):
        assert self.trace['permit_calls'] == 1
        self.trace['post'] = (url, dict(json), dict(headers))
        return _Response()


class _KisClient:
    def __init__(self, trace):
        self.trace = trace
        self.provider = SimpleNamespace(values={
            'KIS_ACCOUNT_PRODUCT_CODE': '01', 'KIS_ACCOUNT_NUMBER': 'mock2-account'})
        self.endpoints = SimpleNamespace(rest_base_url='https://fixture.invalid')

    async def issue_access_token(self):
        return 'fixture-token', 0

    def _base_headers(self):
        return {}

    def _app_key(self):
        return 'fixture-key'

    def _app_secret(self):
        return 'fixture-secret'

    async def _safe_json(self, response):
        return {'rt_cd': '0', 'output': {'ODNO': 'cancel-ack'}}


@pytest.mark.asyncio
async def test_actual_kis_mock2_cancel_uses_original_wire_and_consumes_one_permit(monkeypatch):
    trace = {'permit_calls': 0, 'post': None}
    monkeypatch.setattr('execution.kis_execution.aiohttp.ClientSession',
                        lambda **kwargs: _Session(trace))
    broker = KisCashBroker.__new__(KisCashBroker)
    broker.client = _KisClient(trace)
    broker.mode = 'mock2'
    broker._lock = asyncio.Lock()
    broker._next_request_at = 0.0
    broker.authority_check = lambda: True

    def permit():
        trace['permit_calls'] += 1

    result = await broker.cancel_order_with_permit('native-73', 'native-org', permit)

    assert result == {'rt_cd': '0', 'output': {'ODNO': 'cancel-ack'}}
    assert trace['permit_calls'] == 1
    url, body, headers = trace['post']
    assert url == 'https://fixture.invalid/uapi/domestic-stock/v1/trading/order-rvsecncl'
    assert headers['tr_id'] == 'VTTC0013U'
    assert body == {
        'CANO': 'mock2-account', 'ACNT_PRDT_CD': '01',
        'KRX_FWDG_ORD_ORGNO': 'native-org', 'ORGN_ODNO': 'native-73',
        'ORD_DVSN': '00', 'RVSE_CNCL_DVSN_CD': '02', 'ORD_QTY': '0',
        'ORD_UNPR': '0', 'QTY_ALL_ORD_YN': 'Y', 'EXCG_ID_DVSN_CD': 'KRX'}


@pytest.mark.asyncio
async def test_actual_kis_cancel_refuses_missing_permit_before_post(monkeypatch):
    trace = {'permit_calls': 0, 'post': None}
    monkeypatch.setattr('execution.kis_execution.aiohttp.ClientSession',
                        lambda **kwargs: _Session(trace))
    broker = KisCashBroker.__new__(KisCashBroker)
    broker.client = _KisClient(trace)
    broker.mode = 'mock'
    broker._lock = asyncio.Lock()
    broker._next_request_at = 0.0
    broker.authority_check = lambda: True

    with pytest.raises(TypeError, match='dispatch permit'):
        await broker.cancel_order_with_permit('native-73', 'native-org', None)
    assert trace['post'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/v5/order/create', '/v5/order/cancel',
                                  '/v5/order/amend', '/v5/order/cancel-all'])
async def test_standalone_bybit_probe_client_cannot_mutate_around_common_owner(path):
    from execution.bybit_execution import BybitCredentials, BybitOrderClient

    class _NeverTransport:
        async def request(self, *args, **kwargs):
            pytest.fail('retired probe path reached an HTTP transport')

    credentials = BybitCredentials(api_key='fixture-key', api_secret='fixture-secret', testnet=True)
    client = BybitOrderClient(credentials, _NeverTransport())
    with pytest.raises(RuntimeError, match='retired.*common-owner'):
        await client.request('POST', path, {'category':'linear','symbol':'BTCUSDT'})


@pytest.mark.asyncio
@pytest.mark.parametrize('live_enabled,account_armed,reason', [
    (False, True, 'TIMEFOLIO_LIVE_SUBMIT_ENABLED is false'),
    (True, False, 'account arm token is not enabled')])
async def test_timefolio_runner_keeps_independent_submit_and_account_arm_fences(
        live_enabled, account_armed, reason):
    from timefolio.runner import TimefolioStrategyRunner, TimefolioStrategyRunnerConfig
    from decimal import Decimal

    config = TimefolioStrategyRunnerConfig(
        job_id='fixture-timefolio', account_id='fixture-account', strategy_module='fixture',
        entry_weight_pct=Decimal('1'), max_order_notional=Decimal('100'),
        max_position_notional=Decimal('100'), submit=True)
    runner = TimefolioStrategyRunner.__new__(TimefolioStrategyRunner)
    runner.config = config
    runner.settings = SimpleNamespace(timefolio_live_submit_enabled=live_enabled,
        env={'TIMEFOLIO_ORDER_ARMED_FIXTURE-ACCOUNT': '1' if account_armed else '0'})
    runner._event_source = object()
    runner.bus = object()
    result = await runner.run(duration_sec=1)
    assert result[0]['status'] == 'BLOCKED'
    assert result[0]['reason'] == reason


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter_name', ['arcus','grvt'])
async def test_legacy_extended_adapter_factory_cannot_submit_without_registered_owner(
        tmp_path, adapter_name):
    from execution.extended_brokers import ArcusBrokerAdapter, GrvtBrokerAdapter
    from runtime.settings import RuntimeSettings

    env_path = tmp_path/'.env'
    env_path.write_text('LIVE_ORDER_ENABLED=true\n'
        'ARCUS_USE_TESTNET=true\nARCUS_ORDER_SUBMIT_ENABLED=true\n'
        'GRVT_USE_TESTNET=true\nGRVT_ORDER_SUBMIT_ENABLED=true\n')
    settings = RuntimeSettings.from_env(env_path)
    adapter = (ArcusBrokerAdapter(settings) if adapter_name == 'arcus'
               else GrvtBrokerAdapter(settings))
    with pytest.raises(RuntimeError, match='no registered common-owner'):
        await adapter.submit_order(None)

@pytest.mark.asyncio
async def test_normal_etf_constructor_defers_unknown_adapter_and_never_gives_it_permit(
        tmp_path, monkeypatch):
    from runtime.etf_trading import EtfTradingLoop

    class UnknownOfflineBroker:
        def __init__(self, settings, *, mode):
            self.mode = mode
            self.account = settings.accounts[mode]
            self.mutations = 0

        def account_params(self):
            return {'CANO':self.account,'ACNT_PRDT_CD':'01'}

        async def connect(self):
            return None

        async def account_state(self):
            raise AssertionError('unknown adapter should fail binding before account reads')

        async def submit_order_with_permit(self, intent, permit):
            self.mutations += 1
            raise AssertionError('unknown adapter must not receive a permit')

        async def submit_order(self, intent):
            self.mutations += 1
            raise AssertionError('unknown adapter must not receive a direct submit')

        async def cancel_order(self, order_number, organization):
            self.mutations += 1
            raise AssertionError('unknown adapter must not receive a direct cancel')

    monkeypatch.setattr('runtime.etf_trading.KisCashBroker', UnknownOfflineBroker)
    monkeypatch.setattr('runtime.etf_trading.KiwoomAuthClient', lambda *a, **k: object())
    monkeypatch.setattr('runtime.etf_trading.KisAuthClient', lambda *a, **k: object())
    monkeypatch.setattr('runtime.etf_trading.EtfHistory', lambda *a, **k: SimpleNamespace(day=''))
    settings = SimpleNamespace(state_dir=tmp_path/'state', env={'ETF_EXECUTION_GUARDS':'false'},
        accounts={'mock':'fixture-001','mock2':'fixture-002'})
    class Bus:
        async def publish_order_intent(self, intent):
            return None
    loop = EtfTradingLoop(settings=settings, bus=Bus(), authority_root=tmp_path/'authority')
    try:
        assert loop.execution_registry is None
        assert loop._native_registry_required is True
        assert loop.execution_guards_enabled() is True
        from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
        intent = OrderIntent(intent_id='fixture-unknown', job_id='C3', signal_id='fixture',
            destination=OrderDestination.REAL,
            instrument=InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF,
                raw_symbol='069500', std_symbol='069500', currency='KRW', exchange='KRX'),
            side=Side.BUY, client_order_id='fixture-unknown', quantity=1,
            limit_price=100, metadata={'account_scope':'mock','account_mode':'mock'})
        with pytest.raises((ValueError, TypeError, RuntimeError)):
            await loop._submit_staged(loop.brokers['mock'], intent)
        assert loop.execution_registry is None
        assert all(broker.mutations == 0 for broker in loop.brokers.values())
        assert all(not hasattr(broker, '_common_owner_bound') for broker in loop.brokers.values())
    finally:
        loop.journal.db.close()
        for lease in reversed(tuple(loop._owner_leases.values())):
            lease.close()

@pytest.mark.asyncio
async def test_normal_etf_kis_mutations_are_fenced_before_lazy_registry_binding(
        tmp_path, monkeypatch):
    from runtime.etf_trading import EtfTradingLoop

    class Client:
        def __init__(self, mode):
            self.provider = SimpleNamespace(values={
                'KIS_ACCOUNT_NUMBER':'normal-'+mode, 'KIS_ACCOUNT_PRODUCT_CODE':'01'})

    monkeypatch.setattr('execution.kis_execution.KisAuthClient',
        lambda settings, mode='mock': Client(mode))
    monkeypatch.setattr('runtime.etf_trading.KiwoomAuthClient', lambda *a, **k: object())
    monkeypatch.setattr('runtime.etf_trading.KisAuthClient', lambda *a, **k: object())
    monkeypatch.setattr('runtime.etf_trading.EtfHistory', lambda *a, **k: SimpleNamespace(day=''))
    settings = SimpleNamespace(state_dir=tmp_path/'state', env={'ETF_EXECUTION_GUARDS':'false'},
        accounts={'mock':'normal-mock','mock2':'normal-mock2'})
    loop = EtfTradingLoop(settings=settings, bus=SimpleNamespace(), authority_root=tmp_path/'authority')
    try:
        assert loop.execution_registry is None
        for broker in loop.brokers.values():
            assert broker._common_owner_bound is True
            with pytest.raises(RuntimeError, match='common owner submit permit'):
                await broker.submit_order(None)
            with pytest.raises(RuntimeError, match='common owner cancel permit'):
                await broker.cancel_order('offline-order','offline-org')
    finally:
        loop.journal.db.close()
        for lease in reversed(tuple(loop._owner_leases.values())):
            lease.close()
