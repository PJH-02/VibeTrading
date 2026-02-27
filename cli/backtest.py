"""Backtest composition root for vibetrading_V2."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vibetrading_V2.data.parquet_source import ParquetDataSource
from vibetrading_V2.execution.simulator import SimulatedExecution
from vibetrading_V2.runner.backtest import run_backtest
from vibetrading_V2.runner.runtime import RunnerPorts


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=timezone.utc)


class _PrintLogger:
    def info(self, message: str) -> None:
        print(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run vibetrading_V2 backtest")
    parser.add_argument("--strategy", default="my_strategy_a")
    parser.add_argument("--strategies-dir", default="vibetrading_V2/strategies")
    parser.add_argument("--data-root", default="data/curated")
    args = parser.parse_args()

    clock = _SystemClock()
    ports = RunnerPorts(
        data_source=ParquetDataSource(args.data_root),
        execution=SimulatedExecution(clock),
        clock=clock,
        logger=_PrintLogger(),
    )

    result = run_backtest(args.strategy, ports=ports, strategies_dir=args.strategies_dir)
    print(
        f"[backtest] strategy={result.strategy_name} bars={result.bars_processed} "
        f"orders={result.orders_submitted}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
