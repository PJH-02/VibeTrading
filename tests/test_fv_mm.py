from decimal import Decimal as D
import math
import pytest

from strategy.s1_net_liquidity import Strategy as S1
from strategy.s2_external_anchor import Strategy as S2
from strategy.s3_aggressive_flow import Strategy as S3
from strategy.s4_obi import Strategy as S4
from vibetrading.fv_mm.types import Quote,ActivityWindow,Signal,quote_intents,Intent
from vibetrading.fv_mm.feeds import Book
from vibetrading.fv_mm.calibration import calibrate
from vibetrading.fv_mm.venues import Grvt,Instrument


def q(b="99",a="101",qb="10",qa="10",t=0):
    return Quote(D(b),D(a),D(qb),D(qa),t,0)


@pytest.mark.parametrize("before,after,expected,activity",[
    (q(),q(qb="15"),5,5),
    (q(),q(qa="5"),5,5),
    (q(),q(qb="5"),-5,5),
    (q(),q(qa="15"),-5,5),
    (q(),q(b="100",qb="8"),8,8),
    (q(),q(b="98",qb="40"),-10,10),
    (q(),q(a="100",qa="8"),-8,8),
    (q(),q(a="102",qa="40"),10,10),
    (q(),q(qb="15",qa="15"),0,10),
])
def test_price_aware_ofi(before,after,expected,activity):
    s=S1(coefficient=D(1)); s.on_book(before); s.on_book(after)
    event=s.window.events[-1]
    assert event[1:]==(D(expected),D(activity))


def test_fractional_boundary_uses_current_depth():
    w=ActivityWindow(); w.add(1,D(6),D(10)); w.add(2,D(-2),D(4))
    assert w.value(3,D(9))==(D(1),2)
    assert w.value(3,D(15))[0] is None
    assert w.value(200,D(1))[0] is None


def test_ofi_trades_are_never_double_counted_and_gap_clears_history():
    s=S1(coefficient=D(1)); s.on_book(q()); s.on_book(q(qb="30",t=1))
    x=s.signal(q(qb="30",t=1),1).x
    s.on_trade("Buy",D(100),1)
    assert s.signal(q(qb="30",t=1),1).x==x
    s.on_book(q(t=2),reset=True)
    assert s.signal(q(t=2),2).fv is None


def test_aggressive_flow_is_independent_of_book_and_unknown_side():
    s=S3(coefficient=D(2)); s.on_book(q()); s.on_trade("Unknown",D(100),1)
    assert s.signal(q(),1).fv is None
    s.on_trade("Buy",D(12),2)
    assert s.signal(q(),2).fv==D(104)
    s.on_book(q(qb="30",qa="30",t=3))
    assert len(s.window.events)==1


def test_obi_half_coefficient_is_weighted_mid_without_wait():
    quote=q(qb="30",qa="10"); s=S4(coefficient=D("0.5"))
    assert s.signal(quote,0).fv==D("100.5")
    s.on_trade("Sell",D(10000),0)
    assert s.signal(quote,0).fv==D("100.5")


def test_anchor_basis_frozen_reference_age_and_invalid_lambda():
    s=S2(coefficient=D("0.5"),basis=D(0)); ref=q(b="103",a="105",t=1)
    assert s.signal(q(t=1),1,ref).fv==D(102)
    assert s.signal(q(),5,ref).fv is None
    s.coefficient=D("1.01")
    assert s.signal(q(t=1),1,ref).fv is None
    assert s.coefficient==D("1.01")


def test_economic_gate_tick_rounding_and_true_reduce_only():
    quote=q("99.99","100.01",t=1); signal=Signal("S1",D(1),D("100.05"),D(1),0,"ok")
    kwargs=dict(cost=D(".01"),tick=D(".01"),lot=D(".1"),unit=D(1),inventory=D(1),max_inventory=D(3))
    intents=quote_intents(signal,quote,**kwargs)
    assert [(i.side,i.price,i.reduce_only) for i in intents]==[("Sell",D("100.06"),True),("Buy",D("99.99"),False)]
    assert len(quote_intents(signal,quote,**{**kwargs,"cost":D(".1")}))==1
    assert len(quote_intents(signal,quote,**kwargs,new_exposure=False))==1


def test_book_requires_snapshot_and_contiguous_grvt_deltas():
    b=Book(); assert b.update([["99","1"]],[["101","1"]],1) is None
    assert b.update([["99","1"]],[["101","1"]],10,True)==(D(99),D(101),D(1),D(1))
    with pytest.raises(RuntimeError): b.update([],[],12,previous=10,contiguous=True)
    assert b.update([["99","2"]],[],11,previous=10,contiguous=True)[2]==D(2)
    assert b.update([],[],10) is None


def rows_for_calibration(strategy="S1"):
    rows=[]
    for t in range(601):
        # A deterministic fixed slope for the horizon, no event IID inference.
        rows.append(dict(t=t,mid=100+t*.01,x=.1,ref_mid=110+t*.01,strategy=strategy,reason="unfitted_coefficient"))
    return rows


def test_calibration_horizon_purge_and_future_target():
    r=calibrate(rows_for_calibration(),600)
    assert r["valid"] and r["coefficient"]==pytest.approx(3)
    assert r["cal_start"]>=r["fit_end"]+30
    assert r["error_allowance"]==pytest.approx(0,abs=1e-10)
    changed=rows_for_calibration()
    for row in changed:
        if row["t"]>=450: row["mid"]+=100
    r2=calibrate(changed,600)
    assert r2["coefficient"]==pytest.approx(r["coefficient"])


def test_zero_signal_cannot_create_invented_model():
    rows=rows_for_calibration()
    for row in rows: row["x"]=0
    assert not calibrate(rows,600)["valid"]


def test_grvt_xrp_signing_has_exact_integer_size_testnet_chain_no_builder_fee():
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from execution.extended_brokers import GRVT_ORDER_TYPES
    c=object.__new__(Grvt); c.subaccount="123"; c.private_key="0x"+"11"*32
    c.instruments={"XRP":Instrument("XRP_USDT_Perp",D(".0001"),D(".1"),D(".1"),D(5),{"base_decimals":6,"instrument_hash":"0x030f01"})}
    order=c.signed_order("XRP",Intent("Buy",D("1.2345"),D("10.1"),"OPEN",False),str(1<<63),now_ns=1000000000)
    sig=order["signature"]
    msg={"subAccountID":123,"isMarket":False,"timeInForce":1,"postOnly":True,"reduceOnly":False,"legs":[{"assetID":int("030f01",16),"contractSize":10100000,"limitPrice":1234500000,"isBuyingContract":True}],"nonce":sig["nonce"],"expiration":int(sig["expiration"])}
    signature=bytes.fromhex(sig["r"][2:]+sig["s"][2:])+bytes([sig["v"]])
    recovered=Account.recover_message(encode_typed_data({"name":"GRVT Exchange","version":"0","chainId":326},GRVT_ORDER_TYPES,msg),signature=signature)
    assert recovered==Account.from_key(c.private_key).address
    assert order["builder_fee"]=="0" and sig["chain_id"]=="326"
    with pytest.raises(ValueError): c.signed_order("XRP",Intent("Buy",D("1.23456"),D(10),"OPEN",False),str(1<<63))
    with pytest.raises(ValueError): c.signed_order("XRP",Intent("Buy",D("1.2345"),D("10.123456"),"OPEN",False),str(1<<63))


@pytest.mark.asyncio
async def test_cancel_ack_does_not_release_reserved_quantity(tmp_path):
    from vibetrading.fv_mm.runner import Runner,Working
    from vibetrading.fv_mm.journal import Journal
    class Client:
        async def cancel(self,*args): return {"result":{}}
    r=object.__new__(Runner); r.clients={"bybit":Client()}; r.journal=Journal(tmp_path)
    w=Working("own",Intent("Buy",D(99),D(1),"OPEN",False),D(".8"),1,live=True)
    await r.cancel("bybit","BTC",w)
    assert not w.done and w.leaves==D(".8") and w.live
    r.journal.close()


@pytest.mark.asyncio
async def test_transport_loss_keeps_unknown_order_and_halts_new_exposure(tmp_path):
    from vibetrading.fv_mm.runner import Runner
    from vibetrading.fv_mm.journal import Journal
    from types import SimpleNamespace
    class Client:
        async def create(self,*args): raise asyncio.TimeoutError()
    import asyncio
    r=object.__new__(Runner); r.clients={"bybit":Client()}; r.journal=Journal(tmp_path)
    r.last_api={"bybit":0}; r.config={"minimum_api_interval_seconds":0}; r.market=SimpleNamespace(dirty={})
    r.working={}; r.registry={}; r.halted={}; r.args=SimpleNamespace(trade_testnet=True)
    await r.create("bybit","S1","BTC",Intent("Buy",D(99),D(1),"OPEN",False),q())
    w=r.working["bybit","BTC"]
    assert w.status=="UNKNOWN" and not w.done and w.leaves==1
    assert r.halted["bybit"]=="unknown_submission"
    assert r.journal.db.execute("SELECT status FROM orders").fetchone()[0]=="UNKNOWN"
    r.journal.close()


def test_pending_order_is_not_netted_against_opposite_inventory():
    from vibetrading.fv_mm.runner import Runner,Working
    from types import SimpleNamespace
    r=object.__new__(Runner); r.routes={"S1":("module","BTC")}
    r.market=SimpleNamespace(quotes={("bybit","BTC"):q()})
    r.inventory={("bybit","BTC"):D(-1)}
    r.working={("bybit","BTC"):Working("x",Intent("Buy",D(99),D(2),"OPEN",False),D(2),0,status="UNKNOWN")}
    assert r.gross_reserved("bybit")==D(300)


def test_duplicate_fill_is_not_double_booked(tmp_path):
    from vibetrading.fv_mm.runner import Runner,Working
    from vibetrading.fv_mm.journal import Journal
    from types import SimpleNamespace
    r=object.__new__(Runner); r.journal=Journal(tmp_path); r.fill_generation={"bybit":0}
    r.registry={("bybit","ours"):Working("ours",Intent("Buy",D(99),D(1),"OPEN",False),D(1),0)}
    r.inventory={}; r.position_since={}; r.entry_mid={}; r.market=SimpleNamespace(dirty={})
    row=dict(orderLinkId="ours",symbol="BTCUSDT",execId="unique",side="Buy",execQty=".5",execPrice="99",execFee=".01",isMaker=True,execTime=1)
    r.on_fill("bybit",row); r.on_fill("bybit",row)
    assert r.inventory["bybit","BTC"]==D(".5") and r.fill_generation["bybit"]==1
    assert r.journal.db.execute("SELECT count(*) FROM fills").fetchone()[0]==1
    r.journal.close()


@pytest.mark.asyncio
async def test_grvt_cancel_serializes_ttl_as_api_string():
    c=object.__new__(Grvt); calls=[]
    async def rpc(endpoint,payload,mutation=False):
        calls.append((endpoint,payload,mutation));return {"result":{"ack":True}}
    c.rpc=rpc
    await c.cancel("ETH","123456")
    assert calls==[("cancel_order",{"client_order_id":"123456","time_to_live_ms":"5000"},True)]


@pytest.mark.asyncio
async def test_grvt_server_error_is_unknown_mutation_not_definite_rejection():
    from vibetrading.fv_mm.venues import UncertainMutation
    class Response:
        status=500
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def json(self):return {"code":1002}
    class Session:
        def post(self,*args,**kwargs):return Response()
    c=object.__new__(Grvt);c.trading=True;c.subaccount="123";c.headers={};c.session=Session()
    async def login():pass
    c.login=login
    with pytest.raises(UncertainMutation):await c.rpc("create_order",{"order":{}},mutation=True)
    with pytest.raises(RuntimeError):await c.rpc("positions")
