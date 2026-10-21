"""Paper-only group observations; no child submission, retry, hedge or network."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from vibetrading.execution_groups import ExecutionGroupSpec, GroupLeg, PaperExecutionGroups
from vibetrading.execution_ledger import AccountScope
from vibetrading.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
from vibetrading.portfolio_ledger import PortfolioLedger
from vibetrading.trading_contracts import ProductKind, ProductSpec


def open_owner(root, venue):
    return PortfolioLedger(root / "paper" / (venue.value + ".sqlite"),
        AccountScope(venue.value, "synthetic-group", "linear_perpetual", "paper"),
        owner_root=root, mode="paper", max_orders=20, max_fills=30, max_events=100,
        max_document_bytes=100_000, max_db_bytes=4_000_000, max_wal_bytes=8_000_000)


def rules(venue, *, multiplier=1, base="BTC", currency="USDT"):
    instrument = InstrumentId(venue=venue, asset_class=AssetClass.CRYPTO_PERP,
        raw_symbol=base+currency, std_symbol=base+currency, underlying=base, currency=currency)
    return ProductSpec(instrument, ProductKind.LINEAR_PERPETUAL, D(multiplier), D("0.1"), D("0.1"), D(1),
        currency, currency, currency, "net", "isolated", "synthetic fee", "synthetic funding",
        "explicit isolated simulation assumption", D(".001"), D(".002"))


def group_spec(left, right, identity="group", *, right_rules=None):
    first, second = rules(Venue.BYBIT, multiplier=2), right_rules or rules(Venue.BINANCE)
    return ExecutionGroupSpec(identity, "strategy", "config-v1", 100, 1000,
        (GroupLeg("long", left.scope, "strategy", "config-v1", "left-intent", Side.BUY, D(1),
                  first, "BTC", "explicit instrument underlying and multiplier fixture"),
         GroupLeg("short", right.scope, "strategy", "config-v1", "right-intent", Side.SELL, D(2),
                  second, second.instrument.underlying, "explicit instrument underlying and multiplier fixture")),
        "BTC", ("operator-review-only",), "explicit fixed plan fixture; never executed")


def admit(owner, leg, *, strategy=None, config=True):
    owner.seed_balance(leg.rules.collateral_currency, 10000, {"simulation": True})
    intent = OrderIntent(intent_id=leg.intent_id, job_id="job", signal_id="signal", destination=OrderDestination.REAL,
        instrument=leg.rules.instrument, side=leg.side, quantity=leg.quantity, limit_price=D(100),
        created_ts_ns=100, client_order_id="client-"+leg.intent_id)
    evidence = {"source": "explicit local paper fixture"}
    if config:
        evidence["config_version"] = leg.config_version
    owner.admit_order(intent, strategy or leg.strategy_id, evidence, 10000, leg.rules, now_ns=100,
        funding_buffer=0, margin_rate=D(".1"), account_notional_limit=100000,
        strategy_notional_limit=100000, account_capital_limit=100000, strategy_capital_limit=100000)


def coordinator(left, right, **changes):
    options = dict(max_groups=4, max_observations=10, max_legs=4)
    options.update(changes)
    return PaperExecutionGroups(left, {left.scope.key: left, right.scope.key: right}, **options)


@pytest.fixture
def owners(tmp_path):
    left, right = open_owner(tmp_path, Venue.BYBIT), open_owner(tmp_path, Venue.BINANCE)
    try:
        yield left, right
    finally:
        left.close()
        right.close()


def test_unobserved_orders_are_unknown_without_dispatch_or_phantom_zero(owners):
    left, right = owners
    groups, plan = coordinator(left, right), group_spec(left, right)
    assert groups.create(plan, {"approval": "paper observation only"})
    assert not groups.create(plan, {"approval": "paper observation only"})
    report = groups.report(plan.group_id)
    assert report["state"] == "PLANNED"
    assert report["confirmed_base_delta"]["known_value"] is None
    assert len(report["confirmed_base_delta"]["unknown_legs"]) == 2
    assert report["actual_mark_notional"]["known_value"] is None
    assert report["attributed_position_margin"]["known_value"] is None
    assert groups.observe(plan.group_id, "one", now_ns=200, evidence={"receipt": 1})[0]
    assert len(groups.report(plan.group_id)["pending_reservation_unknown_legs"]) == 2
    assert not left.active_orders() and not right.active_orders()
    assert report["automatic_retry"] is False and report["automatic_hedge"] is False
    assert report["production_gate"] == "BLOCKED"


def test_partial_unknown_and_timeout_preserve_child_reservations(owners):
    left, right = owners
    plan, groups = group_spec(left, right), coordinator(left, right)
    groups.create(plan, {"approval": "paper observation only"})
    admit(left, plan.legs[0])
    admit(right, plan.legs[1])
    left.apply_fill("partial", "left-intent", D(".5"), 100)
    groups.observe(plan.group_id, "partial", now_ns=200, evidence={"receipt": 1})
    report = groups.report(plan.group_id)
    assert report["state"] == "PARTIAL"
    assert D(report["confirmed_base_delta"]["known_value"]) == 1
    assert not report["confirmed_base_delta"]["complete"]
    right.cancel("right-intent")  # ACK/uncertainty cannot release its reservation.
    before = [left.get("left-intent"), right.get("right-intent")]
    groups.observe(plan.group_id, "unknown", now_ns=201, evidence={"receipt": 2})
    assert groups.report(plan.group_id)["state"] == "UNKNOWN"
    timeout = groups.report(plan.group_id, now_ns=1000)
    assert timeout["state"] == "WAITING_EXPLICIT_ACTION"
    assert timeout["timeout_plan_execution"] == "recorded_only_not_executed"
    assert [left.get("left-intent"), right.get("right-intent")] == before
    assert D(timeout["pending_reservation_by_currency"]["USDT"]) == D("30.6")


def test_multiplier_and_opposite_confirmed_legs_can_prove_zero_base_only(owners):
    left, right = owners
    plan, groups = group_spec(left, right), coordinator(left, right)
    groups.create(plan, {"approval": "paper observation only"})
    admit(left, plan.legs[0])
    admit(right, plan.legs[1])
    left.apply_fill("left-fill", "left-intent", 1, 100)
    right.apply_fill("right-fill", "right-intent", 2, 100)
    groups.observe(plan.group_id, "complete", now_ns=200, evidence={"receipt": 1})
    report = groups.report(plan.group_id)
    assert report["state"] == "COMPLETED_CONFIRMED"
    assert D(report["confirmed_base_delta"]["known_value"]) == 0
    assert report["confirmed_base_delta"]["complete"]
    assert report["cross_account_snapshot_atomic"] is False
    assert report["actual_mark_notional"]["status"] == "UNVERIFIED"
    assert report["attributed_position_margin"]["known_value"] is None


@pytest.mark.parametrize("case", ["different_base", "missing_mapping", "missing_config"])
def test_unverified_economics_or_config_prevent_complete_aggregate(owners, case):
    left, right = owners
    plan = group_spec(left, right, right_rules=rules(Venue.BINANCE, base="ETH") if case == "different_base" else None)
    if case == "missing_mapping":
        plan = replace(plan, legs=(plan.legs[0], replace(plan.legs[1], base_mapping_provenance=None)))
    groups = coordinator(left, right)
    groups.create(plan, {"approval": "paper observation only"})
    admit(left, plan.legs[0])
    admit(right, plan.legs[1], config=case != "missing_config")
    left.apply_fill("left-fill", "left-intent", 1, 100)
    right.apply_fill("right-fill", "right-intent", 2, 100)
    groups.observe(plan.group_id, "observed", now_ns=200, evidence={"receipt": 1})
    report = groups.report(plan.group_id)
    assert not report["confirmed_base_delta"]["complete"]
    assert report["confirmed_base_delta"]["unknown_legs"]
    assert report["state"] != "COMPLETED_CONFIRMED"


def test_currencies_are_not_summed_without_fx_or_mark_proof(owners):
    left, right = owners
    plan = group_spec(left, right, right_rules=rules(Venue.BINANCE, currency="USD"))
    groups = coordinator(left, right)
    groups.create(plan, {"approval": "paper observation only"})
    admit(left, plan.legs[0])
    admit(right, plan.legs[1])
    groups.observe(plan.group_id, "observed", now_ns=200, evidence={"receipt": 1})
    report = groups.report(plan.group_id)
    assert set(report["pending_reservation_by_currency"]) == {"USDT", "USD"}
    assert report["actual_mark_notional"]["known_value"] is None


def test_immutable_group_and_observation_identity_collisions(owners):
    left, right = owners
    plan, groups = group_spec(left, right), coordinator(left, right)
    groups.create(plan, {"approval": "paper observation only"})
    with pytest.raises(ValueError, match="collision"):
        groups.create(replace(plan, deadline_ns=2000), {"approval": "paper observation only"})
    groups.observe(plan.group_id, "receipt", now_ns=200, evidence={"receipt": 1})
    with pytest.raises(ValueError, match="collision"):
        groups.observe(plan.group_id, "receipt", now_ns=200, evidence={"receipt": 2})
    assert len(left.group_facts(plan.group_id)) == 2


def test_child_identity_mismatch_does_not_commit_an_observation(owners):
    left, right = owners
    plan, groups = group_spec(left, right), coordinator(left, right)
    groups.create(plan, {"approval": "paper observation only"})
    admit(left, plan.legs[0], strategy="another-strategy")
    with pytest.raises(ValueError, match="identity"):
        groups.observe(plan.group_id, "bad", now_ns=200, evidence={"receipt": 1})
    assert len(left.group_facts(plan.group_id)) == 1


def test_lifetime_group_and_observation_bounds_do_not_evict_facts(owners):
    left, right = owners
    plan, groups = group_spec(left, right), coordinator(left, right, max_groups=1, max_observations=1)
    groups.create(plan, {"approval": "paper observation only"})
    with pytest.raises(RuntimeError, match="lifetime"):
        groups.create(replace(plan, group_id="second"), {"approval": "paper observation only"})
    assert groups.get("second") is None
    assert groups.observe(plan.group_id, "one", now_ns=200, evidence={"receipt": 1})[0]
    with pytest.raises(RuntimeError, match="lifetime"):
        groups.observe(plan.group_id, "two", now_ns=201, evidence={"receipt": 2})
    assert not groups.observe(plan.group_id, "one", now_ns=200, evidence={"receipt": 1})[0]
    assert len(left.group_facts()) == 2


def test_restart_observation_dedup_keeps_group_facts_and_unknown_child_risk(tmp_path):
    left, right = open_owner(tmp_path, Venue.BYBIT), open_owner(tmp_path, Venue.BINANCE)
    plan = group_spec(left, right)
    groups = coordinator(left, right)
    groups.create(plan, {"approval": "paper observation only"})
    admit(left, plan.legs[0])
    admit(right, plan.legs[1])
    _, original = groups.observe(plan.group_id, "first", now_ns=200, evidence={"receipt": 1})
    left.close()
    right.close()
    left, right = open_owner(tmp_path, Venue.BYBIT), open_owner(tmp_path, Venue.BINANCE)
    try:
        groups = coordinator(left, right)
        created, duplicate = groups.observe(plan.group_id, "first", now_ns=200, evidence={"receipt": 1})
        assert not created and duplicate == original
        assert len(left.group_facts()) == 2
        groups.observe(plan.group_id, "after-restart", now_ns=201, evidence={"receipt": 2})
        report = groups.report(plan.group_id)
        assert report["state"] == "UNKNOWN"
        assert len(left.active_orders()) == 1 and len(right.active_orders()) == 1
        assert D(left.get("left-intent")["reservation"]) > 0
        assert D(right.get("right-intent")["reservation"]) > 0
    finally:
        left.close()
        right.close()


def test_group_plan_validation_is_explicit_and_paper_only(owners):
    left, right = owners
    plan = group_spec(left, right)
    with pytest.raises(ValueError, match="counted twice"):
        replace(plan, legs=(plan.legs[0], replace(plan.legs[0], leg_id="duplicate")))
    with pytest.raises(ValueError, match="environment"):
        replace(plan.legs[0], scope=replace(left.scope, environment="live"))
    with pytest.raises(ValueError, match="provenance"):
        replace(plan, timeout_plan_provenance=None)


def test_reconstructed_coordinator_revalidates_persisted_owner_and_leg_inventory(owners):
    left,right=owners
    plan=group_spec(left,right)
    coordinator(left,right).create(plan,{'approval':'paper observation only'})
    with pytest.raises(ValueError,match='registered child owner'):
        PaperExecutionGroups(left,{left.scope.key:left},max_groups=4,max_observations=20,max_legs=4)
    with pytest.raises(ValueError,match='capacity'):
        PaperExecutionGroups(left,{left.scope.key:left},max_groups=4,max_observations=20,max_legs=1)


def test_group_documents_and_leg_count_share_explicit_ledger_bounds(owners):
    left, right = owners
    plan, groups = group_spec(left, right), coordinator(left, right)
    with pytest.raises(ValueError, match="byte budget"):
        groups.create(plan, {"oversized": "x" * 100001})
    assert groups.get(plan.group_id) is None
    too_many = replace(plan, legs=tuple(replace(plan.legs[0], leg_id=str(i), intent_id="intent-"+str(i)) for i in range(5)))
    with pytest.raises(ValueError, match="capacity"):
        groups.create(too_many, {"approval": "paper observation only"})
    groups.create(plan, {"approval": "paper observation only"})
    with pytest.raises(ValueError, match="byte budget"):
        groups.observe(plan.group_id, "oversized", now_ns=200, evidence={"oversized": "x" * 100001})
    assert len(left.group_facts(plan.group_id)) == 1
