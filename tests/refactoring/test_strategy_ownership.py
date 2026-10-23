"""Ownership and adapter identity for canonical and historical strategy paths."""
from decimal import Decimal as D
import importlib
import sys
from types import ModuleType
from pathlib import Path

import pytest

from vibetrading.strategies import StrategyModule, load_strategy
from vibetrading.strategies.legacy import candle_loader


def test_modern_package_reexports_the_single_event_contract():
    import vibetrading.strategies as strategies_package

    from vibetrading.strategies.contract import (
        StrategyModule as canonical_module,
        compile_subscriptions as canonical_compile_subscriptions,
        compile_warmup as canonical_compile_warmup,
        load_strategy as canonical_load_strategy,
    )

    assert strategies_package.StrategyModule is canonical_module
    assert strategies_package.compile_subscriptions is canonical_compile_subscriptions
    assert strategies_package.compile_warmup is canonical_compile_warmup
    assert strategies_package.load_strategy is canonical_load_strategy
    assert "example_signal" not in strategies_package.__all__
    assert "legacy" not in strategies_package.__all__
    assert "research" not in strategies_package.__all__


def test_old_and_new_modules_classes_and_monkeypatches_share_identity(monkeypatch):
    old_contract = importlib.import_module("vibetrading.strategy_contract")
    new_contract = importlib.import_module("vibetrading.strategies.contract")
    assert old_contract is new_contract
    assert sys.modules["vibetrading.strategy_contract"] is new_contract
    assert old_contract.load_strategy is new_contract.load_strategy
    assert old_contract.StrategyModule is new_contract.StrategyModule

    old_example = importlib.import_module("strategies.example_signal")
    new_example = importlib.import_module("vibetrading.strategies.example_signal")
    assert old_example is new_example
    assert sys.modules["strategies.example_signal"] is new_example
    assert old_example.ExampleSignalStrategy is new_example.ExampleSignalStrategy
    assert old_example.create_strategy is new_example.create_strategy

    marker = lambda: 123
    monkeypatch.setattr(old_example, "utc_now_ns", marker)
    assert new_example.utc_now_ns is marker
    assert load_strategy("example_signal").module.utc_now_ns is marker


def test_old_service_loader_is_the_canonical_candle_loader():
    old_service = importlib.import_module("services.signal_gen.strategy_loader")
    canonical = importlib.import_module("vibetrading.strategies.legacy.candle_loader")
    assert old_service is canonical
    assert old_service.get_strategy is canonical.get_strategy
    assert old_service.resolve_strategy_team is canonical.resolve_strategy_team


@pytest.mark.parametrize(
    "alias",
    [
        "example_signal",
        "example_signal.py",
        "strategies.example_signal",
        "strategies/example_signal.py",
        "strategies\\example_signal.py",
        "vibetrading.strategies.example_signal",
        "vibetrading/strategies/example_signal.py",
        "vibetrading\\strategies\\example_signal.py",
    ],
)
def test_loader_aliases_map_to_one_canonical_event_module(alias):
    canonical = importlib.import_module("vibetrading.strategies.example_signal")
    loaded = load_strategy(alias)
    assert isinstance(loaded, StrategyModule)
    assert loaded.module is canonical
    assert loaded.strategy_id == "example_signal"


@pytest.mark.parametrize(
    ("nested_name", "aliases"),
    [
        (
            "plugins.custom",
            [
                "strategies.plugins.custom",
                "strategies/plugins/custom.py",
                "strategies\\plugins\\custom.py",
                "vibetrading.strategies.plugins.custom",
                "vibetrading/strategies/plugins/custom.py",
                "vibetrading\\strategies\\plugins\\custom.py",
            ],
        ),
        (
            "kr.c3",
            [
                "strategies.kr.c3",
                "strategies/kr/c3.py",
                "strategies\\kr\\c3.py",
                "vibetrading.strategies.kr.c3",
                "vibetrading/strategies/kr/c3.py",
                "vibetrading\\strategies\\kr\\c3.py",
            ],
        ),
    ],
)
def test_loader_accepts_qualified_nested_event_modules(nested_name, aliases, monkeypatch):
    canonical = f"vibetrading.strategies.{nested_name}"
    create_calls = []

    class SyntheticStrategy:
        def on_event(self, event, data):
            return []

    def create_strategy():
        create_calls.append(True)
        return SyntheticStrategy()

    module = ModuleType(canonical)
    module.STRATEGY_ID = nested_name
    module.SUBSCRIPTIONS = ["BTC-PERP"]
    module.WARMUP = {"bars": 0}
    module.create_strategy = create_strategy
    monkeypatch.setitem(sys.modules, canonical, module)

    for alias in aliases:
        loaded = load_strategy(alias)
        assert isinstance(loaded, StrategyModule)
        assert loaded.module is module
        assert loaded.strategy_id == nested_name
        assert create_calls == []

    assert hasattr(loaded.create(), "on_event")
    assert create_calls == [True]


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "../example_signal",
        "../../etc/passwd.py",
        "/tmp/example_signal.py",
        "\\tmp\\example_signal.py",
        "C:\\tmp\\example_signal.py",
        "os.path",
        "vibetrading.strategies.legacy.candle.turtle_breakout",
        "vibetrading.strategies.research.state.s1_net_liquidity",
        "strategies/../../example_signal.py",
    ],
)
def test_loader_rejects_traversal_absolute_and_unrelated_namespaces(invalid):
    with pytest.raises(ValueError):
        load_strategy(invalid)


def test_modern_event_and_legacy_candle_contracts_stay_explicit():
    modern = load_strategy("example_signal")
    instance = modern.create()
    assert hasattr(instance, "on_event")
    assert not hasattr(instance, "on_candle")

    with pytest.raises(ValueError):
        load_strategy("vibetrading.strategies.legacy.candle.turtle_breakout")

    candle_loader.clear_strategy_cache()
    legacy = candle_loader.get_strategy("turtle_breakout")
    assert legacy.name == "turtle_breakout"
    assert hasattr(legacy, "on_candle")
    assert not hasattr(legacy, "on_event")

    # A modern example is not automatically registered as a legacy candle strategy.
    with pytest.raises(ImportError):
        candle_loader.get_strategy("example_signal")
    candle_loader.clear_strategy_cache()


@pytest.mark.parametrize(
    "name",
    ["s1_net_liquidity", "s2_external_anchor", "s3_aggressive_flow", "s4_obi"],
)
def test_state_and_frozen_fair_value_families_remain_distinct(name):
    old_state = importlib.import_module(f"strategy.{name}")
    state = importlib.import_module(f"vibetrading.strategies.research.state.{name}")
    old_fair_value = importlib.import_module(f"vibetrading.fv_mm.legacy.{name}")
    fair_value = importlib.import_module(f"vibetrading.strategies.legacy.fair_value.{name}")

    assert old_state is state
    assert old_fair_value is fair_value
    assert state.Strategy is not fair_value.Strategy
    assert state.STRATEGY_ID == fair_value.STRATEGY_ID


def test_legacy_fv_selection_returns_the_exact_frozen_class():
    state_module = importlib.import_module("vibetrading.strategies.research.state.s1_net_liquidity")
    frozen_module = importlib.import_module("vibetrading.strategies.legacy.fair_value.s1_net_liquidity")

    assert type(state_module.Strategy(mode="legacy_fv", coefficient=D(1))) is frozen_module.Strategy
    assert type(state_module.Strategy(coefficient=D(1))) is frozen_module.Strategy
    assert type(state_module.Strategy(mode="state")) is state_module.Strategy


def test_canonical_runtime_dynamic_imports_use_state_namespace(monkeypatch):
    real_import = importlib.import_module
    calls = []

    def recording_import(name, *args, **kwargs):
        calls.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", recording_import)
    from vibetrading.mm_state.coordinator import Coordinator

    coordinator = Coordinator(profile="obi", calibration={})
    assert type(coordinator.modules["S4"]).__module__ == "vibetrading.strategies.research.state.s4_obi"
    assert not any(
        name.startswith("strategy.") or name.startswith("vibetrading.fv_mm.legacy.")
        for name in calls
    )


def test_legacy_instance_selects_canonical_frozen_fair_value_module():
    from vibetrading.mm_state.features import legacy_instance

    frozen = importlib.import_module("vibetrading.strategies.legacy.fair_value.s4_obi")
    selected = legacy_instance("s4_obi", D(1), "legacy_fv", {})
    assert type(selected) is frozen.Strategy


def test_legacy_candle_loader_keeps_team_cache_and_backtest_api():
    from shared.models import TeamType

    candle_loader.clear_strategy_cache()
    first = candle_loader.get_strategy("turtle_breakout")
    second = candle_loader.get_strategy("turtle_breakout")
    assert first is second
    assert first.name == "turtle_breakout"
    assert hasattr(first, "on_candle")
    assert candle_loader.get_strategy_team("turtle_breakout") is TeamType.TRADING
    assert candle_loader.resolve_strategy_team("turtle_breakout") is TeamType.TRADING

    with pytest.raises(ValueError):
        candle_loader.get_strategy("turtle_breakout", expected_team=TeamType.PORTFOLIO)
    with pytest.raises(ValueError):
        candle_loader.resolve_strategy_team("turtle_breakout", requested_team=TeamType.PORTFOLIO)

    candle_loader.clear_strategy_cache()
    assert not candle_loader._loaded_strategies
    assert not candle_loader._strategy_declared_teams


def test_cli_strategy_discovery_uses_canonical_candle_package(monkeypatch):
    # The historical CLI sets this flag and extends sys.path at import time.
    monkeypatch.setenv("STANDALONE_MODE", "true")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    import run_strategy

    found = run_strategy.scan_strategies()
    assert "turtle_breakout" in found
    assert "example_signal" not in found
