"""Independent reserved-attribution and exact existing Paper accounting probes."""
from decimal import Decimal as D
import pytest
from vibetrading.models import Side
from vibetrading.trading_contracts import ProductKind
from test_portfolio_ledger import seeded, ledger, admit, spec, order


@pytest.mark.parametrize('strategy', ['@physical', '@physical_netting_adjustment',
    '@unallocated_funding', '@custom', '@'])
@pytest.mark.parametrize('operation', ['admit', 'claim'])
def test_reserved_strategy_cannot_modify_orders_cash_or_receipts(tmp_path, strategy, operation):
    with seeded(tmp_path) as book:
        before = tuple(book._db.iterdump())
        with pytest.raises(ValueError):
            if operation == 'admit':
                admit(book, strategy=strategy)
            else:
                book.claim_strategy_event(strategy, 'version', '@registration', {'source': 'synthetic'})
        assert tuple(book._db.iterdump()) == before


def test_internal_netting_and_unallocated_funding_survive_restart_without_reassignment(tmp_path):
    rules = spec(ProductKind.LINEAR_PERPETUAL)
    with seeded(tmp_path) as book:
        admit(book, order(rules), rules=rules, strategy='alpha', margin_rate=D('.1'))
        book.apply_fill('alpha-fill', 'one', 1, 100, fee=D('.1'))
        admit(book, order(rules, identity='beta', side=Side.SELL, price=110),
            rules=rules, strategy='beta', margin_rate=D('.1'))
        book.apply_fill('beta-fill', 'beta', 1, 110, fee=D('.2'))
        book.apply_funding('unallocated', rules.instrument.key, D('-2'), 'USDT', {'source': 'synthetic'})
        report = book.report()
        assert D(report['physical_positions'][rules.instrument.key]) == 0
        assert book.position('alpha', rules.instrument.key) == 1
        assert book.position('beta', rules.instrument.key) == -1
        assert D(report['balances']['USDT']) == D('1007.7')
        categories = {row['strategy']:D(row['amount']) for row in report['strategy_cash']}
        assert categories['@physical_netting_adjustment'] == 10
        assert categories['@unallocated_funding'] == -2
        assert sum(categories.values(), D(0)) == D('7.7')
        book.claim_strategy_event('alpha', 'version', '@registration', {'source': 'synthetic'})
    with ledger(tmp_path) as fresh:
        before = fresh.report()
        assert not fresh.apply_fill('alpha-fill', 'one', 1, 100, fee=D('.1'))
        assert not fresh.apply_funding('unallocated', rules.instrument.key, D('-2'), 'USDT', {'source': 'synthetic'})
        after = fresh.report()
        assert after == before
        assert D(after['balances']['USDT']) == D('1007.7')
