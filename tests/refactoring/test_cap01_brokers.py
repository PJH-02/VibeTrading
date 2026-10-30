"""Credential-free CAP-01 broker safety and native fact tests."""
import asyncio
import weakref
from decimal import Decimal

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from execution.brokers import BinanceBrokerAdapter, KiwoomBrokerAdapter
from execution.perp_execution import HttpResponse, IncompleteHistory, OutcomeUnknown, VenueRejected


class FakeTransport:
    mutation_permit_protocol = 1
    def __init__(self, *responses, hook=None): self.responses, self.calls, self.dispatched, self.hook = list(responses), [], [], hook
    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.hook: self.hook(method, url, kwargs)
        if kwargs.get("before_send"):
            kwargs["before_send"]()
            self.dispatched.append((method, url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, BaseException): raise value
        return value if isinstance(value, HttpResponse) else HttpResponse(200, value)


def kiwoom(responses=(), *, hook=None):
    obj = KiwoomBrokerAdapter.__new__(KiwoomBrokerAdapter)
    obj.transport = FakeTransport(*responses, hook=hook)
    obj.account_context = {"venue":"kiwoom", "product":"krx_stock", "environment":"mock", "account_alias":"synthetic", "account_identity_verified":True, "account_id":"acct-synthetic"}
    obj.use_mock = True
    obj.base_url = "https://mockapi.kiwoom.com"
    obj._used_permits = weakref.WeakSet()
    async def token(): return "synthetic-token"
    obj._access_token = token
    return obj


def spot_spec(symbol="BTCUSDT", **overrides):
    return {"symbol":symbol,"status":"TRADING","isSpotTradingAllowed":True,"orderTypes":["LIMIT","LIMIT_MAKER","MARKET"],"permissions":["SPOT"],"filters":[
        {"filterType":"LOT_SIZE","stepSize":"0.001","minQty":"0.001","maxQty":"100"},
        {"filterType":"MARKET_LOT_SIZE","stepSize":"0.001","minQty":"0.001","maxQty":"100"},
        {"filterType":"PRICE_FILTER","tickSize":"0.01","minPrice":"0.01","maxPrice":"1000000"},
        {"filterType":"MIN_NOTIONAL","minNotional":"5"}], **overrides}


def spot(responses=(), *, hook=None, environment="sandbox"):
    obj=BinanceBrokerAdapter.__new__(BinanceBrokerAdapter)
    obj.transport=FakeTransport(*responses,hook=hook)
    obj.account_context={"venue":"binance","product":"spot","environment":environment,"account_alias":"synthetic","account_identity_verified":True,"account_id":"acct-synthetic","strategy_id":"strategy-synthetic"}
    obj._used_permits=weakref.WeakSet(); obj.api_key="synthetic-key"; obj.api_secret="synthetic-secret"
    obj.use_testnet=environment=="sandbox"; obj.base_url="https://testnet.binance.vision" if obj.use_testnet else "https://api.binance.com"
    return obj


def order(*, venue=Venue.BINANCE, asset=AssetClass.CRYPTO_SPOT, symbol="BTCUSDT", side=Side.BUY, quantity="0.1", price="100.00", client_id="Client-ID_1", tif="GTC", metadata=None):
    md={"product":"spot","time_in_force":tif,"account_id":"acct-synthetic","environment":"sandbox","strategy_id":"strategy-synthetic"}
    if metadata: md.update(metadata)
    return OrderIntent(intent_id="i",job_id="j",signal_id="s",destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=venue,asset_class=asset,raw_symbol=symbol,std_symbol=symbol), side=side,
        client_order_id=client_id,quantity=quantity,limit_price=price,metadata=md)


def test_kiwoom_mock_context_cancel_ack_is_pending_and_keeps_native_linkage():
    obj=kiwoom([{"ord_no":"7654321","base_orig_ord_no":"1234567"}]); calls=[]
    result=asyncio.run(obj.cancel_order_with_permit("A005930","1234567",permit=lambda:calls.append(1)))
    method,url,kwargs=obj.transport.calls[-1]
    assert kwargs["headers"]["api-id"]=="kt10003" and kwargs["json_body"]=={"dmst_stex_tp":"KRX","orig_ord_no":"1234567","stk_cd":"005930","cncl_qty":"0"}
    assert result["status"]=="CANCEL_PENDING" and result["cancel_request_id"]=="7654321" and result["original_order_id"]=="1234567"
    assert calls==[1] and len(obj.transport.dispatched)==1


def test_kiwoom_scope_mismatch_mock_sandbox_alias_foreign_side_rejects_before_token_or_dispatch():
    obj=kiwoom(); obj.account_context["environment"]="sandbox"
    with pytest.raises(RuntimeError): obj._validate_scope_context()
    obj=kiwoom(); obj.account_context["unknown_scope_field"]=True
    with pytest.raises(RuntimeError): obj._validate_scope_context()
    assert obj.transport.calls==[]


def test_kiwoom_fill_uses_official_reverse_cursor_semantics_and_vendor_fee_basis():
    rows=[{"ord_no":"1234567","stk_cd":"A005930","cntr_qty":"2","cntr_uv":"70000","tdy_trde_cmsn":"3","tdy_trde_tax":"1"},
          {"ord_no":"1234567","stk_cd":"A000660","cntr_qty":"5","cntr_uv":"1"},
          {"ord_no":"9999999","stk_cd":"A005930","cntr_qty":"8","cntr_uv":"1"}]
    obj=kiwoom([HttpResponse(200,{"cntr":rows},{"cont-yn":"N","next-key":""})])
    result=asyncio.run(obj.fills("005930","1234567"))
    row=result["rows"][0]; body=obj.transport.calls[0][2]["json_body"]
    assert "ord_no" not in body and len(result["rows"])==1
    assert row["event_id"] is None and not row["identity_proven"] and row["quantity_semantics"]=="VENDOR_REPORTED_UNVERIFIED"
    assert row["fee"]=="3" and row["fee_basis"]=="VENDOR_DAY_REPORTED" and row["tax"]=="1"
    assert "notional" not in row and "trade_id" not in row


def test_kiwoom_missing_or_repeated_pagination_evidence_is_not_complete():
    obj=kiwoom([HttpResponse(200,{"oso":[]},{})])
    with pytest.raises(IncompleteHistory): asyncio.run(obj._paged_post("ka10075",{},"oso"))
    obj=kiwoom([HttpResponse(200,{"oso":[]},{"cont-yn":"Y","next-key":"x"}),HttpResponse(200,{"oso":[]},{"cont-yn":"Y","next-key":"x"})])
    with pytest.raises(IncompleteHistory): asyncio.run(obj._paged_post("ka10075",{},"oso"))


def test_kiwoom_order_status_delegates_only_with_explicit_native_target_and_includes_linked_rows():
    obj=kiwoom(); seen=[]
    async def query(symbol,day,oid): seen.append((symbol,day,oid)); return {"status":"FACTS"}
    obj.query_order=query
    assert asyncio.run(obj.order_status("local"))["status"]=="UNKNOWN"
    assert asyncio.run(obj.order_status("local",symbol="005930",day="20261027",order_id="1234567"))["status"]=="FACTS"
    assert seen==[("005930","20261027","1234567")]


def test_kiwoom_submit_wrong_scope_and_unknown_side_are_rejected_pre_auth():
    from types import SimpleNamespace
    obj=kiwoom()
    instrument=SimpleNamespace(asset_class=AssetClass.EQUITY,venue=Venue.KRX,raw_symbol="005930")
    intent=SimpleNamespace(instrument=instrument,side=Side.UNKNOWN,quantity=Decimal("1"),limit_price=Decimal("100"),client_order_id="local",metadata={"product":"krx_stock","account_id":"acct-synthetic","environment":"mock","strategy_id":"s"})
    with pytest.raises(ValueError): asyncio.run(obj.submit_order_with_permit(intent,permit=lambda:None))
    assert obj.transport.calls==[]


def test_spot_list_account_payload_and_fills_preserve_json_list_and_zero_balances():
    obj=spot()
    async def signed(method,path,params,**kw):
        if path=="/api/v3/account": return {"canTrade":True,"balances":[{"asset":"USDT","free":"4.25","locked":"1.00"},{"asset":"BTC","free":"0","locked":"0"}]}
        return [{"symbol":"BTCUSDT","id":1,"orderId":9,"qty":"0.1","price":"100","quoteQty":"10","commission":"0.01","commissionAsset":"BTC","time":12}]
    obj._signed_request=signed
    facts=asyncio.run(obj.account_state()); fills=asyncio.run(obj.fills("BTCUSDT",limit=2))
    assert facts["can_trade"] and len(facts["balances"])==2 and facts["balances"][1]["free"]=="0"
    assert fills["rows"][0]["id"]==1 and fills["rows"][0]["commission"]=="0.01" and not fills["account_history_complete"]


def test_spot_metadata_lot_tick_and_min_notional_enforced_and_bad_local_data_preflighted():
    obj=spot([{"symbols":[spot_spec()]},{"orderId":7,"clientOrderId":"Client-ID_1"}])
    result=asyncio.run(obj.submit_order_with_permit(order(quantity="0.1"),permit=lambda:None))
    assert result["orderId"]==7 and "/api/v3/order?" in obj.transport.calls[-1][1]
    obj=spot([{"symbols":[spot_spec()]}]); bad=order(quantity="0.01")
    with pytest.raises(ValueError): asyncio.run(obj.submit_order_with_permit(bad,permit=lambda:None))
    assert len(obj.transport.calls)==1
    obj=spot(); bad=order(client_id="x"*37)
    with pytest.raises(ValueError): asyncio.run(obj.submit_order_with_permit(bad,permit=lambda:None))
    assert obj.transport.calls==[]


def test_spot_market_blocks_when_notional_requires_reference_and_bad_metadata_fails_closed():
    obj=spot([spot_spec(),{"orderId":1}]); market=order(price=None)
    with pytest.raises(RuntimeError): asyncio.run(obj.submit_order_with_permit(market,permit=lambda:None))
    assert len(obj.transport.calls)==1
    bad_spec=spot_spec(status="BREAK")
    obj=spot([bad_spec])
    with pytest.raises(RuntimeError): asyncio.run(obj.submit_order_with_permit(order(),permit=lambda:None))


def test_spot_environment_host_account_strategy_product_and_side_are_bound():
    obj=spot(environment="live"); obj.base_url="https://testnet.binance.vision"
    with pytest.raises(RuntimeError): obj._validate_context()
    obj=spot(); invalid=order(side=Side.UNKNOWN)
    with pytest.raises(ValueError): asyncio.run(obj.submit_order_with_permit(invalid,permit=lambda:None))
    obj=spot(); invalid=order(metadata={"account_id":"other"})
    with pytest.raises(ValueError): asyncio.run(obj.submit_order_with_permit(invalid,permit=lambda:None))
    assert obj.transport.calls==[]


def test_permit_reuse_async_callback_and_scope_drift_have_zero_dispatch():
    obj=spot([{"symbols":[spot_spec()]},{"orderId":8},{"symbols":[spot_spec()]}]); permit=lambda:None
    asyncio.run(obj.submit_order_with_permit(order(),permit=permit))
    before=len(obj.transport.dispatched)
    with pytest.raises(RuntimeError): asyncio.run(obj.submit_order_with_permit(order(),permit=permit))
    assert len(obj.transport.dispatched)==before
    async def async_permit(): return None
    obj=spot([{"symbols":[spot_spec()]}])
    with pytest.raises(TypeError): asyncio.run(obj.submit_order_with_permit(order(),permit=async_permit))
    assert obj.transport.calls==[]
    def mutate(method,url,kwargs):
        if method=="GET": obj.account_context["environment"]="live"
    obj=spot([{"symbols":[spot_spec()]},{"orderId":9}],hook=mutate)
    sent=[]
    with pytest.raises(RuntimeError): asyncio.run(obj.submit_order_with_permit(order(),permit=lambda:sent.append(1)))
    assert sent==[] and obj.transport.dispatched==[]


def test_post_permit_timeout_and_server_failure_are_unknown_prepermit_failure_is_preserved():
    obj=spot([{"symbols":[spot_spec()]},TimeoutError("synthetic")])
    with pytest.raises(OutcomeUnknown): asyncio.run(obj.submit_order_with_permit(order(),permit=lambda:None))
    obj=spot([{"symbols":[spot_spec()]},HttpResponse(503,{})])
    with pytest.raises(OutcomeUnknown): asyncio.run(obj.submit_order_with_permit(order(),permit=lambda:None))
    obj=spot([TimeoutError("before-send")])
    with pytest.raises(TimeoutError): asyncio.run(obj.instrument_metadata("BTCUSDT"))


def test_spot_explicit_rejection_and_unknown_query_are_sanitized():
    obj=spot([{"symbols":[spot_spec()]},HttpResponse(400,{"code":-1013,"msg":"private text"})])
    with pytest.raises(VenueRejected) as exc: asyncio.run(obj.submit_order_with_permit(order(),permit=lambda:None))
    assert "private text" not in str(exc.value)
    obj=spot([HttpResponse(404,{"code":-2013,"msg":"private text"})])
    assert asyncio.run(obj.query_order("BTCUSDT",order_id=8))["status"]=="UNKNOWN"
    with pytest.raises(ValueError): asyncio.run(obj.query_order("",order_id=8))


def test_kiwoom_submit_ack_identity_scope_and_exact_krw_wire_mapping():
    intent=OrderIntent(intent_id="i",job_id="j",signal_id="s",destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.KRX,asset_class=AssetClass.EQUITY,raw_symbol="A005930",std_symbol="005930"),side=Side.BUY,
        client_order_id="local-correlation",quantity="2.00",limit_price="70000.00",metadata={"product":"krx_stock","account_id":"acct-synthetic","environment":"mock","strategy_id":"strategy-synthetic"})
    obj=kiwoom([{"ord_no":"1234567"}]); obj.account_context["strategy_id"]="strategy-synthetic"
    result=asyncio.run(obj.submit_order_with_permit(intent,permit=lambda:None))
    call=obj.transport.calls[0][2]
    assert call["headers"]["api-id"]=="kt10000" and call["json_body"]=={"dmst_stex_tp":"KRX","stk_cd":"005930","ord_qty":"2","trde_tp":"0","ord_uv":"70000","cond_uv":""}
    assert result["ord_no"]=="1234567"


def test_kiwoom_account_snapshot_paginates_and_separates_alias_from_native_identity():
    obj=kiwoom([{"acctNo":"1234567890"},HttpResponse(200,{"tot_pur_amt":"42","prsm_dpst_aset_amt":"999","acnt_evlt_remn_indv_tot":[{"stk_cd":"A005930","rmnd_qty":"2","trde_able_qty":"1"}]},{"cont-yn":"N","next-key":""})])
    result=asyncio.run(obj.account_state())
    assert result["account_identity"]=={"native_account_id":"1234567890","alias":"synthetic","identity_proven":False}
    assert result["balances"]["tot_pur_amt"]=="42" and result["positions"][0]["trde_able_qty"]=="1" and result["complete"]
    obj=kiwoom([{"acctNo":"not-an-account"}])
    with pytest.raises(IncompleteHistory): asyncio.run(obj.account_state())


def test_kiwoom_timeout_or_missing_native_ack_after_dispatch_is_unknown():
    obj=kiwoom([TimeoutError("synthetic")]); permit=lambda:None
    with pytest.raises(OutcomeUnknown): asyncio.run(obj.cancel_order_with_permit("005930","1234567",permit=permit))
    obj=kiwoom([{"ord_no":"bad"}])
    with pytest.raises(OutcomeUnknown): asyncio.run(obj.cancel_order_with_permit("005930","1234567",permit=lambda:None))
    assert len(obj.transport.dispatched)==1


def test_dispatch_callback_reentry_context_change_is_blocked_before_transport_handoff():
    obj=spot([{"symbols":[spot_spec()]},{"orderId":5}]); calls=[]
    def permit():
        calls.append("permit")
        obj.account_context["account_id"]="switched-account"
    with pytest.raises(RuntimeError): asyncio.run(obj.submit_order_with_permit(order(),permit=permit))
    assert calls==["permit"] and obj.transport.dispatched==[]


def test_kiwoom_modify_pending_ack_requires_original_linkage_and_mapping():
    obj=kiwoom([{"ord_no":"7654321","base_orig_ord_no":"1234567"}])
    result=asyncio.run(obj.modify_order_with_permit("005930","1234567","2.00","70000.00",permit=lambda:None))
    call=obj.transport.calls[0][2]
    assert call["headers"]["api-id"]=="kt10002" and call["json_body"]["orig_ord_no"]=="1234567" and call["json_body"]["mdfy_qty"]=="2" and call["json_body"]["mdfy_uv"]=="70000"
    assert result["status"]=="MODIFY_PENDING" and result["modify_request_id"]=="7654321"


def test_kiwoom_scope_drift_during_authentication_blocks_native_mutation():
    from types import SimpleNamespace
    obj=kiwoom([{"ord_no":"1234567"}])
    instrument=SimpleNamespace(asset_class=AssetClass.EQUITY,venue=Venue.KRX,raw_symbol="005930")
    intent=SimpleNamespace(instrument=instrument,side=Side.BUY,quantity=Decimal("1"),limit_price=Decimal("100"),client_order_id="local",metadata={"product":"krx_stock","account_id":"acct-synthetic","environment":"mock","strategy_id":"s"})
    async def paused_auth():
        obj.account_context["account_id"]="other-account"
        return "synthetic-token"
    obj._access_token=paused_auth
    permits=[]
    with pytest.raises(RuntimeError): asyncio.run(obj.submit_order_with_permit(intent,permit=lambda:permits.append(1)))
    assert permits==[] and obj.transport.dispatched==[]


def test_kiwoom_query_preserves_partial_and_linked_cancel_rows_without_inventing_terminal_state():
    rows=[{"ord_no":"1234567","stk_cd":"A005930","ord_qty":"5","cntr_qty":"2","cntr_uv":"70000","ord_remnq":"3","ori_ord":"0000000","mdfy_cncl":""},
          {"ord_no":"7654321","stk_cd":"A005930","ord_qty":"0","cntr_qty":"0","cntr_uv":"0","ord_remnq":"0","ori_ord":"1234567","mdfy_cncl":"취소확인"}]
    obj=kiwoom([HttpResponse(200,{"acnt_ord_cntr_prps_dtl":rows},{"cont-yn":"N","next-key":""})])
    result=asyncio.run(obj.query_order("005930","20261027","1234567"))
    assert result["status"]=="FACTS" and len(result["rows"])==2
    assert result["rows"][0]["cntr_qty"]=="2" and result["rows"][0]["ord_remnq"]=="3"
    assert result["rows"][1]["mdfy_cncl"]=="취소확인" and "notional" not in result
