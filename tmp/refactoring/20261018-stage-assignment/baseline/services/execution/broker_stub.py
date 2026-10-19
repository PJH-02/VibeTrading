"""
Broker Stub / Paper Trading Adapter
Simulates order execution using fill_logic.py.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import uuid4

from shared.fill_logic import FillResult, get_fill_simulator
from shared.models import Fill, Market, Order, OrderStatus, TradingMode

from .base import BrokerAdapter, OrderError

logger = logging.getLogger(__name__)


class BrokerStub(BrokerAdapter):
    """
    Paper trading / simulation broker adapter.
    
    Uses fill_logic.py for realistic fill simulation
    with slippage, latency, and fees.
    """
    
    def __init__(
        self,
        market: Market,
        initial_balance: Decimal = Decimal("100000"),
        random_seed: Optional[int] = None,
    ) -> None:
        """
        Initialize broker stub.
        
        Args:
            market: Market scope
            initial_balance: Starting balance
            random_seed: Seed for reproducible fills
        """
        super().__init__(market)
        self._balance = initial_balance
        self._random_seed = random_seed
        self._open_orders: Dict[str, Order] = {}  # order_id -> order
        self._order_history: List[Order] = []
        self._fills: List[Fill] = []
        self._last_prices: Dict[str, Decimal] = {}
    
    async def connect(self) -> None:
        """Connect (no-op for stub)."""
        logger.info(f"BrokerStub connected for {self._market}")
        self._connected = True
    
    async def disconnect(self) -> None:
        """Disconnect (no-op for stub)."""
        logger.info("BrokerStub disconnected")
        self._connected = False
    
    def set_price(self, symbol: str, price: Decimal) -> None:
        """
        Update last price for a symbol.
        
        Used by backtesting engine to sync prices.
        """
        self._last_prices[symbol] = price
    
    async def submit_order(self, order: Order) -> Order:
        """Submit order for simulated execution."""
        # Assign external ID
        order.external_id = f"STUB-{uuid4().hex[:8]}"
        order.status = OrderStatus.SUBMITTED
        order.submitted_at = datetime.utcnow()
        order.updated_at = datetime.utcnow()
        
        logger.info(f"Order submitted: {order.side} {order.quantity} {order.symbol} @ {order.order_type}")
        
        # Store in open orders
        self._open_orders[str(order.id)] = order
        
        # Simulate immediate fill for market orders
        if order.order_type.value == "market":
            await self._execute_fill(order)
        
        # Notify callback
        if self._on_order_update_callback:
            self._on_order_update_callback(order)
        
        return order
    
    async def _execute_fill(self, order: Order) -> None:
        """Execute a fill simulation."""
        # Get market price
        market_price = self._last_prices.get(order.symbol)
        if market_price is None:
            logger.warning(f"No price available for {order.symbol}, using order price")
            market_price = order.price or Decimal("0")
        
        if market_price <= 0:
            order.status = OrderStatus.REJECTED
            order.error_message = "No valid price available"
            return
        
        # Simulate fill
        simulator = get_fill_simulator(self._random_seed)
        result = simulator.simulate_fill(order, market_price)
        
        # Apply latency delay (async simulation)
        await asyncio.sleep(result.latency_ms / 1000.0)
        
        # Update order
        order.filled_quantity = order.quantity
        order.status = OrderStatus.FILLED
        order.filled_at = datetime.utcnow()
        order.updated_at = datetime.utcnow()
        
        # Update balance
        notional = result.fill.quantity * result.executed_price
        if order.side.value == "buy":
            self._balance -= notional + result.commission
        else:
            self._balance += notional - result.commission
        
        # Store fill
        self._fills.append(result.fill)
        
        # Remove from open orders
        self._open_orders.pop(str(order.id), None)
        self._order_history.append(order)
        
        logger.info(
            f"Order filled: {order.symbol} @ {result.executed_price} "
            f"(slippage: {result.slippage_bps:.1f} bps, latency: {result.latency_ms}ms)"
        )
        
        # Notify callbacks
        if self._on_fill_callback:
            self._on_fill_callback(result.fill)
        if self._on_order_update_callback:
            self._on_order_update_callback(order)
    
    async def cancel_order(self, order: Order) -> Order:
        """Cancel an open order."""
        order_id = str(order.id)
        
        if order_id not in self._open_orders:
            raise OrderError("Order not found in open orders", order)
        
        order = self._open_orders.pop(order_id)
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = datetime.utcnow()
        order.updated_at = datetime.utcnow()
        
        self._order_history.append(order)
        
        logger.info(f"Order cancelled: {order.id}")
        
        if self._on_order_update_callback:
            self._on_order_update_callback(order)
        
        return order
    
    async def get_order_status(self, order: Order) -> Order:
        """Get current order status."""
        order_id = str(order.id)
        
        if order_id in self._open_orders:
            return self._open_orders[order_id]
        
        # Check history
        for hist_order in self._order_history:
            if str(hist_order.id) == order_id:
                return hist_order
        
        return order
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders."""
        orders = list(self._open_orders.values())
        
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        
        return orders
    
    async def get_account_balance(self) -> Decimal:
        """Get current balance."""
        return self._balance
    
    def get_fills(self) -> List[Fill]:
        """Get all fills."""
        return self._fills.copy()
    
    def reset(self, initial_balance: Optional[Decimal] = None) -> None:
        """
        Reset broker state (for backtesting).
        
        Args:
            initial_balance: New starting balance
        """
        if initial_balance:
            self._balance = initial_balance
        self._open_orders.clear()
        self._order_history.clear()
        self._fills.clear()
        self._last_prices.clear()
