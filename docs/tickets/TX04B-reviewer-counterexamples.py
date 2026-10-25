"""Independent stale-depth regression at the existing Paper runtime seam."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from test_strategy_runtime import (T, BTC, ledger_at, matcher_for, runtime, budget,
    registration, deliver, book, trade)
from vibetrading.models import Side

@pytest.mark.parametrize("delivery", ["tick", "late_native_event", "fresh_trade_old_book", "same_book", "same_book_new_generation"])
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
        elif delivery.startswith("same_book"):
            event=book(T+11,sequence=2)
            if delivery.endswith("new_generation"):event=event.model_copy(update={"sync_generation":2})
            deliver(host,now,event)
        else:
            event=trade(T+11,side=Side.SELL,price=100)
            if delivery=="late_native_event":event=event.model_copy(update={"source_ts_ns":T})
            deliver(host,now,event)
        assert matcher.report()["fills"]==0
        assert ledger.position("probe",BTC.key)==0
        assert D(ledger.report()["balances"]["USDT"])==D(100000)
        if delivery=="same_book_new_generation":
            assert not host.report()["account_faults"]
            assert BTC.key not in host.report()["invalid_inputs"]
    finally:ledger.close()


@pytest.mark.parametrize("delivery",["other_book","other_trade"])
def test_other_fresh_feed_cannot_advance_stale_pending_ioc(tmp_path,delivery):
    from vibetrading.hot_path import HotPathStore
    from vibetrading.strategy_runtime import PaperStrategyRuntime
    from vibetrading.trading_contracts import TradingMode
    from test_strategy_runtime import spec
    other=BTC.model_copy(update={"raw_symbol":"ETHUSDT","std_symbol":"ETHUSDT"})
    ledger=ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT",D(100000),{"synthetic":True});matcher=matcher_for(ledger);now=[T]
        host=PaperStrategyRuntime(hot_path=HotPathStore(),accounts={ledger.scope.key:matcher},
            products={BTC.key:spec(),other.key:spec(other)},known_instruments={BTC.key:BTC,other.key:other},
            reference_feeds={},budgets=budget(max_feed_age_ns=5),settings={"synthetic":True},
            mode=TradingMode.PAPER,phase="paper",now_ns=lambda:now[0])
        reg=registration(ledger,"cross_probe",[])
        def builder(signal,view,config):
            return [replace(a,post_only=False,time_in_force="IOC",intent=a.intent.model_copy(update={"limit_price":D(101)}))for a in reg.action_builder(signal,view,config)]
        host.register(replace(reg,action_builder=builder));deliver(host,now,book())
        if delivery=="other_trade":
            deliver(host,now,book(T+7,instrument=other))
            deliver(host,now,trade(T+11,side=Side.SELL,price=100,instrument=other))
        else:deliver(host,now,book(T+11,instrument=other))
        assert matcher.report()["fills"]==0
        assert ledger.position("cross_probe",BTC.key)==0
        assert D(ledger.report()["balances"]["USDT"])==D(100000)
        assert BTC.key in host.report()["invalid_inputs"]
        assert other.key not in host.report()["invalid_inputs"]
    finally:ledger.close()
