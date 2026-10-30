"""Actual KIS mock/mock2 owner wiring with scripted native HTTP boundaries."""
import hashlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from execution.kis_execution import KisCashBroker
from portfolio.etf_journal import EtfJournal
from runtime.settings import RuntimeSettings
from runtime.etf_trading import EtfTradingLoop
from strategies.kr import c3, kd, kp


@pytest.mark.asyncio
@pytest.mark.parametrize('strategy,symbol,route', [
    ('C3','069500','mock'), ('C3','122630','mock2'),
    ('KP','069500','mock'), ('KP','122630','mock2'),
    ('KD','229200','mock'), ('KD','251340','mock2')])
async def test_actual_etf_strategy_routes_through_common_owner_to_kis_mock_adapter(
        tmp_path, monkeypatch, strategy, symbol, route):
    env_path = tmp_path/'.env'
    env_path.write_text(
        'VIBETRADING_DATA_ROOT=' + tmp_path.as_posix() + '\n'
        'KIS_APP_KEY=fixture-live\nKIS_APP_SECRET=fixture-live-secret\n'
        'KIS_ACCOUNT_NUMBER=fixture-live-account\nKIS_ACCOUNT_PRODUCT_CODE=01\n'
        'KIS_MOCK_APP_KEY=fixture-mock\nKIS_MOCK_APP_SECRET=fixture-mock-secret\n'
        'KIS_MOCK_ACCOUNT_NUMBER=fixture-mock-account\nKIS_MOCK_ACCOUNT_PRODUCT_CODE=01\n'
        'KIS_MOCK2_APP_KEY=fixture-mock2\nKIS_MOCK2_APP_SECRET=fixture-mock2-secret\n'
        'KIS_MOCK2_ACCOUNT_NUMBER=fixture-mock2-account\nKIS_MOCK2_ACCOUNT_PRODUCT_CODE=01\n'
        'ETF_EXECUTION_GUARDS=true\n')
    settings = RuntimeSettings.from_env(env_path)
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.settings = settings
    loop._closing = False
    loop.brokers = {mode:KisCashBroker(settings, mode=mode) for mode in ('mock','mock2')}
    loop.broker = loop.brokers['mock']
    loop._acquire_ownership(tmp_path/'authority')
    loop.journal = EtfJournal(settings.state_dir/'etf-mock'/'journal.sqlite3')
    mock_id = hashlib.sha256(('mock:fixture-mock-account:01').encode()).hexdigest()
    loop.journal.initialize(Decimal(60000), mock_id)
    for mode, account in (('mock','fixture-mock-account'), ('mock2','fixture-mock2-account')):
        identity = hashlib.sha256((mode+':'+account+':01').encode()).hexdigest()
        loop.journal.bind_order_feedback_scope(mode, identity)
    for sid in ('C3','KP','KD'):
        loop.journal.enable_order_feedback(sid)
    loop.execution_registry = loop._build_execution_registry()
    with pytest.raises(RuntimeError, match='common owner submit permit'):
        await loop.brokers[route].submit_order(None)
    with pytest.raises(RuntimeError, match='common owner cancel permit'):
        await loop.brokers[route].cancel_order('fixture-native-order', 'fixture-org')
    loop._physical_holdings = {}
    loop._day = '20261028'
    loop.phase = 'ready'
    loop._checkpoint_recovery_state = 'IDLE'
    loop._feed_invalid = set()
    loop._feed_history_ready = True
    loop.history = SimpleNamespace(day='')
    loop.bus = SimpleNamespace(publish_order_intent=lambda intent: _none())
    loop.quotes = {symbol:(Decimal(100),Decimal(105),__import__('time').time_ns())}
    loop.entry_blocked = lambda *args, **kwargs: False
    loop._feed_entry_blocked = lambda *args, **kwargs: False
    trace = []
    cash_proofs = []
    from execution.execution_owner import ExecutionOwner
    dispatch_owner = ExecutionOwner.dispatch_journal_intent
    async def capture_cash_proof(*, verify, **kwargs):
        def capture_verify():
            proof = verify()
            if kwargs['intent'].side.value == 'buy':
                rows = loop.journal.db.execute(
                    "SELECT intent_id,status,reserve,quantity,filled FROM orders "
                    "WHERE account_scope=? AND side='buy' "
                    "AND status IN ('submitting','unknown','pending','cancel_pending') "
                    "ORDER BY intent_id", (route,)).fetchall()
                canonical = [{'intent_id':str(row['intent_id']), 'status':str(row['status']),
                    'reserve':str(row['reserve']), 'quantity':int(row['quantity']),
                    'filled':int(row['filled'] or 0)} for row in rows]
                import json
                expected = hashlib.sha256(json.dumps(canonical, sort_keys=True,
                    separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
                all_rows = loop.journal.db.execute(
                    'SELECT intent_id,side,status,quantity,filled,notional,reserve,'
                    'cancel_requested,cancel_confirmed FROM orders WHERE account_scope=? ORDER BY intent_id',
                    (route,)).fetchall()
                all_canonical = [{'intent_id':str(row['intent_id']), 'side':str(row['side']),
                    'status':str(row['status']), 'quantity':int(row['quantity']),
                    'filled':int(row['filled'] or 0), 'notional':str(row['notional'] or '0'),
                    'reserve':str(row['reserve']), 'cancel_requested':int(row['cancel_requested'] or 0),
                    'cancel_confirmed':int(row['cancel_confirmed'] or 0)} for row in all_rows]
                expected_local = hashlib.sha256(json.dumps(all_canonical, sort_keys=True,
                    separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
                cash_proofs.append((proof, expected, expected_local))
            return proof
        return await dispatch_owner(journal=kwargs['journal'], registry=kwargs['registry'],
            route_id=kwargs['route_id'], intent=kwargs['intent'], verify=capture_verify,
            timeout_seconds=kwargs['timeout_seconds'])
    monkeypatch.setattr(ExecutionOwner, 'dispatch_journal_intent', staticmethod(capture_cash_proof))

    async def fake_request(method, endpoint, tr_id, data, *, tr_cont='', permit=None):
        if method == 'GET' and endpoint == 'inquire-psbl-order':
            return {'body':{'output':{'max_buy_qty':'10','psbl_qty_calc_unpr':'100','ord_psbl_cash':'60000'}},'headers':{}}
        if method == 'GET' and endpoint == 'inquire-balance':
            return {'body':{'output1':[], 'output2':[{'dnca_tot_amt':'60000'}]},'headers':{}}
        if method == 'POST' and endpoint == 'order-cash':
            if permit is None:
                raise AssertionError('common owner did not supply the KIS mutation permit')
            permit()
            trace.append((route, method, endpoint, tr_id, dict(data)))
            return {'body':{'output':{'ODNO':'native-'+strategy+'-'+route,
                'KRX_FWDG_ORD_ORGNO':'fixture-org'}}}
        raise AssertionError('unexpected scripted KIS call '+method+' '+endpoint)

    for broker in loop.brokers.values():
        broker.request = fake_request
        broker.buying_power = _async_power
    source = {'C3':c3,'KP':kp,'KD':kd}[strategy]
    callback_trace = []
    original = source.on_order_event
    def on_order_event(event, context, instance=None):
        callback_trace.append((event.status, event.strategy_id, context.execution['route']))
        return original(event, context, instance)
    monkeypatch.setattr(source, 'on_order_event', on_order_event)
    try:
        await loop.place(strategy, symbol, __import__('core.models',fromlist=['Side']).Side.BUY,
            1, 'exec01:'+strategy+':'+route, direction=1,
            valid_until_ts_ns=__import__('time').time_ns()+60_000_000_000)
        row = loop.journal.db.execute('SELECT * FROM orders WHERE order_number IS NOT NULL').fetchone()
        assert row is not None and row['account_scope'] == route
        assert row['status'] == 'pending' and loop.journal.dispatch_state(row['intent_id']) == 'ACKED'
        assert len(trace) == 1 and trace[0][0] == route
        assert trace[0][3] == 'VTTC0012U'
        assert trace[0][4]['ORD_QTY'] == '1'
        assert callback_trace == [('ACCEPTED', strategy, route)]
        proof, expected_version, expected_local_version = cash_proofs[0]
        assert proof['orderable_cash'] == '60000'
        assert proof['currency'] == proof['product_currency'] == 'KRW'
        assert proof['account_scope'] == loop._feedback_scope(route).key
        assert proof['quality'] == 'VALID' and proof['observed_ns'] > 0
        assert proof['cash_reservation_version'] == expected_version
        assert proof['journal_fact_version'] == expected_local_version
        route_id = loop._execution_route_ids[route]
        assert loop.execution_registry.binding(route_id).adapter is loop.brokers[route]
    finally:
        loop.journal.db.close()
        for lease in reversed(tuple(loop._owner_leases.values())):
            lease.close()


async def _none():
    return None


async def _async_power(*args, **kwargs):
    return _power()


def _power():
    return {'max_buy_qty':'10','psbl_qty_calc_unpr':'100','ord_psbl_cash':'60000'}


@pytest.mark.asyncio
@pytest.mark.parametrize('strategy,symbol,route', [
    ('C3','069500','mock'), ('C3','122630','mock2'),
    ('KP','069500','mock'), ('KP','122630','mock2'),
    ('KD','229200','mock'), ('KD','251340','mock2')])
async def test_actual_kis_owner_cancel_latefill_remainder_once_after_restart(
        tmp_path, monkeypatch, strategy, symbol, route):
    """The concrete route consumes one submit/cancel permit per native POST."""
    import time
    from core.models import Side

    env_path = tmp_path/'.env'
    env_path.write_text(
        'VIBETRADING_DATA_ROOT=' + tmp_path.as_posix() + '\n'
        'KIS_APP_KEY=fixture-live\nKIS_APP_SECRET=fixture-live-secret\n'
        'KIS_ACCOUNT_NUMBER=fixture-live-account\nKIS_ACCOUNT_PRODUCT_CODE=01\n'
        'KIS_MOCK_APP_KEY=fixture-mock\nKIS_MOCK_APP_SECRET=fixture-mock-secret\n'
        'KIS_MOCK_ACCOUNT_NUMBER=fixture-mock-account\nKIS_MOCK_ACCOUNT_PRODUCT_CODE=01\n'
        'KIS_MOCK2_APP_KEY=fixture-mock2\nKIS_MOCK2_APP_SECRET=fixture-mock2-secret\n'
        'KIS_MOCK2_ACCOUNT_NUMBER=fixture-mock2-account\nKIS_MOCK2_ACCOUNT_PRODUCT_CODE=01\n'
        'ETF_EXECUTION_GUARDS=true\n')
    settings = RuntimeSettings.from_env(env_path)
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.settings = settings
    loop._closing = False
    loop.brokers = {mode:KisCashBroker(settings, mode=mode) for mode in ('mock','mock2')}
    loop.broker = loop.brokers['mock']
    loop._acquire_ownership(tmp_path/'authority')
    loop.journal = EtfJournal(settings.state_dir/'etf-mock'/'journal.sqlite3')
    physical = {'mock':'fixture-mock-account','mock2':'fixture-mock2-account'}
    base_identity = hashlib.sha256(('mock:'+physical['mock']+':01').encode()).hexdigest()
    loop.journal.initialize(Decimal(60000), base_identity)
    for mode, account in physical.items():
        identity = hashlib.sha256((mode+':'+account+':01').encode()).hexdigest()
        loop.journal.bind_order_feedback_scope(mode, identity)
    for sid in ('C3','KP','KD'):
        loop.journal.enable_order_feedback(sid)
    loop.execution_registry = loop._build_execution_registry()
    loop._physical_holdings = {}
    loop._day = '20261028'
    loop.phase = 'ready'
    loop._checkpoint_recovery_state = 'IDLE'
    loop._feed_invalid = set()
    loop._feed_history_ready = True
    loop.history = SimpleNamespace(day='')
    loop.bus = SimpleNamespace(publish_order_intent=lambda intent: _none())
    loop.quotes = {symbol:(Decimal(100),Decimal(105),time.time_ns())}
    loop.entry_blocked = lambda *args, **kwargs: False
    loop._feed_entry_blocked = lambda *args, **kwargs: False
    trace = {'posts':[], 'cancels':[], 'states':{}, 'reads':{}}

    async def fake_request(method, endpoint, tr_id, data, *, tr_cont='', permit=None):
        if method == 'GET' and endpoint == 'inquire-psbl-order':
            return {'body':{'output':{'max_buy_qty':'10','psbl_qty_calc_unpr':'130','ord_psbl_cash':'60000'}},'headers':{}}
        if method == 'GET' and endpoint == 'inquire-balance':
            return {'body':{'output1':[], 'output2':[{'dnca_tot_amt':'60000'}]},'headers':{}}
        if method == 'GET' and endpoint == 'inquire-daily-ccld':
            order_number = data['ODNO']
            states = trace['states'][order_number]
            position = trace['reads'].get(order_number,0)
            trace['reads'][order_number] = position + 1
            state = states[min(position,len(states)-1)]
            return {'body':{'output1':[{'odno':order_number, **state}]},'headers':{}}
        if method == 'POST':
            if permit is None:
                raise AssertionError('common owner did not supply a native mutation permit')
            permit()
            if endpoint == 'order-cash':
                order_number = f'{strategy}-{route}-order-{len(trace["posts"])+1}'
                trace['posts'].append((tr_id,dict(data),order_number))
                if len(trace['posts']) == 1:
                    trace['states'][order_number] = [
                        {'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6'},
                        {'tot_ccld_qty':'5','tot_ccld_amt':'495','rmn_qty':'0','cncl_yn':'Y'}]
                else:
                    quantity = str(data['ORD_QTY'])
                    trace['states'][order_number] = [
                        {'tot_ccld_qty':quantity,'tot_ccld_amt':str(int(quantity)*130),'rmn_qty':'0'}]
                return {'body':{'rt_cd':'0','output':{'ODNO':order_number,
                    'KRX_FWDG_ORD_ORGNO':'fixture-org'}},'headers':{}}
            if endpoint == 'order-rvsecncl':
                trace['cancels'].append((tr_id,dict(data)))
                return {'body':{'rt_cd':'0','output':{}},'headers':{}}
        raise AssertionError('unexpected scripted KIS call '+method+' '+endpoint)

    for broker in loop.brokers.values():
        broker.request = fake_request
        broker.buying_power = _async_power
    source = {'C3':c3,'KP':kp,'KD':kd}[strategy]
    callback_trace = []
    source_callback = source.on_order_event
    def on_order_event(event, context, instance=None):
        callback_trace.append(event.status)
        return source_callback(event, context, instance)
    monkeypatch.setattr(source, 'on_order_event', on_order_event)
    try:
        await loop.place(strategy, symbol, Side.BUY, 10, 'exec01-cancel:'+strategy+':'+route,
            direction=1, valid_until_ts_ns=time.time_ns()+60_000_000_000)
        original_row = loop.journal.db.execute(
            'SELECT * FROM orders WHERE order_number IS NOT NULL').fetchone()
        original_id = original_row['intent_id']
        aged = time.time_ns()-6_000_000_000
        loop.journal.db.execute('UPDATE orders SET created_ns=?,accepted_ns=? WHERE intent_id=?',
                                 (aged,aged,original_id))

        await loop.reconcile()  # source chooses cancel; ACK remains pending; status has 4/10 fill.
        assert len(trace['cancels']) == 1
        assert len(trace['posts']) == 1
        original_now = loop.journal.db.execute('SELECT * FROM orders WHERE intent_id=?',
                                               (original_id,)).fetchone()
        assert original_now['cancel_requested'] == 1 and original_now['cancel_confirmed'] == 0
        assert original_now['filled'] == 4 and original_now['status'] == 'pending'

        await loop.reconcile()  # status confirms cancel after a late 5th fill; source chooses exactly five.
        assert len(trace['posts']) == 2
        assert trace['posts'][0][0] == 'VTTC0012U'
        assert trace['posts'][1][0] == 'VTTC0012U'
        assert trace['cancels'][0][0] == 'VTTC0013U'
        assert trace['posts'][1][1]['ORD_QTY'] == '5'
        replacement = loop.journal.db.execute('SELECT * FROM orders WHERE intent_id<>?',
                                               (original_id,)).fetchone()
        assert int(replacement['quantity']) == 5
        assert loop.journal.db.execute('SELECT cancel_confirmed FROM orders WHERE intent_id=?',
                                       (original_id,)).fetchone()[0] == 1
        assert callback_trace[:6] == ['ACCEPTED','ACCEPTED','CANCEL_PENDING',
                                     'PARTIAL_FILL','CANCELLED','ACCEPTED']
        assert loop.journal.dispatch_state(replacement['intent_id']) == 'ACKED'

        journal_path = settings.state_dir/'etf-mock'/'journal.sqlite3'
        loop.journal.db.close()
        for lease in reversed(tuple(loop._owner_leases.values())):
            lease.close()
        restarted = EtfTradingLoop.__new__(EtfTradingLoop)
        restarted.settings = settings
        restarted._closing = False
        restarted.brokers = {mode:KisCashBroker(settings, mode=mode) for mode in ('mock','mock2')}
        restarted.broker = restarted.brokers['mock']
        restarted._acquire_ownership(tmp_path/'authority')
        restarted.journal = EtfJournal(journal_path)
        restarted._bind_feedback_scopes()
        for sid in ('C3','KP','KD'):
            restarted.journal.enable_order_feedback(sid)
        restarted.execution_registry = restarted._build_execution_registry()
        with pytest.raises(RuntimeError, match='common owner submit permit'):
            await restarted.brokers[route].submit_order(None)
        with pytest.raises(RuntimeError, match='common owner cancel permit'):
            await restarted.brokers[route].cancel_order('fixture-native-order', 'fixture-org')
        restarted._physical_holdings = {}
        restarted._day = '20261028'
        restarted.phase = 'ready'
        restarted._checkpoint_recovery_state = 'IDLE'
        restarted._feed_invalid = set()
        restarted._feed_history_ready = True
        restarted.history = SimpleNamespace(day='')
        restarted.bus = SimpleNamespace(publish_order_intent=lambda intent: _none())
        restarted.quotes = {symbol:(Decimal(100),Decimal(105),time.time_ns())}
        restarted.entry_blocked = lambda *args, **kwargs: False
        restarted._feed_entry_blocked = lambda *args, **kwargs: False
        for broker in restarted.brokers.values():
            broker.request = fake_request
            broker.buying_power = _async_power
        loop = restarted
        await loop.reconcile()
        assert len(trace['posts']) == 2 and len(trace['cancels']) == 1
        assert loop.journal.pending() == []
        assert loop.journal.book(strategy)['positions'][symbol] == 10
    finally:
        loop.journal.db.close()
        for lease in reversed(tuple(loop._owner_leases.values())):
            lease.close()

@pytest.mark.asyncio
async def test_actual_kis_sell_is_blocked_when_native_holdings_are_below_source_position(tmp_path):
    """A source sleeve cannot sell virtual shares absent from KIS account facts."""
    import time
    from core.models import Side
    env_path = tmp_path/'.env'
    env_path.write_text(
        'VIBETRADING_DATA_ROOT=' + tmp_path.as_posix() + '\n'
        'KIS_APP_KEY=fixture-live\nKIS_APP_SECRET=fixture-live-secret\n'
        'KIS_ACCOUNT_NUMBER=fixture-live-account\nKIS_ACCOUNT_PRODUCT_CODE=01\n'
        'KIS_MOCK_APP_KEY=fixture-mock\nKIS_MOCK_APP_SECRET=fixture-mock-secret\n'
        'KIS_MOCK_ACCOUNT_NUMBER=fixture-mock-account\nKIS_MOCK_ACCOUNT_PRODUCT_CODE=01\n'
        'KIS_MOCK2_APP_KEY=fixture-mock2\nKIS_MOCK2_APP_SECRET=fixture-mock2-secret\n'
        'KIS_MOCK2_ACCOUNT_NUMBER=fixture-mock2-account\nKIS_MOCK2_ACCOUNT_PRODUCT_CODE=01\n'
        'ETF_EXECUTION_GUARDS=true\n')
    settings = RuntimeSettings.from_env(env_path)
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.settings = settings
    loop._closing = False
    loop.brokers = {mode:KisCashBroker(settings, mode=mode) for mode in ('mock','mock2')}
    loop.broker = loop.brokers['mock']
    loop._acquire_ownership(tmp_path/'authority')
    loop.journal = EtfJournal(settings.state_dir/'etf-mock'/'journal.sqlite3')
    identity = hashlib.sha256(b'mock:fixture-mock-account:01').hexdigest()
    loop.journal.initialize(Decimal(60000), identity)
    for mode, account in (('mock','fixture-mock-account'), ('mock2','fixture-mock2-account')):
        account_hash = hashlib.sha256((mode+':'+account+':01').encode()).hexdigest()
        loop.journal.bind_order_feedback_scope(mode, account_hash)
    for strategy in ('C3','KP','KD'):
        loop.journal.enable_order_feedback(strategy)
    loop.journal.db.execute('INSERT INTO positions(strategy,symbol,quantity) VALUES(?,?,?)',
        ('C3','069500',5))
    loop.execution_registry = loop._build_execution_registry()
    loop._physical_holdings = {}
    loop._day = '20261028'
    loop.phase = 'ready'
    loop._checkpoint_recovery_state = 'IDLE'
    loop._feed_invalid = set()
    loop._feed_history_ready = True
    loop.history = SimpleNamespace(day='')
    loop.bus = SimpleNamespace(publish_order_intent=lambda intent: _none())
    loop.quotes = {'069500':(Decimal(100),Decimal(105),time.time_ns())}
    loop.entry_blocked = lambda *args, **kwargs: False
    loop._feed_entry_blocked = lambda *args, **kwargs: False
    posts = []

    async def fake_request(method, endpoint, tr_id, data, *, tr_cont='', permit=None):
        if method == 'GET' and endpoint == 'inquire-psbl-order':
            return {'body':{'output':{'max_buy_qty':'10','psbl_qty_calc_unpr':'100',
                'ord_psbl_cash':'60000'}},'headers':{}}
        if method == 'GET' and endpoint == 'inquire-balance':
            return {'body':{'output1':[{'pdno':'069500','hldg_qty':'3'}],
                'output2':[{'dnca_tot_amt':'60000'}]},'headers':{}}
        if method == 'POST':
            posts.append((method,endpoint,dict(data)))
            if permit is not None:
                permit()
            return {'body':{'output':{'ODNO':'unexpected'}},'headers':{}}
        raise AssertionError('unexpected KIS request '+method+' '+endpoint)

    for broker in loop.brokers.values():
        broker.request = fake_request
        broker.buying_power = _async_power
    try:
        with pytest.raises(Exception, match='SELL exceeds fresh unreserved native holdings'):
            await loop.place('C3','069500',Side.SELL,4,'exec01-native-share-shortfall',
                direction=-1,valid_until_ts_ns=time.time_ns()+60_000_000_000)
        row = loop.journal.db.execute(
            "SELECT status,quantity FROM orders WHERE side='sell' ORDER BY created_ns DESC LIMIT 1").fetchone()
        assert row is not None and int(row['quantity']) == 4
        assert row['status'] == 'blocked'
        assert posts == []
    finally:
        loop.journal.db.close()
        for lease in reversed(tuple(loop._owner_leases.values())):
            lease.close()

@pytest.mark.asyncio
async def test_shared_kis_orderable_cash_blocks_cross_strategy_buy_overallocation(tmp_path):
    """C3 and KP share native KIS cash reservations in the common owner."""
    import time
    from core.models import Side
    env_path = tmp_path/'.env'
    env_path.write_text(
        'VIBETRADING_DATA_ROOT=' + tmp_path.as_posix() + '\n'
        'KIS_APP_KEY=fixture-live\nKIS_APP_SECRET=fixture-live-secret\n'
        'KIS_ACCOUNT_NUMBER=fixture-live-account\nKIS_ACCOUNT_PRODUCT_CODE=01\n'
        'KIS_MOCK_APP_KEY=fixture-mock\nKIS_MOCK_APP_SECRET=fixture-mock-secret\n'
        'KIS_MOCK_ACCOUNT_NUMBER=fixture-mock-account\nKIS_MOCK_ACCOUNT_PRODUCT_CODE=01\n'
        'KIS_MOCK2_APP_KEY=fixture-mock2\nKIS_MOCK2_APP_SECRET=fixture-mock2-secret\n'
        'KIS_MOCK2_ACCOUNT_NUMBER=fixture-mock2-account\nKIS_MOCK2_ACCOUNT_PRODUCT_CODE=01\n'
        'ETF_EXECUTION_GUARDS=true\n')
    settings = RuntimeSettings.from_env(env_path)
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.settings = settings
    loop._closing = False
    loop.brokers = {mode:KisCashBroker(settings, mode=mode) for mode in ('mock','mock2')}
    loop.broker = loop.brokers['mock']
    loop._acquire_ownership(tmp_path/'authority')
    loop.journal = EtfJournal(settings.state_dir/'etf-mock'/'journal.sqlite3')
    identity = hashlib.sha256(b'mock:fixture-mock-account:01').hexdigest()
    loop.journal.initialize(Decimal(60000), identity)
    for mode, account in (('mock','fixture-mock-account'), ('mock2','fixture-mock2-account')):
        account_hash = hashlib.sha256((mode+':'+account+':01').encode()).hexdigest()
        loop.journal.bind_order_feedback_scope(mode, account_hash)
    for strategy in ('C3','KP','KD'):
        loop.journal.enable_order_feedback(strategy)
    loop.execution_registry = loop._build_execution_registry()
    loop._physical_holdings = {}
    loop._day = '20261028'
    loop.phase = 'ready'
    loop._checkpoint_recovery_state = 'IDLE'
    loop._feed_invalid = set()
    loop._feed_history_ready = True
    loop.history = SimpleNamespace(day='')
    loop.bus = SimpleNamespace(publish_order_intent=lambda intent: _none())
    loop.quotes = {'069500':(Decimal(100),Decimal(105),time.time_ns())}
    loop.entry_blocked = lambda *args, **kwargs: False
    loop._feed_entry_blocked = lambda *args, **kwargs: False
    posts = []

    async def fake_request(method, endpoint, tr_id, data, *, tr_cont='', permit=None):
        if method == 'GET' and endpoint == 'inquire-psbl-order':
            return {'body':{'output':{'max_buy_qty':'10','psbl_qty_calc_unpr':'100',
                'ord_psbl_cash':'150'}},'headers':{}}
        if method == 'GET' and endpoint == 'inquire-balance':
            return {'body':{'output1':[], 'output2':[{'dnca_tot_amt':'60000'}]},'headers':{}}
        if method == 'POST' and endpoint == 'order-cash':
            if permit is None:
                raise AssertionError('common owner permit missing')
            permit()
            posts.append((tr_id,dict(data)))
            return {'body':{'output':{'ODNO':'cash-'+str(len(posts)),
                'KRX_FWDG_ORD_ORGNO':'fixture-org'}}}
        raise AssertionError('unexpected KIS request '+method+' '+endpoint)

    for broker in loop.brokers.values():
        broker.request = fake_request
    try:
        expiry = time.time_ns()+60_000_000_000
        await loop.place('C3','069500',Side.BUY,1,'exec01-shared-cash-c3',
            direction=1,valid_until_ts_ns=expiry)
        with pytest.raises(Exception, match='orderable cash'):
            await loop.place('KP','069500',Side.BUY,1,'exec01-shared-cash-kp',
                direction=1,valid_until_ts_ns=expiry)
        rows = loop.journal.db.execute(
            "SELECT strategy,status,reserve,quantity,price FROM orders WHERE account_scope='mock' AND side='buy' "
            "ORDER BY strategy").fetchall()
        assert [row['strategy'] for row in rows] == ['C3','KP']
        assert rows[0]['status'] == 'pending' and rows[1]['status'] == 'blocked'
        attempted_total = Decimal(rows[0]['reserve']) + Decimal(rows[1]['price']) * int(rows[1]['quantity']) * Decimal('1.005')
        assert attempted_total > Decimal('150')
        assert len(posts) == 1
    finally:
        loop.journal.db.close()
        for lease in reversed(tuple(loop._owner_leases.values())):
            lease.close()
