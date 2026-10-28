"""Lead-authored arithmetic and accounting boundaries; no venue access."""
from dataclasses import replace
from decimal import Decimal as D, localcontext
import pytest
from core.models import Side
from execution.trading_contracts import ProductKind
from test_portfolio_ledger import ledger, admit, order, spec


def rules():
    return replace(spec(ProductKind.LINEAR_PERPETUAL), multiplier=D(10),
                   maker_fee_rate=D('.001'), taker_fee_rate=D('.002'))


def setup(book):
    product = rules()
    book.seed_balance('USDT', 10000, {'fixture':'synthetic'})
    admit(book, order(product, 'alpha-open', quantity='2', price='100'),
          rules=product, strategy='alpha', margin_rate=D('.1'))
    book.apply_fill('native-1','alpha-open',2,100,fee=2)
    return product


def value(book, product, mark):
    return book.valuation({product.instrument.key:D(mark)},
        maintenance_margin_rates={product.instrument.key:D('.05')},
        account_version=book.account_version(),asof_ns=10,now_ns=11,max_age_ns=10)


def test_physical_netting_and_virtual_mark_pnl_are_not_double_counted(tmp_path):
    with ledger(tmp_path) as book:
        product=setup(book)
        first=value(book,product,'110')
        for k,v in {'cash':'9998','unrealized':'200','equity':'10198',
                    'initial_margin':'200','maintenance_margin':'110','available':'9998'}.items():
            assert D(str(first[k]))==D(v),(k,first)
        admit(book,order(product,'beta-open',side=Side.SELL,price='110'),
              rules=product,strategy='beta',margin_rate=D('.1'))
        book.apply_fill('native-2','beta-open',1,110,fee='2.2')
        ver=book.account_version()
        assert book.apply_funding('fund-1',product.instrument.key,-5,'USDT',
            {'period':'proved-period-position-attribution'},
            allocations={'alpha':D(-10),'beta':D(5)},account_version=ver)
        assert not book.apply_funding('fund-1',product.instrument.key,-5,'USDT',
            {'period':'proved-period-position-attribution'},
            allocations={'alpha':D(-10),'beta':D(5)},account_version=ver)
        admit(book,order(product,'alpha-close',side=Side.SELL,price='120'),
              rules=product,strategy='alpha',margin_rate=D('.1'))
        book.apply_fill('native-3','alpha-close',1,120,fee='1.2')
        with localcontext() as ctx:
            ctx.prec=2
            final=value(book,product,'115')
        for k,v in {'cash':'10289.6','unrealized':'0','equity':'10289.6',
                    'realized':'300','fees':'5.4','funding':'-5',
                    'initial_margin':'210','maintenance_margin':'115','available':'10079.6'}.items():
            assert D(str(final[k]))==D(v),(k,final)
        assert '@physical_netting_adjustment' not in final['strategies']
        assert sum(D(str(final['strategies'][sid]['equity']))
                   for sid in ('alpha','beta'))==D('289.6')


def test_explicit_funding_allocation_requires_version_before_mutation(tmp_path):
    with ledger(tmp_path) as book:
        product=setup(book)
        before=book.report()
        with pytest.raises(ValueError):
            book.apply_funding('unproved',product.instrument.key,-1,'USDT',{'period':'x'},
                               allocations={'alpha':D(-1)})
        assert book.report()==before


def test_no_mark_is_unavailable_and_unknown_keeps_reserved_risk(tmp_path):
    with ledger(tmp_path) as book:
        product=setup(book)
        missing=book.valuation({},maintenance_margin_rates={},account_version=book.account_version(),
            asof_ns=10,now_ns=11,max_age_ns=10)
        assert missing['quality']=='UNAVAILABLE' and missing['equity'] is None
        admit(book,order(product,'pending',quantity='.5',price='115'),
              rules=product,strategy='alpha',margin_rate=D('.1'))
        book.cancel('pending',confirmed=False)
        current=value(book,product,'115')
        assert D(str(current['reserved']))==D('58.65')
        with pytest.raises((RuntimeError,ValueError)):
            admit(book,order(product,'blind-retry',quantity='.5',price='115'),
                  rules=product,strategy='alpha',margin_rate=D('.1'))
