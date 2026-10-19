"""
Abstract Broker Adapter Interface
Base class for market-specific order execution.

Extension point for future brokers:
- Kiwoom, Korea Investment (KR)
- Alpaca, Interactive Brokers (US)
- Bybit, OKX (Crypto futures)
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Callable, List, Optional

from shared.models import Fill, Market, Order, OrderStatus


class BrokerAdapter(ABC):
    """
    Abstract broker adapter interface.
    
    All execution logic is delegated to market-specific
    adapters that implement this interface.
    """
    
    def __init__(self, market: Market) -> None:
        """
        Initialize broker adapter.
        
        Args:
            market: Market scope for this adapter
        """
        self._market = market
        self._connected = False
        self._on_fill_callback: Optional[Callable[[Fill], None]] = None
        self._on_order_update_callback: Optional[Callable[[Order], None]] = None
    
    @property
    def market(self) -> Market:
        """Get market scope."""
        return self._market
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to broker."""
        return self._connected
    
    def set_on_fill_callback(self, callback: Callable[[Fill], None]) -> None:
        """Set callback for fill events."""
        self._on_fill_callback = callback
    
    def set_on_order_update_callback(self, callback: Callable[[Order], None]) -> None:
        """Set callback for order status updates."""
        self._on_order_update_callback = callback
    
    @abstractmethod
    async def connect(self) -> None:
        """
        Connect to broker.
        
        Raises:
            ConnectionError: If connection fails
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from broker."""
        pass
    
    @abstractmethod
    async def submit_order(self, order: Order) -> Order:
        """
        Submit order to broker.
        
        Args:
            order: Order to submit
            
        Returns:
            Updated order with external_id and status
            
        Raises:
            OrderError: If submission fails
        """
        pass
    
    @abstractmethod
    async def cancel_order(self, order: Order) -> Order:
        """
        Cancel an open order.
        
        Args:
            order: Order to cancel
            
        Returns:
            Updated order with cancelled status
        """
        pass
    
    @abstractmethod
    async def get_order_status(self, order: Order) -> Order:
        """
        Get current status of an order.
        
        Args:
            order: Order to check
            
        Returns:
            Order with updated status and fill info
        """
        pass
    
    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """
        Get all open orders.
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            List of open orders
        """
        pass
    
    @abstractmethod
    async def get_account_balance(self) -> Decimal:
        """
        Get account balance.
        
        Returns:
            Available balance in quote currency
        """
        pass
    
    async def start(self) -> None:
        """Start the broker adapter."""
        await self.connect()
        self._connected = True
    
    async def stop(self) -> None:
        """Stop the broker adapter."""
        self._connected = False
        await self.disconnect()


class OrderError(Exception):
    """Exception raised by order operations."""
    
    def __init__(
        self,
        message: str,
        order: Optional[Order] = None,
        broker_error_code: Optional[str] = None,
    ) -> None:
        self.order = order
        self.broker_error_code = broker_error_code
        super().__init__(message)
