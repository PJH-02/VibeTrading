"""
Abstract Data Feed Provider Interface
Base class for market-scoped data collectors.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import AsyncIterator, List, Optional

from shared.models import Candle, Market, Tick


class DataFeedProvider(ABC):
    """
    Abstract base class for market data feed providers.
    
    Each market (KR, US, Crypto) implements this interface
    to provide market data in a normalized format.
    """
    
    def __init__(self, market: Market) -> None:
        """
        Initialize data feed provider.
        
        Args:
            market: Market scope for this provider
        """
        self._market = market
        self._running = False
    
    @property
    def market(self) -> Market:
        """Get market scope."""
        return self._market
    
    @property
    def is_running(self) -> bool:
        """Check if feed is running."""
        return self._running
    
    @abstractmethod
    async def connect(self) -> None:
        """
        Connect to data source.
        
        Raises:
            ConnectionError: If connection fails
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from data source."""
        pass
    
    @abstractmethod
    async def subscribe_candles(
        self,
        symbols: List[str],
        interval: str = "1m",
    ) -> None:
        """
        Subscribe to candle updates for symbols.
        
        Args:
            symbols: List of symbol identifiers
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
        """
        pass
    
    @abstractmethod
    async def unsubscribe_candles(
        self,
        symbols: List[str],
    ) -> None:
        """
        Unsubscribe from candle updates.
        
        Args:
            symbols: List of symbols to unsubscribe
        """
        pass
    
    @abstractmethod
    async def stream_candles(self) -> AsyncIterator[Candle]:
        """
        Stream candle updates.
        
        Yields:
            Candle objects as they arrive
        """
        pass
    
    @abstractmethod
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Candle]:
        """
        Fetch historical candles.
        
        Args:
            symbol: Symbol identifier
            interval: Candle interval
            start_time: Start of time range
            end_time: End of time range (default: now)
            limit: Maximum candles to return
            
        Returns:
            List of historical candles
        """
        pass
    
    async def start(self) -> None:
        """Start the data feed."""
        await self.connect()
        self._running = True
    
    async def stop(self) -> None:
        """Stop the data feed."""
        self._running = False
        await self.disconnect()
    
    @staticmethod
    def normalize_symbol(symbol: str, market: Market) -> str:
        """
        Normalize symbol to standard format.
        
        Args:
            symbol: Raw symbol from exchange
            market: Market type
            
        Returns:
            Normalized symbol string
        """
        # Remove common suffixes/prefixes
        symbol = symbol.upper().strip()
        
        if market == Market.CRYPTO:
            # Ensure USDT pairing is explicit
            if not symbol.endswith("USDT") and not symbol.endswith("USD"):
                symbol = f"{symbol}USDT"
        
        return symbol


class DataFeedError(Exception):
    """Exception raised by data feed operations."""
    
    def __init__(self, message: str, market: Market, symbol: Optional[str] = None) -> None:
        self.market = market
        self.symbol = symbol
        super().__init__(f"[{market.value}] {message}")
