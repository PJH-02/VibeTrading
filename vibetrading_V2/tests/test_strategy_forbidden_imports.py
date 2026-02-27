from __future__ import annotations

import textwrap

import pytest

from vibetrading_V2.core.errors import StrategySandboxError
from vibetrading_V2.strategy.registry import load_strategy_bundle


def test_registry_blocks_forbidden_strategy_imports(tmp_path):
    strategy_file = tmp_path / "forbidden_strategy.py"
    strategy_file.write_text(
        textwrap.dedent(
            """
            import requests
            from vibetrading_V2.strategy.bundle import StrategyBundle, StrategyMeta

            def _build():
                return object()

            BUNDLE = StrategyBundle(
                meta=StrategyMeta(
                    name="forbidden",
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

    with pytest.raises(StrategySandboxError, match="requests"):
        load_strategy_bundle(strategy_file)

