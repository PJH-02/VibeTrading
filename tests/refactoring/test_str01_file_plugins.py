"""STR-01 real file-plugin acceptance without a live broker or network."""
from decimal import Decimal as D
import importlib

import pandas as pd
import pytest

from execution.execution_ledger import AccountScope
from strategies import load_strategy
from strategies.kr._etf_plugin import (
    StrategyUnsupported, execution_symbols, followup_due, intraday_source_decision, module_for,
    plan_entry_quantities, session_action,
)
from str01_native_history_fixture import DEFAULT_DAY, build_native_history

DAY = DEFAULT_DAY
ACCOUNT = {"venue": "kis", "account": "str01-paper", "product": "cash",
           "environment": "paper"}
SCOPE = AccountScope(**ACCOUNT)


def config(facts, strategy):
    cfg = {"day": facts["day"], "history_root": str(facts["root"]), "mode": "paper",
           "job_id": f"str01-{strategy.lower()}", "strategy": strategy,
           "config_version": "str01-v1", "account": dict(ACCOUNT),
           "account_scope": SCOPE.key,
           "history_manifest_sha256": facts["manifest_sha256"],
           "symbols": ["069500", "122630", "229200", "251340"],
           "execution_venue": "kis", "reference_venue": "kiwoom"}
    if strategy == "C3":
        cfg["index_key"] = "kis:index:KOSPI200"
    return cfg


def plugin(facts, strategy):
    wrapper = load_strategy(f"strategies.kr.{strategy.lower()}")
    return wrapper.create().configure(config(facts, strategy)), wrapper.module


def frame(symbol, opens, closes):
    stamps = pd.date_range(f"{DAY} 09:01", periods=len(opens), freq="1min")
    return pd.DataFrame({
        "timestamp": stamps, "date": DAY, "symbol": symbol, "source": "KIWOOM",
        "open": opens, "high": [max(a, b) * 1.0002 for a, b in zip(opens, closes)],
        "low": [min(a, b) * .9998 for a, b in zip(opens, closes)],
        "close": closes, "volume": [1000 + i for i in range(len(opens))],
        "trading_value": [float("nan")] * len(opens),
    })


def drifting_frame(symbol, drift):
    price, opens, closes = 100.0, [], []
    for _ in range(30):
        close = price * (1 + drift)
        opens.append(price)
        closes.append(close)
        price = close
    return frame(symbol, opens, closes)


def test_native_history_covers_every_execution_vehicle(tmp_path):
    facts = build_native_history(tmp_path / "history")
    for strategy, levered in (("C3", "122630"), ("KP", "122630"), ("KD", "251340")):
        instance, _ = plugin(facts, strategy)
        expected = set(execution_symbols(strategy))
        assert set(instance.history.daily) == expected
        assert set(instance.history.minutes) == expected
        assert levered in instance.history.daily
        assert instance.history.day == DAY


def test_configuration_rejects_wrong_mode_venue_index_and_vehicle(tmp_path):
    facts = build_native_history(tmp_path / "history")
    for strategy in ("C3", "KP", "KD"):
        bad = config(facts, strategy)
        bad["mode"] = "live"
        with pytest.raises(ValueError, match="PAPER"):
            plugin(facts, strategy)[0].configure(bad)

        bad = config(facts, strategy)
        bad["reference_venue"] = "krx"
        with pytest.raises(ValueError, match="kiwoom"):
            plugin(facts, strategy)[0].configure(bad)

        bad = config(facts, strategy)
        bad["account"] = dict(ACCOUNT, environment="live")
        bad["account_scope"] = AccountScope(**bad["account"]).key
        with pytest.raises(ValueError, match="paper"):
            plugin(facts, strategy)[0].configure(bad)

        bad = config(facts, strategy)
        bad["symbols"] = [symbol for symbol in bad["symbols"] if symbol != "122630"]
        if strategy == "KD":
            continue
        with pytest.raises(ValueError, match="vehicles"):
            plugin(facts, strategy)[0].configure(bad)

    bad = config(facts, "C3")
    bad["index_key"] = "kiwoom:etf:069500"
    with pytest.raises(ValueError, match="KOSPI200"):
        plugin(facts, "C3")[0].configure(bad)


def test_startup_rejects_manifest_bytes_changed_after_config_was_frozen(tmp_path):
    facts = build_native_history(tmp_path / "history")
    manifest = facts["root"] / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    wrapper = load_strategy("strategies.kr.kp")
    with pytest.raises(StrategyUnsupported, match="manifest digest"):
        wrapper.create().configure(config(facts, "KP"))


def test_optional_sibling_resolution_keeps_kp_kd_independent_of_c3(monkeypatch):
    original = importlib.import_module

    def without_c3(name, package=None):
        if name == "strategies.kr.c3":
            raise ModuleNotFoundError(name)
        return original(name, package)

    monkeypatch.setattr(importlib, "import_module", without_c3)
    assert module_for("KP").STRATEGY_ID == "KP"
    assert module_for("KD").STRATEGY_ID == "KD"
    with pytest.raises(ModuleNotFoundError):
        module_for("C3")


def test_checkpoint_roundtrip_accepts_legacy_schema(tmp_path):
    facts = build_native_history(tmp_path / "history")
    instance, _ = plugin(facts, "KP")
    state = instance.checkpoint_state()
    assert state["followups"] == {} and state["first_open"] is None

    restored, _ = plugin(facts, "KP")
    assert restored.restore_state(state).checkpoint_state() == state

    legacy = {"day": DAY, "entries": 0, "direction": 0,
              "safety_stop": None, "exit_pending": False}
    assert restored.restore_state(legacy).checkpoint_state()["followups"] == {}
    with pytest.raises(ValueError, match="schema"):
        restored.restore_state(dict(legacy, unknown=1))


def test_c3_native_open_decision_is_exposed_and_causal(tmp_path):
    facts = build_native_history(tmp_path / "history")
    instance, module = plugin(facts, "C3")
    decision = module.native_open_decision(
        instance.history, 100.0, facts["last_index_close"] * 1.001)
    assert decision["action"] == "entry"
    assert decision["direction"] == 1
    assert decision["leverage"] == 2
    assert len(decision["identity"]) == 64


def test_shared_intraday_decision_and_kd_stop_precedes_thirty_minute_gate(tmp_path):
    facts = build_native_history(tmp_path / "history")
    kp, _ = plugin(facts, "KP")
    kd, _ = plugin(facts, "KD")

    kp_frame = drifting_frame("069500", .008)
    kp_decision = intraday_source_decision(
        "KP", kp.history, kp_frame.timestamp.iloc[-1], kp_frame,
        {"positions": {}, "direction": 0, "entries": 0, "safety_stop": None})
    assert kp_decision["action"] == "entry" and kp_decision["direction"] == 1

    kd_frame = drifting_frame("229200", .008)
    timestamp = kd_frame.timestamp.iloc[-1]
    stop = float(kd_frame.close.iloc[-1]) + 1
    kd_decision = intraday_source_decision(
        "KD", kd.history, timestamp, kd_frame,
        {"positions": {"229200": D(1)}, "direction": 1, "entries": 1,
         "safety_stop": stop}, bid=100, ask=101)
    assert kd_decision["action"] == "paper_stop"
    assert kd_decision["detail"]["price"] <= stop


def test_source_owned_schedule_and_sizing_contract():
    assert session_action("KP", pd.Timestamp("2026-10-13 09:00:00")) == "overnight_recovery"
    assert session_action("KP", pd.Timestamp("2026-10-13 09:04:59")) == "overnight_recovery"
    assert session_action("KP", pd.Timestamp("2026-10-13 09:05:00")) is None
    assert session_action("KP", pd.Timestamp("2026-10-13 15:19:54")) is None
    assert session_action("KP", pd.Timestamp("2026-10-13 15:19:55")) == "session_exit"
    assert session_action("C3", pd.Timestamp("2026-10-13 15:20:04")) is None
    assert session_action("C3", pd.Timestamp("2026-10-13 15:20:05")) == "session_exit"
    assert session_action("KP", pd.Timestamp("2026-10-13 15:30:00")) == "session_exit"
    assert session_action("KP", pd.Timestamp("2026-10-13 15:30:01")) is None

    assert plan_entry_quantities(0, {"069500": 1.0}, {"069500": D(100)}) == []
    assert plan_entry_quantities(1, {"069500": 1.0}, {"069500": D(100)}) == []
    assert plan_entry_quantities(
        1_000_000, {"069500": 1.0, "122630": 0.0},
        {"069500": D(100), "122630": D(100)}) == [("069500", 9950)]

    order = {"status": "pending", "order_type": "limit", "created_ns": 1_000}
    assert not followup_due(order, 1_000 + 4_999_999_999)
    assert followup_due(order, 1_000 + 5_000_000_000)
    assert not followup_due(dict(order, cancel_requested=True), 1_000 + 5_000_000_000)
