import pytest
from scripts.analyze_fv_pilot import metrics,latency_move,longer_block_check


def test_paper_partial_fills_and_fees_have_no_real_order_count():
    orders=[{'id':'a','role':'OPEN','status':'FILLED','live':1,'cancel_request':None},{'id':'b','role':'FORCED_REDUCE','status':'FILLED','live':None,'cancel_request':None}]
    fills=[{'id':1,'order_id':'a','t':1,'side':'Buy','qty':'.4','price':'100','fee':'.1','maker':1,'basis':'queue'},{'id':2,'order_id':'a','t':2,'side':'Buy','qty':'.6','price':'100','fee':'.1','maker':1,'basis':'queue'},{'id':3,'order_id':'b','t':4,'side':'Sell','qty':'1','price':'101','fee':'.1','maker':0,'basis':'sweep'}]
    states=[{'mid':'100','pnl_ex_funding':'-.2'},{'mid':'101','pnl_ex_funding':'.7'}]
    r=metrics(orders,fills,states,'USDT')
    assert r['pnl_ex_funding']==pytest.approx(.7) and r['full_round_trips']==1
    assert r['hypothetical_fills']==3 and r['hypothetical_filled_orders']==2
    assert r['external_order_submissions']==0 and r['normal_close_rate']==0
    assert r['funding_pnl'] is None  # Current funding rates are not settled payments.


def test_unclosed_inventory_marked_and_not_faked_as_a_roundtrip():
    orders=[{'id':'a','role':'OPEN','status':'PARTIAL','live':1,'cancel_request':None}]
    fills=[{'id':1,'order_id':'a','t':1,'side':'Buy','qty':'1','price':'100','fee':'.1','maker':1,'basis':'queue'}]
    r=metrics(orders,fills,[{'mid':'102','pnl_ex_funding':'1.9'}],'USD')
    assert r['currency']=='USD' and r['end_inventory']==1
    assert r['pnl_ex_funding']==pytest.approx(1.9) and r['full_round_trips']==0


def test_latency_remaining_move_uses_delayed_price_and_future_horizon():
    rows=[{'t':i,'mid':100+i*.1,'fv':101+i*.1} for i in range(61)]
    r=latency_move(rows,10)
    assert r['n']==31 and r['assumed_order_delay_seconds']==10
    assert 193<r['signed_remaining_move_mean_bps']<200


def test_longer_dependence_check_uses_120s_blocks_and_frozen_forecast():
    rows=[{'t':i,'mid':100+i*.01,'x':.1,'reason':'ok'} for i in range(1801)]
    r=longer_block_check(rows,3.)
    assert r['n']==1771 and r['time_blocks']==15
    assert r['slope_ci95']==pytest.approx([3,3])
    assert r['mse_improvement_ci95'][0]>0
