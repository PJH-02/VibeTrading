import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numpy as np
from fv_research_extract import Series,PriceBook,bbo_features,ofi_value,mask_transport
from evaluate_fv_indicators import clean_anchors,features,fit_features,rolling

def test_joint_model_requires_observed_variation_in_both_inputs(monkeypatch):
    import evaluate_fv_indicators as research
    from fv_research_extract import STATE_NAMES,FLOW_NAMES
    n=600;t=np.arange(n,dtype=float);wave=np.sin(t/17)
    d={'t':t,'mid':100+np.sin(t/40),
       **{'avg_'+name:np.zeros(n) for name in STATE_NAMES},
       **{name:np.zeros(n) for name in FLOW_NAMES}}
    d['avg_depth']=np.full(n,100.);d['ofi']=wave*20
    mask=(t>=60)&(t<=n-181)
    monkeypatch.setattr(research,'WINDOWS',(1,))
    monkeypatch.setattr(research,'HORIZONS',np.array([1]))
    monkeypatch.setattr(research,'SELECT_H',())
    rows,_,missing=research.analyze_pair('test','venue','BTC',d,d,mask,mask,1)
    assert any(r['indicator']=='ofi' for r in rows)
    assert not any(r['indicator']=='ofi_trade_joint' for r in rows)
    assert any(r.get('indicator')=='ofi_trade_joint' for r in missing)

def book(t,seq=1,snapshot=True,venue='bybit',previous=None):
    return dict(receive_ts_ns=int(t*1e9),connection_id='one',sync_generation=1,
                final_update_id=seq,previous_update_id=previous,raw_payload='{}',
                update_type='snapshot' if snapshot else 'delta',
                bids=[{'price':100-i,'quantity':3} for i in range(10)],
                asks=[{'price':101+i,'quantity':1} for i in range(10)])

def test_microprice_cross_weights_and_l2():
    f=bbo_features({100-i:3 for i in range(10)},{101+i:1 for i in range(10)})
    assert f[3]==.5 and f[4]==.5
    assert np.isclose(f[6],(.25/100.5)*1e4)

def test_bbo_is_not_faked_as_l2():
    assert bbo_features({100:3},{101:1}) is None

def test_price_aware_ofi_has_correct_direction():
    assert ofi_value((100,101,3,1),(100,101,5,1))==2
    assert ofi_value((100,101,3,1),(100,101,3,.5))==.5
    assert ofi_value((100,101,3,1),(101,102,7,2))==8

def test_quiet_asset_is_valid_without_fake_trade():
    out=[];s=Series('bybit','BTC',out.append);s.book(book(0));s.advance(6)
    assert out[-1]['valid_seconds']==1 and out[-1]['trade_buy']==0

def test_transport_gaps_are_independent_of_price_changes():
    rows=[{'venue':'bybit','t':x,'valid_seconds':1.,'bad':0} for x in (1,4,5,6)]
    mask_transport(rows,{'bybit':[(3.5,5.5)]})
    assert [r['bad'] for r in rows]==[0,1,1,1]

def test_arcus_sequence_gap_invalidates_book():
    out=[];s=Series('arcus','BTC',out.append);s.book(book(0))
    s.book(book(.1,10,False));s.book(book(.2,12,False));s.advance(1)
    assert s.counts['invalid_delta']==1 and out[0]['bad']>0

def test_continuous_bitget_snapshots_do_not_reset_flow():
    out=[];s=Series('bitget','BTC',out.append);s.book(book(0))
    r=book(.2,2);r['bids'][0]['quantity']=5;s.book(r);s.advance(1)
    assert s.counts['resets']==1 and out[0]['ofi']==2

def test_snapshot_is_not_liquidity_replenishment():
    out=[];s=Series('bybit','BTC',out.append);s.book(book(0));s.advance(1)
    assert out[0]['bid_add']==0 and out[0]['ask_add']==0

def test_audited_nonadvancing_frame_does_not_destroy_synced_book():
    out=[];s=Series('binance','BTC',out.append);s.book(book(0,100))
    r=book(.1,100,False);r['sync_generation']=0;s.book(r)
    r=book(.2,105,False,previous=100);r['raw_payload']='{"data":{"U":100,"u":105,"pu":99}}';s.book(r)
    s.advance(1)
    assert s.state is not None and s.counts['invalid_delta']==0 and s.counts['stale_nonadvancing_ignored']==1

def test_gap_inside_target_path_is_excluded_not_only_endpoints():
    d={'valid_seconds':np.ones(500),'bad':np.zeros(500),'mid':np.ones(500)}
    d['bad'][250]=1;ok=clean_anchors(d)
    assert not ok[100] and not ok[300] and ok[65]

def test_window_has_no_future_values():
    x=np.array([1.,2.,3.,100.]);assert rolling(x,2)[2]==5

def test_unknown_aggressor_is_not_zero_signed_flow():
    d={k:np.ones(100) for k in ('avg_depth','avg_obi_l1','avg_obi_l2_10','avg_obi_visible','avg_microprice_bps','ofi','bid_add','ask_add','bid_remove','ask_remove','trade_buy','trade_sell','trade_unknown')}
    x=features(d,1);assert np.isnan(x[:,6]).all() and np.isnan(x[:,7]).all()

def test_normalization_and_winsorization_are_train_only():
    x=np.arange(100.)[:,None];y=(x*2)
    m=fit_features(x,y);assert m['high'][0]<99
    before=m['coef'].copy();extreme=np.clip([[1e9]],m['low'],m['high'])
    assert extreme[0,0]==m['high'][0] and np.array_equal(before,m['coef'])

def test_incremental_book_matches_full_depth_after_random_updates():
    rng=np.random.default_rng(42);optimized=PriceBook();plain={}
    for _ in range(1000):
        price=int(rng.integers(1,200));qty=float(rng.integers(0,50))
        if qty:optimized[price]=qty;plain[price]=qty
        else:optimized.pop(price,None);plain.pop(price,None)
        assert optimized.prices==sorted(plain)
        assert np.isclose(optimized.total,sum(plain.values()))
        assert optimized.top(True)=={p:plain[p] for p in sorted(plain,reverse=True)[:10]}

def test_bulk_book_updates_preserve_exact_levels_and_deletions():
    b=PriceBook();b.apply([{'price':i,'quantity':2.5} for i in range(200)])
    b.apply([{'price':i,'quantity':0 if i%2 else 4.5} for i in range(200)])
    assert dict(b)=={i:4.5 for i in range(0,200,2)}
    assert b.prices==list(range(0,200,2)) and b.total==450

def test_dense_seconds_reject_duplicate_source_rows():
    import pandas as pd,pytest
    from evaluate_fv_indicators import dense_frame
    with pytest.raises(ValueError,match='Duplicated'):
        dense_frame(pd.DataFrame([{'venue':'bybit','asset':'BTC','t':1,'mid':100}]*2))

def test_fast_bootstrap_matches_original_with_time_gaps():
    from evaluate_fv_indicators import block_inference
    from diagnose_fv_prediction import loss_metrics
    rng=np.random.default_rng(2);t=np.r_[np.arange(1000),np.arange(3000,4000)].astype(float)
    y=rng.normal(size=len(t));p=y*.1+rng.normal(scale=.1,size=len(t))
    fast=block_inference(t,y,p,30);original=loss_metrics(t,y,p,30)
    assert fast['time_blocks']==original['time_blocks']
    assert np.allclose(fast['mse_gain_ci95'],original['mse_gain_ci95'],atol=1e-12)
    assert fast['p_loss_gain_block_centered']==original['p_loss_gain_block_centered']
