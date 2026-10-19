from decimal import Decimal as D
from dataclasses import replace
from types import SimpleNamespace
import pytest

from vibetrading.fv_mm.types import Quote,Intent,Signal
from vibetrading.fv_pilot.public import Book,Instrument
from vibetrading.fv_pilot.simulation import PaperMatching
from vibetrading.mm_state.features import Microstructure
from vibetrading.mm_state.lifecycle import LifecyclePolicy
from vibetrading.mm_state.policy import state_quote_intents,risk_state
from vibetrading.mm_state.calibration import purged_walk_forward
from vibetrading.mm_state.analytics import Markouts
from strategy.s2_external_anchor import Strategy as S2
from strategy.s3_aggressive_flow import Strategy as S3


def quote(t,b=100,a=102,qb=10,qa=10,source=0):
    return Quote(D(str(b)),D(str(a)),D(str(qb)),D(str(qa)),t,source)


def matching(**kwargs):
    i=Instrument("BTCUSDT","USDT",D(1),D(".1"),D(".1"),D(1),D(".0002"),D(".00055"))
    e=PaperMatching({"bybit":{"BTC":i}},{"bybit":{"entry_seconds":.1,"cancel_seconds":.2}},**kwargs)
    b=Book();b.update([[100,10],[99,5]],[[102,10],[103,5]],1,True)
    e.on_book("bybit","BTC",quote(0),b)
    return e,b


def submit(e,t=0,p=100,qty=1,side="Buy",reduce=False):
    return e.submit_intent("bybit","S1","BTC",Intent(side,D(p),D(qty),"REDUCE" if reduce else "OPEN",reduce),t)


def test_stock_integral_invariant_to_identical_message_repeats_and_trade_notional():
    outputs=[]
    for repeats in (1,100):
        f=Microstructure(window=1,tick=1)
        for j in range(repeats):f.book(quote(j/repeats))
        f.trade("Buy",D(2),1,D(101))
        outputs.append(f.snapshot(1))
    assert outputs[0].liquidity_seconds==pytest.approx(10)
    assert outputs[0].liquidity_seconds==pytest.approx(outputs[1].liquidity_seconds)
    assert outputs[0].ofi==outputs[1].ofi==0


def test_no_trade_is_missing_ratio_and_backward_time_clears_history():
    f=Microstructure(window=1);f.book(quote(0));f.book(quote(1))
    assert f.snapshot(1).ready and f.snapshot(1).liquidity_seconds is None
    f.book(quote(.5));assert not f.snapshot(.5).ready


def test_book_gap_requires_new_warmup_and_no_replenishment_invention():
    f=Microstructure(window=1,tick=1);f.book(quote(0));f.book(quote(1))
    assert f.snapshot(1).ready
    f.book(quote(5,qa=100))
    assert not f.snapshot(5).ready
    assert f.snapshot(5).ask_refill==0


def test_l2_net_proxy_does_not_call_new_price_level_a_gross_refill():
    f=Microstructure(window=1,tick=1);b=Book()
    b.update([[100,10],[99,10]],[[102,10],[103,10]],1,True)
    f.book(quote(0),book=b)
    b.update([],[[102,0],[104,100]],2)
    f.book(quote(1,a=103),book=b)
    x=f.snapshot(1)
    assert x.ask_refill==0 and x.ask_gap_ticks==1
    assert x.l2_obi is not None


def test_past_arrival_processed_before_later_book_and_cancel_even_when_timer_skips():
    e,b=matching();o=submit(e);e.cancel_intent(o,.05)
    e.advance(1)
    assert e.counts['bybit:S1:active']==1
    assert o.status=='CANCELLED' and o.active_ts==pytest.approx(.1)
    assert o.cancel_at==pytest.approx(.25)


def test_inside_spread_maker_has_zero_queue_and_can_partial_fill():
    e,b=matching();o=submit(e,p=101,qty=2);e.advance(.1)
    assert o.active and o.queue==0
    e.on_trade('bybit','BTC','Sell',D('.4'),D(101),.2,0)
    assert o.leaves==D('1.6') and o.status=='PARTIAL'


def test_public_volume_never_reused_for_two_virtual_orders():
    e,b=matching();one=submit(e,qty=2);two=submit(e,p=101,qty=2)
    e.advance(.1)
    e.on_trade('bybit','BTC','Sell',D('.5'),D(99),.2,0)
    assert (one.intent.quantity-one.leaves)+(two.intent.quantity-two.leaves)==D('.5')


def test_cancel_pending_remains_fillable_until_effective_cancel():
    e,b=matching();o=submit(e,p=101);e.advance(.1);e.cancel_intent(o,.15)
    e.on_trade('bybit','BTC','Sell',D(1),D(101),.2,0)
    assert o.status=='FILLED' and o.cancel_at==pytest.approx(.35)


def test_reduce_only_cannot_reverse_position_on_racing_fills():
    position={'qty':D('.4')}
    def fill(o,qty,*args):position['qty']-=qty
    e,b=matching(position_callback=lambda o:position['qty'],fill_callback=fill)
    o=submit(e,p=101,qty=1,side='Sell',reduce=True);e.advance(.1)
    e.on_trade('bybit','BTC','Buy',D(20),D(102),.2,0)
    assert position['qty']==0 and o.leaves==D('.6')
    e.on_trade('bybit','BTC','Buy',D(20),D(102),.3,0)
    assert position['qty']==0 and o.leaves==D('.6')


def test_default_emergency_taker_disabled_and_foreign_latency_rejected():
    e,b=matching()
    with pytest.raises(ValueError,match='disabled'):e.submit_intent('bybit','S1','BTC',Intent('Buy',D(102),D(1),'OPEN',False),0,True)
    with pytest.raises(ValueError,match='substitution'):
        PaperMatching({}, {'arcus':{'entry_seconds':1,'cancel_seconds':1,'proxy_venue':'grvt'}})


def test_minor_resize_and_reprice_retains_queue_until_material_change():
    e,b=matching();o=submit(e);e.advance(.1)
    policy=LifecyclePolicy()
    tiny=replace(o.intent,quantity=D('1.000001'))
    assert policy.reason(o,tiny,3,D(1)) is None
    assert policy.reason(o,replace(tiny,price=D(99)),3,D(1)) is None
    assert policy.reason(o,replace(tiny,price=D(98)),3,D(1))=='PRICE_STALE'
    assert policy.reason(o,tiny,.2,D(1),hard_risk=True)=='RISK_CANCEL'


def state(direction=1,inventory=0,jump=False):
    f={'ofi':2 if jump else 0,'obi':.9 if jump else .6,'liquidity_seconds':.1 if jump else 10,
       'rv_bps':1,'spread_bps':2,'ask_gap_ticks':4 if jump else 1}
    return Signal('S4',D(direction),None,None,5,'ok',direction=direction,strength=D(1),state='BUY',ready=True,metadata={'features':f})


@pytest.mark.parametrize('inventory',[D(-2),D(0),D(2)])
@pytest.mark.parametrize('direction',[-1,0,1])
def test_policy_prices_nonmarketable_and_reductions_cannot_add_inventory(inventory,direction):
    q=quote(0,b=100,a=104)
    intents=state_quote_intents(state(direction),q,cost=D('.1'),tick=D(1),lot=D('.1'),unit=D(1),inventory=inventory,max_inventory=D(3))
    for i in intents:
        assert i.price<q.ask if i.side=='Buy' else i.price>q.bid
        assert i.price%D(1)==0 and i.quantity%D('.1')==0
        if i.reduce_only:assert i.side==('Sell' if inventory>0 else 'Buy')


def test_jump_risk_stops_new_exposure_but_keeps_maker_inventory_unwind():
    s=state(jump=True);q=quote(0)
    assert risk_state(s)[0]=='JUMP_RISK'
    x=state_quote_intents(s,q,cost=D('.1'),tick=D(1),lot=D('.1'),unit=D(1),inventory=D(2),max_inventory=D(3))
    assert len(x)==1 and x[0].side=='Sell' and x[0].reduce_only and x[0].price==q.ask


def test_s2_requires_origin_and_offline_stationarity_then_buys_local_catchup():
    c={'baseline_bp':0,'stationarity':{'adf_p':.01,'kpss_p':.1,'samples':300},'maker_roundtrip_bp':0,'uncertainty_bp':0,'residual_abs_q90':1}
    m=S2(mode='state',calibration=c,window_seconds=.5,tick=.01)
    m.on_book(quote(0,b=99.99,a=100.01));m.signal(quote(0,b=99.99,a=100.01),0,quote(0,b=99.99,a=100.01))
    q=quote(1.2,b=99.99,a=100.01);m.on_book(q)
    ref=quote(1.2,b=100.19,a=100.21)
    s=m.signal(q,1.2,ref)
    assert s.state=='BINANCE_LEADS' and s.direction==1 and s.fv is None
    m.calibration={**c,'stationarity':{}}
    assert m.signal(q,1.2,ref).direction==0


def test_s2_spread_convergence_without_local_profit_does_not_take_profit():
    m=S2(mode='state');s=state(-1);s=replace(s,metadata={**s.metadata,'residual_bp':0})
    q=quote(0,b=101.99,a=102.01)
    kwargs=dict(cost=D('.01'),tick=D('.01'),lot=D('.1'),unit=D(1),inventory=D(-1),max_inventory=D(1),new_exposure=False,
                position_context={'entry_price':101,'entry_residual_bp':100,'initial_quantity':1})
    assert not m.order_intents(s,q,**kwargs)
    forced=m.order_intents(s,q,**kwargs,force_reduce=True)
    assert forced[0].reduce_only and forced[0].side=='Buy' and forced[0].price<q.ask


def test_s3_requires_absorption_before_breakout_not_trade_sign_alone():
    m=S3(mode='state',window_seconds=.5,tick=1)
    for t in (0,.5):m.on_book(quote(t))
    m.on_trade('Buy',D(20),.5,D(102))
    assert m.signal(quote(.5),.5).direction==0 and m.phase=='PRESSURE'
    m.on_trade('Buy',D(20),.6,D(102));m.on_book(quote(.7,qa=30))
    assert m.signal(quote(.7,qa=30),.7).state=='ABSORPTION'
    m.on_trade('Buy',D(100),.8,D(102));m.on_book(quote(.9,qa=5));m.on_book(quote(1,a=103,qa=5))
    assert m.signal(quote(1,a=103,qa=5),1).direction==1


def test_markout_uses_future_quote_and_censors_gap_and_missing_tail():
    e,b=matching();o=submit(e,p=101)
    r=Markouts(horizons=(1,2));r.add(o,D(1),D(101),D('.01'),0)
    r.observe('bybit','BTC',quote(.5,b=109,a=111));assert not r.rows
    r.observe('bybit','BTC',quote(1,b=102,a=104));assert r.rows[0][9]==pytest.approx((103/101-1)*1e4)
    r.gap('bybit');r.observe('bybit','BTC',quote(2,b=104,a=106));assert r.rows[-1][9] is None
    r.add(o,D(1),D(101),D(0),2);r.finish();assert r.rows[-1][-1]=='TAIL_CENSORED'


def test_walkforward_fit_labels_purged_and_embargo_never_shuffled():
    times=list(range(1000))
    folds=list(purged_walk_forward(times,300,100,horizon=30,embargo=5))
    assert folds
    for train,test in folds:
        assert max(times[i]+30 for i in train)<min(times[i] for i in test)-5
        assert train==sorted(train) and test==sorted(test) and not set(train)&set(test)


def test_markout_isolates_routes_and_bounds_pending_without_future_scan():
    e,b=matching();o=submit(e,p=101);r=Markouts(horizons=(1,30))
    r.max_pending=4
    for j in range(4):r.add(o,D(1),D(101),D(0),j)
    assert len(r.pending)==4 and any(x[-1]=='CAPACITY_CENSORED' for x in r.rows)
    before=len(r.pending);r.observe('bybit','ETH',quote(40))
    assert len(r.pending)==before
    r.observe('bybit','BTC',quote(40));assert not r.pending


def test_static_ablation_can_reduce_inventory_without_new_exposure():
    from vibetrading.mm_state.coordinator import Coordinator
    m=Coordinator(profile='static',calibration={'risk':{}})
    orders=m.order_intents(state(0),quote(1),unit=D(1),lot=D('.1'),inventory=D(2),max_inventory=D(3),new_exposure=False)
    assert len(orders)==1 and orders[0].reduce_only and orders[0].quantity==2 and orders[0].side=='Sell'


def test_s2_tp1_half_quantity_requires_actual_local_profit_and_no_repeat():
    m=S2(mode='state');s=replace(state(1),metadata={**state(1).metadata,'residual_bp':5})
    risk=dict(cost=D('.01'),tick=D('.01'),lot=D('.1'),unit=D(1),inventory=D(2),max_inventory=D(2),new_exposure=False,
              position_context={'entry_price':100,'entry_residual_bp':20,'initial_quantity':2})
    orders=m.order_intents(s,quote(0,b=100.99,a=101.01),**risk)
    assert len(orders)==1 and orders[0].role=='S2_TP1_MAKER' and orders[0].quantity==1
    assert not m.order_intents(s,quote(0,b=100.99,a=101.01),**{**risk,'inventory':D(1)})


def test_s2_baseline_drift_blocks_even_valid_old_stationarity():
    m=S2(mode='state',calibration={'baseline_bp':0,'mad_bp':.1,'stationarity':{'adf_p':.01,'kpss_p':.1,'samples':300}})
    m.cached_baseline=10
    q=quote(0,b=99.99,a=100.01);m.on_book(q)
    s=m.signal(q,0,q)
    assert s.direction==0 and s.reason=='CROSS_BASELINE_DRIFT'


def test_s3_sell_side_absorption_then_bid_supply_exhaustion():
    m=S3(mode='state',window_seconds=.5,tick=1)
    for t in (0,.5):m.on_book(quote(t))
    m.on_trade('Sell',D(20),.5,D(100));m.signal(quote(.5),.5)
    m.on_trade('Sell',D(20),.6,D(100));m.on_book(quote(.7,qb=30))
    assert m.signal(quote(.7,qb=30),.7).state=='ABSORPTION'
    m.on_trade('Sell',D(100),.8,D(100));m.on_book(quote(.9,qb=5));m.on_book(quote(1,b=99,qb=5))
    assert m.signal(quote(1,b=99,qb=5),1).direction==-1


def test_proportional_cancel_credit_never_double_counts_known_trades():
    e,b=matching(queue_model='proportional_cancel');o=submit(e);e.advance(.1)
    e.on_trade('bybit','BTC','Sell',D(2),D(100),.2,0)
    assert o.queue==8
    b.update([[100,8]],[],2)
    e.on_book('bybit','BTC',quote(.3,qb=8),b)
    assert o.queue==8
    b.update([[100,4]],[],3)
    e.on_book('bybit','BTC',quote(.4,qb=4),b)
    assert o.queue==4


def test_operational_circuits_latch_and_count_only_observed_markouts():
    from vibetrading.mm_state.risk import RiskGuard,validate_latency_profile
    g=RiskGuard(minimum_orders=2,minimum_markouts=2)
    for t in (0,1):g.event('bybit','SEND',t);g.event('bybit','REJECT',t)
    assert g.halted['bybit']=='REJECTION_RATE_LIMIT'
    g.acknowledgement('grvt',0,6);assert g.halted['grvt']=='ACK_LATENCY_LIMIT'
    row=('id','bitget','BTC',0,1,1,'1','100','99',-10,2,'OBSERVED')
    g.markout(row);g.markout(row);assert g.halted['bitget']=='ADVERSE_MAKER_MARKOUT'
    with pytest.raises(ValueError,match='provenance'):
        validate_latency_profile({'entry_seconds':.1,'cancel_seconds':.1},'arcus')
    with pytest.raises(ValueError,match='substitution'):
        validate_latency_profile({'entry_seconds':.1,'cancel_seconds':.1,'source_venue':'grvt'},'arcus')


def test_nonfinite_book_and_trade_fail_closed():
    assert not quote(0,qb='NaN').valid()
    f=Microstructure(window=1);f.book(quote(0));f.trade('Buy',D('NaN'),.5,D(100))
    assert f.snapshot(.5).buy==0


def test_unbounded_depth_is_rejected_instead_of_silently_truncated():
    b=Book()
    with pytest.raises(RuntimeError,match='capacity'):
        b.update([[10000-j,1] for j in range(4097)],[[10001,1]],1,True)


def test_stationarity_never_concatenates_short_disjoint_training_blocks():
    from vibetrading.mm_state.calibration import fit_states
    f={'liquidity_seconds':1,'rv_bps':1,'spread_bps':1,'ofi':.5,'obi':.5,'trade_imbalance':.6,'buy':1,'sell':1,'unknown':0}
    rows=[{'t':(j//100)*200+j%100,'ready':True,'mid':100+j*.001,'ref_mid':100,'metadata':{'features':f}} for j in range(300)]
    r=fit_states(rows,'S2')
    assert not r['valid'] and r['reason']=='INSUFFICIENT_CONTIGUOUS_MATCHED_REFERENCE'


def test_live_source_staleness_is_not_hidden_by_fresh_receipt_timestamp():
    from vibetrading.mm_state.clock import require_source_fresh
    require_source_fresh(9_900_000_000,wall_ns=10_000_000_000)
    with pytest.raises(RuntimeError,match='Stale'):
        require_source_fresh(6_000_000_000,wall_ns=10_000_000_000)
    with pytest.raises(RuntimeError,match='clock'):
        require_source_fresh(12_000_000_000,wall_ns=10_000_000_000)


def test_testnet_historical_trade_snapshot_cannot_inflate_new_flow(tmp_path):
    from vibetrading.fv_mm.journal import Journal
    from vibetrading.fv_mm.market import Markets
    j=Journal(tmp_path);m=Markets(j)
    m.on_book('grvt','SOL',quote(0,source=10_000_000_000),False)
    m.on_trade('grvt','SOL','Buy',D(1000),.1,1_000_000_000,D(102))
    assert m.models['grvt','SOL'].engine.snapshot(.1).buy==0
    assert j.db.execute("SELECT count(*) FROM events WHERE kind='historical_trade_skipped'").fetchone()[0]==1
    j.close()
