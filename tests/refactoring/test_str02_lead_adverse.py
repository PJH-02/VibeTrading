"""Lead-authored adverse acceptance checks, independent of worker fixtures."""
from decimal import Decimal as D, localcontext
import pytest
from execution.execution_ledger import AccountScope
from execution.trading_contracts import StrategyOrderContext, StrategyOrderEvent
from test_portfolio_ledger import seeded, admit, order


def test_order_quantities_do_not_depend_on_decimal_context():
    with localcontext() as ctx:
        ctx.prec = 2
        event = StrategyOrderEvent('e', 'alpha', AccountScope('binance','acct','cash','paper'),
            'o', 'PARTIAL_FILL', 1, D('123.45'), D('23.45'), D('100'))
        assert event.remaining_quantity == D('100')


def test_context_cannot_keep_mutable_cursor_or_actor():
    cursor = ([1, 2],)
    event = StrategyOrderContext(None, {'balance': '100'}, 1, cursor, 'VALID')
    cursor[0].append(3)
    assert event.cursor == ((1, 2),)
    with pytest.raises((ValueError, TypeError)):
        StrategyOrderContext(None, {}, 1, (object(),), 'VALID')


def test_rejection_is_idempotent_across_observation_clocks(tmp_path):
    with seeded(tmp_path) as ledger:
        ledger.enable_order_feedback('alpha')
        intent = order(identity='unsupported')
        ledger.record_order_rejection(intent.intent_id,'alpha','unsupported FOK',intent=intent,now_ns=10)
        ledger.record_order_rejection(intent.intent_id,'alpha','unsupported FOK',intent=intent,now_ns=20)
        assert len(ledger.order_feedback('alpha')) == 1


def test_local_rejection_does_not_impersonate_different_venue(tmp_path):
    from core.models import Venue
    with seeded(tmp_path) as ledger:
        ledger.enable_order_feedback('alpha')
        intent = order(identity='foreign')
        foreign = intent.model_copy(update={'instrument': intent.instrument.model_copy(update={'venue': Venue.BYBIT})})
        with pytest.raises((ValueError, TypeError)):
            ledger.record_order_rejection(foreign.intent_id,'alpha','unsupported FOK',intent=foreign,now_ns=10)
        assert not ledger.order_feedback('alpha')


def test_same_native_outcome_id_across_two_orders_stays_distinct(tmp_path):
    with seeded(tmp_path) as ledger:
        ledger.enable_order_feedback('alpha')
        admit(ledger,order(identity='one'))
        admit(ledger,order(identity='two'))
        ledger.cancel('one',confirmed=True,evidence={'id':'native-repeated-token'})
        ledger.cancel('two',confirmed=True,evidence={'id':'native-repeated-token'})
        events = ledger.order_feedback('alpha')
        assert len(events) == 4
        assert {e.metadata['intent_id'] for _,e in events if e.status=='CANCELLED'} == {'one','two'}


def test_feedback_is_not_visible_before_owner_commit(tmp_path):
    with seeded(tmp_path) as ledger:
        ledger.enable_order_feedback('alpha')
        admit(ledger)
        with pytest.raises(RuntimeError):
            with ledger.transaction():
                ledger.apply_fill('rolled-back','one','0.4','99',fee='0.1')
                raise RuntimeError('crash before owner commit')
        assert [e.status for _,e in ledger.order_feedback('alpha')] == ['ACCEPTED']
        ledger.apply_fill('rolled-back','one','0.4','99',fee='0.1')
        assert len(ledger.order_feedback('alpha')) == 2


def test_unknown_cancel_observations_after_late_fill_do_not_collide(tmp_path):
    with seeded(tmp_path) as ledger:
        ledger.enable_order_feedback('alpha')
        admit(ledger)
        ledger.request_cancel('one', 'alpha')
        ledger.cancel('one', confirmed=False)
        ledger.apply_fill('late', 'one', '0.4', '99', fee='0.1')
        ledger.cancel('one', confirmed=False)
        ledger.request_cancel('one', 'alpha')
        assert D(ledger.get('one')['filled']) == D('0.4')
        assert ledger.order_feedback('alpha')[-1][1].remaining_quantity == D('0.6')
