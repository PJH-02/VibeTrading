"""
Binance Crypto Broker Adapter
Order execution via Binance API.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from shared.config import get_settings
from shared.models import Fill, Market, Order, OrderSide, OrderStatus, OrderType, TradingMode

from .base import BrokerAdapter, OrderError

logger = logging.getLogger(__name__)

try:
    from binance import AsyncClient, BinanceSocketManager
    from binance.exceptions import BinanceAPIException
except ImportError:
    AsyncClient = None
    BinanceSocketManager = None
    BinanceAPIException = Exception


class CryptoBinanceAdapter(BrokerAdapter):
    """
    Binance spot/margin order execution adapter.
    
    Connects to Binance API for order submission
    and WebSocket for real-time fill updates.
    """
    
    def __init__(self, testnet: bool = True) -> None:
        """
        Initialize Binance adapter.
        
        Args:
            testnet: Use testnet API (default True for safety)
        """
        super().__init__(Market.CRYPTO)
        self._testnet = testnet
        self._client: Optional[AsyncClient] = None
        self._bsm: Optional[BinanceSocketManager] = None
        self._user_socket = None
    
    async def connect(self) -> None:
        """Connect to Binance API."""
        if AsyncClient is None:
            raise OrderError("python-binance library not installed")
        
        settings = get_settings()
        
        try:
            self._client = await AsyncClient.create(
                api_key=settings.binance.api_key,
                api_secret=settings.binance.api_secret,
                testnet=self._testnet,
            )
            
            # Start user data stream for fill updates
            self._bsm = BinanceSocketManager(self._client)
            self._user_socket = self._bsm.user_socket()
            
            # Start listening task
            asyncio.create_task(self._listen_user_stream())
            
            self._connected = True
            logger.info(f"Connected to Binance {'Testnet' if self._testnet else 'Live'}")
            
        except Exception as e:
            raise OrderError(f"Failed to connect to Binance: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from Binance."""
        if self._user_socket:
            await self._user_socket.__aexit__(None, None, None)
        
        if self._client:
            await self._client.close_connection()
            self._client = None
        
        self._connected = False
        logger.info("Disconnected from Binance")
    
    async def _listen_user_stream(self) -> None:
        """Listen for user data stream events."""
        if not self._user_socket:
            return
        
        try:
            async with self._user_socket as stream:
                while self._connected:
                    msg = await stream.recv()
                    await self._handle_user_event(msg)
        except Exception as e:
            logger.error(f"User stream error: {e}")
    
    async def _handle_user_event(self, msg: dict) -> None:
        """Handle user stream event."""
        event_type = msg.get("e")
        
        if event_type == "executionReport":
            await self._handle_execution_report(msg)
    
    async def _handle_execution_report(self, msg: dict) -> None:
        """Handle order execution report."""
        try:
            # Parse fill from execution report
            if msg.get("x") == "TRADE":  # Trade execution
                fill = Fill(
                    market=Market.CRYPTO,
                    order_id=msg.get("c"),  # Client order ID
                    external_id=str(msg.get("t")),  # Trade ID
                    mode=TradingMode.LIVE if not self._testnet else TradingMode.PAPER,
                    symbol=msg.get("s"),
                    side=OrderSide.BUY if msg.get("S") == "BUY" else OrderSide.SELL,
                    quantity=Decimal(str(msg.get("l", "0"))),
                    price=Decimal(str(msg.get("L", "0"))),
                    commission=Decimal(str(msg.get("n", "0"))),
                    commission_asset=msg.get("N"),
                    slippage_bps=Decimal("0"),  # Calculate from order price
                    latency_ms=0,
                )
                
                if self._on_fill_callback:
                    self._on_fill_callback(fill)
                    
        except Exception as e:
            logger.error(f"Error handling execution report: {e}")
    
    async def submit_order(self, order: Order) -> Order:
        """Submit order to Binance."""
        if not self._client:
            raise OrderError("Not connected to Binance", order)
        
        try:
            # Map order type
            binance_type = {
                OrderType.MARKET: "MARKET",
                OrderType.LIMIT: "LIMIT",
                OrderType.STOP: "STOP_LOSS",
                OrderType.STOP_LIMIT: "STOP_LOSS_LIMIT",
            }.get(order.order_type, "MARKET")
            
            # Build order params
            params = {
                "symbol": order.symbol,
                "side": order.side.value.upper(),
                "type": binance_type,
                "quantity": str(order.quantity),
                "newClientOrderId": str(order.id),
            }
            
            if order.order_type == OrderType.LIMIT and order.price:
                params["price"] = str(order.price)
                params["timeInForce"] = "GTC"
            
            if order.stop_price:
                params["stopPrice"] = str(order.stop_price)
            
            # Submit order
            result = await self._client.create_order(**params)
            
            order.external_id = str(result.get("orderId"))
            order.status = self._map_status(result.get("status", "NEW"))
            order.submitted_at = datetime.utcnow()
            order.updated_at = datetime.utcnow()
            
            logger.info(f"Order submitted to Binance: {order.external_id}")
            
            if self._on_order_update_callback:
                self._on_order_update_callback(order)
            
            return order
            
        except BinanceAPIException as e:
            order.status = OrderStatus.REJECTED
            order.error_message = str(e.message)
            logger.error(f"Binance order rejected: {e.message}")
            raise OrderError(e.message, order, str(e.code))
    
    async def cancel_order(self, order: Order) -> Order:
        """Cancel order on Binance."""
        if not self._client:
            raise OrderError("Not connected", order)
        
        try:
            await self._client.cancel_order(
                symbol=order.symbol,
                origClientOrderId=str(order.id),
            )
            
            order.status = OrderStatus.CANCELLED
            order.cancelled_at = datetime.utcnow()
            order.updated_at = datetime.utcnow()
            
            if self._on_order_update_callback:
                self._on_order_update_callback(order)
            
            return order
            
        except BinanceAPIException as e:
            raise OrderError(f"Cancel failed: {e.message}", order, str(e.code))
    
    async def get_order_status(self, order: Order) -> Order:
        """Get order status from Binance."""
        if not self._client:
            raise OrderError("Not connected", order)
        
        try:
            result = await self._client.get_order(
                symbol=order.symbol,
                origClientOrderId=str(order.id),
            )
            
            order.status = self._map_status(result.get("status", "NEW"))
            order.filled_quantity = Decimal(str(result.get("executedQty", "0")))
            order.updated_at = datetime.utcnow()
            
            return order
            
        except BinanceAPIException as e:
            raise OrderError(f"Get status failed: {e.message}", order, str(e.code))
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get open orders from Binance."""
        if not self._client:
            return []
        
        try:
            params = {}
            if symbol:
                params["symbol"] = symbol
            
            results = await self._client.get_open_orders(**params)
            
            orders = []
            for r in results:
                order = Order(
                    market=Market.CRYPTO,
                    mode=TradingMode.LIVE if not self._testnet else TradingMode.PAPER,
                    symbol=r["symbol"],
                    side=OrderSide.BUY if r["side"] == "BUY" else OrderSide.SELL,
                    order_type=self._map_order_type(r["type"]),
                    quantity=Decimal(str(r["origQty"])),
                    filled_quantity=Decimal(str(r["executedQty"])),
                    price=Decimal(str(r["price"])) if r.get("price") else None,
                    status=self._map_status(r["status"]),
                    strategy_name="unknown",
                    external_id=str(r["orderId"]),
                )
                orders.append(order)
            
            return orders
            
        except BinanceAPIException:
            return []
    
    async def get_account_balance(self) -> Decimal:
        """Get USDT balance from Binance."""
        if not self._client:
            return Decimal("0")
        
        try:
            account = await self._client.get_account()
            for balance in account.get("balances", []):
                if balance["asset"] == "USDT":
                    return Decimal(str(balance["free"]))
            return Decimal("0")
        except BinanceAPIException:
            return Decimal("0")
    
    @staticmethod
    def _map_status(binance_status: str) -> OrderStatus:
        """Map Binance status to OrderStatus."""
        mapping = {
            "NEW": OrderStatus.SUBMITTED,
            "PARTIALLY_FILLED": OrderStatus.PARTIAL,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.CANCELLED,
        }
        return mapping.get(binance_status, OrderStatus.PENDING)
    
    @staticmethod
    def _map_order_type(binance_type: str) -> OrderType:
        """Map Binance order type to OrderType."""
        mapping = {
            "MARKET": OrderType.MARKET,
            "LIMIT": OrderType.LIMIT,
            "STOP_LOSS": OrderType.STOP,
            "STOP_LOSS_LIMIT": OrderType.STOP_LIMIT,
        }
        return mapping.get(binance_type, OrderType.MARKET)
