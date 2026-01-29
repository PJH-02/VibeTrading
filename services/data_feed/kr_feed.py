"""
Korea Stock Market Data Feed Provider (Stub)
Interface-compliant stub for future Kiwoom/KIS integration.
"""

import logging
from datetime import datetime
from typing import AsyncIterator, List, Optional

from shared.models import Candle, Market

from .base import DataFeedError, DataFeedProvider

logger = logging.getLogger(__name__)


class KRDataFeed(DataFeedProvider):
    """
    Korea stock market data feed provider.
    
    STUB IMPLEMENTATION - Interface compliance only.
    Future integration points:
    - Kiwoom Securities API
    - Korea Investment & Securities (KIS) API
    - 대신증권 (Daishin) API
    """
    
    def __init__(self) -> None:
        super().__init__(Market.KR)
        logger.warning("KRDataFeed is a stub implementation - no live data available")
    
    async def connect(self) -> None:
        """Connect to KR data source (stub)."""
        logger.info("KRDataFeed.connect() - stub, no actual connection")
        # Future: Kiwoom/KIS API connection
        pass
    
    async def disconnect(self) -> None:
        """Disconnect from KR data source (stub)."""
        logger.info("KRDataFeed.disconnect() - stub")
        pass
    
    async def subscribe_candles(
        self,
        symbols: List[str],
        interval: str = "1m",
    ) -> None:
        """Subscribe to candles (stub)."""
        logger.info(f"KRDataFeed.subscribe_candles({symbols}, {interval}) - stub")
        # Future: Real-time subscription via Kiwoom/KIS
        pass
    
    async def unsubscribe_candles(
        self,
        symbols: List[str],
    ) -> None:
        """Unsubscribe from candles (stub)."""
        logger.info(f"KRDataFeed.unsubscribe_candles({symbols}) - stub")
        pass
    
    async def stream_candles(self) -> AsyncIterator[Candle]:
        """Stream candles (stub - yields nothing)."""
        logger.warning("KRDataFeed.stream_candles() - stub, no data available")
        # This is an async generator that yields nothing
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
        - 네이버 금융 (Naver Finance) web scraping
        - KRX 한국거래소 data portal
        - 증권사 API (Kiwoom, KIS, Daishin)
        """
        logger.warning(f"KRDataFeed.get_historical_candles({symbol}) - stub, returning empty")
        return []
    
    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """
        Normalize KR stock symbol.
        
        KR stocks use 6-digit codes (e.g., "005930" for Samsung)
        """
        # Remove common suffixes
        symbol = symbol.upper().strip()
        
        # Remove .KS or .KQ suffixes
        for suffix in [".KS", ".KQ", ".KRX"]:
            if symbol.endswith(suffix):
                symbol = symbol[:-len(suffix)]
        
        # Pad to 6 digits if numeric
        if symbol.isdigit():
            symbol = symbol.zfill(6)
        
        return symbol
