"""STR-01 runtime contract and verified native Kiwoom identity tests."""
import json
from types import MappingProxyType

import pytest

from core.models import AssetClass, InstrumentId, Venue
from feeds.providers.kiwoom import KiwoomCollector, KiwoomSubscription
from test_runtime_checkpoint import BTC as CHECKPOINT_BTC
from test_runtime_checkpoint import (host_for as checkpoint_host_for, ledger_at as checkpoint_ledger_at,
                                     matcher_for as checkpoint_matcher_for)
from test_strategy_runtime import BTC, ledger_at, matcher_for, registration, runtime


def _registration_with_validator(ledger, observed, hook):
    item = registration(ledger, "str01", observed)

    class Plugin:
        def on_event(self, event, data):
            return []

        def validate_registration(self, facts):
            return hook(facts)

    item.module.module.create_strategy = lambda: Plugin()
    return item


def test_registration_validator_gets_frozen_exact_account_inventory_before_claim(tmp_path, monkeypatch):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", 1000, {"source": "fixture"})
        host = runtime(ledger, matcher_for(ledger), [1])
        observed, claim_order = [], []
        original_claim = ledger.claim_strategy_event

        def claim(*args, **kwargs):
            claim_order.append("claim")
            return original_claim(*args, **kwargs)

        monkeypatch.setattr(ledger, "claim_strategy_event", claim)

        def validate(facts):
            claim_order.append("validate")
            assert isinstance(facts, MappingProxyType)
            assert facts["job_id"] == "str01"
            assert facts["strategy_id"] == "str01"
            assert facts["config_version"] == "config-1"
            assert tuple(facts["accounts"]) == (ledger.scope.key,)
            assert facts["accounts"][ledger.scope.key]["scope"] == {
                "venue": ledger.scope.venue, "account": ledger.scope.account,
                "product": ledger.scope.product, "environment": ledger.scope.environment,
            }
            assert facts["accounts"][ledger.scope.key]["capabilities"] == {
                "allow_taker_gtc": False, "allow_market_orders": False,
            }
            assert tuple(facts["products"]) == (BTC.key,)
            assert tuple(facts["instruments"]) == (BTC.key,)
            assert facts["required_feeds"] == (BTC.key,)
            assert facts["reference_feeds"] == {}
            with pytest.raises(TypeError):
                facts["job_id"] = "changed"

        host.register(_registration_with_validator(ledger, observed, validate))
        assert claim_order == ["validate", "claim"]
    finally:
        ledger.close()


@pytest.mark.parametrize("hook", [
    lambda facts: (_ for _ in ()).throw(ValueError("rejected")),
    lambda facts: _async_result(),
])
def test_registration_validator_failure_or_awaitable_precedes_durable_claim(tmp_path, monkeypatch, hook):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", 1000, {"source": "fixture"})
        host = runtime(ledger, matcher_for(ledger), [1])
        calls = []
        monkeypatch.setattr(ledger, "claim_strategy_event", lambda *a, **k: calls.append("claim"))
        with pytest.raises(ValueError):
            host.register(_registration_with_validator(ledger, [], hook))
        assert calls == []
    finally:
        ledger.close()


async def _awaitable():
    return None


def _async_result():
    return _awaitable()


def test_legacy_plugin_without_validator_still_registers(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        ledger.seed_balance("USDT", 1000, {"source": "fixture"})
        host = runtime(ledger, matcher_for(ledger), [1])
        host.register(registration(ledger, "legacy", []))
        assert host.report()["strategies"]["legacy"]["state"] in {"PAPER", "WARMING"}
    finally:
        ledger.close()


def test_registration_validator_runs_again_on_checkpoint_recreated_instance(tmp_path):
    from types import ModuleType, SimpleNamespace
    from runtime.strategy_runtime import StrategyRegistration, StrategyRisk
    from vibetrading.strategy_contract import StrategyModule

    ledger = checkpoint_ledger_at(tmp_path)
    now, validations = [1_800_000_000_000_000_000], []
    try:
        ledger.seed_balance("USDT", 1000, {"source": "fixture"})

        class Plugin:
            def __init__(self):
                self.state = {"count": 0}

            def on_event(self, event, data):
                return []

            def validate_registration(self, facts):
                validations.append(facts["job_id"])

            def checkpoint_state(self):
                return dict(self.state)

            def restore_state(self, state):
                self.state = dict(state)

        source = ModuleType("str01_checkpoint_plugin")
        source.create_strategy = Plugin
        module = StrategyModule(source, "str01-checkpoint", [
            {"instrument_key": CHECKPOINT_BTC.key, "event_type": "order_book"}], {"bars": 0})

        def registration_for(account_ledger):
            return StrategyRegistration("str01-checkpoint-job", module, "v1", "cfg1", {},
                {"receipt": "fixture"}, True, (CHECKPOINT_BTC.key,), 0,
                (account_ledger.scope.key,),
                {account_ledger.scope.key: StrategyRisk(0, 100000, 100000, 100000, 100000, 0.1)},
                lambda signal, view, config: [])

        source_host = checkpoint_host_for(ledger, checkpoint_matcher_for(ledger), now)
        source_host.register(registration_for(ledger))
        source_host._checkpoint_capture_guard(SimpleNamespace(hot_path=source_host.hot_path))
        state = source_host.capture_runtime_state(max_bytes=262144)
        assert validations == ["str01-checkpoint-job"]
        ledger.close()

        ledger = checkpoint_ledger_at(tmp_path)
        restored = checkpoint_host_for(ledger, checkpoint_matcher_for(ledger), now)
        restored.register(registration_for(ledger))
        assert validations == ["str01-checkpoint-job", "str01-checkpoint-job"]
        restored.begin_checkpoint_recovery()
        restored.restore_runtime_state(state, verify=lambda: True, max_bytes=262144)
        assert validations == ["str01-checkpoint-job"] * 3
    finally:
        ledger.close()


def _kiwoom_reference(**updates):
    native = KiwoomCollector.__new__(KiwoomCollector).instrument(
        KiwoomSubscription(symbol="069500", market="KRX"))
    return native.model_copy(update=updates) if updates else native


@pytest.mark.parametrize("feed_updates", [
    {"std_symbol": "OTHER:KRX:069500"},
    {"exchange": "NXT", "std_symbol": "KIWOOM:NXT:069500"},
    {"raw_symbol": "069501", "std_symbol": "KIWOOM:KRX:069501"},
])
def test_kiwoom_reference_mapping_rejects_spoofed_or_mismatched_identity(tmp_path, feed_updates):
    execution = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF, raw_symbol="069500",
                             std_symbol="069500", currency="KRW", exchange="KRX")
    reference = _kiwoom_reference(**feed_updates)
    ledger = ledger_at(tmp_path, execution)
    try:
        ledger.seed_balance("KRW", 1000, {"source": "fixture"})
        with pytest.raises(ValueError, match="Reference mapping"):
            runtime(ledger, matcher_for(ledger), [1], instrument=execution, reference=reference)
    finally:
        ledger.close()


def test_native_kiwoom_krx_spelling_maps_to_matching_kis_product(tmp_path):
    execution = InstrumentId(venue=Venue.KIS, asset_class=AssetClass.ETF, raw_symbol="069500",
                             std_symbol="069500", currency="KRW", exchange="KRX")
    reference = _kiwoom_reference()
    ledger = ledger_at(tmp_path, execution)
    try:
        ledger.seed_balance("KRW", 1000, {"source": "fixture"})
        host = runtime(ledger, matcher_for(ledger), [1], instrument=execution, reference=reference)
        assert host.reference_feeds[execution.key] == reference.key
    finally:
        ledger.close()


def test_ledger_facts_project_generic_payload_without_truncation_or_plan_interpretation(tmp_path):
    ledger = ledger_at(tmp_path)
    try:
        host = runtime(ledger, matcher_for(ledger), [1])
        for index in range(70):
            intent_id = f"intent-{index:03}"
            payload = {
                "intent_id": intent_id,
                "created_ts_ns": index,
                "instrument": BTC.model_dump(mode="json"),
                "metadata": {"source_tag": "fixture", "plan": {"kind": "opaque", "followup": True}},
            }
            ledger._db.execute(
                "INSERT INTO portfolio_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (intent_id, f"client-{intent_id}", "fixture", "fingerprint", json.dumps(payload),
                 "{}", "{}", 999999999999999999, "OPEN", "1", "0", "0", "1", "USDT",
                 BTC.key, "buy", "100", "0"),
            )
        facts = host._ledger_facts((ledger.scope.key,))
        orders = facts[ledger.scope.key]["orders"]
        assert len(orders) == 70
        assert set(orders[0]) == {
            "intent_id", "strategy", "instrument", "raw_symbol", "side", "state",
            "quantity", "filled", "reservation", "expiry_ns", "created_ns", "metadata",
        }
        assert orders[0]["raw_symbol"] == BTC.raw_symbol
        assert orders[0]["metadata"] == {"source_tag": "fixture", "plan": {"kind": "opaque", "followup": True}}
        assert "kind" not in orders[0] and "followup" not in orders[0]
    finally:
        ledger.close()
