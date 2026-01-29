"""
US Stock Market Data Feed Provider (Stub)
Interface-compliant stub for future broker integration.
"""

import logging
from datetime import datetime
from typing import AsyncIterator, List, Optional

from shared.models import Candle, Market

from .base import DataFeedError, DataFeedProvider

logger = logging.getLogger(__name__)


class USDataFeed(DataFeedProvider):
    """
    US stock market data feed provider.
    
    STUB IMPLEMENTATION - Interface compliance only.
    Future integration points:
    - Alpaca Markets API
    - Interactive Brokers TWS API
    - TD Ameritrade API
    - Polygon.io
    """
    
    def __init__(self) -> None:
        super().__init__(Market.US)
        logger.warning("USDataFeed is a stub implementation - no live data available")
    
    async def connect(self) -> None:
        """Connect to US data source (stub)."""
        logger.info("USDataFeed.connect() - stub, no actual connection")
        pass
    
    async def disconnect(self) -> None:
        """Disconnect from US data source (stub)."""
        logger.info("USDataFeed.disconnect() - stub")
        pass
    
    async def subscribe_candles(
        self,
        symbols: List[str],
        interval: str = "1m",
    ) -> None:
        """Subscribe to candles (stub)."""
        logger.info(f"USDataFeed.subscribe_candles({symbols}, {interval}) - stub")
        pass
    
    async def unsubscribe_candles(
        self,
        symbols: List[str],
    ) -> None:
        """Unsubscribe from candles (stub)."""
        logger.info(f"USDataFeed.unsubscribe_candles({symbols}) - stub")
        pass
    
    async def stream_candles(self) -> AsyncIterator[Candle]:
        """Stream candles (stub - yields nothing)."""
        logger.warning("USDataFeed.stream_candles() - stub, no data available")
        return
        yield  # Makes this a generator
    
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Candle]:
        """
        Fetch historical candles (stub).
        
        Future integration options:
        - Polygon.io REST API
        - Alpaca Markets historical data
        - Yahoo Finance (yfinance)
        """
        logger.warning(f"USDataFeed.get_historical_candles({symbol}) - stub, returning empty")
        return []
    
    @staticmethod  
    def normalize_symbol(symbol: str) -> str:
        """
        Normalize US stock symbol.
        
        US stocks use ticker symbols (e.g., "AAPL", "MSFT")
        """
        symbol = symbol.upper().strip()
        
        # Remove common exchange suffixes
        for suffix in [".US", ".NYSE", ".NASDAQ", ".ARCA"]:
            if symbol.endswith(suffix):
                symbol = symbol[:-len(suffix)]
        
        return symbol
