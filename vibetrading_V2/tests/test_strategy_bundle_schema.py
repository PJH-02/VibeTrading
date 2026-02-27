from __future__ import annotations

import textwrap

import pytest

from vibetrading_V2.core.errors import StrategyValidationError
from vibetrading_V2.strategy.registry import load_strategy_bundle


def test_registry_loads_valid_bundle(tmp_path):
    strategy_file = tmp_path / "valid_strategy.py"
    strategy_file.write_text(
        textwrap.dedent(
            """
            from vibetrading_V2.strategy.bundle import StrategyBundle, StrategyMeta

            def _build():
                return object()

            def get_bundle():
                return StrategyBundle(
                    meta=StrategyMeta(
                        name="demo",
                        universe=["BTC-USD"],
                        timeframe="1m",
                        required_fields=["close"],
                    ),
                    build=_build,
                )
            """
        ),
        encoding="utf-8",
    )

    bundle = load_strategy_bundle(strategy_file)
    assert bundle.meta.name == "demo"
    assert bundle.meta.universe == ["BTC-USD"]
    assert bundle.overrides is None


def test_registry_rejects_empty_required_fields(tmp_path):
    strategy_file = tmp_path / "bad_strategy.py"
    strategy_file.write_text(
        textwrap.dedent(
            """
            from vibetrading_V2.strategy.bundle import StrategyBundle, StrategyMeta

            def _build():
                return object()

            BUNDLE = StrategyBundle(
                meta=StrategyMeta(
                    name="bad",
                    universe=["BTC-USD"],
                    timeframe="1m",
                    required_fields=[],
                ),
                build=_build,
            )
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(StrategyValidationError, match="required_fields"):
        load_strategy_bundle(strategy_file)

