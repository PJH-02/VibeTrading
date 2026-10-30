"""Scripted TX06 transport tests. Run only from the guarded source copy."""
import asyncio
from copy import deepcopy
import hashlib
import hmac
import json
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from execution.perp_execution import ArcusPerpAdapter, ArcusSigningComposition, BinancePerpAdapter, BybitPerpAdapter, CapabilityBlocked, GrvtPerpAdapter, GrvtSigningComposition, HttpResponse, IncompleteHistory, OutcomeUnknown, HyperliquidPerpAdapter, HyperliquidSdkSigningComposition, PerpScope, capability_for, make_perp_adapter


class ScriptedTransport:
    mutation_permit_protocol = 1

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls, self.dispatched = [], []
        self.before_dispatch = None

    async def request(self, method, url, **kwargs):
        call = {"method": method, "url": url, **kwargs}
        self.calls.append(call)
        if self.before_dispatch:
            await self.before_dispatch()
        if kwargs["before_send"]:
            kwargs["before_send"]()
        self.dispatched.append(call)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response if isinstance(response, HttpResponse) else HttpResponse(200, response)

    async def rpc(self, url, message, *, before_send):
        return await self.request("WS", url, json_body=deepcopy(message), before_send=before_send)


class Permit:
    def __init__(self):
        self.calls = 0
        self.allowed = True

    def __call__(self):
        self.calls += 1
        if not self.allowed or self.calls > 1:
            raise CapabilityBlocked("Owner halted or permit already consumed")


def scope(venue="binance", environment="sandbox"):
    return PerpScope(venue, "linear_perpetual", environment, "synthetic-account", "USDT", "42" if venue == "grvt" else "")


def binance_market(**overrides):
    return {"symbol": "BTCUSDT", "contractType": "PERPETUAL", "status": "TRADING", "baseAsset": "BTC",
        "quoteAsset": "USDT", "marginAsset": "USDT", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"}], **overrides}


def bybit_market(**overrides):
    return {"symbol": "BTCUSDT", "contractType": "LinearPerpetual", "status": "Trading", "baseCoin": "BTC",
        "quoteCoin": "USDT", "settleCoin": "USDT", "priceFilter": {"tickSize": "0.1"},
        "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "minNotionalValue": "5"}, **overrides}


def grvt_market(**overrides):
    return {"instrument": "BTC_USDT_Perp", "kind": "PERPETUAL", "settlement_period": "PERPETUAL",
        "venues": ["ORDERBOOK"], "base": "BTC", "quote": "USDT", "tick_size": "0.1", "min_size": "0.001",
        "base_decimals": 9, "instrument_hash": "0x01", **overrides}


def order(adapter, **overrides):
    return {"scope_key": adapter.scope.key, "symbol": "BTC_USDT_Perp" if adapter.scope.venue == "grvt" else "BTCUSDT",
        "side": "buy", "quantity": "0.001",
        "limit_price": "50000" if adapter.scope.venue == "hyperliquid" else "50000.1", "time_in_force": "POST_ONLY",
        "reduce_only": False, "client_order_id": "9223372036854775809" if adapter.scope.venue == "grvt" else "MiXeD-1",
        **overrides}


def bybit(result):
    return {"retCode": 0, "result": result}


class GrvtSigner:
    def __init__(self):
        self.nonce = 2**32 - 1

    def prepare(self, selected, request, instrument, now_ns):
        return {"sub_account_id": selected.subaccount, "is_market": False,
            "time_in_force": "GOOD_TILL_TIME", "post_only": True, "reduce_only": request["reduce_only"],
            "legs": [{"instrument": request["symbol"], "size": request["quantity"],
                      "limit_price": request["limit_price"], "is_buying_asset": request["side"] == "buy"}],
            "signature": {"nonce": self.nonce, "expiration": str(now_ns + 60_000_000_000), "chain_id": "326"},
            "metadata": {"client_order_id": request["client_order_id"]}}


def grvt_adapter(transport, signer=None):
    return GrvtPerpAdapter(scope("grvt"), transport, session_headers={"Cookie": "synthetic-cookie", "X-Grvt-Account-Id": "fixture"},
        signer=signer or GrvtSigner(), clock_ns=lambda: 1_000_000_000)


def test_product_environment_capability_is_explicit_and_toss_rejected():
    with pytest.raises(CapabilityBlocked, match="DATA_ONLY"):
        PerpScope("toss", "linear_perpetual", "sandbox", "x", "USDT")
    for product in ("spot", "coin_m_perpetual", "inverse_perpetual"):
        with pytest.raises(CapabilityBlocked):
            PerpScope("binance", product, "sandbox", "x", "USDT")
    with pytest.raises(ValueError, match="sandbox/live"):
        scope(environment="testnet")
    for venue, settlement, subaccount in (("binance", "USDT", ""), ("bybit", "USDT", ""),
            ("grvt", "USDT", "42"), ("arcus", "USDG", "2"), ("hyperliquid", "USDC", "")):
        selected = PerpScope(venue, "linear_perpetual", "sandbox", "x", settlement, subaccount)
        receipt = capability_for(selected)
        assert receipt.request_mapping_complete and not receipt.code_complete
        assert not receipt.live_verified and not receipt.sandbox_verified
        assert not receipt.owner_integrated
        assert receipt.private_stream == "NATIVE_FRAME_NORMALIZER_ONLY"
        assert receipt.implementation_status == "REQUEST_MAPPING_ONLY" and len(receipt.blockers) >= 3
        assert receipt.receipt()["request_mapping_complete"] is True
        assert receipt.runtime_readiness.startswith("BLOCKED_")


def test_no_transport_without_actual_dispatch_permit_protocol():
    with pytest.raises(CapabilityBlocked, match="network-seam"):
        BinancePerpAdapter(scope(), object(), api_key="fixture", api_secret="secret")


@pytest.mark.parametrize("venue", ["binance", "bybit", "grvt"])
def test_missing_or_async_owner_permit_never_dispatches(venue):
    async def scenario():
        transport = ScriptedTransport()
        adapter = grvt_adapter(transport) if venue == "grvt" else make_perp_adapter(
            scope(venue), transport=transport, api_key="fixture", api_secret="secret")
        async def invalid_permit():
            pass
        for permit in (None, invalid_permit):
            with pytest.raises(TypeError, match="permit"):
                await adapter.submit_with_permit({}, permit)
            with pytest.raises(TypeError, match="permit"):
                await adapter.cancel_with_permit("BTCUSDT", "x", permit)
        assert not transport.calls
    asyncio.run(scenario())


def test_binance_perp_metadata_submit_delete_and_signature():
    async def scenario():
        ack = {"symbol": "BTCUSDT", "orderId": 123, "clientOrderId": "MiXeD-1", "status": "NEW", "executedQty": "0"}
        transport = ScriptedTransport({"symbols": [binance_market()]}, ack, {"orderId": 123})
        adapter = BinancePerpAdapter(scope(), transport, api_key="fixture", api_secret="secret", clock_ns=lambda: 1_000_000_000)
        metadata = await adapter.instrument_metadata("BTCUSDT")
        assert metadata["lot_size"] == "0.001" and metadata["kind"] == "linear_perpetual"
        metadata["raw"]["symbol"] = "changed"
        permit = Permit()
        result = await adapter.submit_with_permit(order(adapter), permit)
        assert result["order_id"] == "123" and permit.calls == 1
        request = transport.dispatched[1]
        assert request["url"] == "https://demo-fapi.binance.com/fapi/v1/order"
        params = dict(request["params"])
        signature = params.pop("signature")
        assert signature == hmac.new(b"secret", urlencode(params).encode(), hashlib.sha256).hexdigest()
        assert params["newClientOrderId"] == "MiXeD-1" and params["timeInForce"] == "GTX"
        assert params["positionSide"] == "BOTH" and params["reduceOnly"] == "false"
        cancel = await adapter.cancel_with_permit("BTCUSDT", "MiXeD-1", Permit())
        assert not cancel["terminal_proven"]
        assert transport.dispatched[-1]["method"] == "DELETE"
        assert all("/api/v3" not in c["url"] and "/dapi" not in c["url"] for c in transport.calls)
        with pytest.raises(CapabilityBlocked, match="already been used"):
            await adapter.submit_with_permit(order(adapter), permit)
    asyncio.run(scenario())


def test_revoke_during_transport_queue_prevents_network_mutation():
    async def scenario():
        transport = ScriptedTransport({"symbols": [binance_market()]})
        adapter = BinancePerpAdapter(scope(), transport, api_key="fixture", api_secret="secret")
        await adapter.instrument_metadata("BTCUSDT")
        permit = Permit()
        async def revoke():
            await asyncio.sleep(0)
            permit.allowed = False
        transport.before_dispatch = revoke
        with pytest.raises(CapabilityBlocked, match="halted"):
            await adapter.submit_with_permit(order(adapter), permit)
        assert len(transport.dispatched) == 1 and permit.calls == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [TimeoutError("offline"), asyncio.CancelledError(), HttpResponse(503, {})])
def test_possible_handoff_never_retries_and_keeps_unknown(failure):
    async def scenario():
        transport = ScriptedTransport({"symbols": [binance_market()]}, failure)
        adapter = BinancePerpAdapter(scope(), transport, api_key="fixture", api_secret="secret")
        await adapter.instrument_metadata("BTCUSDT")
        with pytest.raises(OutcomeUnknown):
            await adapter.submit_with_permit(order(adapter), Permit())
        assert len(transport.dispatched) == 2
    asyncio.run(scenario())


def test_decimal_precision_product_and_client_id_validation_before_permit():
    async def scenario():
        transport = ScriptedTransport({"symbols": [binance_market()]})
        adapter = BinancePerpAdapter(scope(), transport, api_key="fixture", api_secret="secret")
        await adapter.instrument_metadata("BTCUSDT")
        permit = Permit()
        for changes in ({"quantity": "0.0015"}, {"quantity": 0.001}, {"client_order_id": "a" * 37}, {"scope_key": "other"}, {"reduce_only": "false"}):
            with pytest.raises((ValueError, TypeError)):
                await adapter.submit_with_permit(order(adapter, **changes), permit)
        assert permit.calls == 0 and len(transport.dispatched) == 1
        transport.responses.append({"symbols": [binance_market(contractType="CURRENT_QUARTER")]})
        with pytest.raises(CapabilityBlocked):
            await adapter.instrument_metadata("BTCUSDT")
    asyncio.run(scenario())


def test_bybit_linear_wire_signed_exact_body_and_private_rest_same_fill_identity():
    async def scenario():
        fill = {"category": "linear", "execId": "exec-1", "symbol": "BTCUSDT", "orderId": "o-1", "orderLinkId": "MiXeD-1",
            "side": "Buy", "execQty": "0.001", "execPrice": "50000.1", "execFee": "0.02", "isMaker": True,
            "execTime": "1000", "execType": "Trade", "feeCurrency": "USDT"}
        transport = ScriptedTransport(bybit({"list": [bybit_market()]}), bybit({"orderId": "o-1", "orderLinkId": "MiXeD-1"}),
                                      bybit({"list": [fill], "nextPageCursor": "next"}))
        adapter = BybitPerpAdapter(scope("bybit"), transport, api_key="fixture", api_secret="secret", clock_ns=lambda: 1_000_000_000)
        await adapter.instrument_metadata("BTCUSDT")
        await adapter.submit_with_permit(order(adapter), Permit())
        request = transport.dispatched[1]
        payload = json.loads(request["data"])
        assert payload["category"] == "linear" and payload["positionIdx"] == 0 and payload["timeInForce"] == "PostOnly"
        assert request["headers"]["X-BAPI-SIGN"] == hmac.new(b"secret", ("1000fixture5000" + request["data"]).encode(), hashlib.sha256).hexdigest()
        page = await adapter.fills("BTCUSDT", start_ns=0, end_ns=2_000_000_000)
        assert not page["complete"] and page["cursor"]["token"] == "next"
        normalized = adapter.normalize_private_event({"topic": "execution.linear", "data": [fill]})
        assert normalized[0] == page["rows"][0]
        with pytest.raises(ValueError, match="scope/window"):
            await adapter.fills("ETHUSDT", start_ns=0, end_ns=2_000_000_000, cursor=page["cursor"])
    asyncio.run(scenario())


def test_bybit_pagination_cycle_fails_closed_and_empty_history_is_unknown():
    async def scenario():
        transport = ScriptedTransport(bybit({"list": [], "nextPageCursor": "a"}), bybit({"list": [], "nextPageCursor": "a"}),
                                      bybit({"list": []}), bybit({"list": []}))
        adapter = BybitPerpAdapter(scope("bybit"), transport, api_key="fixture", api_secret="secret")
        with pytest.raises(IncompleteHistory, match="cycle"):
            await adapter.working_orders("BTCUSDT")
        row = await adapter.order_status("BTCUSDT", "MiXeD-1")
        assert row["status"] == "UNKNOWN" and not row["terminal"] and not row["history_complete"]
    asyncio.run(scenario())


def test_grvt_uint32_nonce_uint64_client_id_and_existing_signer_composition():
    async def scenario():
        transport = ScriptedTransport({"result": grvt_market()}, {"result": {"order_id": "g-1"}}, {"result": {}})
        signer = GrvtSigner()
        adapter = grvt_adapter(transport, signer)
        await adapter.instrument_metadata("BTC_USDT_Perp")
        await adapter.submit_with_permit(order(adapter), Permit())
        signed = transport.dispatched[1]["json_body"]["order"]
        assert signed["signature"]["nonce"] == 2**32 - 1
        assert signed["metadata"]["client_order_id"] == "9223372036854775809"
        await adapter.cancel_with_permit("BTC_USDT_Perp", "9223372036854775809", Permit())
        assert transport.dispatched[-1]["json_body"]["time_to_live_ms"] == "5000"
        signer.nonce = 2**32
        permit = Permit()
        with pytest.raises(ValueError, match="uint32"):
            await adapter.submit_with_permit(order(adapter), permit)
        assert permit.calls == 0
        with pytest.raises(ValueError, match="uint64"):
            await adapter.submit_with_permit(order(adapter, client_order_id=str(2**64)), permit)
    asyncio.run(scenario())


def test_grvt_signer_composition_never_calls_legacy_transport_or_constructor():
    class Existing:
        chain_id = 326
        sub_account = "42"
        use_testnet = True
        instruments = {}
        def prepare_order(self, supplied, now_ns):
            assert supplied == {"prepared_intent": True}
            assert self.instruments["BTC_USDT_Perp"] == grvt_market()
            return {"params": {"order": {"composed": now_ns}}}
    signer = GrvtSigningComposition(Existing(), lambda row: {"prepared_intent": True})
    assert signer.prepare(scope("grvt"), {"symbol": "BTC_USDT_Perp"}, {"raw": grvt_market()}, 7) == {"composed": 7}
    with pytest.raises(CapabilityBlocked, match="scope differs"):
        signer.prepare(scope("grvt", "live"), {"symbol": "BTC_USDT_Perp"}, {"raw": grvt_market()}, 7)


def test_opposite_venue_funding_conventions_normalize_to_received_cashflow():
    async def scenario():
        grvt_row = {"sub_account_id": "42", "instrument": "BTC_USDT_Perp", "currency": "USDT", "amount": "2.5", "tx_id": "tx-1", "event_time": "1000"}
        grvt = grvt_adapter(ScriptedTransport({"result": [grvt_row], "next": ""}))
        paid = await grvt.funding("BTC_USDT_Perp", start_ns=0, end_ns=2000)
        assert paid["rows"][0]["amount"] == "-2.5"
        bybit_row = {"id": "tx-2", "symbol": "BTCUSDT", "category": "linear", "currency": "USDT", "funding": "2.5", "transactionTime": "1"}
        transport = ScriptedTransport(bybit({"list": [bybit_row]}))
        bybit_adapter = BybitPerpAdapter(scope("bybit"), transport, api_key="fixture", api_secret="secret")
        received = await bybit_adapter.funding("BTCUSDT", start_ns=0, end_ns=2_000_000)
        assert received["rows"][0]["amount"] == "2.5"
        assert "symbol" not in transport.calls[0]["params"]  # vendor transaction endpoint filters locally
    asyncio.run(scenario())


def test_account_currency_and_margin_values_are_not_invented():
    async def scenario():
        row = {"accountType": "UNIFIED", "totalEquity": "1000", "totalAvailableBalance": "900",
               "totalInitialMargin": "100", "totalMaintenanceMargin": "50"}
        adapter = BybitPerpAdapter(scope("bybit"), ScriptedTransport(bybit({"list": [row]})), api_key="fixture", api_secret="secret")
        result = await adapter.account_state()
        assert result["currency"] == "USD" and result["settlement_currency"] == "USDT"
        bad = deepcopy(row)
        bad["totalInitialMargin"] = ""
        adapter.transport.responses.append(bybit({"list": [bad]}))
        with pytest.raises(ValueError):
            await adapter.account_state()
    asyncio.run(scenario())


ADDRESS = "0x" + "11" * 20
SIGNER_ADDRESS = "0x" + "22" * 20
NOW_NS = 1_800_000_000_000_000_000
CLOID = "0x" + "ab" * 16


def arcus_market(**changes):
    return {"marketDisplayName": "BTC-USD", "marketId": 1, "status": "ONLINE",
        "tickSize": "0.1", "stepSize": "0.001", "minOrderSize": "0.001",
        "maxOrderSize": "100", "minOrderNotional": "10", "baseAsset": "BTC", "quoteAsset": "USD", **changes}


def arcus_signer():
    # The real existing prepare/sign implementation, without its constructor,
    # settings discovery, auth session, or transport. This seed is synthetic.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from execution.extended_brokers import ArcusBrokerAdapter
    from core.models import AssetClass, Side, Venue

    existing = ArcusBrokerAdapter.__new__(ArcusBrokerAdapter)
    existing.use_testnet, existing.address, existing.env = True, ADDRESS, {"ARCUS_ACCOUNT_INDEX": "2"}
    existing.markets, existing.api_secret = {}, "01" * 32
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(existing.api_secret))
    existing.api_key = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def intent_factory(row):
        return SimpleNamespace(instrument=SimpleNamespace(venue=Venue.ARCUS, asset_class=AssetClass.CRYPTO_PERP,
            std_symbol="BTCUSDT"), side=Side(row["side"]), quantity=Decimal(row["quantity"]),
            limit_price=Decimal(row["limit_price"]), client_order_id=row["client_order_id"],
            metadata={"reduce_only": row["reduce_only"],
                "time_in_force": {"POST_ONLY": "ALO", "GTC": "GTT", "IOC": "IOC", "FOK": "FOK"}[row["time_in_force"]]})

    return ArcusSigningComposition(existing, intent_factory), key.public_key()


def arcus_adapter(transport, **options):
    signer, public_key = arcus_signer()
    selected = PerpScope("arcus", "linear_perpetual", "sandbox", "synthetic-account", "USDG", "2")
    return ArcusPerpAdapter(selected, transport, account_address=ADDRESS,
        signer=signer, clock_ns=lambda: NOW_NS, **options), public_key


def arcus_order_row(**changes):
    return {"address": ADDRESS, "accountIndex": 2, "marketDisplayName": "BTC-USD", "orderId": "arcus-1",
        "clientId": "MiXeD-1", "status": "OPEN", "originalSize": "0.003", "remainingSize": "0.002",
        "updatedAt": NOW_NS // 1000, **changes}


def arcus_fill_row(**changes):
    return {"address": ADDRESS, "accountIndex": 2, "marketDisplayName": "BTC-USD", "orderId": "arcus-1",
        "tradeId": "trade-1", "clientId": "MiXeD-1", "side": "BUY", "size": "0.001",
        "price": "50000.1", "fee": "0.01", "createdAt": NOW_NS // 1000, "role": "MAKER", **changes}


def test_arcus_existing_ed25519_composition_exact_order_and_cancel_signature():
    async def scenario():
        transport = ScriptedTransport({"markets": [arcus_market()]}, {"status": 200, "result": {"orderId": "a-1"}},
            {"status": 200, "result": {}}, {"status": 200, "result": {}})
        adapter, public_key = arcus_adapter(transport)
        metadata = await adapter.instrument_metadata("BTC-USD")
        assert metadata["valuation_conversion_required"] and metadata["position_mode"] is None
        assert metadata["account_configuration_verified"] is False
        permit = Permit()
        result = await adapter.submit_with_permit(order(adapter, symbol="BTC-USD"), permit)
        assert result["client_order_id"] == "MiXeD-1" and not result["terminal"] and permit.calls == 1
        call = transport.dispatched[1]
        assert call["url"] == "wss://api.testnet.arcus.xyz/v1/ws"
        request = call["json_body"]["request"]
        payload = request["payload"]
        assert payload["timeInForce"] == "ALO" and payload["clientTime"] == str(NOW_NS)
        canonical = {"ad": ADDRESS, "ai": 2, "ct": NOW_NS, "g": int(payload["goodTilTime"]) * 1000,
            "m": 1, "op": 1, "p": 500001, "q": 1, "r": 0, "s": 0, "t": 3, "v": 1, "c": "MiXeD-1"}
        public_key.verify(bytes.fromhex(request["signature"]), json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode())
        for client_id, order_id in (("MiXeD-1", None), (None, "a-1")):
            receipt = await adapter.cancel_with_permit("BTC-USD", client_id, Permit(), order_id=order_id)
            assert receipt["acknowledged"] and not receipt["terminal_proven"]
            request = transport.dispatched[-1]["json_body"]["request"]
            canonical = {"ad": ADDRESS, "ai": 2, "ct": NOW_NS, "m": 1, "op": 2, "v": 1,
                "c" if client_id else "id": client_id or order_id}
            public_key.verify(bytes.fromhex(request["signature"]), json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode())
        assert all(c["params"] == {"market": "BTC-USD"} for c in transport.calls[:1])
    asyncio.run(scenario())


def test_arcus_account_reads_private_identity_and_history_share_event_id():
    async def scenario():
        fill = arcus_fill_row()
        transport = ScriptedTransport({"address": ADDRESS, "accountIndex": 2, "equity": "100", "freeCollateral": "80", "sequenceNumber": 4},
            {"positions": {"1": {"marketDisplayName": "BTC-USD", "size": "0.002", "averageEntryPrice": "50000", "marginMode": "CROSS", "marginUsed": "20"}}},
            {"orders": [arcus_order_row()]}, {"fills": [fill]}, {"orders": []})
        adapter, _ = arcus_adapter(transport)
        account = await adapter.account_state()
        assert account["currency"] == "USD" and account["settlement_currency"] == "USDG" and not account["margin_totals_proven"]
        assert (await adapter.positions("BTC-USD"))[0]["quantity"] == "0.002"
        assert (await adapter.working_orders("BTC-USD"))[0]["cumulative_quantity"] == "0.001"
        page = await adapter.fills("BTC-USD", start_ns=NOW_NS - 1000, end_ns=NOW_NS + 1000)
        frame = {"channel": "userFills", "id": ADDRESS, "accountIndex": 2, "type": "channel_data", "contents": fill}
        native = adapter.normalize_private_event(frame)[0]
        assert native["event_id"] == page["rows"][0]["event_id"] == "arcus-1:trade-1"
        assert not native["snapshot_is_complete_history"]
        unknown = await adapter.order_status("BTC-USD", "MiXeD-1")
        assert unknown["status"] == "UNKNOWN" and not unknown["terminal"]
        for call in transport.calls:
            assert call["params"]["address"] == ADDRESS and call["params"]["accountIndex"] == 2
        assert not adapter.normalize_private_event({"type": "gap", "reason": "dropped"})[0]["ready"]
        with pytest.raises(CapabilityBlocked, match="another account"):
            adapter.normalize_private_event({**frame, "id": SIGNER_ADDRESS})
        with pytest.raises(CapabilityBlocked, match="subaccount"):
            adapter.normalize_private_event({**frame, "accountIndex": 3})
    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["scope", "client", "quantity", "tier", "cancel_scope"])
def test_arcus_validation_blocks_before_mutation_permit(fault):
    async def scenario():
        transport = ScriptedTransport({"markets": [arcus_market(tickTiers=[{"upToPrice": "100000", "tick": "1"}] if fault == "tier" else [])]})
        adapter, _ = arcus_adapter(transport)
        await adapter.instrument_metadata("BTC-USD")
        permit = Permit()
        if fault == "scope":
            adapter.signer.adapter.env["ARCUS_ACCOUNT_INDEX"] = "3"
        changes = {"client_order_id": "bad:id"} if fault == "client" else {"quantity": "101"} if fault == "quantity" else {}
        if fault == "cancel_scope":
            original = adapter.signer.cancel
            def changed(*args, **kwargs):
                message = original(*args, **kwargs)
                message["request"]["payload"]["address"] = SIGNER_ADDRESS
                return message
            adapter.signer.cancel = changed
        with pytest.raises((ValueError, CapabilityBlocked)):
            if fault == "cancel_scope":
                await adapter.cancel_with_permit("BTC-USD", "MiXeD-1", permit)
            else:
                await adapter.submit_with_permit(order(adapter, symbol="BTC-USD", **changes), permit)
        assert permit.calls == 0 and len(transport.calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [TimeoutError("offline"), asyncio.CancelledError(), {"status": "broken"}, {"status": 200}, {"status": 503}])
def test_arcus_possible_handoff_unknown_does_not_retry(failure):
    async def scenario():
        transport = ScriptedTransport({"markets": [arcus_market()]}, failure)
        adapter, _ = arcus_adapter(transport)
        await adapter.instrument_metadata("BTC-USD")
        with pytest.raises(OutcomeUnknown):
            await adapter.submit_with_permit(order(adapter, symbol="BTC-USD"), Permit())
        assert len(transport.dispatched) == 2 and not transport.responses
    asyncio.run(scenario())


def test_arcus_inclusive_pagination_and_cross_account_funding_fail_closed():
    async def scenario():
        fills = [arcus_fill_row(tradeId=str(i)) for i in range(1000)]
        transport = ScriptedTransport({"fills": fills}, {"fills": fills},
            {"fundingPayments": [{"address": SIGNER_ADDRESS, "marketId": 1, "marketDisplayName": "BTC-USD", "time": NOW_NS // 1000, "payment": "1"}]})
        adapter, _ = arcus_adapter(transport)
        page = await adapter.fills("BTC-USD", start_ns=NOW_NS - 1000, end_ns=NOW_NS + 1000)
        assert not page["complete"]
        with pytest.raises(IncompleteHistory, match="cycle"):
            await adapter.fills("BTC-USD", start_ns=NOW_NS - 1000, end_ns=NOW_NS + 1000, cursor=page["cursor"])
        assert transport.calls[1]["params"]["to"] == NOW_NS // 1000
        with pytest.raises(CapabilityBlocked, match="another account"):
            await adapter.funding("BTC-USD", start_ns=NOW_NS - 1000, end_ns=NOW_NS + 1000)
    asyncio.run(scenario())


def hyperliquid_adapter(transport, nonce_source, *, alias="synthetic-account", address=ADDRESS, vault_address=None):
    signatures = []
    def official_sign(wallet, action, vault, nonce, expires, mainnet):
        signatures.append((wallet.address, deepcopy(action), vault, nonce, expires, mainnet))
        return {"r": "0x1", "s": "0x2", "v": 27}
    signer = HyperliquidSdkSigningComposition(SimpleNamespace(address=SIGNER_ADDRESS), sign_l1_action=official_sign)
    selected = PerpScope("hyperliquid", "linear_perpetual", "sandbox", alias, "USDC")
    return HyperliquidPerpAdapter(selected, transport, account_address=address, signer=signer,
        nonce_source=nonce_source, vault_address=vault_address, clock_ns=lambda: NOW_NS), signatures


def nonce_store(tmp_path):
    from execution.execution_nonce import SignerNonceStore
    return SignerNonceStore(tmp_path, environment="sandbox", max_signers=8, max_db_bytes=1048576, max_wal_bytes=1048576)


def hyperliquid_ack(status):
    return {"status": "ok", "response": {"type": "order", "data": {"statuses": [status]}}}


def hyperliquid_fill(**changes):
    return {"coin": "BTC", "oid": 7, "tid": 8, "side": "B", "sz": "0.001", "px": "50000.1",
        "fee": "0.01", "feeToken": "USDC", "time": NOW_NS // 10**6, "crossed": False, **changes}


def test_hyperliquid_sdk_composition_durable_signer_nonce_shared_across_accounts_restart(tmp_path):
    async def scenario():
        with nonce_store(tmp_path) as store:
            transport = ScriptedTransport({"universe": [{"name": "ETH", "szDecimals": 4}, {"name": "BTC", "szDecimals": 3}]},
                hyperliquid_ack({"resting": {"oid": 7}}), hyperliquid_ack("success"))
            adapter, signatures = hyperliquid_adapter(transport, store, vault_address=ADDRESS)
            metadata = await adapter.instrument_metadata("BTC")
            assert metadata["asset_id"] == 1 and metadata["margin_mode"] is None
            assert not metadata["account_configuration_verified"]
            permit = Permit()
            receipt = await adapter.submit_with_permit(order(adapter, symbol="BTC", client_order_id=CLOID), permit)
            assert receipt["order_id"] == "7" and not receipt["terminal"] and permit.calls == 1
            wire = transport.dispatched[1]["json_body"]
            assert wire["action"] == {"type": "order", "orders": [{"a": 1, "b": True, "p": "50000", "s": "0.001",
                "r": False, "t": {"limit": {"tif": "Alo"}}, "c": CLOID}], "grouping": "na"}
            assert signatures[0] == (SIGNER_ADDRESS, wire["action"], ADDRESS, wire["nonce"], wire["expiresAfter"], False)
            cancel = await adapter.cancel_with_permit("BTC", CLOID, Permit())
            assert not cancel["terminal_proven"]
            second = transport.dispatched[-1]["json_body"]
            assert second["action"] == {"type": "cancelByCloid", "cancels": [{"asset": 1, "cloid": CLOID}]}
            assert second["nonce"] > wire["nonce"]
        with nonce_store(tmp_path) as reopened:
            transport = ScriptedTransport({"universe": [{"name": "BTC", "szDecimals": 3}]}, hyperliquid_ack({"resting": {"oid": 9}}))
            other, _ = hyperliquid_adapter(transport, reopened, alias="another-account", address="0x" + "33" * 20)
            await other.instrument_metadata("BTC")
            await other.submit_with_permit(order(other, symbol="BTC", client_order_id=CLOID), Permit())
            assert transport.dispatched[-1]["json_body"]["nonce"] > second["nonce"]
    asyncio.run(scenario())


def test_hyperliquid_account_query_uses_account_not_signer_and_fill_private_identity(tmp_path):
    async def scenario():
        fill = hyperliquid_fill()
        account = {"marginSummary": {"accountValue": "100", "totalMarginUsed": "20"}, "withdrawable": "80",
            "crossMaintenanceMarginUsed": "10", "assetPositions": [{"position": {"coin": "BTC", "szi": "-0.001", "entryPx": "50000", "leverage": {"type": "isolated"}}}]}
        transport = ScriptedTransport({"universe": [{"name": "BTC", "szDecimals": 3}]}, account, account,
            [{"coin": "BTC", "oid": 7, "cloid": CLOID, "sz": "0.001"}], [fill], {"status": "unknownOid"})
        with nonce_store(tmp_path) as store:
            adapter, _ = hyperliquid_adapter(transport, store)
            await adapter.instrument_metadata("BTC")
            assert (await adapter.account_state())["currency"] == "USDC"
            assert (await adapter.positions("BTC"))[0]["margin_mode"] == "isolated"
            assert (await adapter.working_orders("BTC"))[0]["client_order_id"] == CLOID
            page = await adapter.fills("BTC", start_ns=NOW_NS - 10**6, end_ns=NOW_NS + 10**6)
            native = adapter.normalize_private_event({"channel": "userFills", "data": {"user": ADDRESS, "isSnapshot": True, "fills": [fill]}})[0]
            assert native["event_id"] == page["rows"][0]["event_id"] == "BTC:7:8"
            assert native["snapshot"] and not native["snapshot_is_complete_history"]
            assert not page["history_complete"] and page["retention_limited"] and not page["absence_resolves_unknown"]
            assert (await adapter.order_status("BTC", CLOID))["status"] == "UNKNOWN"
            assert all(c["json_body"]["user"] == ADDRESS for c in transport.calls[1:])
            with pytest.raises(CapabilityBlocked, match="account address"):
                adapter.normalize_private_event({"channel": "userFills", "data": {"user": SIGNER_ADDRESS, "fills": [fill]}})
            assert not adapter.normalize_private_event({"channel": "disconnected"})[0]["ready"]
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [TimeoutError("offline"), asyncio.CancelledError(), {"status": "ok"},
    {"status": "ok", "response": {"data": {"statuses": None}}}, hyperliquid_ack({})])
def test_hyperliquid_handoff_failures_unknown_no_retry(tmp_path, failure):
    async def scenario():
        transport = ScriptedTransport({"universe": [{"name": "BTC", "szDecimals": 3}]}, failure)
        with nonce_store(tmp_path) as store:
            adapter, _ = hyperliquid_adapter(transport, store)
            await adapter.instrument_metadata("BTC")
            with pytest.raises(OutcomeUnknown):
                await adapter.submit_with_permit(order(adapter, symbol="BTC", client_order_id=CLOID), Permit())
            assert len(transport.dispatched) == 2 and not transport.responses
    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [{"time_in_force": "FOK"}, {"client_order_id": "0x" + "AB" * 16},
    {"limit_price": "50000.12"}, {"quantity": "0.0015"}, {"scope_key": "other"}])
def test_hyperliquid_invalid_product_wire_values_never_use_permit(tmp_path, changes):
    async def scenario():
        transport = ScriptedTransport({"universe": [{"name": "BTC", "szDecimals": 3}]})
        with nonce_store(tmp_path) as store:
            adapter, _ = hyperliquid_adapter(transport, store)
            await adapter.instrument_metadata("BTC")
            permit = Permit()
            with pytest.raises((ValueError, CapabilityBlocked)):
                await adapter.submit_with_permit(order(adapter, symbol="BTC", client_order_id=CLOID) | changes, permit)
            assert not permit.calls and len(transport.calls) == 1
    asyncio.run(scenario())


def test_hyperliquid_inclusive_capped_history_cannot_claim_complete_or_skip_ties(tmp_path):
    async def scenario():
        fills = [hyperliquid_fill(tid=i) for i in range(2000)]
        transport = ScriptedTransport({"universe": [{"name": "BTC", "szDecimals": 3}]}, fills, fills)
        with nonce_store(tmp_path) as store:
            adapter, _ = hyperliquid_adapter(transport, store)
            await adapter.instrument_metadata("BTC")
            page = await adapter.fills("BTC", start_ns=NOW_NS - 10**6, end_ns=NOW_NS + 10**6)
            assert not page["complete"] and not page["history_complete"]
            with pytest.raises(IncompleteHistory, match="cycle"):
                await adapter.fills("BTC", start_ns=NOW_NS - 10**6, end_ns=NOW_NS + 10**6, cursor=page["cursor"])
            query = transport.calls[-1]["json_body"]
            assert query["startTime"] == NOW_NS // 10**6 and query["aggregateByTime"] is False
    asyncio.run(scenario())


@pytest.mark.parametrize("venue", ["arcus", "hyperliquid"])
def test_new_venue_halt_during_transport_wait_prevents_mutation(tmp_path, venue):
    async def scenario():
        with nonce_store(tmp_path) as store:
            transport = ScriptedTransport({"markets": [arcus_market()]} if venue == "arcus" else
                {"universe": [{"name": "BTC", "szDecimals": 3}]})
            adapter, _ = arcus_adapter(transport) if venue == "arcus" else hyperliquid_adapter(transport, store)
            symbol = "BTC-USD" if venue == "arcus" else "BTC"
            client_id = "MiXeD-1" if venue == "arcus" else CLOID
            await adapter.instrument_metadata(symbol)
            permit = Permit()
            async def halt():
                await asyncio.sleep(0)
                permit.allowed = False
            transport.before_dispatch = halt
            with pytest.raises(CapabilityBlocked, match="halted"):
                await adapter.submit_with_permit(order(adapter, symbol=symbol, client_order_id=client_id), permit)
            assert len(transport.dispatched) == 1 and permit.calls == 1
    asyncio.run(scenario())


def test_hyperliquid_funding_private_snapshot_and_rest_preserve_signed_cashflow(tmp_path):
    async def scenario():
        funding = {"time": NOW_NS // 10**6, "delta": {"type": "funding", "coin": "BTC", "usdc": "-0.25"}}
        transport = ScriptedTransport([funding])
        with nonce_store(tmp_path) as store:
            adapter, _ = hyperliquid_adapter(transport, store)
            page = await adapter.funding("BTC", start_ns=NOW_NS - 10**6, end_ns=NOW_NS + 10**6)
            row = adapter.normalize_private_event({"channel": "userFundings", "data": {"user": ADDRESS,
                "isSnapshot": True, "fundings": [funding]}})[0]
            assert row["amount"] == page["rows"][0]["amount"] == "-0.25"
            assert row["event_id"] == page["rows"][0]["event_id"]
            assert row["snapshot"] and not row["snapshot_is_complete_history"]
            assert not page["history_complete"] and not page["absence_resolves_unknown"]
    asyncio.run(scenario())


def test_hyperliquid_nonce_protocol_reuse_and_vault_query_binding_fail_closed(tmp_path):
    class Nonce:
        durable_nonce_protocol = 1
        def reserve(self, key, now_ms):
            assert key == "sandbox:" + SIGNER_ADDRESS
            return now_ms

    async def scenario():
        transport = ScriptedTransport({"universe": [{"name": "BTC", "szDecimals": 3}]}, hyperliquid_ack({"resting": {"oid": 7}}))
        adapter, _ = hyperliquid_adapter(transport, Nonce())
        await adapter.instrument_metadata("BTC")
        await adapter.submit_with_permit(order(adapter, symbol="BTC", client_order_id=CLOID), Permit())
        permit = Permit()
        with pytest.raises(CapabilityBlocked, match="nonce"):
            await adapter.submit_with_permit(order(adapter, symbol="BTC", client_order_id=CLOID), permit)
        assert not permit.calls and len(transport.calls) == 2
        with pytest.raises(CapabilityBlocked, match="persisted"):
            hyperliquid_adapter(transport, object())
        with pytest.raises(ValueError, match="vaultAddress"):
            hyperliquid_adapter(transport, Nonce(), vault_address=SIGNER_ADDRESS)
    asyncio.run(scenario())
