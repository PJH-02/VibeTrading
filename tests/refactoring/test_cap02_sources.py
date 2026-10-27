"""Focused credential-free tests for CAP-02 native source additions."""
import asyncio
import weakref

import pytest

from execution.brokers import KiwoomBrokerAdapter
from execution.perp_execution import HttpResponse, IncompleteHistory
from execution.reconciliation_sources import NativePollingSource


class ScriptedTransport:
    mutation_permit_protocol = 1
    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []
    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.pages.pop(0)
        return response if isinstance(response, HttpResponse) else HttpResponse(200, response)


def kiwoom(*pages):
    obj = KiwoomBrokerAdapter.__new__(KiwoomBrokerAdapter)
    obj.transport = ScriptedTransport(*pages)
    obj.account_context = {"venue":"kiwoom", "product":"krx_stock", "environment":"mock",
        "account_alias":"synthetic", "account_identity_verified":True, "account_id":"local-fixture"}
    obj.use_mock = True
    obj.base_url = "https://mockapi.kiwoom.com"
    obj._used_permits = weakref.WeakSet()
    async def token(): return "synthetic-token"
    obj._access_token = token
    return obj


def execution(order="7654321", cntr="0000001", symbol="A005930", **extra):
    return {"ord_no":order, "stk_cd":symbol, "io_tp_nm":"현금매수", "cntr_no":cntr,
            "cntr_qty":"2", "cntr_uv":"70000", "cntr_tm":"09:30:01", **extra}


def test_kiwoom_kt00009_native_ids_pagination_filter_and_unknown_fee():
    first = HttpResponse(200, {"acnt_ord_cntr_prst_array":[execution(), execution(cntr="0000002"),
        execution(order="1234567"), execution(cntr="0000003",io_tp_nm="융자매수")]},
        {"cont-yn":"Y", "next-key":"next-1"})
    second = HttpResponse(200, {"acnt_ord_cntr_prst_array":[execution(cntr="0000004")]},
        {"cont-yn":"N", "next-key":""})
    adapter = kiwoom(first, second)
    result = asyncio.run(adapter.execution_fills("005930", "20261028", "7654321"))
    assert result["complete"] is True
    assert [r["event_id"] for r in result["rows"]] == [
        "kiwoom:20261028:7654321:0000001", "kiwoom:20261028:7654321:0000002",
        "kiwoom:20261028:7654321:0000004"]
    assert all(r["fee"] is None and r["fee_known"] is False for r in result["rows"])
    first_call = adapter.transport.calls[0][2]
    assert first_call["headers"]["api-id"] == "kt00009"
    assert first_call["json_body"] == {"ord_dt":"20261028", "stk_bond_tp":"1", "mrkt_tp":"0",
        "sell_tp":"0", "qry_tp":"1", "dmst_stex_tp":"KRX", "stk_cd":"005930"}
    assert adapter.transport.calls[1][2]["headers"]["next-key"] == "next-1"


def test_kiwoom_execution_fails_closed_on_incomplete_pagination_and_bad_native_amount():
    adapter = kiwoom(HttpResponse(200, {"acnt_ord_cntr_prst_array":[execution()]}, {}))
    with pytest.raises(IncompleteHistory):
        asyncio.run(adapter.execution_fills("005930", "20261028"))
    adapter = kiwoom(HttpResponse(200, {"acnt_ord_cntr_prst_array":[execution(cntr_qty="NaN")]},
                                  {"cont-yn":"N", "next-key":""}))
    with pytest.raises(IncompleteHistory):
        asyncio.run(adapter.execution_fills("005930", "20261028"))


def test_source_requires_trusted_identity_and_returns_explicit_poll_mode():
    adapter = kiwoom()
    adapter.account_state = lambda: None
    async def facts(): return {"account_identity":{"native_account_id":"synthetic"}}
    adapter.account_state = facts
    source = NativePollingSource(adapter, [{"symbol":"005930", "instrument_key":"krx:equity:005930",
                                            "day":"20261028"}],
        identity_verifier=lambda row: row.get("account_identity", {}).get("native_account_id") == "synthetic",
        clock_ns=lambda: 10)
    auth = asyncio.run(source.authenticate())
    receipt = asyncio.run(source.subscribe())
    assert auth["scope_key"] == adapter.scope.key and auth["success"] is True
    assert receipt["mode"] == "BOUNDED_REST_POLL" and receipt["success"] is True
    assert "websocket" in receipt["proof"]


def test_source_rejects_unverified_identity_and_scope_change_after_await():
    adapter = kiwoom()
    async def facts(): return {"native":True}
    adapter.account_state = facts
    source = NativePollingSource(adapter, [{"symbol":"005930", "instrument_key":"krx:equity:005930", "day":"20261028"}],
        identity_verifier=lambda _: 1, clock_ns=lambda: 1)
    with pytest.raises(IncompleteHistory):
        asyncio.run(source.authenticate())
    source = NativePollingSource(adapter, [{"symbol":"005930", "instrument_key":"krx:equity:005930", "day":"20261028"}],
        identity_verifier=lambda _: True, clock_ns=lambda: 1)
    async def changing_facts():
        adapter.account_context["account_id"] = "changed"
        return {"native":True}
    adapter.account_state = changing_facts
    with pytest.raises(IncompleteHistory):
        asyncio.run(source.authenticate())

class PerpReadTransport:
    """Scripted native HTTP responses for existing adapter read methods."""
    mutation_permit_protocol = 1
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        body = self.responses.pop(0)
        from execution.perp_execution import HttpResponse
        return body if isinstance(body, HttpResponse) else HttpResponse(200, body)


def test_two_real_perp_adapters_history_and_account_snapshot_use_native_reads():
    from execution.perp_execution import BinancePerpAdapter, BybitPerpAdapter, PerpScope

    async def run_binance():
        scope = PerpScope("binance", "linear_perpetual", "sandbox", "fixture", "USDT")
        transport = PerpReadTransport([
            [], [],
            {"assets":[{"asset":"USDT", "marginBalance":"10", "availableBalance":"9", "initialMargin":"1", "maintMargin":"0"}]},
            [], [],
        ])
        adapter = BinancePerpAdapter(scope, transport, api_key="fixture", api_secret="fixture", clock_ns=lambda: 1_000_000_000)
        source = NativePollingSource(adapter, [{"symbol":"BTCUSDT", "instrument_key":"binance:crypto_perp:BTCUSDT"}],
            identity_verifier=lambda facts: facts.get("scope_key") == scope.key, clock_ns=lambda: 5_000_000_000)
        hist = await source.history(0, 2_000_000_000)
        snap = await source.account_snapshot()
        assert hist["complete"] and hist["fills"] == [] and hist["funding"] == []
        assert snap["complete"] and snap["positions"] == {"binance:crypto_perp:BTCUSDT":"0"}
        assert len(transport.calls) == 5

    async def run_bybit():
        scope = PerpScope("bybit", "linear_perpetual", "sandbox", "fixture", "USDT")
        empty_history = {"retCode":0, "result":{"list":[], "nextPageCursor":""}}
        transport = PerpReadTransport([
            empty_history, empty_history,
            {"retCode":0, "result":{"list":[{"accountType":"UNIFIED", "totalEquity":"10", "totalAvailableBalance":"9", "totalInitialMargin":"1", "totalMaintenanceMargin":"0"}]}},
            {"retCode":0, "result":{"list":[], "nextPageCursor":""}},
            {"retCode":0, "result":{"list":[], "nextPageCursor":""}},
        ])
        adapter = BybitPerpAdapter(scope, transport, api_key="fixture", api_secret="fixture", clock_ns=lambda: 1_000_000_000)
        source = NativePollingSource(adapter, [{"symbol":"BTCUSDT", "instrument_key":"bybit:crypto_perp:BTCUSDT"}],
            identity_verifier=lambda facts: facts.get("scope_key") == scope.key, clock_ns=lambda: 5_000_000_000)
        hist = await source.history(0, 2_000_000_000)
        snap = await source.account_snapshot()
        assert hist["complete"] and hist["fills"] == [] and hist["funding"] == []
        assert snap["complete"] and snap["positions"] == {"bybit:crypto_perp:BTCUSDT":"0"}
        assert len(transport.calls) == 5

    asyncio.run(run_binance())
    asyncio.run(run_bybit())


def test_native_perp_terminal_cumulative_uses_raw_fields_and_generic_partial_enum():
    from execution.perp_execution import BinancePerpAdapter, BybitPerpAdapter, PerpScope
    scope = PerpScope("bybit", "linear_perpetual", "sandbox", "fixture", "USDT")
    adapter = BybitPerpAdapter(scope, PerpReadTransport([]), api_key="fixture", api_secret="fixture")
    source = NativePollingSource(adapter, [{"symbol":"BTCUSDT", "instrument_key":"bybit:crypto_perp:BTCUSDT"}],
        identity_verifier=lambda _: True, clock_ns=lambda: 1)
    raw = adapter._normalize_order({"symbol":"BTCUSDT", "orderId":"o1", "orderLinkId":"c1",
        "orderStatus":"PartiallyFilledCanceled", "cumExecQty":"3"})
    mapped = source._native_order(raw, "bybit:crypto_perp:BTCUSDT", "BTCUSDT")
    assert mapped["status"] == "CANCELED" and mapped["terminal_proven"] is True
    assert mapped["cumulative_quantity"] == "3"
    raw = adapter._normalize_order({"symbol":"BTCUSDT", "orderId":"o2", "orderLinkId":"c2",
        "orderStatus":"PartiallyFilled", "cumExecQty":"2"})
    assert source._native_order(raw, "bybit:crypto_perp:BTCUSDT", "BTCUSDT")["status"] == "PARTIAL"
    raw["raw"].pop("cumExecQty")
    mapped = source._native_order(raw, "bybit:crypto_perp:BTCUSDT", "BTCUSDT")
    assert mapped["terminal_proven"] is False and "cumulative_quantity" not in mapped


def test_kiwoom_cancel_terminal_requires_complete_linked_proof_and_execution_sum():
    adapter = kiwoom()
    async def facts(symbol, day, order_id):
        return {"status":"FACTS", "complete":True, "rows":[
            {"stk_cd":"A005930", "ord_no":"7654321", "ori_ord":"0000000", "ord_qty":"+0000000005", "io_tp_nm":"현금매수"},
            {"stk_cd":"A005930", "ord_no":"7654322", "ori_ord":"7654321", "io_tp_nm":"현금매수", "mdfy_cncl":"취소확인",
             "cnfm_qty":"+0000000002", "ord_remnq":"+0000000000"}]}
    async def executions(symbol, day, order_id=None, **kwargs):
        rows = [{"scope_key":adapter.scope.key, "event_id":f"f{i}", "symbol":"005930", "order_id":"7654321", "quantity":"1"} for i in range(1,4)]
        return {"scope_key":adapter.scope.key, "complete":True, "rows":rows}
    adapter.query_order = facts
    adapter.execution_fills = executions
    source = NativePollingSource(adapter, [{"symbol":"005930", "instrument_key":"krx:equity:005930", "day":"20261028", "native_order_id":"7654321"}],
        identity_verifier=lambda _: True, clock_ns=lambda: 1)
    async def run():
        order = await source.query_order("7654321", symbol="005930")
        assert order["status"] == "CANCELED" and order["terminal_proven"] is True
        assert order["cumulative_quantity"] == "3"
        # A changed confirmation quantity cannot be treated as terminal evidence.
        async def bad_facts(symbol, day, order_id):
            result = await facts(symbol, day, order_id)
            result["rows"][1]["cnfm_qty"] = "1"
            return result
        adapter.query_order = bad_facts
        order = await source.query_order("7654321", symbol="005930")
        assert order["status"] == "UNKNOWN" and order["terminal_proven"] is False
    asyncio.run(run())


def test_history_kiwoom_reads_whole_symbol_day_for_every_bound_order_once():
    adapter = kiwoom()
    calls = []
    async def executions(symbol, day, order_id=None, **kwargs):
        calls.append((symbol, day, order_id))
        return {"scope_key":adapter.scope.key, "complete":True, "rows":[
            {"scope_key":adapter.scope.key, "kind":"fill", "event_id":"e1", "symbol":symbol,
             "order_id":"7654321", "quantity":"1", "price":"2", "fee":None, "exchange_time":"09:00:00"},
            {"scope_key":adapter.scope.key, "kind":"fill", "event_id":"e2", "symbol":symbol,
             "order_id":"7654322", "quantity":"1", "price":"2", "fee":None, "exchange_time":"09:00:01"}]}
    adapter.execution_fills = executions
    source = NativePollingSource(adapter, [
        {"symbol":"005930", "instrument_key":"krx:equity:005930", "day":"20261028", "native_order_id":"7654321"},
        {"symbol":"005930", "instrument_key":"krx:equity:005930", "day":"20261028", "native_order_id":"7654322"}],
        identity_verifier=lambda _: True, clock_ns=lambda: 1)
    from datetime import datetime
    day = datetime.strptime("20261028", "%Y%m%d").date()
    day_start = (day.toordinal() - datetime(1970, 1, 1).date().toordinal()) * 86_400_000_000_000 - 9 * 3_600_000_000_000
    result = asyncio.run(source.history(day_start, day_start + 86_400_000_000_000 - 1))
    assert result["complete"] and len(result["fills"]) == 2
    assert calls == [("005930", "20261028", None)]


def test_history_aggregate_row_and_document_limits_are_global_across_symbols():
    from execution.perp_execution import BinancePerpAdapter, PerpScope
    scope = PerpScope("binance", "linear_perpetual", "sandbox", "fixture", "USDT")
    adapter = BinancePerpAdapter(scope, PerpReadTransport([]), api_key="fixture", api_secret="fixture")
    source = NativePollingSource(adapter, [
        {"symbol":"BTCUSDT", "instrument_key":"binance:crypto_perp:BTCUSDT"},
        {"symbol":"ETHUSDT", "instrument_key":"binance:crypto_perp:ETHUSDT"}],
        identity_verifier=lambda _: True, clock_ns=lambda: 1, max_rows=2)
    source._history_bytes_this_call = 0
    rows = [{"event_id":"a"}, {"event_id":"b"}]
    source._append_bounded([], rows)
    with pytest.raises(IncompleteHistory, match="row bound"):
        source._append_bounded(rows, [{"event_id":"c"}])
    source.max_document_bytes = 16
    with pytest.raises(IncompleteHistory, match="byte bound"):
        source._append_bounded([], [{"payload":"x" * 32}])


def test_grvt_connection_sequence_gap_marks_ready_session_blocked():
    from execution.perp_execution import GrvtPerpAdapter, PerpScope
    adapter = object.__new__(GrvtPerpAdapter)
    adapter.scope = PerpScope("grvt", "linear_perpetual", "sandbox", "fixture", "USDT", "42")
    adapter.normalize_private_event = lambda frame: [frame]
    source = NativePollingSource(adapter, [{"symbol":"BTC_USDT_Perp", "instrument_key":"grvt:crypto_perp:BTCUSDT"}],
        identity_verifier=lambda _: True, clock_ns=lambda: 1)
    class Session:
        ready = True
        accepted = []
        gaps = []
        def ingest(self, row, generation=None): self.accepted.append(row)
        def mark_gap(self, reason): self.ready = False; self.gaps.append(reason)
    session = Session()
    def frame(sequence):
        return {"kind":"fill", "scope_key":source.scope_key, "symbol":"BTC_USDT_Perp",
                "event_id":f"e{sequence}", "order_id":"o", "quantity":"1", "price":"2",
                "fee":"0", "fee_currency":"USDT", "connection_sequence":sequence,
                "sequence_requires_connection_scope":True}
    assert source.ingest_frame(session, frame(10), generation=4) == 1
    with pytest.raises(IncompleteHistory, match="sequence"):
        source.ingest_frame(session, frame(12), generation=4)
    assert session.ready is False and session.gaps == ["PRIVATE_FRAME_REJECTED"]


def test_invalid_private_frame_marks_session_gap_before_reraising():
    from execution.perp_execution import BinancePerpAdapter, PerpScope
    scope = PerpScope("binance", "linear_perpetual", "sandbox", "fixture", "USDT")
    adapter = BinancePerpAdapter(scope, PerpReadTransport([]), api_key="fixture", api_secret="fixture")
    source = NativePollingSource(adapter, [{"symbol":"BTCUSDT", "instrument_key":"binance:crypto_perp:BTCUSDT"}],
        identity_verifier=lambda _: True, clock_ns=lambda: 1)
    class Session:
        ready = True
        gaps = []
        def ingest(self, *args, **kwargs): raise AssertionError("invalid fact must not reach ingest")
        def mark_gap(self, reason): self.ready = False; self.gaps.append(reason)
    session = Session()
    with pytest.raises(KeyError):
        source.ingest_frame(session, {"e":"ORDER_TRADE_UPDATE", "o":{"s":"BTCUSDT", "ps":"BOTH",
            "x":"TRADE", "l":"1", "t":1, "i":2, "S":"BUY", "L":"2", "n":"0", "N":"USDT", "T":1, "m":False}}, 1)
    assert session.ready is False and session.gaps == ["PRIVATE_FRAME_REJECTED"]


def test_spot_from_origin_history_and_native_order_query_are_scoped():
    from execution.brokers import BinanceBrokerAdapter
    adapter = BinanceBrokerAdapter.__new__(BinanceBrokerAdapter)
    adapter.account_context = {"venue":"binance", "product":"spot", "environment":"sandbox", "account_alias":"fixture",
        "account_identity_verified":True, "account_id":"fixture-account"}
    adapter.use_testnet = True
    adapter.base_url = "https://testnet.binance.vision"
    adapter.api_key = "fixture"
    adapter.api_secret = "fixture"
    adapter.transport = PerpReadTransport([
        [],
        {"canTrade":True, "balances":[{"asset":"BTC", "free":"1", "locked":"0"}]},
        [],
        {"symbol":"BTCUSDT", "orderId":77, "clientOrderId":"local-77", "status":"CANCELED", "executedQty":"3"},
    ])
    source = NativePollingSource(adapter, [{"symbol":"BTCUSDT", "instrument_key":"binance:crypto_spot:BTCUSDT",
        "base_asset":"BTC", "native_order_id":77, "client_order_id":"local-77"}],
        identity_verifier=lambda facts: facts.get("can_trade") is True, clock_ns=lambda: 1)
    async def run():
        history = await source.history(0, 10**12)
        assert history["complete"] is True
        assert history["coverage_proofs"][0]["query"] == "myTrades fromId=0"
        assert "fromId=0" in adapter.transport.calls[0][1]
        snapshot = await source.account_snapshot()
        assert snapshot["orders"][-1]["status"] == "CANCELED"
        assert snapshot["orders"][-1]["terminal_proven"] is True
        assert snapshot["orders"][-1]["cumulative_quantity"] == "3"
    asyncio.run(run())


def test_kiwoom_client_only_order_binding_stays_unknown_without_native_lookup():
    adapter = kiwoom()
    source = NativePollingSource(adapter, [{"symbol":"005930", "instrument_key":"krx:equity:005930",
        "day":"20261028", "client_order_id":"client-only"}],
        identity_verifier=lambda _: True, clock_ns=lambda: 1)
    result = asyncio.run(source.query_order("client-only", symbol="005930"))
    assert result["status"] == "UNKNOWN" and result["terminal_proven"] is False
    assert adapter.transport.calls == []


def test_spot_source_rejects_too_small_native_page_budget_before_io():
    from execution.brokers import BinanceBrokerAdapter
    adapter = BinanceBrokerAdapter.__new__(BinanceBrokerAdapter)
    adapter.account_context = {"venue":"binance", "product":"spot", "environment":"sandbox", "account_alias":"fixture",
        "account_identity_verified":True, "account_id":"fixture-account"}
    adapter.use_testnet = True
    adapter.base_url = "https://testnet.binance.vision"
    adapter.api_key = "fixture"
    adapter.api_secret = "fixture"
    adapter.transport = PerpReadTransport([])
    source = NativePollingSource(adapter, [{"symbol":"BTCUSDT", "instrument_key":"binance:crypto_spot:BTCUSDT", "base_asset":"BTC"}],
        identity_verifier=lambda _: True, clock_ns=lambda: 1, max_pages=9)
    with pytest.raises(IncompleteHistory, match="page budget"):
        asyncio.run(source.history(0, 10**12))
    assert adapter.transport.calls == []


def test_hyperliquid_order_status_proves_cumulative_from_origsz_and_leaves():
    from types import SimpleNamespace
    from execution.perp_execution import HyperliquidPerpAdapter, HyperliquidSdkSigningComposition, PerpScope
    class Nonces:
        durable_nonce_protocol = 1
        def reserve(self, *args, **kwargs): return 1
    cloid = "0x" + "1" * 32
    def adapter_for(native_order):
        transport = PerpReadTransport([{"status":"order", "order":{"status":"canceled", "order":{
            "coin":"BTC", "oid":7, "cloid":cloid, **native_order}}}])
        signer = HyperliquidSdkSigningComposition(SimpleNamespace(address="0x" + "2" * 40),
            sign_l1_action=lambda *args: {"r":"0x1", "s":"0x2", "v":27})
        adapter = HyperliquidPerpAdapter(PerpScope("hyperliquid", "linear_perpetual", "sandbox", "fixture", "USDC"),
            transport, account_address="0x" + "3" * 40, signer=signer, nonce_source=Nonces())
        source = NativePollingSource(adapter, [{"symbol":"BTC", "instrument_key":"hyperliquid:crypto_perp:BTC", "client_order_id":cloid}],
            identity_verifier=lambda _: True, clock_ns=lambda: 1)
        return source
    async def run():
        source = adapter_for({"origSz":"5", "sz":"2"})
        fact = await source.query_order("0x" + "1" * 32, symbol="BTC")
        assert fact["status"] == "CANCELED" and fact["terminal_proven"] is True
        assert fact["cumulative_quantity"] == "3"
        source = adapter_for({"sz":"2"})
        fact = await source.query_order("0x" + "1" * 32, symbol="BTC")
        assert fact["status"] == "UNKNOWN" and fact["terminal_proven"] is False
        assert "cumulative_quantity" not in fact
    asyncio.run(run())


# Lead-authored native-adapter + real-ledger restart integration.
"""Lead-authored tests use original native adapters, transport mapping and real paper ledger."""
import asyncio
from decimal import Decimal as D
from urllib.parse import urlsplit
import pytest
from core.models import AssetClass,InstrumentId,OrderDestination,OrderIntent,Side,Venue
from execution.execution_ledger import AccountScope
from execution.perp_execution import PerpScope,BinancePerpAdapter,BybitPerpAdapter,HttpResponse
from execution.reconciliation import ReconciliationSession
from execution.reconciliation_sources import NativePollingSource
from execution.trading_contracts import ProductKind,ProductSpec
from portfolio.portfolio_ledger import PortfolioLedger

NOW=2_000_000_000
class NativeReadTransport:
    mutation_permit_protocol=1
    def __init__(self,venue):self.venue=venue;self.calls=[]
    async def request(self,method,url,**kwargs):
        path=urlsplit(url).path;self.calls.append((method,path,kwargs))
        assert method=='GET', 'Native recovery must never submit/cancel'
        if self.venue=='binance':
            if path.endswith('/account'):
                value={'assets':[{'asset':'USDT','marginBalance':'10000.8','availableBalance':'9000','initialMargin':'30','maintMargin':'3'}]}
            elif path.endswith('/userTrades'):
                value=[{'symbol':'BTCUSDT','id':i,'orderId':77,'side':'BUY','qty':q,'price':'100','commission':fee,'commissionAsset':'USDT','time':1000+i,'maker':False} for i,q,fee in [(1,'2','0.1'),(2,'1','0.1')]]
            elif path.endswith('/income'):
                value=[{'tranId':'funding-1','symbol':'BTCUSDT','income':'1','asset':'USDT','time':1100}]
            elif path.endswith('/positionRisk'):
                value=[{'symbol':'BTCUSDT','positionSide':'BOTH','marginType':'cross','positionAmt':'3','entryPrice':'100'}]
            elif path.endswith('/openOrders'):value=[]
            elif path.endswith('/order'):
                value={'symbol':'BTCUSDT','orderId':77,'clientOrderId':'client-1','status':'CANCELED','executedQty':'3'}
            else:raise AssertionError(path)
        else:
            if path.endswith('/wallet-balance'):
                result={'list':[{'accountType':'UNIFIED','totalEquity':'10000.8','totalAvailableBalance':'9000','totalInitialMargin':'30','totalMaintenanceMargin':'3'}]}
            elif path.endswith('/execution/list'):
                result={'list':[{'execId':str(i),'orderId':'77','orderLinkId':'client-1','symbol':'BTCUSDT','side':'Buy','execQty':q,'execPrice':'100','execFee':fee,'feeCurrency':'USDT','execTime':1000+i,'isMaker':False,'execType':'Trade'} for i,q,fee in [(1,'2','0.1'),(2,'1','0.1')]],'nextPageCursor':''}
            elif path.endswith('/transaction-log'):
                result={'list':[{'id':'funding-1','symbol':'BTCUSDT','funding':'1','currency':'USDT','transactionTime':1100,'category':'linear'}],'nextPageCursor':''}
            elif path.endswith('/position/list'):
                result={'list':[{'positionIdx':0,'symbol':'BTCUSDT','size':'3','side':'Buy','avgPrice':'100'}],'nextPageCursor':''}
            elif path.endswith('/order/realtime'):
                if kwargs['params'].get('orderLinkId'):
                    result={'list':[{'symbol':'BTCUSDT','orderId':'77','orderLinkId':'client-1','orderStatus':'PartiallyFilledCanceled','cumExecQty':'3'}],'nextPageCursor':''}
                else:result={'list':[],'nextPageCursor':''}
            else:raise AssertionError(path)
            value={'retCode':0,'result':result}
        return HttpResponse(200,value)


def ledger(tmp_path,venue):
    product=ProductSpec(instrument=InstrumentId(venue=Venue(venue),asset_class=AssetClass.CRYPTO_PERP,raw_symbol='BTCUSDT',std_symbol='BTCUSDT'),kind=ProductKind.LINEAR_PERPETUAL,multiplier=D('1'),tick_size=D('0.1'),lot_size=D('0.1'),min_notional=D('1'),quote_currency='USDT',settlement_currency='USDT',collateral_currency='USDT',position_mode='net',margin_mode='isolated',fee_provenance='synthetic',funding_provenance='synthetic',margin_model_provenance='explicit paper',maker_fee_rate=D('0'),taker_fee_rate=D('0'))
    b=PortfolioLedger(tmp_path/'paper/book.sqlite',AccountScope(venue,'acct','trading','paper'),owner_root=tmp_path,mode='paper',max_orders=20,max_fills=30,max_events=60,max_document_bytes=100000,max_db_bytes=4000000,max_wal_bytes=8000000)
    if not b.get('i'):
        b.seed_balance('USDT',D('10000'),{'fixture':'paper'})
        intent=OrderIntent(intent_id='i',job_id='j',signal_id='s',destination=OrderDestination.REAL,instrument=product.instrument,side=Side.BUY,quantity=D('5'),limit_price=D('100'),client_order_id='client-1',created_ts_ns=1,metadata={})
        b.admit_order(intent,'alpha',{'fixture':'paper'},1000,product,funding_buffer=0,account_notional_limit=10000,strategy_notional_limit=10000,account_capital_limit=10000,strategy_capital_limit=10000,now_ns=1,margin_rate=D('.1'))
    return b,product.instrument.key

@pytest.mark.parametrize('venue',['binance','bybit'])
def test_actual_native_reads_reconcile_cancel_late_fills_funding_and_restart_once(tmp_path,venue):
    async def run():
        transport=NativeReadTransport(venue)
        selected=PerpScope(venue,'linear_perpetual','sandbox','acct','USDT')
        cls=BinancePerpAdapter if venue=='binance' else BybitPerpAdapter
        adapter=cls(selected,transport,api_key='synthetic-key',api_secret='synthetic-secret',clock_ns=lambda:NOW)
        b,key=ledger(tmp_path,venue)
        source=NativePollingSource(adapter,[{'symbol':'BTCUSDT','instrument_key':key,'native_order_id':'77','client_order_id':'client-1'}],identity_verifier=lambda facts:facts.get('scope_key')==selected.key,clock_ns=lambda:NOW)
        s=ReconciliationSession(b,selected.key,max_orders=8,max_events=50,max_document_bytes=90000,max_staleness_ns=1000000000,clock_ns=lambda:NOW)
        s.bind_order('i','77',symbol='BTCUSDT')
        await s.start(source.authenticate,source.subscribe)
        view=await s.reconcile(source,start_ns=0,end_ns=NOW)
        assert view['ready']
        assert b.get('i')['state']=='CANCELED'
        assert b.get('i')['filled']=='3'
        assert b.position('alpha',key)==D('3')
        assert D(b.report()['balances']['USDT'])==D('10000.8')
        versions=[x['version'] for x in s.events()];assert versions==sorted(set(versions))
        await s.stop();assert not s.snapshot()['ready']
        assert (await source.query_order('77','BTCUSDT'))['status']=='CANCELED'
        b.close()
        b,key=ledger(tmp_path,venue)
        restarted=ReconciliationSession(b,selected.key,max_orders=8,max_events=50,max_document_bytes=90000,max_staleness_ns=1000000000,clock_ns=lambda:NOW)
        assert not restarted.snapshot()['ready']
        await restarted.start(source.authenticate,source.subscribe)
        result=await restarted.reconcile(source,start_ns=0,end_ns=NOW)
        assert result['ready']
        assert b.position('alpha',key)==D('3')
        assert D(b.report()['balances']['USDT'])==D('10000.8')
        assert all(c[0]=='GET' for c in transport.calls)
        b.close()
    asyncio.run(run())


def test_kiwoom_native_two_fills_cancel_remainder_restart_real_ledger(tmp_path):
    import weakref
    from datetime import datetime,timezone
    from execution.brokers import KiwoomBrokerAdapter
    from test_cap01_brokers import FakeTransport
    start=int(datetime(2026,10,27,15,tzinfo=timezone.utc).timestamp())*10**9
    end=start+86400*10**9-1
    executions=[{'ord_no':'7654321','stk_cd':'A005930','io_tp_nm':'현금매수','cntr_no':f'{i:07d}','cntr_qty':q,'cntr_uv':'100','cntr_tm':'09:30:01','mdfy_cncl_tp':'','orig_ord_no':'0000000'} for i,q in [(1,'2'),(2,'1')]]
    class CashReadTransport:
        mutation_permit_protocol=1
        def __init__(self):self.calls=[]
        async def request(self,method,url,**kwargs):
            self.calls.append((method,url,kwargs));assert url.endswith('/api/dostk/acnt')
            api=kwargs['headers']['api-id']
            if api=='ka00001':data={'acctNo':'1234567890'}
            elif api=='kt00018':data={'acnt_evlt_remn_indv_tot':[{'stk_cd':'A005930','rmnd_qty':'3','trde_able_qty':'3'}]}
            elif api=='kt00009':data={'acnt_ord_cntr_prst_array':executions}
            elif api=='ka10075':data={'oso':[]}
            elif api=='kt00007':data={'acnt_ord_cntr_prps_dtl':[
                {'ord_no':'7654321','ori_ord':'0000000','stk_cd':'A005930','ord_qty':'5','cntr_qty':'3','ord_remnq':'0','mdfy_cncl':'','cnfm_qty':'0','io_tp_nm':'현금매수'},
                {'ord_no':'7654322','ori_ord':'7654321','stk_cd':'A005930','ord_qty':'2','cntr_qty':'0','ord_remnq':'0','mdfy_cncl':'취소확인','cnfm_qty':'2','io_tp_nm':'현금매수'}]}
            else:raise AssertionError(api)
            return HttpResponse(200,data,{'cont-yn':'N','next-key':''})
    transport=CashReadTransport()
    adapter=KiwoomBrokerAdapter.__new__(KiwoomBrokerAdapter)
    adapter.transport=transport;adapter.account_context={'venue':'kiwoom','product':'krx_stock','environment':'mock','account_alias':'synthetic','account_identity_verified':True,'account_id':'acct','native_account_id':'1234567890'}
    adapter.use_mock=True;adapter.base_url='https://mockapi.kiwoom.com';adapter._used_permits=weakref.WeakSet()
    async def token():return 'synthetic-token'
    adapter._access_token=token
    product=ProductSpec(instrument=InstrumentId(venue=Venue.KIWOOM,asset_class=AssetClass.EQUITY,raw_symbol='005930',std_symbol='005930'),kind=ProductKind.CASH,multiplier=D('1'),tick_size=D('1'),lot_size=D('1'),min_notional=D('1'),quote_currency='KRW',settlement_currency='KRW',collateral_currency='KRW',position_mode='net',margin_mode='cash',fee_provenance='explicit missing native perfillfee',funding_provenance='not applicable cash',margin_model_provenance='cash',maker_fee_rate=D('0'),taker_fee_rate=D('0'))
    def cashbook():
        b=PortfolioLedger(tmp_path/'paper/book.sqlite',AccountScope('kiwoom','acct','cash','paper'),owner_root=tmp_path,mode='paper',max_orders=20,max_fills=30,max_events=60,max_document_bytes=100000,max_db_bytes=4000000,max_wal_bytes=8000000)
        if not b.get('i'):
            b.seed_balance('KRW',10000,{'fixture':'paper'})
            intent=OrderIntent(intent_id='i',job_id='j',signal_id='s',destination=OrderDestination.REAL,instrument=product.instrument,side=Side.BUY,quantity=D('5'),limit_price=D('100'),client_order_id='local-1',created_ts_ns=1,metadata={})
            b.admit_order(intent,'alpha',{'fixture':'paper'},1000,product,funding_buffer=0,account_notional_limit=10000,strategy_notional_limit=10000,account_capital_limit=10000,strategy_capital_limit=10000,now_ns=1)
        return b
    async def run():
        key=product.instrument.key
        source=NativePollingSource(adapter,[{'symbol':'005930','instrument_key':key,'native_order_id':'7654321','client_order_id':'local-1','day':'20261028'}],identity_verifier=lambda facts:facts.get('account_identity',{}).get('native_account_id')=='1234567890',clock_ns=lambda:end)
        b=cashbook();s=ReconciliationSession(b,adapter.scope.key,max_orders=8,max_events=50,max_document_bytes=90000,max_staleness_ns=10**9,clock_ns=lambda:end)
        s.bind_order('i','7654321',symbol='005930');g=await s.start(source.authenticate,source.subscribe)
        rows=(await adapter.execution_fills('005930','20261028','7654321'))['rows']
        assert s.ingest(rows[0],g)
        assert b.get('i')['filled']=='2'
        s.ingest({'kind':'order','scope_key':adapter.scope.key,'symbol':'005930','order_id':'7654321','status':'CANCEL_PENDING','cumulative_quantity':'2','terminal_proven':False},g)
        assert D(b.get('i')['reservation'])>0
        assert s.ingest(rows[1],g)
        with pytest.raises(ValueError,match='fee'):
            await s.reconcile(source,start_ns=start,end_ns=end)
        assert b.get('i')['state']=='CANCELED'
        assert b.get('i')['filled']=='3';assert b.position('alpha',key)==D('3')
        assert not s.snapshot()['ready'] # source did not fabricate missing fee as verified zero
        b.close()
        b=cashbook();s2=ReconciliationSession(b,adapter.scope.key,max_orders=8,max_events=50,max_document_bytes=90000,max_staleness_ns=10**9,clock_ns=lambda:end)
        await s2.start(source.authenticate,source.subscribe)
        with pytest.raises(ValueError,match='fee'):
            await s2.reconcile(source,start_ns=start,end_ns=end)
        assert b.get('i')['filled']=='3';assert b.position('alpha',key)==D('3')
        assert D(b.report()['balances']['KRW'])==D('9700')
        assert not s2.snapshot()['ready']
        assert all('/ordr' not in call[1] for call in transport.calls)
        b.close()
    asyncio.run(run())


def test_hyperliquid_incomplete_bound_open_hint_waits_for_individual_query_and_foreign_hint_blocks():
    from types import SimpleNamespace
    from execution.perp_execution import HyperliquidPerpAdapter,HyperliquidSdkSigningComposition,PerpScope
    class Nonces:
        durable_nonce_protocol=1
        def reserve(self,*args,**kwargs):raise AssertionError('read-only source cannot reserve mutation nonce')
    cloid='0x'+'1'*32
    account={'marginSummary':{'accountValue':'10000','totalMarginUsed':'30'},'withdrawable':'9900','crossMaintenanceMarginUsed':'3','assetPositions':[]}
    def source_for(oid):
        transport=PerpReadTransport([account,{'assetPositions':[]},[{'coin':'BTC','oid':oid,'cloid':cloid,'sz':'2'}],{'status':'order','order':{'status':'canceled','order':{'coin':'BTC','oid':7,'cloid':cloid,'origSz':'5','sz':'2'}}}])
        signer=HyperliquidSdkSigningComposition(SimpleNamespace(address='0x'+'2'*40),sign_l1_action=lambda *args: {'r':'0x1','s':'0x2','v':27})
        adapter=HyperliquidPerpAdapter(PerpScope('hyperliquid','linear_perpetual','sandbox','fixture','USDC'),transport,account_address='0x'+'3'*40,signer=signer,nonce_source=Nonces())
        return NativePollingSource(adapter,[{'symbol':'BTC','instrument_key':'hyperliquid:crypto_perp:BTC','native_order_id':'7','client_order_id':cloid}],identity_verifier=lambda facts: facts.get('account_address')=='0x'+'3'*40,clock_ns=lambda:1000),transport
    async def run():
        source,transport=source_for(7);snapshot=await source.account_snapshot()
        assert len(snapshot['orders'])==1
        assert snapshot['orders'][0]['status']=='CANCELED'
        assert snapshot['orders'][0]['cumulative_quantity']=='3'
        assert len(transport.calls)==4
        source,transport=source_for(8)
        with pytest.raises(IncompleteHistory,match='bound order'):
            await source.account_snapshot()
        assert len(transport.calls)==3
    asyncio.run(run())
