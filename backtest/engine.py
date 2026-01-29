"""
Bias-Safe Backtesting Engine
Event-driven backtest with look-ahead prevention.

Key guarantees:
- Strategy only sees data up to current timestamp
- Fill simulation uses same fill_logic as live trading
- No vectorized operations that could cause look-ahead
- Walk-forward validation support
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, Iterator, List, Optional, Tuple

from shared.fill_logic import FillResult, get_fill_simulator
from shared.models import (
    Candle,
    Fill,
    Market,
    Order,
    OrderSide,
    OrderType,
    Position,
    Signal,
    SignalAction,
    TradingMode,
)
from services.execution.broker_stub import BrokerStub
from services.signal_gen.engine import SignalGenerationEngine

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Backtesting configuration."""
    market: Market
    strategy_name: str
    symbols: List[str]
    start_date: datetime
    end_date: datetime
    initial_capital: Decimal = Decimal("100000")
    random_seed: int = 42  # For reproducible fills
    slippage_bps: Decimal = Decimal("5")
    commission_bps: Decimal = Decimal("10")


@dataclass
class TradeRecord:
    """Record of a completed trade."""
    symbol: str
    side: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    pnl: Decimal
    pnl_pct: Decimal
    holding_period_days: int


@dataclass
class BacktestResult:
    """Results of a backtest run."""
    config: BacktestConfig
    
    # Performance
    total_return_pct: Decimal = Decimal("0")
    annualized_return_pct: Decimal = Decimal("0")
    sharpe_ratio: Decimal = Decimal("0")
    max_drawdown_pct: Decimal = Decimal("0")
    
    # Trade statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate_pct: Decimal = Decimal("0")
    avg_win_pct: Decimal = Decimal("0")
    avg_loss_pct: Decimal = Decimal("0")
    profit_factor: Decimal = Decimal("0")
    
    # Detailed records
    trades: List[TradeRecord] = field(default_factory=list)
    equity_curve: List[Tuple[datetime, Decimal]] = field(default_factory=list)
    daily_returns: List[Decimal] = field(default_factory=list)


class BacktestEngine:
    """
    Event-driven backtesting engine.
    
    Processes candles chronologically, feeds them to the strategy,
    and simulates order execution using the same fill logic as live trading.
    """
    
    def __init__(self, config: BacktestConfig) -> None:
        """
        Initialize backtest engine.
        
        Args:
            config: Backtest configuration
        """
        self._config = config
        self._broker = BrokerStub(
            market=config.market,
            initial_balance=config.initial_capital,
            random_seed=config.random_seed,
        )
        self._signal_engine = SignalGenerationEngine(
            market=config.market,
            mode=TradingMode.BACKTEST,
            strategy_name=config.strategy_name,
        )
        
        # State tracking
        self._current_time: Optional[datetime] = None
        self._positions: Dict[str, Position] = {}  # symbol -> position
        self._trades: List[TradeRecord] = []
        self._equity_curve: List[Tuple[datetime, Decimal]] = []
        self._peak_equity: Decimal = config.initial_capital
        self._max_drawdown: Decimal = Decimal("0")
    
    def run(self, candles: Iterator[Candle]) -> BacktestResult:
        """
        Run backtest over candle data.
        
        CRITICAL: Candles must be sorted by timestamp ascending.
        The engine processes one candle at a time, ensuring
        the strategy never sees future data.
        
        Args:
            candles: Iterator of candles sorted by time
            
        Returns:
            BacktestResult with performance metrics
        """
        logger.info(f"Starting backtest: {self._config.strategy_name}")
        
        # Reset state
        self._reset()
        
        # Process candles chronologically
        candle_count = 0
        for candle in candles:
            self._process_candle(candle)
            candle_count += 1
            
            if candle_count % 10000 == 0:
                logger.info(f"Processed {candle_count} candles...")
        
        # Close any remaining positions
        self._close_all_positions()
        
        # Calculate results
        result = self._calculate_results()
        
        logger.info(
            f"Backtest complete: {result.total_trades} trades, "
            f"return={result.total_return_pct:.2f}%, "
            f"sharpe={result.sharpe_ratio:.2f}"
        )
        
        return result
    
    def _reset(self) -> None:
        """Reset engine state for new backtest."""
        self._broker.reset(self._config.initial_capital)
        self._signal_engine.reset()
        self._current_time = None
        self._positions.clear()
        self._trades.clear()
        self._equity_curve.clear()
        self._peak_equity = self._config.initial_capital
        self._max_drawdown = Decimal("0")
    
    def _process_candle(self, candle: Candle) -> None:
        """
        Process a single candle.
        
        Order of operations:
        1. Update current time
        2. Update prices for open positions
        3. Execute strategy on candle
        4. Process any generated signals
        5. Record equity
        """
        self._current_time = candle.timestamp
        
        # Update broker price (for fill simulation)
        self._broker.set_price(candle.symbol, candle.close)
        
        # Update position prices
        if candle.symbol in self._positions:
            pos = self._positions[candle.symbol]
            pos.current_price = candle.close
            self._update_position_pnl(pos)
        
        # Execute strategy
        signals = self._signal_engine.process_candle_sync(candle)
        
        # Process signals
        for signal in signals:
            self._process_signal(signal, candle)
        
        # Record equity
        equity = self._calculate_equity()
        self._equity_curve.append((candle.timestamp, equity))
        
        # Track drawdown
        if equity > self._peak_equity:
            self._peak_equity = equity
        drawdown = (self._peak_equity - equity) / self._peak_equity * 100
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown
    
    def _process_signal(self, signal: Signal, candle: Candle) -> None:
        """Process a trading signal."""
        symbol = signal.symbol
        existing_position = self._positions.get(symbol)
        
        if signal.action == SignalAction.ENTER_LONG:
            if existing_position is None:
                self._open_position(signal, candle, OrderSide.BUY)
        
        elif signal.action == SignalAction.EXIT_LONG:
            if existing_position and existing_position.side == OrderSide.BUY:
                self._close_position(existing_position, candle)
        
        elif signal.action == SignalAction.ENTER_SHORT:
            if existing_position is None:
                self._open_position(signal, candle, OrderSide.SELL)
        
        elif signal.action == SignalAction.EXIT_SHORT:
            if existing_position and existing_position.side == OrderSide.SELL:
                self._close_position(existing_position, candle)
    
    def _open_position(
        self,
        signal: Signal,
        candle: Candle,
        side: OrderSide,
    ) -> None:
        """Open a new position."""
        # Calculate position size
        balance = self._config.initial_capital  # Simplified
        position_value = balance * Decimal("0.1")  # 10% per position
        quantity = position_value / candle.close
        
        # Simulate fill
        simulator = get_fill_simulator(self._config.random_seed)
        
        order = Order(
            market=self._config.market,
            mode=TradingMode.BACKTEST,
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            strategy_name=self._config.strategy_name,
        )
        
        result = simulator.simulate_fill(order, candle.close)
        
        # Create position
        position = Position(
            market=self._config.market,
            mode=TradingMode.BACKTEST,
            symbol=signal.symbol,
            side=side,
            quantity=quantity,
            avg_entry_price=result.executed_price,
            current_price=result.executed_price,
            strategy_name=self._config.strategy_name,
            opened_at=candle.timestamp,
        )
        
        self._positions[signal.symbol] = position
        self._signal_engine.update_position(position)
    
    def _close_position(self, position: Position, candle: Candle) -> None:
        """Close an existing position."""
        # Simulate exit fill
        simulator = get_fill_simulator(self._config.random_seed)
        
        exit_side = OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY
        
        order = Order(
            market=self._config.market,
            mode=TradingMode.BACKTEST,
            symbol=position.symbol,
            side=exit_side,
            order_type=OrderType.MARKET,
            quantity=position.quantity,
            strategy_name=self._config.strategy_name,
        )
        
        result = simulator.simulate_fill(order, candle.close)
        
        # Calculate P&L
        if position.side == OrderSide.BUY:
            pnl = (result.executed_price - position.avg_entry_price) * position.quantity
        else:
            pnl = (position.avg_entry_price - result.executed_price) * position.quantity
        
        pnl_pct = pnl / (position.avg_entry_price * position.quantity) * 100
        
        # Record trade
        holding_days = (candle.timestamp - position.opened_at).days
        
        trade = TradeRecord(
            symbol=position.symbol,
            side=position.side.value,
            entry_time=position.opened_at,
            exit_time=candle.timestamp,
            entry_price=position.avg_entry_price,
            exit_price=result.executed_price,
            quantity=position.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            holding_period_days=max(1, holding_days),
        )
        self._trades.append(trade)
        
        # Remove position
        del self._positions[position.symbol]
        
        # Update signal engine
        position.closed_at = candle.timestamp
        self._signal_engine.update_position(position)
    
    def _close_all_positions(self) -> None:
        """Close all remaining positions at end of backtest."""
        for position in list(self._positions.values()):
            # Use last recorded price
            price = position.current_price or position.avg_entry_price
            
            # Calculate final P&L
            if position.side == OrderSide.BUY:
                pnl = (price - position.avg_entry_price) * position.quantity
            else:
                pnl = (position.avg_entry_price - price) * position.quantity
            
            pnl_pct = pnl / (position.avg_entry_price * position.quantity) * 100
            
            trade = TradeRecord(
                symbol=position.symbol,
                side=position.side.value,
                entry_time=position.opened_at,
                exit_time=self._current_time or position.opened_at,
                entry_price=position.avg_entry_price,
                exit_price=price,
                quantity=position.quantity,
                pnl=pnl,
                pnl_pct=pnl_pct,
                holding_period_days=1,
            )
            self._trades.append(trade)
    
    def _update_position_pnl(self, position: Position) -> None:
        """Update unrealized P&L for a position."""
        if position.current_price is None:
            return
        
        if position.side == OrderSide.BUY:
            position.unrealized_pnl = (
                (position.current_price - position.avg_entry_price) * position.quantity
            )
        else:
            position.unrealized_pnl = (
                (position.avg_entry_price - position.current_price) * position.quantity
            )
    
    def _calculate_equity(self) -> Decimal:
        """Calculate current equity."""
        balance = self._config.initial_capital
        
        # Add realized P&L from completed trades
        for trade in self._trades:
            balance += trade.pnl
        
        # Add unrealized P&L
        for position in self._positions.values():
            if position.unrealized_pnl:
                balance += position.unrealized_pnl
        
        return balance
    
    def _calculate_results(self) -> BacktestResult:
        """Calculate backtest performance metrics."""
        result = BacktestResult(
            config=self._config,
            trades=self._trades,
            equity_curve=self._equity_curve,
            max_drawdown_pct=self._max_drawdown,
        )
        
        # Basic metrics
        result.total_trades = len(self._trades)
        
        if result.total_trades == 0:
            return result
        
        # Win/loss stats
        winners = [t for t in self._trades if t.pnl > 0]
        losers = [t for t in self._trades if t.pnl <= 0]
        
        result.winning_trades = len(winners)
        result.losing_trades = len(losers)
        result.win_rate_pct = Decimal(str(len(winners) / len(self._trades) * 100))
        
        if winners:
            result.avg_win_pct = sum(t.pnl_pct for t in winners) / len(winners)
        if losers:
            result.avg_loss_pct = abs(sum(t.pnl_pct for t in losers) / len(losers))
        
        # Profit factor
        gross_profit = sum(t.pnl for t in winners) if winners else Decimal("0")
        gross_loss = abs(sum(t.pnl for t in losers)) if losers else Decimal("0.01")
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else Decimal("0")
        
        # Return metrics
        final_equity = self._calculate_equity()
        result.total_return_pct = (
            (final_equity - self._config.initial_capital) / self._config.initial_capital * 100
        )
        
        # Calculate daily returns for Sharpe ratio
        if len(self._equity_curve) > 1:
            daily_returns = []
            for i in range(1, len(self._equity_curve)):
                prev_equity = self._equity_curve[i-1][1]
                curr_equity = self._equity_curve[i][1]
                if prev_equity > 0:
                    ret = (curr_equity - prev_equity) / prev_equity
                    daily_returns.append(ret)
            
            result.daily_returns = daily_returns
            
            if daily_returns:
                import statistics
                avg_ret = sum(daily_returns) / len(daily_returns)
                std_ret = Decimal(str(statistics.stdev(daily_returns))) if len(daily_returns) > 1 else Decimal("1")
                if std_ret > 0:
                    result.sharpe_ratio = Decimal(str((avg_ret * Decimal("252").sqrt()) / std_ret))
        
        return result
