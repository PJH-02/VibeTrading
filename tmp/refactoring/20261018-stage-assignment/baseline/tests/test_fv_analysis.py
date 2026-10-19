import math
import pytest
from scripts.analyze_fv_run import diagnostics,funding_for,trade_metrics


def test_venue_funding_signs_are_opposite():
    assert funding_for("bybit","BTC",{"funding":[{"symbol":"BTCUSDT","funding":"2"}]})==2
    assert funding_for("grvt","BTC",{"funding":[{"instrument":"BTC_USDT_Perp","amount":"2"}]})==-2
    assert funding_for("grvt","BTC",{"funding":[{"instrument":"BTC_USDT_Perp","amount":"-2"}]})==2
    assert funding_for("grvt","BTC",{"funding":{"unavailable":"timeout"}}) is None


def test_actual_partial_fills_fees_and_complete_roundtrip():
    orders=[dict(client_id="a",role="OPEN",status="Filled",send=0,live=1,receive=0),dict(client_id="b",role="REDUCE",status="Filled",send=3,live=4,receive=3)]
    fills=[dict(client_id="a",side="Buy",qty=".4",price="100",fee=".1",maker=1,source_ns=0,receive=1),dict(client_id="a",side="Buy",qty=".6",price="100",fee=".1",maker=1,source_ns=10**9,receive=2),dict(client_id="b",side="Sell",qty="1",price="101",fee=".1",maker=1,source_ns=3*10**9,receive=4)]
    samples=[dict(t=i,mid=100 if i<4 else 101) for i in range(5)]
    r=trade_metrics(fills,orders,samples,-.05)
    assert r["net_pnl_usdt"]==pytest.approx(.65)
    assert r["full_round_trips"]==1 and r["normal_close_rate"]==1
    assert r["fills"]==3 and r["filled_order_count"]==2
    assert r["end_signed_inventory"]==0


def test_quote_touch_or_live_order_is_not_a_fill():
    orders=[dict(client_id="a",role="OPEN",status="Cancelled",send=0,live=1,receive=0)]
    r=trade_metrics([],orders,[dict(t=1,mid=100),dict(t=2,mid=110)],0)
    assert r["net_pnl_usdt"]==0 and r["fills"]==0
    assert r["normal_close_rate"] is None and r["round_trip_win_rate"] is None


def test_model_diagnostics_preserve_horizon_and_block_dependence():
    rows=[dict(t=i,x=.1,mid=100+i*.01,reason="ok") for i in range(901)]
    r=diagnostics(rows,30,3.)
    assert r["n"]==871 and r["nonoverlapping_pairs"]==30
    assert r["feature_slope_zero_intercept"]==pytest.approx(3.)
    assert r["time_blocks_60s"]==15
    assert r["forecast_rmse"]<1e-12
    assert r["no_change_rmse"]==pytest.approx(.3)


def test_unfitted_model_has_feature_only_diagnostic():
    rows=[dict(t=i,x=.1,mid=100+i*.01,reason="unfitted_coefficient") for i in range(100)]
    r=diagnostics(rows,30,None)
    assert "forecast_rmse" not in r
    assert r["feature_slope_zero_intercept"]==pytest.approx(3.)
    assert r["p_block_bootstrap_centered"] is None
