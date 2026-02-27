from __future__ import annotations

import textwrap
from datetime import datetime, timezone

from vibetrading_V2.core.types import Bar, Fill, OrderIntent
from vibetrading_V2.runner.backtest import run_backtest
from vibetrading_V2.runner.runtime import RunnerPorts


class _DataSource:
    def get_bars(self, symbol: str):
        return [
            Bar(
                symbol=symbol,
                timestamp=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1,
            ),
            Bar(
                symbol=symbol,
                timestamp=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
                open=101,
                high=102,
                low=100,
                close=101,
                volume=1,
            ),
        ]


class _Execution:
    def __init__(self) -> None:
        self.orders: list[OrderIntent] = []

    def execute(self, order: OrderIntent) -> Fill:
        self.orders.append(order)
        return Fill(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=100.0,
            timestamp=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        )


class _Clock:
    def now(self):
        return datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)


def test_backtest_runner_loads_bundle_and_executes_orders(tmp_path) -> None:
    strategy_file = tmp_path / "runner_strategy.py"
    strategy_file.write_text(
        textwrap.dedent(
            """
            from vibetrading_V2.core.types import OrderIntent
            from vibetrading_V2.strategy.base import Strategy
            from vibetrading_V2.strategy.bundle import StrategyBundle, StrategyMeta

            class RunnerTestStrategy(Strategy):
                def on_bar(self, bar):
                    if bar.close >= bar.open:
                        return [OrderIntent(symbol=bar.symbol, side="buy", quantity=1.0)]
                    return []

            def get_bundle():
                return StrategyBundle(
                    meta=StrategyMeta(
                        name="runner_test",
                        universe=["BTC-USD"],
                        timeframe="1m",
                        required_fields=["open", "close"],
                    ),
                    build=RunnerTestStrategy,
                )
            """
        ),
        encoding="utf-8",
    )

    execution = _Execution()
    logger = _Logger()
    ports = RunnerPorts(
        data_source=_DataSource(),
        execution=execution,
        clock=_Clock(),
        logger=logger,
    )

    result = run_backtest(str(strategy_file), ports=ports)

    assert result.mode == "backtest"
    assert result.strategy_name == "runner_test"
    assert result.bars_processed == 2
    assert result.orders_submitted == 2
    assert len(result.fills) == 2
    assert len(execution.orders) == 2
    assert logger.messages and "runner_test" in logger.messages[0]
