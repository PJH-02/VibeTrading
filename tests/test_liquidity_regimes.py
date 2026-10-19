import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from extract_pilot_liquidity_notional import stock_integral
from analyze_liquidity_regimes import ratio,realized_rv,onsets,contrast,signals

def test_persistent_quote_stock_integrates_duration_not_message_count():
    grid=np.array([1.,2.])
    sparse=stock_integral(np.array([0.]),np.array([100.]),grid)
    dense=stock_integral(np.array([0.,.1,.4,.7,1.3]),np.full(5,100.),grid)
    assert np.allclose(sparse,[100,100]) and np.allclose(sparse,dense)

def test_supply_integral_exact_price_quantity_state_changes():
    actual=stock_integral(np.array([0.,.2,.7,1.3]),np.array([100.,200.,100.,100.]),np.array([1.,2.]))
    assert np.allclose(actual,[150,100])

def test_no_trade_ratio_is_missing_instead_of_infinite_high_liquidity():
    d={'avg_depth':np.array([50.,50.]),'trade_buy':np.array([0.,10.]),'trade_sell':np.zeros(2),'trade_unknown':np.zeros(2)}
    r,q=ratio(d,1,'quantity_buffer_seconds')
    assert np.isnan(r[0]) and r[1]==10

def test_future_realized_volatility_does_not_include_prior_jump():
    d={'mid':np.array([100.,110.,121.,121.])}
    rv=realized_rv(d,np.array([1]),np.array([2]))
    assert np.allclose(rv,1000)

def test_event_onset_counts_crossings_and_both_sides_share_cooldown():
    x=np.array([0.,2.,2.,0.,2.,-2.,-2.,0.,2.])
    assert onsets(x,np.arange(1,9),-1,1,3)==[(1,1),(4,1),(8,1)]
    assert onsets(x,np.arange(1,9),-1,1,4)==[(1,1),(5,-1)]

def test_conditional_contrast_bootstrap_preserves_observed_group_mean():
    t=np.arange(2400.);hi=(t%2)==0;lo=~hi;y=hi.astype(float)*2
    r=contrast(t,y,hi,lo,60)
    assert r['mean_high_minus_low']==2 and r['ci_low']==2 and r['ci_high']==2

def test_weak_refill_uses_consumed_side_and_preserves_trade_direction():
    from fv_research_extract import STATE_NAMES,FLOW_NAMES
    d={**{'avg_'+k:np.zeros(3) for k in STATE_NAMES},**{k:np.zeros(3) for k in FLOW_NAMES}}
    d['avg_depth']=np.full(3,100.)
    d['trade_buy']=np.array([10.,10.,0.]);d['trade_sell']=np.array([0.,0.,10.])
    d['ask_add']=np.array([0.,1.,0.]);d['ask_remove']=np.array([0.,5.,0.])
    d['bid_add']=np.array([0.,0.,3.]);d['bid_remove']=np.array([0.,0.,3.])
    value=signals(d,1)['aggressive_weak_refill']
    assert np.isnan(value[0])
    assert np.isclose(value[1],np.arcsinh(.1)*5/6)
    assert np.isclose(value[2],-np.arcsinh(.1)/2)
