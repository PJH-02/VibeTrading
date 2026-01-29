"""
Walk-Forward Validation
Prevents overfitting by testing on out-of-sample data.

Fixed window sizes (as per assumptions):
- In-Sample: 252 days (1 trading year)
- Out-of-Sample: 63 days (1 quarter)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterator, List, Optional, Tuple

from shared.models import Candle, Market

from .engine import BacktestConfig, BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardWindow:
    """Definition of a walk-forward window."""
    window_id: int
    in_sample_start: datetime
    in_sample_end: datetime
    out_of_sample_start: datetime
    out_of_sample_end: datetime


@dataclass
class WalkForwardConfig:
    """Walk-forward validation configuration."""
    market: Market
    strategy_name: str
    symbols: List[str]
    start_date: datetime
    end_date: datetime
    
    # Window sizes (in days)
    in_sample_days: int = 252  # 1 trading year
    out_of_sample_days: int = 63  # 1 quarter
    
    # Overlap / step
    step_days: int = 63  # Move forward by OOS period
    
    # Per-window config
    initial_capital: Decimal = Decimal("100000")
    random_seed: int = 42


@dataclass
class WalkForwardResult:
    """Results of walk-forward validation."""
    config: WalkForwardConfig
    windows: List[WalkForwardWindow] = field(default_factory=list)
    
    # Aggregated OOS results
    in_sample_results: List[BacktestResult] = field(default_factory=list)
    out_of_sample_results: List[BacktestResult] = field(default_factory=list)
    
    # Summary statistics
    avg_oos_return_pct: Decimal = Decimal("0")
    avg_oos_sharpe: Decimal = Decimal("0")
    avg_oos_win_rate_pct: Decimal = Decimal("0")
    oos_equity_curve: List[Tuple[datetime, Decimal]] = field(default_factory=list)
    
    # Overfitting detection
    is_return_degradation: Decimal = Decimal("0")  # IS return - OOS return
    is_sharpe_degradation: Decimal = Decimal("0")  # IS sharpe - OOS sharpe


class WalkForwardValidator:
    """
    Walk-forward validation engine.
    
    Splits data into rolling in-sample and out-of-sample windows.
    For each window:
    1. Run backtest on in-sample data (optimization period)
    2. Test on out-of-sample data (validation period)
    3. Roll forward to next window
    
    This prevents overfitting to historical data.
    """
    
    def __init__(
        self,
        config: WalkForwardConfig,
        candle_provider: callable,
    ) -> None:
        """
        Initialize walk-forward validator.
        
        Args:
            config: Walk-forward configuration
            candle_provider: Callable(start, end, symbols) -> Iterator[Candle]
        """
        self._config = config
        self._candle_provider = candle_provider
    
    def generate_windows(self) -> List[WalkForwardWindow]:
        """
        Generate walk-forward windows.
        
        Returns:
            List of WalkForwardWindow definitions
        """
        windows = []
        window_id = 0
        
        current_is_start = self._config.start_date
        
        while True:
            is_end = current_is_start + timedelta(days=self._config.in_sample_days)
            oos_start = is_end
            oos_end = oos_start + timedelta(days=self._config.out_of_sample_days)
            
            # Check if OOS period extends beyond end date
            if oos_end > self._config.end_date:
                break
            
            windows.append(WalkForwardWindow(
                window_id=window_id,
                in_sample_start=current_is_start,
                in_sample_end=is_end,
                out_of_sample_start=oos_start,
                out_of_sample_end=oos_end,
            ))
            
            window_id += 1
            current_is_start += timedelta(days=self._config.step_days)
        
        logger.info(f"Generated {len(windows)} walk-forward windows")
        return windows
    
    def run(self) -> WalkForwardResult:
        """
        Run walk-forward validation.
        
        Returns:
            WalkForwardResult with per-window and aggregated metrics
        """
        logger.info(f"Starting walk-forward validation: {self._config.strategy_name}")
        
        result = WalkForwardResult(config=self._config)
        result.windows = self.generate_windows()
        
        if not result.windows:
            logger.warning("No valid walk-forward windows generated")
            return result
        
        # Run each window
        for window in result.windows:
            logger.info(f"Running window {window.window_id}: IS {window.in_sample_start} - {window.in_sample_end}")
            
            # In-sample backtest
            is_result = self._run_window(
                window.in_sample_start,
                window.in_sample_end,
            )
            result.in_sample_results.append(is_result)
            
            # Out-of-sample backtest
            oos_result = self._run_window(
                window.out_of_sample_start,
                window.out_of_sample_end,
            )
            result.out_of_sample_results.append(oos_result)
            
            # Add OOS equity to combined curve
            result.oos_equity_curve.extend(oos_result.equity_curve)
        
        # Calculate aggregate statistics
        self._calculate_aggregates(result)
        
        logger.info(
            f"Walk-forward complete: {len(result.windows)} windows, "
            f"OOS return={result.avg_oos_return_pct:.2f}%, "
            f"degradation={result.is_return_degradation:.2f}%"
        )
        
        return result
    
    def _run_window(
        self,
        start: datetime,
        end: datetime,
    ) -> BacktestResult:
        """Run backtest for a single window."""
        config = BacktestConfig(
            market=self._config.market,
            strategy_name=self._config.strategy_name,
            symbols=self._config.symbols,
            start_date=start,
            end_date=end,
            initial_capital=self._config.initial_capital,
            random_seed=self._config.random_seed,
        )
        
        engine = BacktestEngine(config)
        
        # Get candles for window
        candles = self._candle_provider(
            start,
            end,
            self._config.symbols,
        )
        
        return engine.run(candles)
    
    def _calculate_aggregates(self, result: WalkForwardResult) -> None:
        """Calculate aggregate statistics from window results."""
        if not result.out_of_sample_results:
            return
        
        # OOS averages
        oos_returns = [r.total_return_pct for r in result.out_of_sample_results]
        oos_sharpes = [r.sharpe_ratio for r in result.out_of_sample_results]
        oos_win_rates = [r.win_rate_pct for r in result.out_of_sample_results]
        
        result.avg_oos_return_pct = sum(oos_returns) / len(oos_returns)
        result.avg_oos_sharpe = sum(oos_sharpes) / len(oos_sharpes)
        result.avg_oos_win_rate_pct = sum(oos_win_rates) / len(oos_win_rates)
        
        # IS averages
        is_returns = [r.total_return_pct for r in result.in_sample_results]
        is_sharpes = [r.sharpe_ratio for r in result.in_sample_results]
        
        avg_is_return = sum(is_returns) / len(is_returns)
        avg_is_sharpe = sum(is_sharpes) / len(is_sharpes)
        
        # Degradation (higher = more overfitting)
        result.is_return_degradation = avg_is_return - result.avg_oos_return_pct
        result.is_sharpe_degradation = avg_is_sharpe - result.avg_oos_sharpe


def generate_report(result: WalkForwardResult) -> str:
    """
    Generate human-readable walk-forward report.
    
    Args:
        result: WalkForwardResult to report on
        
    Returns:
        Formatted report string
    """
    lines = [
        "=" * 60,
        "WALK-FORWARD VALIDATION REPORT",
        "=" * 60,
        f"Strategy: {result.config.strategy_name}",
        f"Period: {result.config.start_date.date()} to {result.config.end_date.date()}",
        f"Windows: {len(result.windows)}",
        f"IS Period: {result.config.in_sample_days} days",
        f"OOS Period: {result.config.out_of_sample_days} days",
        "",
        "-" * 60,
        "OUT-OF-SAMPLE PERFORMANCE (What Matters)",
        "-" * 60,
        f"Average Return: {result.avg_oos_return_pct:.2f}%",
        f"Average Sharpe: {result.avg_oos_sharpe:.2f}",
        f"Average Win Rate: {result.avg_oos_win_rate_pct:.1f}%",
        "",
        "-" * 60,
        "OVERFITTING ANALYSIS",
        "-" * 60,
        f"Return Degradation (IS - OOS): {result.is_return_degradation:.2f}%",
        f"Sharpe Degradation (IS - OOS): {result.is_sharpe_degradation:.2f}",
        "",
    ]
    
    # Add warning if significant degradation
    if result.is_return_degradation > 10:
        lines.append("⚠️  WARNING: Significant return degradation - potential overfitting")
    if result.is_sharpe_degradation > 0.5:
        lines.append("⚠️  WARNING: Significant Sharpe degradation - potential overfitting")
    
    lines.append("")
    lines.append("-" * 60)
    lines.append("PER-WINDOW RESULTS")
    lines.append("-" * 60)
    
    for i, (window, is_res, oos_res) in enumerate(zip(
        result.windows,
        result.in_sample_results,
        result.out_of_sample_results,
    )):
        lines.append(
            f"Window {i}: IS={is_res.total_return_pct:+.2f}% | "
            f"OOS={oos_res.total_return_pct:+.2f}% | "
            f"Trades={oos_res.total_trades}"
        )
    
    lines.append("=" * 60)
    
    return "\n".join(lines)
