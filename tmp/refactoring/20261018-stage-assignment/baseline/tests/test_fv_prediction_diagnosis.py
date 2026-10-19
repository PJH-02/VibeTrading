import numpy as np
import pytest

from scripts.diagnose_fv_prediction import future_mid, feature_matrix, fit_ridge, predict, loss_metrics, legacy_bitget_quotes


def book():
    return {'t':np.array([0.,1.,2.,7.,8.]),'bid':np.array([99.,100.,101.,105.,106.]),
            'ask':np.array([101.,102.,103.,107.,108.]),'bid_qty':np.array([5.,5.,5.,10.,20.]),
            'ask_qty':np.array([5.,5.,5.,5.,5.]),'reset':np.zeros(5)}


def test_future_label_uses_asof_not_next_update():
    y,valid=future_mid(book(),np.array([.2]),1.)
    assert valid.tolist()==[True] and y.tolist()==[101.]


def test_future_label_requires_bounds_and_fresh_evidence():
    y,valid=future_mid(book(),np.array([4.,7.9]),1.)
    assert valid.tolist()==[True,False]  # age=3s allowed; beyond the evidence end forbidden
    _,valid=future_mid(book(),np.array([4.1]),1.)
    assert not valid[0]


def test_features_do_not_use_future_quote_or_future_trade():
    q=book();tr=[{'t':7.5,'qty':'3','side':'Buy'},{'t':8.,'qty':'1000','side':'Sell'}]
    x,mid,valid=feature_matrix(q,tr,np.array([7.5]))
    altered=book();altered['bid_qty'][-1]=99999
    xx,mm,vv=feature_matrix(altered,tr[:-1],np.array([7.5]))
    assert np.allclose(x,xx) and np.allclose(mid,mm) and valid[0] and vv[0]
    assert x[0,1]==pytest.approx(3/7.5)


def test_snapshot_in_feature_window_withholds_flow_features():
    q=book();q['reset'][3]=1
    _,_,valid=feature_matrix(q,[],np.array([7.5]))
    assert not valid[0]


def test_frozen_ridge_fit_uses_training_location_and_scale():
    x=np.arange(10.).reshape(-1,1);y=3*x[:,0]+2
    m=fit_ridge(x,y,penalty=0)
    assert predict(m,np.array([[20.]]))[0]==pytest.approx(62)
    assert m['mean'][0]==pytest.approx(4.5)


def test_gain_requires_enough_time_blocks_for_inference():
    t=np.arange(200.);y=np.ones(200)
    r=loss_metrics(t,y,y*.5,120)
    assert r['rmse_reduction_percent']==pytest.approx(50)
    assert r['mse_reduction_fraction']==pytest.approx(.75)
    assert r['mse_gain_ci95'] is None and r['p_loss_gain_block_centered'] is None


def test_legacy_bitget_flags_repair_continuous_full_snapshots_without_mutation():
    original=[{'seq':10,'reset':1},{'seq':12,'reset':1},{'seq':1,'reset':1}]
    repaired=legacy_bitget_quotes(original)
    assert [r['reset'] for r in repaired]==[1,0,1]
    assert [r['reset'] for r in original]==[1,1,1]
