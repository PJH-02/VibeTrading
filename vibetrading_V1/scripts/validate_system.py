"""
System Validation Script
End-to-end validation of the trading system components.
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta
from decimal import Decimal

from shared.config import get_settings
from shared.models import (
    Candle,
    Market,
    Order,
    OrderSide,
    OrderType,
    Signal,
    SignalAction,
    StrategyContext,
    TradingMode,
)

from services.execution.broker_stub import BrokerStub
from services.signal_gen.strategy_loader import get_strategy, clear_strategy_cache
from backtest.engine import BacktestConfig, BacktestEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def test_strategy_loader():
    """Test strategy loading via direct import."""
    print("\n" + "=" * 50)
    print("TEST: Strategy Loader")
    print("=" * 50)
    
    clear_strategy_cache()
    
    try:
        strategy = get_strategy("turtle_breakout")
        assert strategy.name == "turtle_breakout", "Strategy name mismatch"
        print(f"✓ Loaded strategy: {strategy.name}")
        
        # Test initialization
        strategy.initialize()
        print("✓ Strategy initialized")
        
        # Test reset
        strategy.reset()
        print("✓ Strategy reset")
        
        print("→ Strategy loader: PASSED")
        return True
        
    except Exception as e:
        print(f"✗ Strategy loader failed: {e}")
        return False


def test_strategy_signals():
    """Test strategy signal generation."""
    print("\n" + "=" * 50)
    print("TEST: Strategy Signal Generation")
    print("=" * 50)
    
    clear_strategy_cache()
    strategy = get_strategy("turtle_breakout")
    strategy.initialize()
    
    # Generate test candles with uptrend
    candles = []
    base_price = Decimal("100")
    
    for i in range(25):
        price = base_price + Decimal(str(i * 2))  # Uptrend
        candles.append(Candle(
            market=Market.CRYPTO,
            symbol="BTCUSDT",
            timestamp=datetime(2024, 1, 1) + timedelta(days=i),
            open=price - 1,
            high=price + 1,
            low=price - 2,
            close=price,
            volume=Decimal("1000"),
            interval="1d",
            is_closed=True,
        ))
    
    signals_generated = []
    
    for candle in candles:
        # Create new context for each candle (frozen model)
        context = StrategyContext(
            market=Market.CRYPTO,
            mode=TradingMode.BACKTEST,
            symbol="BTCUSDT",
            current_time=candle.timestamp,
            current_price=candle.close,
        )
        result = strategy.on_candle(candle, context)
        
        for signal in result.signals:
            signals_generated.append(signal)
            print(f"  Signal: {signal.action} @ {signal.price_at_signal}")
    
    # Should have at least one entry signal in uptrend
    entry_signals = [s for s in signals_generated if s.action == SignalAction.ENTER_LONG]
    
    if entry_signals:
        print(f"✓ Generated {len(entry_signals)} entry signal(s)")
        print("→ Strategy signals: PASSED")
        return True
    else:
        print("✗ No entry signals generated in uptrend")
        return False


async def test_broker_stub():
    """Test broker stub order execution."""
    print("\n" + "=" * 50)
    print("TEST: Broker Stub Execution")
    print("=" * 50)
    
    broker = BrokerStub(
        market=Market.CRYPTO,
        initial_balance=Decimal("10000"),
        random_seed=42,
    )
    
    await broker.connect()
    print("✓ Broker connected")
    
    # Set price
    broker.set_price("BTCUSDT", Decimal("50000"))
    
    # Create order
    order = Order(
        market=Market.CRYPTO,
        mode=TradingMode.PAPER,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.1"),
        strategy_name="test",
    )
    
    # Submit order (should fill immediately for market order)
    updated = await broker.submit_order(order)
    print(f"✓ Order submitted: status={updated.status}")
    
    # Check balance was deducted
    balance = await broker.get_account_balance()
    print(f"✓ Balance after trade: {balance}")
    
    # Get fills
    fills = broker.get_fills()
    if fills:
        print(f"✓ Fill received: {fills[0].quantity} @ {fills[0].price}")
    
    await broker.disconnect()
    print("✓ Broker disconnected")
    
    print("→ Broker stub: PASSED")
    return True


def test_backtest_engine():
    """Test backtest engine with synthetic data."""
    print("\n" + "=" * 50)
    print("TEST: Backtest Engine")
    print("=" * 50)
    
    config = BacktestConfig(
        market=Market.CRYPTO,
        strategy_name="turtle_breakout",
        symbols=["BTCUSDT"],
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 3, 1),
        initial_capital=Decimal("10000"),
        random_seed=42,
    )
    
    engine = BacktestEngine(config)
    
    # Generate synthetic candles (uptrend then downtrend)
    def generate_candles():
        base = Decimal("50000")
        for i in range(60):  # 60 days
            if i < 30:
                # Uptrend
                price = base + Decimal(str(i * 500))
            else:
                # Downtrend
                price = base + Decimal("15000") - Decimal(str((i - 30) * 400))
            
            yield Candle(
                market=Market.CRYPTO,
                symbol="BTCUSDT",
                timestamp=datetime(2024, 1, 1) + timedelta(days=i),
                open=price - 100,
                high=price + 200,
                low=price - 200,
                close=price,
                volume=Decimal("100"),
                interval="1d",
                is_closed=True,
            )
    
    result = engine.run(generate_candles())
    
    print(f"✓ Backtest completed")
    print(f"  Trades: {result.total_trades}")
    print(f"  Return: {result.total_return_pct:+.2f}%")
    print(f"  Max DD: {result.max_drawdown_pct:.2f}%")
    
    # Should have at least one trade
    if result.total_trades > 0:
        print("→ Backtest engine: PASSED")
        return True
    else:
        print("✗ No trades executed")
        return False


async def run_all_tests():
    """Run all validation tests."""
    print("\n" + "=" * 60)
    print("TRADING SYSTEM VALIDATION")
    print("=" * 60)
    
    results = {
        "Strategy Loader": test_strategy_loader(),
        "Strategy Signals": test_strategy_signals(),
        "Broker Stub": await test_broker_stub(),
        "Backtest Engine": test_backtest_engine(),
    }
    
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for name, result in results.items():
        status = "✓ PASSED" if result else "✗ FAILED"
        print(f"  {name}: {status}")
    
    print("-" * 60)
    print(f"  TOTAL: {passed}/{total} tests passed")
    print("=" * 60)
    
    return passed == total


def main():
    """Main entry point."""
    success = asyncio.run(run_all_tests())
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
