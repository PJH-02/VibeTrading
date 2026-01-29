"""
Unified Fill Logic
Single source of truth for slippage, fees, and fill simulation.
Used identically by backtest and live execution.
"""

import random
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from shared.config import get_settings
from shared.models import Fill, Market, Order, OrderSide, OrderType, TradingMode


@dataclass
class FillResult:
    """Result of a fill simulation."""
    fill: Fill
    executed_price: Decimal
    slippage_bps: Decimal
    latency_ms: int
    commission: Decimal


class FillSimulator:
    """
    Simulates order fills with slippage, latency, and fees.
    
    This class is the SINGLE SOURCE for fill logic, used by both
    backtesting and live paper trading.
    """
    
    def __init__(
        self,
        slippage_bps: Optional[int] = None,
        min_latency_ms: Optional[int] = None,
        random_seed: Optional[int] = None,
    ) -> None:
        """
        Initialize fill simulator.
        
        Args:
            slippage_bps: Override slippage basis points
            min_latency_ms: Override minimum latency
            random_seed: Seed for reproducible simulations
        """
        settings = get_settings()
        self._settings = settings.fill_logic
        self._slippage_override = slippage_bps
        self._latency_override = min_latency_ms
        
        if random_seed is not None:
            random.seed(random_seed)
    
    def get_slippage_bps(self, market: Market) -> int:
        """Get slippage in basis points for market."""
        if self._slippage_override is not None:
            return self._slippage_override
        return self._settings.get_slippage_bps(market)
    
    def get_min_latency_ms(self) -> int:
        """Get minimum latency in milliseconds."""
        if self._latency_override is not None:
            return self._latency_override
        return self._settings.min_latency_ms
    
    def calculate_slippage(
        self,
        market: Market,
        side: OrderSide,
        base_price: Decimal,
        quantity: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """
        Calculate slippage for an order.
        
        Slippage is always pessimistic:
        - Buy orders: price increases (worse fill)
        - Sell orders: price decreases (worse fill)
        
        Args:
            market: Market type
            side: Order side
            base_price: Base execution price
            quantity: Order quantity
            
        Returns:
            Tuple of (adjusted_price, slippage_bps)
        """
        base_bps = self.get_slippage_bps(market)
        
        # Add random variation (0.5x to 1.5x base slippage)
        variation = Decimal(str(random.uniform(0.5, 1.5)))
        actual_bps = Decimal(str(base_bps)) * variation
        
        # Size impact: larger orders have more slippage
        # Simple linear model: add 1 bps per 10% of typical volume
        size_factor = Decimal("1.0")  # Could be enhanced with volume data
        actual_bps *= size_factor
        
        # Calculate price adjustment
        # bps = 0.01%, so 10 bps = 0.1%
        adjustment = base_price * actual_bps / Decimal("10000")
        
        if side == OrderSide.BUY:
            # Buys get worse price (higher)
            adjusted_price = base_price + adjustment
        else:
            # Sells get worse price (lower)
            adjusted_price = base_price - adjustment
        
        return adjusted_price, actual_bps
    
    def calculate_latency_ms(self) -> int:
        """
        Calculate execution latency.
        
        Returns simulated latency in milliseconds.
        Never returns zero (zero latency is forbidden).
        """
        min_latency = self.get_min_latency_ms()
        
        # Add random jitter (0 to 100% additional latency)
        jitter = random.uniform(0, 1.0)
        actual_latency = int(min_latency * (1 + jitter))
        
        # Ensure minimum latency
        return max(actual_latency, 1)
    
    def calculate_commission(
        self,
        market: Market,
        quantity: Decimal,
        price: Decimal,
    ) -> tuple[Decimal, str]:
        """
        Calculate trading commission/fees.
        
        Args:
            market: Market type
            quantity: Trade quantity
            price: Execution price
            
        Returns:
            Tuple of (commission_amount, commission_asset)
        """
        notional = quantity * price
        
        # Fee rates by market (conservative estimates)
        fee_rates = {
            Market.CRYPTO: Decimal("0.001"),   # 0.1% (10 bps)
            Market.KR: Decimal("0.00015"),     # 0.015% base + tax
            Market.US: Decimal("0.0001"),      # $0.01/share or 0.01%
        }
        
        rate = fee_rates.get(market, Decimal("0.001"))
        commission = notional * rate
        
        # Commission asset (typically quote currency)
        commission_asset = "USD" if market == Market.US else "USDT" if market == Market.CRYPTO else "KRW"
        
        return commission, commission_asset
    
    def simulate_fill(
        self,
        order: Order,
        market_price: Decimal,
        timestamp: Optional[datetime] = None,
    ) -> FillResult:
        """
        Simulate a fill for an order.
        
        This is the core fill simulation method used by both
        backtesting and paper trading.
        
        Args:
            order: Order to fill
            market_price: Current market price
            timestamp: Fill timestamp (defaults to now)
            
        Returns:
            FillResult with fill details
        """
        timestamp = timestamp or datetime.utcnow()
        
        # Determine base price
        if order.order_type == OrderType.MARKET:
            base_price = market_price
        elif order.order_type == OrderType.LIMIT and order.price:
            # Limit order: use limit price if it would be filled
            if order.side == OrderSide.BUY:
                base_price = min(order.price, market_price)
            else:
                base_price = max(order.price, market_price)
        else:
            base_price = market_price
        
        # Apply slippage
        executed_price, slippage_bps = self.calculate_slippage(
            market=order.market,
            side=order.side,
            base_price=base_price,
            quantity=order.remaining_quantity,
        )
        
        # Calculate latency
        latency_ms = self.calculate_latency_ms()
        
        # Calculate commission
        commission, commission_asset = self.calculate_commission(
            market=order.market,
            quantity=order.remaining_quantity,
            price=executed_price,
        )
        
        # Create fill
        fill = Fill(
            id=uuid4(),
            timestamp=timestamp,
            market=order.market,
            order_id=order.id,
            mode=order.mode,
            symbol=order.symbol,
            side=order.side,
            quantity=order.remaining_quantity,
            price=executed_price,
            commission=commission,
            commission_asset=commission_asset,
            slippage_bps=slippage_bps,
            latency_ms=latency_ms,
            metadata={
                "market_price": str(market_price),
                "order_type": order.order_type.value,
            },
        )
        
        return FillResult(
            fill=fill,
            executed_price=executed_price,
            slippage_bps=slippage_bps,
            latency_ms=latency_ms,
            commission=commission,
        )
    
    def can_fill_limit_order(
        self,
        order: Order,
        market_price: Decimal,
    ) -> bool:
        """
        Check if a limit order can be filled at current price.
        
        Args:
            order: Limit order to check
            market_price: Current market price
            
        Returns:
            True if order can be filled
        """
        if order.order_type != OrderType.LIMIT or order.price is None:
            return True  # Market orders always fillable
        
        if order.side == OrderSide.BUY:
            # Buy limit: fill if market price <= limit price
            return market_price <= order.price
        else:
            # Sell limit: fill if market price >= limit price
            return market_price >= order.price
    
    def can_trigger_stop(
        self,
        order: Order,
        market_price: Decimal,
    ) -> bool:
        """
        Check if a stop order should be triggered.
        
        Args:
            order: Stop order to check
            market_price: Current market price
            
        Returns:
            True if stop should trigger
        """
        if order.order_type not in {OrderType.STOP, OrderType.STOP_LIMIT}:
            return False
        
        if order.stop_price is None:
            return False
        
        if order.side == OrderSide.BUY:
            # Buy stop: trigger if market price >= stop price
            return market_price >= order.stop_price
        else:
            # Sell stop: trigger if market price <= stop price
            return market_price <= order.stop_price


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Convenience Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_default_simulator: Optional[FillSimulator] = None


def get_fill_simulator(random_seed: Optional[int] = None) -> FillSimulator:
    """Get default fill simulator instance."""
    global _default_simulator
    if _default_simulator is None or random_seed is not None:
        _default_simulator = FillSimulator(random_seed=random_seed)
    return _default_simulator


def simulate_fill(
    order: Order,
    market_price: Decimal,
    timestamp: Optional[datetime] = None,
) -> FillResult:
    """Convenience function to simulate a fill."""
    return get_fill_simulator().simulate_fill(order, market_price, timestamp)
