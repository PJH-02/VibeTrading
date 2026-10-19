from decimal import Decimal as D
from pathlib import Path
import pytest

from vibetrading.fv_mm.journal import Journal
from vibetrading.fv_mm.types import Quote,Intent
from vibetrading.fv_pilot.public import Book,Instrument,PublicFeeds
from vibetrading.fv_pilot.simulation import PaperMatching
from vibetrading.fv_pilot.market import PilotMarkets


def setup(journal=None,emergency_taker=False):
    i=Instrument("BTCUSDT","USDT",D(1),D(".1"),D(".1"),D(5),D(".0002"),D(".00055"))
    engine=PaperMatching({"bybit":{"BTC":i}},{"bybit":{"entry_seconds":1,"cancel_seconds":.5}},journal,emergency_taker=emergency_taker)
    book=Book();book.update([[99,5],[98,2]],[[101,2],[102,3]],1,True)
    def quote(t,b=99,a=101,source=10**9):
        q=Quote(D(b),D(a),D(5),D(2),t,source,book.sequence)
        engine.on_book("bybit","BTC",q,book)
        return q
    quote(0)
    return engine,book,quote


def open_order(engine,price=99,qty=1,t=0,ioc=False,side="Buy",reduce=False):
    return engine.submit_intent("bybit","S1","BTC",Intent(side,D(price),D(qty),"REDUCE" if reduce else "OPEN",reduce),t,ioc)


def test_no_fill_before_latency_or_from_book_touch():
    e,b,q=setup();o=open_order(e)
    e.on_trade("bybit","BTC","Sell",D(100),D(99),.5,2*10**9)
    assert not o.active and o.leaves==1
    q(1)
    assert o.active and o.queue==5
    b.update([[99,0],[98,0],[97,2]],[],2);q(1.2,b=97)
    assert o.leaves==1  # A disappearing quote cannot prove our maker fill.


def test_entire_visible_queue_precedes_partial_fill():
    e,b,q=setup();o=open_order(e,qty=2);q(1)
    e.on_trade("bybit","BTC","Sell",D(4),D(99),1.1,2*10**9)
    assert o.queue==1 and o.leaves==2
    e.on_trade("bybit","BTC","Sell",D("1.5"),D(99),1.2,3*10**9)
    assert o.queue==0 and o.leaves==D("1.5")
    e.on_trade("bybit","BTC","Sell",D(10),D(99),1.3,4*10**9)
    assert o.done and o.status=="FILLED"


def test_post_only_cross_after_delay_is_rejected():
    e,b,q=setup();o=open_order(e)
    q(1,b=97,a=98)
    assert o.done and o.status=="POST_ONLY_REJECTED" and o.leaves==1


def test_cancel_retains_exposure_and_can_fill_until_effective():
    e,b,q=setup();o=open_order(e);q(1)
    e.cancel_intent(o,1.1)
    assert not o.done and o.cancel_at==1.6
    e.on_trade("bybit","BTC","Sell",D(6),D(99),1.4,3*10**9)
    assert o.status=="FILLED"


def test_cancel_before_activation_does_not_release_before_arrival():
    e,b,q=setup();o=open_order(e);e.cancel_intent(o,.1)
    e.advance(.9);assert not o.done
    e.advance(1);assert o.status=="CANCELLED" and not o.active


def test_older_or_unknown_aggressor_is_not_execution_evidence():
    e,b,q=setup();o=open_order(e);q(1,source=10*10**9)
    e.on_trade("bybit","BTC","Sell",D(100),D(99),1.1,9*10**9)
    e.on_trade("bybit","BTC","Unknown",D(100),D(98),1.2,11*10**9)
    assert o.leaves==1 and o.queue==5


def test_outside_visible_depth_does_not_mean_empty_queue():
    e,b,q=setup();o=open_order(e,price=95);q(1)
    assert o.queue is None
    e.on_trade("bybit","BTC","Sell",D(100),D(94),1.1,2*10**9)
    assert o.leaves==1


def test_ioc_uses_arrival_depth_and_protective_limit():
    e,b,q=setup(emergency_taker=True);o=open_order(e,price=101,qty=4,ioc=True);q(1)
    assert o.done and o.status=="IOC_PARTIAL" and o.leaves==2
    second=open_order(e,price=101,qty=1,t=1,ioc=True);q(2)
    assert second.status=="IOC_NO_FILL"  # Do not reuse the same displayed volume.


def test_trade_through_caps_fill_by_actual_volume():
    e,b,q=setup();o=open_order(e,qty=2);q(1)
    e.on_trade("bybit","BTC","Sell",D(".4"),D(98),1.1,2*10**9)
    assert o.leaves==D("1.6")


def test_fees_and_paper_ledger_are_explicit(tmp_path):
    j=Journal(tmp_path);e,b,q=setup(j);o=open_order(e);q(1)
    e.on_trade("bybit","BTC","Sell",D(6),D(99),1.1,2*10**9)
    r=dict(j.db.execute("select * from pilot_fills").fetchone())
    assert D(r["fee"])==D(".0198") and r["maker"]==1
    assert j.db.execute("select count(*) from orders").fetchone()[0]==0
    j.close()


def test_paper_order_never_subtracted_from_real_book(tmp_path):
    j=Journal(tmp_path);m=PilotMarkets(j)
    q=Quote(D(99),D(101),D(5),D(2),0,0)
    assert m.external("bybit","BTC",q)==q and len(m.models)==16
    j.close()


def test_sequence_gap_stops_using_delta_book():
    b=Book();b.update([[99,1]],[[101,1]],5,True)
    with pytest.raises(RuntimeError,match="sequence gap"):b.update([],[],7,False,previous=5,contiguous=True)


def test_repeated_full_snapshots_preserve_aggressor_activity(tmp_path):
    import time
    j=Journal(tmp_path);m=PilotMarkets(j,strategy_mode="legacy_fv");f=PublicFeeds(m.book,m.trade,m.on_gap,m.on_reference)
    m.models['bitget','SOL'].coefficient=D(1)
    f.book('bitget','SOL',[[99,5]],[[101,5]],1,True,0,'{}')
    f.trade('bitget','SOL','Buy',10,101,'trade-1',0,'{}')
    f.book('bitget','SOL',[[99,7]],[[101,5]],2,True,0,'{}')
    q,s=m.signal('bitget','SOL',time.monotonic())
    assert s.fv is not None and s.x>0
    j.close()
