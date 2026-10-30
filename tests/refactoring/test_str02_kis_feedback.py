"""Phase C source-owned KIS feedback using only a scripted in-memory transport."""
from decimal import Decimal
import hashlib

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from execution.execution_ledger import AccountScope
from execution.kis_execution import KisCashBroker
from execution.trading_contracts import StrategyOrderContext, StrategyOrderEvent
from portfolio.etf_journal import EtfJournal
from runtime.settings import RuntimeSettings
from strategies.kr import c3, kd, kp

SOURCES = {'C3': c3, 'KP': kp, 'KD': kd}


def _settings(tmp_path):
    env = tmp_path / 'worker.env'
    env.write_text(
        'VIBETRADING_DATA_ROOT=' + tmp_path.as_posix() + '\n'
        'KIS_APP_KEY=fixture-real\nKIS_APP_SECRET=fixture-secret\n'
        'KIS_ACCOUNT_NUMBER=fixture-real-account\nKIS_ACCOUNT_PRODUCT_CODE=01\n'
        'KIS_MOCK_APP_KEY=fixture-mock\nKIS_MOCK_APP_SECRET=fixture-mock-secret\n'
        'KIS_MOCK_ACCOUNT_NUMBER=fixture-mock-account\nKIS_MOCK_ACCOUNT_PRODUCT_CODE=01\n'
        'KIS_MOCK2_APP_KEY=fixture-mock2\nKIS_MOCK2_APP_SECRET=fixture-mock2-secret\n'
        'KIS_MOCK2_ACCOUNT_NUMBER=fixture-mock2-account\nKIS_MOCK2_ACCOUNT_PRODUCT_CODE=01\n'
        'ETF_EXECUTION_GUARDS=true\n')
    return RuntimeSettings.from_env(env)


def _order(strategy, route, symbol, side=Side.BUY, *, created=None, expiry=None):
    now = __import__('time').time_ns()
    created = created or now - 10_000_000_000
    expiry = expiry or now + 60_000_000_000
    identity = hashlib.sha256((strategy + ':' + symbol).encode()).hexdigest()
    meta = {'account_scope': route, 'account_mode': route, 'strategy': strategy,
            'direction': 1, 'valid_until_ts_ns': expiry, 'intent_quote': {
                'bid': '100', 'ask': '105', 'received_ns': now, 'intent_ns': created},
            'source_provider': 'kiwoom'}
    return OrderIntent(intent_id=identity, job_id=strategy, signal_id='initial',
        destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF,
            raw_symbol=symbol, std_symbol=symbol, currency='KRW', exchange='KRX'),
        side=side, client_order_id=identity[:32], quantity=Decimal(10),
        limit_price=Decimal(100), created_ts_ns=created, metadata=meta)


def _context(event, order, *, now_ns, book=None, power=None, replacement_id=None):
    source = SOURCES[event.strategy_id]
    scope = event.account_scope
    order_facts = {'intent_id': order.intent_id, 'strategy': event.strategy_id,
        'symbol': order.instrument.raw_symbol, 'side': order.side.value,
        'quantity': 10, 'filled': int(event.filled_quantity), 'status': event.status,
        'created_ns': order.created_ts_ns, 'accepted_ns': order.created_ts_ns,
        'order_type': 'limit', 'cancel_requested': event.status in {'CANCEL_PENDING','CANCELLED'},
        'cancel_confirmed': event.status == 'CANCELLED', 'replacement_id': replacement_id,
        'price': '100', 'direction': 1, 'safety_stop': None,
        'account_scope': scope.environment, 'metadata': dict(order.metadata),
        'payload': order.model_dump_json()}
    execution = {'order': order_facts,
        'book': book or {'cash': '100000', 'reserved': '0', 'positions': {}},
        'quote': {'bid': '100', 'ask': '105', 'observed_ns': now_ns},
        'buying_power': power or {'market_reserve_price': '130', 'max_buy_qty': 10},
        'account_identity': scope.account, 'route': scope.environment,
        'mode': scope.environment, 'config_version': source.STRATEGY_VERSION}
    return StrategyOrderContext(data=None, execution=execution, now_ns=now_ns,
                                cursor=(1,), quality='KIS_JOURNAL_FACTS')


def _event(strategy, route, account, order, status, filled=0, *, event_id='event'):
    scope = AccountScope('kis', account, 'cash', route)
    return StrategyOrderEvent(event_id=event_id, strategy_id=strategy,
        account_scope=scope, order_id=order.intent_id, status=status,
        event_ts_ns=None, quantity=Decimal(10), filled_quantity=Decimal(filled),
        remaining_quantity=Decimal(10-filled), metadata={'source_timestamp_ns': None})


@pytest.mark.parametrize('strategy,symbol,route', [
    ('C3','069500','mock'), ('KP','122630','mock2'), ('KD','229200','mock')])
def test_original_sources_choose_cancel_then_exact_confirmed_remainder(strategy, symbol, route):
    source = SOURCES[strategy]
    now = __import__('time').time_ns()
    account = hashlib.sha256((route + ':fixture-account:01').encode()).hexdigest()
    order = _order(strategy, route, symbol, created=now-8_000_000_000,
                   expiry=now+60_000_000_000)
    accepted = _event(strategy, route, account, order, 'ACCEPTED', event_id='accepted')
    plan = source.on_order_event(accepted, _context(accepted, order, now_ns=now))
    assert len(plan.actions) == 1 and plan.actions[0].action.value == 'CANCEL'
    assert plan.actions[0].target_order_id == order.intent_id
    assert plan.actions[0].mode.value == route

    cancelled = _event(strategy, route, account, order, 'CANCELLED', 4, event_id='cancelled')
    plan = source.on_order_event(cancelled, _context(cancelled, order, now_ns=now+1,
        book={'cash':'100000','reserved':'0','positions':{symbol:4}},
        power={'market_reserve_price':'130','max_buy_qty':5}))
    assert len(plan.actions) == 1 and plan.actions[0].action.value == 'NEW'
    intent = plan.actions[0].intent
    assert intent.quantity == Decimal(5)  # 6 factual shares, capped by owner buying power.
    assert intent.limit_price is None
    assert intent.metadata['valid_until_ts_ns'] == order.metadata['valid_until_ts_ns']
    assert plan.actions[0].time_in_force == 'IOC'

    late = _event(strategy, route, account, order, 'CANCELLED', 5, event_id='late-fill')
    late_plan = source.on_order_event(late, _context(late, order, now_ns=now+2,
        replacement_id=intent.intent_id))
    assert late_plan.actions == ()


@pytest.mark.parametrize('strategy,symbol,route', [
    ('C3','069500','mock'), ('KP','122630','mock2'), ('KD','229200','mock')])
def test_real_kis_cash_adapter_posts_source_remainder_to_exact_mock_namespace(tmp_path, strategy, symbol, route):
    source = SOURCES[strategy]
    now = __import__('time').time_ns()
    account = hashlib.sha256((route + ':fixture-account:01').encode()).hexdigest()
    order = _order(strategy, route, symbol, created=now-8_000_000_000,
                   expiry=now+60_000_000_000)
    cancelled = _event(strategy, route, account, order, 'CANCELLED', 4, event_id='cancelled:'+strategy)
    plan = source.on_order_event(cancelled, _context(cancelled, order, now_ns=now,
        power={'market_reserve_price':'130','max_buy_qty':6}))
    intent = plan.actions[0].intent
    broker = KisCashBroker(_settings(tmp_path), mode=route)
    broker.authority_check = lambda: True
    calls = []
    async def fake_transport(method, endpoint, tr_id, data, *, tr_cont='', permit=None):
        if permit is not None:
            permit()
        calls.append((method, endpoint, tr_id, data))
        return {'body': {'output': {'ODNO': 'fixture-order'}}}
    broker.request = fake_transport
    async def permit():
        raise AssertionError('permit is expected to be synchronous')
    import asyncio
    result = asyncio.run(broker.submit_order_with_permit(intent, lambda: calls.append(('PERMIT', '', '', {}))))
    assert result['ODNO'] == 'fixture-order'
    post = [call for call in calls if call[0] == 'POST']
    assert len(post) == 1
    assert post[0][1] == 'order-cash'
    assert post[0][2] == ('VTTC' if route == 'mock2' else 'VTTC') + '0012U'
    assert post[0][3]['ORD_QTY'] == str(intent.quantity)
    assert post[0][3]['ORD_UNPR'] == '0'


def test_journal_feedback_is_atomic_idempotent_bounded_and_timestamp_honest(tmp_path):
    journal = EtfJournal(tmp_path/'feedback.sqlite3', max_feedback_rows=8,
                         max_feedback_bytes=32768)
    journal.initialize(Decimal(6000), 'account-hash')
    journal.bind_order_feedback_scope('mock', 'account-hash')
    journal.enable_order_feedback('C3')
    order = _order('C3','mock','069500')
    assert journal.stage(order, '20261028', 1, None)
    accepted = journal.accepted(order.intent_id, {'ODNO':'native-1'})
    assert accepted is not None
    event = journal.order_feedback_event(accepted)
    assert event.status == 'ACCEPTED' and event.event_ts_ns is None
    partial = journal.apply_status(order.intent_id,
        {'tot_ccld_qty':'4','tot_ccld_amt':'400','rmn_qty':'6'})
    assert partial is not None
    fill_event = journal.order_feedback_event(partial)
    assert fill_event.status == 'PARTIAL_FILL'
    assert fill_event.fill_quantity == Decimal(4) and fill_event.fill_price == Decimal(100)
    assert fill_event.fee is None and fill_event.metadata['source_timestamp_ns'] is None
    assert 'not broker fee' in fill_event.metadata['fee_basis']
    again = journal.apply_status(order.intent_id,
        {'tot_ccld_qty':'4','tot_ccld_amt':'400','rmn_qty':'6'})
    assert again == partial
    assert journal.claim_order_feedback(partial, 'C3') is not None
    assert journal.claim_order_feedback(partial, 'C3') is None
    before = journal.book('C3')['positions']['069500']
    journal.db.close()
    reopened = EtfJournal(tmp_path/'feedback.sqlite3', max_feedback_rows=8,
                          max_feedback_bytes=32768)
    assert reopened.book('C3')['positions']['069500'] == before
    assert reopened.order_feedback('KP') == []
    reopened.db.close()


def test_feedback_capacity_keeps_factual_fill_and_stops_new_callback(tmp_path):
    journal = EtfJournal(tmp_path/'capacity.sqlite3', max_feedback_rows=1,
                         max_feedback_bytes=32768)
    journal.initialize(Decimal(6000), 'account-hash')
    journal.bind_order_feedback_scope('mock', 'account-hash')
    journal.enable_order_feedback('C3')
    order = _order('C3','mock','069500')
    journal.stage(order, '20261028', 1, None)
    journal.accepted(order.intent_id, {'ODNO':'native-2'})
    assert journal.apply_status(order.intent_id,
        {'tot_ccld_qty':'3','tot_ccld_amt':'300','rmn_qty':'7'}) == 0
    assert journal.db.execute('SELECT filled FROM orders WHERE intent_id=?', (order.intent_id,)).fetchone()[0] == 3
    assert journal.db.execute('SELECT COUNT(*) FROM order_feedback').fetchone()[0] == 1
    journal.db.close()


def test_wrong_original_scope_and_paper_without_data_never_select_kis_actions():
    source = SOURCES['KP']
    now = __import__('time').time_ns()
    order = _order('KP','mock2','122630',created=now-8_000_000_000)
    good = _event('KP','mock2','physical-hash',order,'ACCEPTED',event_id='scope-check')
    wrong = StrategyOrderEvent(event_id='wrong-scope',strategy_id='KP',
        account_scope=AccountScope('kis','other-account','cash','mock2'), order_id=order.intent_id,
        status='ACCEPTED', event_ts_ns=None, quantity=Decimal(10),
        filled_quantity=Decimal(0), remaining_quantity=Decimal(10))
    context = _context(good, order, now_ns=now)
    bad_context = StrategyOrderContext(data=None, execution=dict(context.execution),
        now_ns=now, cursor=(2,), quality='KIS_JOURNAL_FACTS')
    with pytest.raises(ValueError, match='account scope'):
        source.on_order_event(wrong, bad_context)
    paper = StrategyOrderEvent(event_id='paper-unavailable', strategy_id='KP',
        account_scope=AccountScope('kis','paper-account','cash','paper'), order_id='paper-order',
        status='UNKNOWN', event_ts_ns=None, quantity=Decimal(1),
        filled_quantity=Decimal(0), remaining_quantity=Decimal(1))
    unavailable = StrategyOrderContext(data=None, execution={}, now_ns=now,
        cursor=None, quality='UNAVAILABLE')
    assert source.on_order_event(paper, unavailable) == []


@pytest.mark.asyncio
async def test_raising_source_callback_fault_is_durable_and_stops_that_strategy(tmp_path, monkeypatch):
    from runtime.etf_trading import EtfTradingLoop
    physical = 'fixture-physical'
    identity = hashlib.sha256(('mock:' + physical + ':01').encode()).hexdigest()
    journal = EtfJournal(tmp_path/'fault.sqlite3')
    journal.initialize(Decimal(6000), identity)
    journal.bind_order_feedback_scope('mock', identity)
    journal.enable_order_feedback('C3')
    order = _order('C3','mock','069500')
    journal.stage(order, '20261028', 1, None)
    receipt = journal.accepted(order.intent_id, {'ODNO':'fault-native'})
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.journal = journal
    loop.brokers = {'mock': object(), 'mock2': object()}
    loop._account_ids = {'mock':physical, 'mock2':'fixture-secondary'}
    loop._owner_leases = {'mock':object(),'mock2':object()}
    loop._closing = False
    loop._check_ownership = lambda: None
    loop.broker_for = lambda symbol, route=None: object()
    original = SOURCES['C3'].on_order_event
    def explode(event, context, instance=None):
        raise RuntimeError('fixture callback failure')
    monkeypatch.setattr(SOURCES['C3'], 'on_order_event', explode)
    with pytest.raises(RuntimeError, match='fixture callback failure'):
        await loop._deliver_order_feedback(order.intent_id, feedback_row_id=receipt)
    assert journal.order_feedback_fault('C3') is not None
    journal.db.close()
    reopened = EtfJournal(tmp_path/'fault.sqlite3')
    assert reopened.order_feedback_fault('C3') is not None
    gate = EtfTradingLoop.__new__(EtfTradingLoop)
    gate.journal = reopened
    assert gate.entry_blocked('C3') is True
    monkeypatch.setattr(SOURCES['C3'], 'on_order_event', original)
    reopened.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('strategy,symbol,route', [
    ('C3','069500','mock'), ('C3','122630','mock2'),
    ('KP','069500','mock'), ('KP','122630','mock2'),
    ('KD','229200','mock'), ('KD','251340','mock2')])
async def test_owner_to_source_to_concrete_kis_broker_cancel_race_and_restart_once(
        tmp_path, monkeypatch, strategy, symbol, route):
    from types import SimpleNamespace
    from runtime.etf_trading import EtfTradingLoop
    settings = _settings(tmp_path)
    counters = {'posts': [], 'cancels': [], 'states': {}, 'callbacks': []}

    def make_brokers():
        brokers = {mode: KisCashBroker(settings, mode=mode) for mode in ('mock','mock2')}
        for mode, broker in brokers.items():
            async def transport(method, endpoint, tr_id, data, *, tr_cont='', permit=None, _mode=mode, _broker=broker):
                if method == 'POST':
                    _broker.authority_check()
                    if permit is not None:
                        permit()
                    if endpoint == 'order-cash':
                        identity = f'{_mode}-order-{len(counters["posts"])+1}'
                        counters['posts'].append((identity, dict(data), tr_id))
                        if len(counters['posts']) == 1:
                            counters['states'][identity] = [
                                {'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6'},
                                {'tot_ccld_qty':'5','tot_ccld_amt':'495','rmn_qty':'0','cncl_yn':'Y'}]
                        else:
                            quantity = str(data['ORD_QTY'])
                            counters['states'][identity] = [
                                {'tot_ccld_qty':quantity,'tot_ccld_amt':str(int(quantity)*130),
                                 'rmn_qty':'0'}]
                        return {'body':{'rt_cd':'0','output':{'ODNO':identity,
                            'KRX_FWDG_ORD_ORGNO':'fixture-org'}}}
                    if endpoint == 'order-rvsecncl':
                        counters['cancels'].append((dict(data),tr_id))
                        return {'body':{'rt_cd':'0','output':{}}}
                    raise AssertionError('unexpected scripted KIS POST endpoint')
                if endpoint == 'inquire-psbl-order':
                    return {'body':{'output':{'max_buy_qty':'100',
                        'psbl_qty_calc_unpr':'130'}},'headers':{}}
                if endpoint == 'inquire-daily-ccld':
                    identity = data['ODNO']
                    states = counters['states'][identity]
                    value = states.pop(0) if len(states) > 1 else states[0]
                    return {'body':{'output1':[{'odno':identity, **value}]},'headers':{}}
                raise AssertionError('unexpected scripted KIS GET endpoint: '+endpoint)
            broker.request = transport
        return brokers

    def make_loop(journal, authority_root):
        loop = EtfTradingLoop.__new__(EtfTradingLoop)
        loop.settings = settings
        loop._closing = False
        loop.brokers = make_brokers()
        loop.broker = loop.brokers['mock']
        loop._acquire_ownership(authority_root)
        loop.journal = journal
        loop._bind_feedback_scopes()
        for sid in ('C3','KP','KD'):
            journal.enable_order_feedback(sid)
        loop._day = '20261028'
        loop._feed_invalid = set()
        loop._feed_history_ready = True
        loop._checkpoint_recovery_state = 'IDLE'
        loop.phase = 'ready'
        loop.history = SimpleNamespace(day='')
        loop.bus = SimpleNamespace(publish_order_intent=lambda intent: _async_none())
        loop.quotes = {symbol:(Decimal(100),Decimal(105),__import__('time').time_ns())}
        loop.execution_guards_enabled = lambda: True
        loop.entry_blocked = lambda *args, **kwargs: False
        return loop

    source = SOURCES[strategy]
    original_callback = source.on_order_event
    def callback_trace(event, context, instance=None):
        counters['callbacks'].append((event.status,
            context.execution.get('book',{}).get('physical_positions'),
            context.execution.get('book',{}).get('physical_quality')))
        return original_callback(event, context, instance)
    monkeypatch.setattr(source, 'on_order_event', callback_trace)

    journal_path = settings.state_dir/'etf-mock'/'journal.sqlite3'
    journal = EtfJournal(journal_path)
    physical_mock = 'fixture-mock-account'
    initial_identity = hashlib.sha256(('mock:'+physical_mock+':01').encode()).hexdigest()
    journal.initialize(Decimal(60000), initial_identity)
    secondary_identity = hashlib.sha256(('mock2:fixture-mock2-account:01').encode()).hexdigest()
    journal.db.execute('INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)',
                       ('mock2_account_identity',secondary_identity))
    loop = make_loop(journal, tmp_path/'authority')
    expiry = __import__('time').time_ns()+60_000_000_000
    await loop.place(strategy, symbol, Side.BUY, 10, 'e2e:'+strategy+':'+route,
        direction=1, valid_until_ts_ns=expiry)
    original_row = journal.db.execute('SELECT * FROM orders WHERE order_number IS NOT NULL').fetchone()
    original_id = original_row['intent_id']
    original_number = original_row['order_number']
    aged = __import__('time').time_ns()-6_000_000_000
    journal.db.execute('UPDATE orders SET created_ns=?,accepted_ns=? WHERE intent_id=?',
                       (aged,aged,original_id))

    await loop.reconcile()
    assert len(counters['posts']) == 1, 'cancel ACK/current partial status cannot submit a remainder'
    assert len(counters['cancels']) == 1
    assert journal.db.execute('SELECT cancel_confirmed FROM orders WHERE intent_id=?',
                              (original_id,)).fetchone()[0] == 0
    assert journal.db.execute('SELECT status FROM orders WHERE intent_id=?',
                              (original_id,)).fetchone()[0] == 'pending'

    await loop.reconcile()
    assert len(counters['posts']) == 2
    replacement = journal.db.execute('SELECT * FROM orders WHERE intent_id<>?',
                                     (original_id,)).fetchone()
    replacement_intent = OrderIntent.model_validate_json(replacement['payload'])
    assert replacement_intent.quantity == Decimal(5)
    assert replacement_intent.limit_price is None
    assert replacement_intent.metadata['valid_until_ts_ns'] == expiry
    assert replacement_intent.metadata['replacement_for'] == original_id
    assert replacement['order_type'] == 'market'
    assert counters['posts'][1][1]['ORD_QTY'] == '5'
    assert counters['posts'][1][1]['ORD_UNPR'] == '0'
    callback_statuses = [entry[0] for entry in counters['callbacks']]
    assert callback_statuses[:5] == [
        'ACCEPTED','ACCEPTED','CANCEL_PENDING','PARTIAL_FILL','CANCELLED']
    assert all(physical is None and quality == 'UNAVAILABLE'
               for _,physical,quality in counters['callbacks'])

    replacement_number = replacement['order_number']
    await loop.reconcile()
    assert journal.db.execute('SELECT status FROM orders WHERE order_number=?',
                              (replacement_number,)).fetchone()[0] == 'complete'
    post_count = len(counters['posts'])
    for lease in reversed(tuple(loop._owner_leases.values())):
        lease.close()
    journal.db.close()

    restarted_journal = EtfJournal(journal_path)
    restarted = make_loop(restarted_journal, tmp_path/'authority')
    await restarted.reconcile()
    assert len(counters['posts']) == post_count == 2
    assert restarted_journal.pending() == []
    assert restarted_journal.db.execute('SELECT COUNT(*) FROM order_feedback WHERE claimed=0').fetchone()[0] == 0
    for lease in reversed(tuple(restarted._owner_leases.values())):
        lease.close()
    restarted_journal.db.close()


async def _async_none():
    return None


def test_source_rejects_wrong_strategy_fractional_kis_shares_and_expired_ttl():
    now = __import__('time').time_ns()
    order = _order('KP','mock2','122630',created=now-8_000_000_000,
                   expiry=now+60_000_000_000)
    valid = _event('KP','mock2','physical-hash',order,'ACCEPTED',event_id='valid')
    context = _context(valid, order, now_ns=now)
    wrong_strategy = StrategyOrderEvent(event_id='wrong-strategy', strategy_id='KD',
        account_scope=valid.account_scope, order_id=order.intent_id, status='ACCEPTED',
        event_ts_ns=None, quantity=Decimal(10), filled_quantity=Decimal(0),
        remaining_quantity=Decimal(10))
    with pytest.raises(ValueError, match='original strategy'):
        kp.on_order_event(wrong_strategy, context)

    fractional = StrategyOrderEvent(event_id='fractional', strategy_id='KP',
        account_scope=valid.account_scope, order_id=order.intent_id, status='ACCEPTED',
        event_ts_ns=None, quantity=Decimal('10.5'), filled_quantity=Decimal(0),
        remaining_quantity=Decimal('10.5'))
    with pytest.raises(ValueError, match='quantity facts'):
        kp.on_order_event(fractional, context)

    expired_context = StrategyOrderContext(data=None, execution=dict(context.execution),
        now_ns=order.metadata['valid_until_ts_ns']+1, cursor=(3,), quality='KIS_JOURNAL_FACTS')
    assert kp.on_order_event(valid, expired_context).actions == ()


@pytest.mark.asyncio
async def test_restart_delivers_queued_fill_receipts_in_order_and_caps_stale_cancel_to_current_remainder(
        tmp_path, monkeypatch):
    from runtime.etf_trading import EtfTradingLoop
    route, physical = 'mock', 'fixture-restart-account'
    identity = hashlib.sha256((route + ':' + physical + ':01').encode()).hexdigest()
    path = tmp_path/'queued-feedback.sqlite3'
    journal = EtfJournal(path)
    journal.initialize(Decimal(60000), identity)
    journal.bind_order_feedback_scope(route, identity)
    journal.enable_order_feedback('C3')
    order = _order('C3', route, '069500', created=__import__('time').time_ns())
    journal.stage(order, '20261028', 1, None)
    journal.accepted(order.intent_id, {'ODNO':'queued-native'})
    first = journal.apply_status(order.intent_id,
        {'tot_ccld_qty':'4','tot_ccld_amt':'396','rmn_qty':'6'})
    second = journal.apply_status(order.intent_id,
        {'tot_ccld_qty':'7','tot_ccld_amt':'693','rmn_qty':'3'})
    assert first is not None and second is not None and first != second
    journal.db.close()  # Simulate process exit before any source callback claims either fill.

    reopened = EtfJournal(path)
    loop = EtfTradingLoop.__new__(EtfTradingLoop)
    loop.journal = reopened
    loop.brokers = {'mock': object(), 'mock2': object()}
    loop._account_ids = {'mock':physical, 'mock2':'fixture-secondary'}
    loop._owner_leases = {'mock':object(), 'mock2':object()}
    loop._closing = False
    loop._check_ownership = lambda: None
    loop.broker_for = lambda symbol, route=None: object()
    delivered = []
    source = SOURCES['C3']
    original = source.on_order_event
    def trace(event, context, instance=None):
        delivered.append((event.event_id, event.status, event.fill_quantity,
                          context.execution['order']['current_filled'],
                          context.execution['order']['current_remaining']))
        return original(event, context, instance)
    monkeypatch.setattr(source, 'on_order_event', trace)
    pending = reopened.db.execute(
        'SELECT row_id,order_id FROM order_feedback WHERE claimed=0 ORDER BY row_id').fetchall()
    for receipt in pending:
        await loop._deliver_order_feedback(receipt['order_id'], feedback_row_id=int(receipt['row_id']))
    assert [row[2] for row in delivered if row[2] is not None] == [Decimal(4), Decimal(3)]
    assert [row[3:] for row in delivered if row[2] is not None] == [(7, 3), (7, 3)]
    assert len({row[0] for row in delivered}) == len(delivered)
    assert reopened.db.execute('SELECT COUNT(*) FROM order_feedback WHERE claimed=0').fetchone()[0] == 0
    # Replaying the durable queue after another restart cannot call either fact twice.
    reopened.db.close()
    reopened = EtfJournal(path)
    loop.journal = reopened
    pending = reopened.db.execute(
        'SELECT row_id,order_id FROM order_feedback WHERE claimed=0 ORDER BY row_id').fetchall()
    for receipt in pending:
        await loop._deliver_order_feedback(receipt['order_id'], feedback_row_id=int(receipt['row_id']))
    assert len(delivered) == 3  # accepted callback plus the two ordered cumulative fill facts

    stale_cancel = _event('C3', route, identity, order, 'CANCELLED', 4,
                          event_id='stale-cancel-before-late-fill')
    stale_context = _context(stale_cancel, order, now_ns=__import__('time').time_ns(),
        book={'cash':'60000','reserved':'0','positions':{}},
        power={'market_reserve_price':'100','max_buy_qty':100})
    execution = dict(stale_context.execution)
    stale_order = dict(execution['order'])
    stale_order.update({'current_filled':7, 'current_remaining':3,
        'cancel_confirmed':True, 'current_status':'pending'})
    execution['order'] = stale_order
    stale_context = StrategyOrderContext(data=None, execution=execution,
        now_ns=stale_context.now_ns, cursor=stale_context.cursor, quality=stale_context.quality)
    stale_plan = original(stale_cancel, stale_context)
    assert len(stale_plan.actions) == 1
    assert stale_plan.actions[0].intent.quantity == Decimal(3)
    assert stale_plan.actions[0].intent.quantity < stale_cancel.remaining_quantity
    reopened.db.close()
