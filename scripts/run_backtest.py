"""
Backtest Runner CLI
Command-line interface for running backtests and validation.
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from decimal import Decimal

from shared.config import get_settings
from shared.models import Market, TradingMode

from backtest.data_loader import BacktestDataLoader, create_candle_provider
from backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from backtest.walk_forward import WalkForwardConfig, WalkForwardValidator, generate_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def print_result(result: BacktestResult) -> None:
    """Print backtest results to console."""
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Strategy: {result.config.strategy_name}")
    print(f"Period: {result.config.start_date.date()} to {result.config.end_date.date()}")
    print(f"Symbols: {', '.join(result.config.symbols)}")
    print("-" * 60)
    print(f"Total Return: {result.total_return_pct:+.2f}%")
    print(f"Max Drawdown: {result.max_drawdown_pct:.2f}%")
    print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
    print("-" * 60)
    print(f"Total Trades: {result.total_trades}")
    print(f"Win Rate: {result.win_rate_pct:.1f}%")
    print(f"Profit Factor: {result.profit_factor:.2f}")
    print(f"Avg Win: {result.avg_win_pct:.2f}%")
    print(f"Avg Loss: {result.avg_loss_pct:.2f}%")
    print("=" * 60 + "\n")


def run_backtest(args) -> int:
    """Run a single backtest."""
    config = BacktestConfig(
        market=Market(args.market),
        strategy_name=args.strategy,
        symbols=args.symbols.split(","),
        start_date=datetime.fromisoformat(args.start),
        end_date=datetime.fromisoformat(args.end),
        initial_capital=Decimal(args.capital),
        random_seed=args.seed,
    )
    
    engine = BacktestEngine(config)
    
    # Load data
    loader = BacktestDataLoader(Market(args.market))
    candles = loader.load_candles(
        config.start_date,
        config.end_date,
        config.symbols,
        args.interval,
    )
    
    # Run backtest
    result = engine.run(candles)
    
    # Print results
    print_result(result)
    
    return 0


def run_walk_forward(args) -> int:
    """Run walk-forward validation."""
    config = WalkForwardConfig(
        market=Market(args.market),
        strategy_name=args.strategy,
        symbols=args.symbols.split(","),
        start_date=datetime.fromisoformat(args.start),
        end_date=datetime.fromisoformat(args.end),
        in_sample_days=args.is_days,
        out_of_sample_days=args.oos_days,
        step_days=args.step_days,
        initial_capital=Decimal(args.capital),
        random_seed=args.seed,
    )
    
    # Create candle provider
    provider = create_candle_provider(Market(args.market), args.interval)
    
    # Run walk-forward
    validator = WalkForwardValidator(config, provider)
    result = validator.run()
    
    # Print report
    report = generate_report(result)
    print(report)
    
    return 0


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Trading System Backtest Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Backtest command
    bt_parser = subparsers.add_parser("backtest", help="Run single backtest")
    bt_parser.add_argument("--strategy", required=True, help="Strategy name")
    bt_parser.add_argument("--market", default="crypto", choices=["crypto", "kr", "us"])
    bt_parser.add_argument("--symbols", required=True, help="Comma-separated symbols")
    bt_parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    bt_parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    bt_parser.add_argument("--capital", default="100000", help="Initial capital")
    bt_parser.add_argument("--interval", default="1d", help="Candle interval")
    bt_parser.add_argument("--seed", type=int, default=42, help="Random seed")
    bt_parser.set_defaults(func=run_backtest)
    
    # Walk-forward command
    wf_parser = subparsers.add_parser("walkforward", help="Run walk-forward validation")
    wf_parser.add_argument("--strategy", required=True, help="Strategy name")
    wf_parser.add_argument("--market", default="crypto", choices=["crypto", "kr", "us"])
    wf_parser.add_argument("--symbols", required=True, help="Comma-separated symbols")
    wf_parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    wf_parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    wf_parser.add_argument("--is-days", type=int, default=252, help="In-sample days")
    wf_parser.add_argument("--oos-days", type=int, default=63, help="Out-of-sample days")
    wf_parser.add_argument("--step-days", type=int, default=63, help="Step size days")
    wf_parser.add_argument("--capital", default="100000", help="Initial capital")
    wf_parser.add_argument("--interval", default="1d", help="Candle interval")
    wf_parser.add_argument("--seed", type=int, default=42, help="Random seed")
    wf_parser.set_defaults(func=run_walk_forward)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
