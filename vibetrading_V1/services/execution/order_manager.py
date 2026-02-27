"""
Order Manager
Converts signals to orders, manages lifecycle, and persists state.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from nats.aio.msg import Msg

from shared.config import get_settings
from shared.database import get_postgres
from shared.messaging import Subjects, deserialize_message, ensure_connected
from shared.models import (
    Fill,
    Market,
    Order,
    OrderEvent,
    OrderSide,
    OrderStatus,
    OrderType,
    Signal,
    SignalAction,
    TradingMode,
)

from .base import BrokerAdapter

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Manages order lifecycle from signal to fill.
    
    Responsibilities:
    - Convert signals to orders
    - Submit orders via broker adapter
    - Handle fill notifications
    - Persist order/fill state
    - Subscribe to kill switch
    """
    
    def __init__(
        self,
        market: Market,
        mode: TradingMode,
        broker: BrokerAdapter,
    ) -> None:
        """
        Initialize order manager.
        
        Args:
            market: Market scope
            mode: Trading mode
            broker: Broker adapter for execution
        """
        self._market = market
        self._mode = mode
        self._broker = broker
        self._running = False
        self._killed = False  # Kill switch state
        self._pending_orders: Dict[UUID, Order] = {}
    
    @property
    def market(self) -> Market:
        """Get market scope."""
        return self._market
    
    @property
    def mode(self) -> TradingMode:
        """Get trading mode."""
        return self._mode
    
    @property
    def is_running(self) -> bool:
        """Check if manager is running."""
        return self._running
    
    @property
    def is_killed(self) -> bool:
        """Check if kill switch is active."""
        return self._killed
    
    async def start(self) -> None:
        """Start order manager."""
        logger.info(f"Starting order manager: market={self._market}, mode={self._mode}")
        
        # Set up broker callbacks
        self._broker.set_on_fill_callback(self._on_fill)
        self._broker.set_on_order_update_callback(self._on_order_update)
        
        # Connect broker
        await self._broker.start()
        
        # In standalone mode, skip NATS subscriptions
        settings = get_settings()
        if not settings.standalone_mode:
            # Subscribe to signals
            messaging = await ensure_connected()
            await messaging.subscribe(
                subject=Subjects.signals(self._market.value),
                handler=self._on_signal_message,
                durable=f"order_manager_{self._market.value}",
                queue="order_managers",
            )
            
            # Subscribe to kill switch
            await messaging.subscribe(
                subject=Subjects.KILL_SWITCH,
                handler=self._on_kill_switch,
                durable=f"kill_switch_{self._market.value}",
            )
        else:
            logger.info("Order manager started (standalone mode - no NATS)")
        
        self._running = True
        logger.info("Order manager started")
    
    async def stop(self) -> None:
        """Stop order manager."""
        self._running = False
        await self._broker.stop()
        logger.info("Order manager stopped")
    
    async def _on_signal_message(self, msg: Msg) -> None:
        """Handle incoming signal message."""
        try:
            signal = deserialize_message(msg.data, Signal)
            await self._process_signal(signal)
            await msg.ack()
        except Exception as e:
            logger.error(f"Error processing signal: {e}")
            await msg.nak(delay=5)
    
    async def _on_kill_switch(self, msg: Msg) -> None:
        """Handle kill switch activation."""
        logger.warning("KILL SWITCH activated - rejecting all new orders")
        self._killed = True
        await msg.ack()
        
        # Cancel all pending orders
        for order in list(self._pending_orders.values()):
            try:
                await self._broker.cancel_order(order)
            except Exception as e:
                logger.error(f"Error cancelling order on kill: {e}")
    
    async def _process_signal(self, signal: Signal) -> None:
        """
        Process a trading signal.
        
        Args:
            signal: Signal to process
        """
        # Check kill switch
        if self._killed:
            logger.warning(f"Signal rejected - kill switch active: {signal.symbol}")
            return
        
        logger.info(f"Processing signal: {signal.action} {signal.symbol}")
        
        # Create order from signal
        order = self._signal_to_order(signal)
        
        # Persist order
        await self._persist_order(order)
        
        # Submit to broker
        try:
            updated_order = await self._broker.submit_order(order)
            self._pending_orders[updated_order.id] = updated_order
            
            # Publish order event
            await self._publish_order_event(updated_order, "submitted")
            
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.error_message = str(e)
            await self._persist_order(order)
            logger.error(f"Order submission failed: {e}")
    
    def _signal_to_order(self, signal: Signal) -> Order:
        """
        Convert signal to order.
        
        Args:
            signal: Trading signal
            
        Returns:
            Order ready for submission
        """
        # Determine order side
        if signal.action in {SignalAction.ENTER_LONG, SignalAction.EXIT_SHORT}:
            side = OrderSide.BUY
        else:
            side = OrderSide.SELL
        
        # Get position size (from settings)
        settings = get_settings()
        balance = settings.initial_balance  # Configurable via INITIAL_BALANCE
        position_size_pct = settings.risk.max_position_size_pct / Decimal("100")
        notional = balance * position_size_pct
        quantity = notional / signal.price_at_signal
        
        return Order(
            market=signal.market,
            mode=signal.mode,
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            strategy_name=signal.strategy_name,
            signal_id=signal.id,
            metadata={"signal_action": signal.action.value},
        )
    
    def _on_fill(self, fill: Fill) -> None:
        """Handle fill notification from broker."""
        asyncio.create_task(self._handle_fill_async(fill))
    
    async def submit_signal_direct(self, signal: Signal) -> None:
        """
        Process a signal directly (for standalone mode without NATS).
        
        Args:
            signal: Trading signal to process
        """
        await self._process_signal(signal)
    
    async def _handle_fill_async(self, fill: Fill) -> None:
        """Async fill handling."""
        logger.info(f"Fill received: {fill.symbol} @ {fill.price}")
        
        # Persist fill
        await self._persist_fill(fill)
        
        # Publish fill event
        await self._publish_fill(fill)
        
        # Update pending orders
        self._pending_orders.pop(fill.order_id, None)
    
    def _on_order_update(self, order: Order) -> None:
        """Handle order status update from broker."""
        asyncio.create_task(self._handle_order_update_async(order))
    
    async def _handle_order_update_async(self, order: Order) -> None:
        """Async order update handling."""
        await self._persist_order(order)
        await self._publish_order_event(order, order.status.value)
        
        if order.is_terminal:
            self._pending_orders.pop(order.id, None)
    
    async def _persist_order(self, order: Order) -> None:
        """Persist order to PostgreSQL (skipped in standalone mode)."""
        settings = get_settings()
        if settings.standalone_mode:
            return
        
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                await session.execute(
                    text("""
                        INSERT INTO orders (
                            id, external_id, market, mode, symbol, side, order_type,
                            quantity, filled_quantity, price, stop_price, status,
                            strategy_name, signal_id, created_at, updated_at,
                            submitted_at, filled_at, cancelled_at, error_message, metadata
                        ) VALUES (
                            :id, :external_id, :market, :mode, :symbol, :side, :order_type,
                            :quantity, :filled_quantity, :price, :stop_price, :status,
                            :strategy_name, :signal_id, :created_at, :updated_at,
                            :submitted_at, :filled_at, :cancelled_at, :error_message, :metadata
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            external_id = EXCLUDED.external_id,
                            filled_quantity = EXCLUDED.filled_quantity,
                            status = EXCLUDED.status,
                            updated_at = EXCLUDED.updated_at,
                            submitted_at = EXCLUDED.submitted_at,
                            filled_at = EXCLUDED.filled_at,
                            cancelled_at = EXCLUDED.cancelled_at,
                            error_message = EXCLUDED.error_message
                    """),
                    {
                        "id": str(order.id),
                        "external_id": order.external_id,
                        "market": order.market.value,
                        "mode": order.mode.value,
                        "symbol": order.symbol,
                        "side": order.side.value,
                        "order_type": order.order_type.value,
                        "quantity": float(order.quantity),
                        "filled_quantity": float(order.filled_quantity),
                        "price": float(order.price) if order.price else None,
                        "stop_price": float(order.stop_price) if order.stop_price else None,
                        "status": order.status.value,
                        "strategy_name": order.strategy_name,
                        "signal_id": str(order.signal_id) if order.signal_id else None,
                        "created_at": order.created_at,
                        "updated_at": order.updated_at,
                        "submitted_at": order.submitted_at,
                        "filled_at": order.filled_at,
                        "cancelled_at": order.cancelled_at,
                        "error_message": order.error_message,
                        "metadata": order.metadata,
                    }
                )
        except Exception as e:
            logger.error(f"Error persisting order: {e}")
    
    async def _persist_fill(self, fill: Fill) -> None:
        """Persist fill to PostgreSQL (skipped in standalone mode)."""
        settings = get_settings()
        if settings.standalone_mode:
            return
        
        try:
            postgres = get_postgres()
            async with postgres.session() as session:
                from sqlalchemy import text
                
                await session.execute(
                    text("""
                        INSERT INTO fills (
                            id, order_id, external_id, market, mode, symbol, side,
                            quantity, price, commission, commission_asset,
                            slippage_bps, latency_ms, filled_at, metadata
                        ) VALUES (
                            :id, :order_id, :external_id, :market, :mode, :symbol, :side,
                            :quantity, :price, :commission, :commission_asset,
                            :slippage_bps, :latency_ms, :filled_at, :metadata
                        )
                    """),
                    {
                        "id": str(fill.id),
                        "order_id": str(fill.order_id),
                        "external_id": fill.external_id,
                        "market": fill.market.value,
                        "mode": fill.mode.value,
                        "symbol": fill.symbol,
                        "side": fill.side.value,
                        "quantity": float(fill.quantity),
                        "price": float(fill.price),
                        "commission": float(fill.commission),
                        "commission_asset": fill.commission_asset,
                        "slippage_bps": float(fill.slippage_bps),
                        "latency_ms": fill.latency_ms,
                        "filled_at": fill.timestamp,
                        "metadata": fill.metadata,
                    }
                )
        except Exception as e:
            logger.error(f"Error persisting fill: {e}")
    
    async def _publish_order_event(self, order: Order, event_type: str) -> None:
        """Publish order event to NATS (skipped in standalone mode)."""
        settings = get_settings()
        if settings.standalone_mode:
            return
        
        try:
            messaging = await ensure_connected()
            
            event = OrderEvent(
                market=order.market,
                order=order,
                event_type=event_type,
            )
            
            await messaging.publish(
                subject=Subjects.orders(self._market.value),
                data=event,
            )
        except Exception as e:
            logger.error(f"Error publishing order event: {e}")
    
    async def _publish_fill(self, fill: Fill) -> None:
        """Publish fill to NATS (skipped in standalone mode)."""
        settings = get_settings()
        if settings.standalone_mode:
            return
        
        try:
            messaging = await ensure_connected()
            await messaging.publish(
                subject=Subjects.fills(self._market.value),
                data=fill,
            )
        except Exception as e:
            logger.error(f"Error publishing fill: {e}")
