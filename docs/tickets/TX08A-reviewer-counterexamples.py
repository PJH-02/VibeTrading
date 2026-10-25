"""Independent same-physical-account policy boundary checks."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from test_strategy_runtime import T,ledger_at,matcher_for,runtime,registration,book,deliver

@pytest.mark.parametrize("field",["account_notional_limit","account_capital_limit"])
def test_second_strategy_cannot_redeclare_shared_account_policy(tmp_path,field):
    ledger=ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT",D(100000),{"synthetic":True})
        now=[T];host=runtime(ledger,matcher_for(ledger),now)
        first=registration(ledger,"alpha",[])
        limits=replace(first.risk[ledger.scope.key],**{field:D(1000)})
        host.register(replace(first,risk={ledger.scope.key:limits}))
        before=tuple(ledger._db.iterdump());jobs=tuple(host._jobs)
        with pytest.raises(ValueError):host.register(registration(ledger,"beta",[]))
        assert tuple(ledger._db.iterdump())==before
        assert tuple(host._jobs)==jobs
        assert len(deliver(host,now,book()))==1
        assert len(ledger.report()["orders"])==1
    finally:ledger.close()


def test_numeric_same_policy_allows_distinct_strategy_budgets(tmp_path):
    ledger=ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT",D(100000),{"synthetic":True})
        now=[T];host=runtime(ledger,matcher_for(ledger),now)
        host.register(registration(ledger,"alpha",[]))
        second=registration(ledger,"beta",[])
        limits=replace(second.risk[ledger.scope.key],account_notional_limit=D("100000.00"),
            account_capital_limit=D("100000.0"),strategy_notional_limit=D(1000),strategy_capital_limit=D(500))
        host.register(replace(second,risk={ledger.scope.key:limits}))
        assert len(deliver(host,now,book()))==2
        assert len(ledger.report()["orders"])==2
    finally:ledger.close()
