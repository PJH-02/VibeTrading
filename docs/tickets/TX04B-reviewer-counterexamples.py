"""Independent stale-depth regression at the existing Paper runtime seam."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from test_strategy_runtime import (T, BTC, ledger_at, matcher_for, runtime, budget,
    registration, deliver, book, trade)
from vibetrading.models import Side

@pytest.mark.parametrize("delivery", ["tick", "late_native_event", "fresh_trade_old_book"])
def test_missing_freshness_never_consumes_cached_ioc_depth(tmp_path, delivery):
    ledger=ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT",D(100000),{"synthetic":True})
        matcher=matcher_for(ledger);now=[T];observed=[]
        host=runtime(ledger,matcher,now,budgets=budget(max_feed_age_ns=5))
        reg=registration(ledger,"probe",observed)
        def builder(signal,view,config):
            values=reg.action_builder(signal,view,config)
            return [replace(action,post_only=False,time_in_force="IOC",
                intent=action.intent.model_copy(update={"limit_price":D(101)})) for action in values]
        host.register(replace(reg,action_builder=builder))
        assert len(deliver(host,now,book()))==1
        assert matcher.report()["fills"]==0
        if delivery=="tick":host.advance(T+11)
        else:
            event=trade(T+11,side=Side.SELL,price=100)
            if delivery=="late_native_event":event=event.model_copy(update={"source_ts_ns":T})
            deliver(host,now,event)
        assert matcher.report()["fills"]==0
        assert ledger.position("probe",BTC.key)==0
        assert D(ledger.report()["balances"]["USDT"])==D(100000)
    finally:ledger.close()
