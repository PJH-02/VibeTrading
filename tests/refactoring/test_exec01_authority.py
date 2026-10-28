"""Focused owner guards for the existing KIS ETF journal handoff."""
from decimal import Decimal, localcontext
import hashlib
import json
import time

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from execution.execution_ledger import AccountScope
from execution.process_lease import ProcessLease
from execution.execution_owner import ExecutionOwner, GateBlocked
from execution.venues.registry import Capabilities, RouteSpec, VenueRegistry
from portfolio.etf_journal import EtfJournal


SCOPE = AccountScope("kis", "fixture-account", "cash", "mock")


class ScriptedKisCash:
    name = "kis"
    mode = "mock"
    scope = SCOPE

    def __init__(self):
        self.posts = 0

    async def submit_with_permit(self, intent, *, permit):
        permit()
        self.posts += 1
        return {"ODNO": "native-" + intent.intent_id}


def _intent(identity, strategy, side=Side.BUY, quantity=10, *, symbol="069500"):
    expires = time.time_ns() + 60_000_000_000
    metadata = {
        "account_scope": "mock", "direction": 1, "safety_stop": None,
        "replacement_for": None, "valid_until_ts_ns": expires,
        "intent_quote": {"bid": "99", "ask": "100", "received_ns": time.time_ns(),
                         "intent_ns": time.time_ns() - 1},
        "source_provider": "fixture-source",
    }
    return OrderIntent(
        intent_id=identity, job_id=strategy, signal_id="signal-" + identity,
        client_order_id="client-" + identity, destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF,
            raw_symbol=symbol, std_symbol=symbol, currency="KRW", exchange="KRX"),
        side=side, quantity=Decimal(quantity), limit_price=Decimal(100), metadata=metadata)


def _stage(journal, intent):
    payload = intent.model_dump_json()
    evidence = {
        "contract": "etf-dispatch-v1",
        "intent_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "account_scope": "mock", "strategy": intent.job_id,
        "signal_id": intent.signal_id,
        "valid_until_ts_ns": intent.metadata["valid_until_ts_ns"],
        "quote": intent.metadata["intent_quote"],
        "source_provider": "fixture-source",
    }
    return journal.stage(intent, "20261029", 1, None,
                         dispatch_evidence=evidence, require_evidence=True)


def _route(adapter, *, reduce=False):
    route = RouteSpec("kis-mock", "kis", "krx_cash", "cash", "mock", SCOPE,
                      "native-kis-mock-account")
    registry = VenueRegistry(max_routes=2, max_aliases=2, max_fact_bytes=4096)
    capabilities = Capabilities({"cash"}, {"mock"}, {"GTC"}, False, False,
                                False, owner_enforced_reduce_only=reduce)
    registry.register(route, adapter, {"submit": "submit_with_permit"}, capabilities,
        lambda: {"open_orders": 0, "unknown_orders": 0, "unresolved_fills": 0,
                 "account_dependencies": 0, "strategy_aliases": 0, "rollback_refs": 0})
    return registry


def _cash_proof(journal, *, cash="100000", scope=SCOPE.key):
    rows = journal.db.execute(
        "SELECT intent_id,side,status,quantity,filled,notional,reserve,"
        "cancel_requested,cancel_confirmed FROM orders "
        "WHERE account_scope='mock' ORDER BY intent_id").fetchall()
    body = [{"intent_id": str(row["intent_id"]), "status": str(row["status"]),
             "side": str(row["side"]), "reserve": str(row["reserve"] or "0"),
             "quantity": int(row["quantity"]), "filled": int(row["filled"] or 0),
             "notional": str(row["notional"] or "0"),
             "cancel_requested": int(row["cancel_requested"] or 0),
             "cancel_confirmed": int(row["cancel_confirmed"] or 0)} for row in rows]
    version = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"quality": "VALID", "account_scope": scope,
            "observed_ns": time.time_ns(), "currency": "KRW",
            "product_currency": "KRW", "orderable_cash": cash,
            "journal_fact_version": version}


def _journal(tmp_path):
    journal = EtfJournal(tmp_path / "journal.sqlite3")
    journal.initialize(Decimal("6000"), "fixture-account")
    journal.bind_order_feedback_scope("mock", "fixture-account")
    return journal


@pytest.mark.asyncio
async def test_static_journal_buy_reserves_shared_physical_cash_once(tmp_path):
    journal = _journal(tmp_path)
    adapter = ScriptedKisCash()
    registry = _route(adapter)
    try:
        other = _intent("other-buy", "KP")
        current = _intent("current-buy", "C3")
        assert _stage(journal, other)
        assert _stage(journal, current)
        assert sum(Decimal(row[0]) for row in journal.db.execute(
            "SELECT reserve FROM orders WHERE side='buy'")) == Decimal("2010.000")

        async def dispatch(cash):
            proof = _cash_proof(journal, cash=cash)
            return await ExecutionOwner.dispatch_journal_intent(
                journal=journal, registry=registry, route_id="kis-mock", intent=current,
                verify=lambda: proof, timeout_seconds=1)

        with pytest.raises(GateBlocked, match="exceed fresh orderable cash"):
            await dispatch("2009.999")
        assert journal.dispatch_state(current.intent_id) == "PREPARED"
        assert adapter.posts == 0
        result = await dispatch("2010")
        assert result["ODNO"] == "native-current-buy"
        assert adapter.posts == 1
    finally:
        journal.db.close()


@pytest.mark.asyncio
async def test_static_journal_cash_fact_version_catches_late_terminal_buy_fill(tmp_path):
    journal = _journal(tmp_path)
    adapter = ScriptedKisCash()
    registry = _route(adapter)
    try:
        old = _intent("old-cancelled-buy", "KP", quantity=4)
        assert _stage(journal, old)
        journal.accepted(old.intent_id, {"ODNO": "old-native"})
        journal.apply_status(old.intent_id,
            {"tot_ccld_qty": "0", "tot_ccld_amt": "0", "rmn_qty": "4", "cncl_yn": "Y"})
        current = _intent("current-after-cancel", "C3")
        assert _stage(journal, current)
        observed_cash = _cash_proof(journal)

        # A late fill changes a terminal row after the native cash snapshot.
        journal.apply_status(old.intent_id,
            {"tot_ccld_qty": "1", "tot_ccld_amt": "100", "rmn_qty": "3", "cncl_yn": "Y"})
        with pytest.raises(GateBlocked, match="Local journal facts changed"):
            await ExecutionOwner.dispatch_journal_intent(
                journal=journal, registry=registry, route_id="kis-mock", intent=current,
                verify=lambda: observed_cash, timeout_seconds=1)
        assert adapter.posts == 0
        assert journal.dispatch_state(current.intent_id) == "PREPARED"
    finally:
        journal.db.close()


@pytest.mark.asyncio
async def test_static_journal_sell_uses_exact_instrument_key_and_excludes_current(tmp_path):
    journal = _journal(tmp_path)
    adapter = ScriptedKisCash()
    registry = _route(adapter, reduce=True)
    try:
        buy = _intent("filled-buy", "C3", quantity=4)
        assert _stage(journal, buy)
        journal.accepted(buy.intent_id, {"ODNO": "buy-1"})
        journal.apply_status(buy.intent_id,
            {"tot_ccld_qty": "4", "tot_ccld_amt": "400", "rmn_qty": "0"})
        prior = _intent("prior-sell", "C3", Side.SELL, quantity=1)
        current = _intent("current-sell", "C3", Side.SELL, quantity=2)
        assert _stage(journal, prior)
        assert _stage(journal, current)
        key = current.instrument.key

        def verifier():
            return {"quality": "VALID", "account_scope": SCOPE.key,
                "observed_ns": time.time_ns(),
                "physical_holdings": {key: "10"},
                "aggregate_pending_sell": {key: "1"}}

        result = await ExecutionOwner.dispatch_journal_intent(
            journal=journal, registry=registry, route_id="kis-mock", intent=current,
            verify=verifier, timeout_seconds=1)
        assert result["ODNO"] == "native-current-sell"
        assert adapter.posts == 1
    finally:
        journal.db.close()
"""All canonical linear adapters pass the same owner submit permit in fixtures."""
from decimal import Decimal

import pytest

from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from execution.execution_ledger import AccountScope
from execution.execution_owner import ExecutionBudgets, ExecutionOwner
from execution.execution_ledger import ExecutionLedger
from execution.perp_execution import BybitPerpAdapter, PerpScope
from execution.trading_contracts import ActionKind, ExecutionAction, StrategyActionPlan, TradingMode
from execution.venues.registry import AliasBinding, Capabilities, RouteSpec, VenueRegistry
from runtime.runtime_control import GateBlocked, RuntimeControl
from test_exec01_native import NOW, evidence, open_gates, readiness
import test_perp_execution as perp_fixtures


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["bybit", "grvt", "arcus", "hyperliquid"])
async def test_canonical_perpetual_uses_same_owner_permit(venue, tmp_path):
    if venue == "bybit":
        symbol, client_id = "BTCUSDT", "bybit-owner-1"
        transport = perp_fixtures.ScriptedTransport(
            perp_fixtures.bybit({"list": [perp_fixtures.bybit_market()]}),
            perp_fixtures.bybit({"orderId": "bybit-native-1", "orderLinkId": client_id}))
        adapter = BybitPerpAdapter(perp_fixtures.scope("bybit"), transport,
                                   api_key="fixture", api_secret="fixture",
                                   clock_ns=lambda: NOW)
    elif venue == "grvt":
        symbol, client_id = "BTC_USDT_Perp", "9223372036854775809"
        transport = perp_fixtures.ScriptedTransport(
            {"result": perp_fixtures.grvt_market()}, {"result": {"order_id": "grvt-native-1"}})
        adapter = perp_fixtures.grvt_adapter(transport)
    elif venue == "arcus":
        symbol, client_id = "BTC-USD", "arcus-owner-1"
        transport = perp_fixtures.ScriptedTransport(
            {"markets": [perp_fixtures.arcus_market()]},
            {"status": 200, "result": {"orderId": "arcus-native-1"}})
        adapter, _ = perp_fixtures.arcus_adapter(transport)
    else:
        symbol, client_id = "BTC", perp_fixtures.CLOID
        transport = perp_fixtures.ScriptedTransport(
            {"universe": [{"name": "ETH", "szDecimals": 4}, {"name": "BTC", "szDecimals": 3}]},
            perp_fixtures.hyperliquid_ack({"resting": {"oid": 7}}))
        with perp_fixtures.nonce_store(tmp_path) as nonce_store:
            adapter, _ = perp_fixtures.hyperliquid_adapter(transport, nonce_store)
            await _submit_owner_fixture(adapter, transport, venue, symbol, client_id, tmp_path)
        return
    await _submit_owner_fixture(adapter, transport, venue, symbol, client_id, tmp_path)


async def _submit_owner_fixture(adapter, transport, venue, symbol, client_id, tmp_path):
    native_metadata = await adapter.instrument_metadata(symbol)
    native_id = "physical-fixture-" + venue
    scope = AccountScope(venue, adapter.scope.account_alias, "linear_perpetual", "sandbox")
    route = RouteSpec("canonical-" + venue, venue, "linear", "linear_perpetual",
                      "sandbox", scope, native_id)
    registry = VenueRegistry(max_routes=2, max_aliases=4, max_fact_bytes=8192)
    post_only = venue == "grvt"
    capabilities = Capabilities({"linear_perpetual"}, {"sandbox"}, {"GTC", "POST_ONLY"},
                                True, True, False)

    def native_facts(requested_symbol):
        assert requested_symbol == symbol
        product = dict(native_metadata)
        product.update(position_mode="net", margin_mode="isolated")
        if venue == "grvt":
            assert (product["quote_currency"], product["settlement_currency"],
                    product["collateral_currency"]) == ("USDT", "USDT", "USDT"), product
        normalized = {
            "scope_key": adapter.scope.key, "venue": venue,
            "product": "linear_perpetual", "environment": "sandbox",
            "currency": adapter.scope.settlement_currency,
            "settlement_currency": adapter.scope.settlement_currency,
            "equity": "1000000", "available": "1000000",
            "initial_margin": "0", "maintenance_margin": "0",
            "raw": {"fixture_account_state": "scripted"},
        }
        return {
            "account_facts": {
                "normalized": normalized, "observed_ns": NOW,
                "account_version": "fixture-account-v1",
                "reconciliation_state": "RECONCILED", "unknown": False,
                "position_mode": "net", "margin_mode": "isolated",
                "native_identity": {
                    "native_account_id": native_id,
                    "authenticated_session_id": "scripted-session-" + venue,
                    "scope_key": adapter.scope.key,
                    "source": "authenticated_native_session",
                    "permissions": ("account_read", "trade"),
                },
            },
            "product_specs": {symbol: product},
            "taker_fee_rate": "0.001", "max_age_ns": 1_000_000_000,
        }

    registry.bind_existing_adapter(route, adapter, reference_provider=lambda: {
        "open_orders": 0, "unknown_orders": 0, "unresolved_fills": 0,
        "reconciliation_sessions": 0, "active_intents": 0,
        "active_reservations": 0, "active_signers": 0,
        "rollback_refs": 0, "strategy_aliases": 0,
    }, capabilities=capabilities, native_facts_provider=native_facts)
    registry.register_aliases((AliasBinding(
        "alpha", venue + "-perp", route.route_id, symbol, "linear",
        "linear_perpetual", "sandbox", scope),))
    ledger = ExecutionLedger(tmp_path / (venue + "-owner.sqlite3"), scope,
        max_intents=8, max_evidence_bytes=16384, max_fills=8,
        authority_root=tmp_path / (venue + "-lease"), native_account_id=native_id)
    owner = ExecutionOwner(ledger, RuntimeControl(ledger), object(),
        ExecutionBudgets(Decimal("10000000"), Decimal("100000000"), 4,
                         10_000_000_000, 1.0, 1.0), clock=lambda: NOW)
    open_gates(owner, "alpha")
    instrument_venue = Venue(venue)
    intent = OrderIntent(
        intent_id="intent-" + venue, job_id="alpha", signal_id="signal-" + venue,
        destination=OrderDestination.REAL, client_order_id=client_id,
        instrument=InstrumentId(venue=instrument_venue,
            asset_class=AssetClass.CRYPTO_PERP, raw_symbol=symbol, std_symbol=symbol),
        side=Side.BUY, quantity=Decimal("0.001"),
        limit_price=Decimal("50000") if venue == "hyperliquid" else Decimal("50000.1"),
        metadata={"account_id": scope.account, "environment": "sandbox"})
    action = ExecutionAction(action_id="action-" + venue, action=ActionKind.NEW,
        scope=scope, strategy_id="alpha", config_version="cfg-1", mode=TradingMode.SANDBOX,
        expiry_ns=NOW + 1_000_000_000, intent=intent, time_in_force="GTC",
        post_only=post_only)
    try:
        if venue == "arcus":
            # Arcus currently reports USD quote and USDG settlement with no
            # proven conversion or full margin totals. New exposure must stay
            # blocked; this must not disable factual recovery for an older
            # already-owned order.
            with pytest.raises(GateBlocked, match="cross-currency, FX, or wrong-collateral"):
                await owner.dispatch_plan(StrategyActionPlan(
                    "plan-" + venue, "alpha", "cfg-1", NOW, (action,)), registry=registry,
                    readiness=readiness(owner, scope, intent.instrument.key),
                    evidence=evidence(owner, "alpha", "sandbox"))
            assert owner.ledger.get(intent.intent_id) is None

            # Seed a durable previously accepted native order to exercise only
            # owned read/cancel/reconcile paths, which need no new-risk margin.
            ev = evidence(owner, "alpha", "sandbox")
            owner.ledger.authorize(
                intent, evidence=ev, expiry_ns=action.expiry_ns,
                epochs=ev["expected_epochs"], reserve_unit=intent.limit_price,
                route_id=route.route_id, strategy_id="alpha", config_version="cfg-1",
                attempt_id=intent.intent_id + ":attempt:1", action="NEW")
            owner.ledger.mark_handoff(intent.intent_id)
            owner.ledger.bind_native_order(intent.intent_id, "arcus-native-1")
            owner.ledger.outcome(intent.intent_id, "SUBMITTED", {})
            registry.restore_owned(route.route_id, [{
                "identities": [client_id, "arcus-native-1"],
                "strategy_id": "alpha", "account_scope": scope,
            }])
            cancel = ExecutionAction(action_id="cancel-arcus", action=ActionKind.CANCEL,
                scope=scope, strategy_id="alpha", config_version="cfg-1",
                mode=TradingMode.SANDBOX, expiry_ns=NOW + 1_000_000_000,
                target_order_id=client_id)
            open_row = perp_fixtures.arcus_order_row(
                orderId="arcus-native-1", clientId=client_id, originalSize="0.001",
                remainingSize="0.001", updatedAt=NOW // 1000)
            transport.responses.insert(0, {"orders": [open_row]})
            queried = await owner.query_order(cancel, registry=registry)
            assert queried["status"] == "OPEN"
            cancel_result = await owner.cancel_action(cancel, registry=registry,
                readiness=readiness(owner, scope, intent.instrument.key))
            assert cancel_result["status"] == "CANCEL_PENDING"
            closed_row = dict(open_row, status="CANCELED", updatedAt=(NOW // 1000) + 1)
            transport.responses.insert(0, {"orders": [closed_row]})
            reconciled = await owner.reconcile_order(cancel, registry=registry)
            assert reconciled["status"] == "CANCELED"
            assert owner.ledger.get(intent.intent_id)["state"] == "CANCELLED"
            assert any(call["method"] == "WS" for call in transport.dispatched)
            return

        result = await owner.dispatch_plan(StrategyActionPlan(
            "plan-" + venue, "alpha", "cfg-1", NOW, (action,)), registry=registry,
            readiness=readiness(owner, scope, intent.instrument.key),
            evidence=evidence(owner, "alpha", "sandbox"))
        assert result["results"][0]["status"] == "SUBMITTED", result
        assert owner.ledger.get(intent.intent_id)["state"] == "SUBMITTED"
        assert owner.ledger.get(intent.intent_id)["route_id"] == route.route_id
        assert transport.dispatched[-1]
    finally:
        await owner.close()


def _feedback_owner(tmp_path):
    scope = AccountScope("binance", "shared-feedback-account", "linear_perpetual", "sandbox")
    ledger = ExecutionLedger(tmp_path / "feedback-owner.sqlite3", scope, max_intents=16,
        max_evidence_bytes=8192, max_fills=16, authority_root=tmp_path / "feedback-lease",
        native_account_id="shared-physical-account")
    owner = ExecutionOwner(ledger, RuntimeControl(ledger), object(),
        ExecutionBudgets(Decimal("1000000"), Decimal("10000000"), 8,
                         10_000_000_000, 1.0, 1.0), clock=lambda: NOW)
    return owner, scope


def _native_fill_order(owner, scope, identity, strategy, symbol, side, fill_id,
                       *, price="100", quantity="2", fill_quantity="1"):
    from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Venue
    intent = OrderIntent(intent_id=identity, job_id=strategy, signal_id="signal:" + identity,
        client_order_id="client:" + identity, destination=OrderDestination.REAL,
        instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
            raw_symbol=symbol, std_symbol=symbol), side=side,
        quantity=Decimal(quantity), limit_price=Decimal(price),
        metadata={"account_id": scope.account, "environment": scope.environment})
    expiry = NOW + 1_000_000_000
    owner.ledger.authorize(intent, evidence={"fixture": "durable-source-order"},
        expiry_ns=expiry, epochs={"global": 1}, reserve_unit=Decimal(price),
        route_id="binance-linear", strategy_id=strategy, config_version="cfg-1",
        attempt_id=identity + ":attempt:1", action="NEW")
    owner.ledger.mark_handoff(identity)
    native_order = "native:" + identity
    owner.ledger.bind_native_order(identity, native_order)
    owner.ledger.outcome(identity, "SUBMITTED", {"order_id": native_order})
    action = ExecutionAction(action_id="fill-action:" + identity, action=ActionKind.CANCEL,
        scope=scope, strategy_id=strategy, config_version="cfg-1",
        mode=TradingMode.SANDBOX, expiry_ns=expiry, target_order_id=native_order)
    owner.apply_fill(action, fill_id=fill_id, quantity=fill_quantity, price=price,
                     fee="0", order_id=native_order)
    return action


@pytest.mark.asyncio
async def test_feedback_context_separates_strategy_and_account_journal_positions(tmp_path):
    owner, scope = _feedback_owner(tmp_path)
    observed = {}
    try:
        _native_fill_order(owner, scope, "alpha-long", "alpha", "BTCUSDT", Side.BUY,
                           "alpha-fill", quantity="2", fill_quantity="2")
        _native_fill_order(owner, scope, "beta-short", "beta", "BTCUSDT", Side.SELL,
                           "beta-fill", quantity="1", fill_quantity="1")

        async def capture(event, context):
            observed[event.strategy_id] = dict(context.execution)
            return None

        await owner.deliver_feedback("alpha", capture)
        await owner.deliver_feedback("beta", capture)
        assert observed["alpha"]["strategy_position"] == "2"
        assert observed["beta"]["strategy_position"] == "-1"
        assert observed["alpha"]["account_journal_position"] == "1"
        assert observed["beta"]["account_journal_position"] == "1"
        for context in observed.values():
            assert context["native_physical_position"] is None
            assert context["native_physical_position_quality"] == "UNAVAILABLE"
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_native_fill_receipts_are_account_product_symbol_scoped(tmp_path):
    owner, scope = _feedback_owner(tmp_path)
    try:
        _native_fill_order(owner, scope, "btc-order", "alpha", "BTCUSDT", Side.BUY,
                           "venue-trade-77", quantity="2", fill_quantity="1")
        _native_fill_order(owner, scope, "eth-order", "alpha", "ETHUSDT", Side.BUY,
                           "venue-trade-77", quantity="2", fill_quantity="1")
        btc_key = "binance:crypto_perp:BTCUSDT"
        eth_key = "binance:crypto_perp:ETHUSDT"
        assert owner.ledger.position(btc_key) == Decimal("1")
        assert owner.ledger.position(eth_key) == Decimal("1")

        duplicate = ExecutionAction(action_id="changed-duplicate", action=ActionKind.CANCEL,
            scope=scope, strategy_id="alpha", config_version="cfg-1",
            mode=TradingMode.SANDBOX, expiry_ns=NOW + 1_000_000_000,
            target_order_id="native:btc-order")
        with pytest.raises(ValueError, match="Fill identity conflicts"):
            owner.apply_fill(duplicate, fill_id="venue-trade-77", quantity="1",
                price="101", fee="0", order_id="native:btc-order")
        assert owner.ledger.position(btc_key) == Decimal("1")
        assert owner.ledger.position(eth_key) == Decimal("1")
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_feedback_remaining_quantity_uses_exact_difference_under_low_precision(tmp_path):
    owner, scope = _feedback_owner(tmp_path)
    try:
        from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Venue
        quantity = Decimal("1.000000000000000000000000000000000001")
        filled = Decimal("0.000000000000000000000000000000000001")
        intent = OrderIntent(intent_id="precision-order", job_id="alpha", signal_id="precision-signal",
            client_order_id="precision-client", destination=OrderDestination.REAL,
            instrument=InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_PERP,
                raw_symbol="BTCUSDT", std_symbol="BTCUSDT"),
            side=Side.BUY, quantity=quantity, limit_price=Decimal("100"))
        action = ExecutionAction(action_id="precision-action", action=ActionKind.CANCEL,
            scope=scope, strategy_id="alpha", config_version="cfg-1",
            mode=TradingMode.SANDBOX, expiry_ns=NOW + 1_000_000_000,
            target_order_id=intent.client_order_id)
        with localcontext() as context:
            context.prec = 4
            event = owner._feedback(action, status="PARTIAL_FILL", order_id=intent.client_order_id,
                intent=intent, provenance="NATIVE_FILL", cumulative_filled=filled)
        assert event.remaining_quantity == Decimal("1.000000000000000000000000000000000000")
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_source_event_claim_is_durable_and_next_identity_is_admitted(tmp_path):
    owner, scope = _feedback_owner(tmp_path)
    claim = {"event": {"event_id": "market-1", "price": "100"},
             "cursor": ["run-1", 1], "config_version": "cfg-1",
             "scope": scope.key, "evidence": {"input_version": 7}}
    try:
        assert owner.ledger.claim_source_event("alpha", "market-1", claim,
            runtime_id="1" * 32) is True
    finally:
        await owner.close()

    from execution.execution_ledger import ExecutionLedger
    ledger = ExecutionLedger(tmp_path / "feedback-owner.sqlite3", scope, max_intents=16,
        max_evidence_bytes=8192, max_fills=16, authority_root=tmp_path / "feedback-lease",
        native_account_id="shared-physical-account")
    try:
        assert ledger.claim_source_event("alpha", "market-1", claim,
            runtime_id="2" * 32) is False
        with pytest.raises(ValueError, match="conflicts with its original contents"):
            ledger.claim_source_event("alpha", "market-1", {**claim, "cursor": ["run-1", 2]},
                runtime_id="2" * 32)
        next_event = {**claim, "event": {"event_id": "market-2", "price": "101"},
                      "cursor": ["run-1", 2]}
        with pytest.raises(RuntimeError, match="source-state contract"):
            ledger.claim_source_event("alpha", "market-2", next_event,
                runtime_id="2" * 32)
    finally:
        ledger.close()


def test_legacy_process_lease_is_strict_and_native_route_aliases_are_explicit(tmp_path):
    root = tmp_path.resolve()
    for venue, environment in (("BINANCE", "testnet"), ("binance", "live"),
                               ("kis", "mock2"), ("binance", "LIVE")):
        with pytest.raises(ValueError):
            ProcessLease.account(root, venue, "account", environment)

    sandbox = ProcessLease.account_for_route(root, "binance", "account", "sandbox")
    testnet = ProcessLease.account_for_route(root, "binance", "account", "testnet")
    live = ProcessLease.account_for_route(root, "binance", "account", "live")
    real = ProcessLease.account_for_route(root, "binance", "account", "real")
    mock2 = ProcessLease.account_for_route(root, "kis", "account", "mock2")
    mock = ProcessLease.account_for_route(root, "kis", "account", "mock")
    assert sandbox.path == testnet.path
    assert live.path == real.path
    assert mock2.path == mock.path

    extension = "native-test-extension-" + str(id(root))
    with pytest.raises(ValueError, match="Noncanonical execution venue"):
        ProcessLease.account(root, extension, "account", "testnet")
    ProcessLease.register_extension_venue(extension)
    assert ProcessLease.account(root, extension, "account", "testnet").path == \
        ProcessLease.account_for_route(root, extension, "account", "sandbox").path
