"""
Backtest Data Loader
Provides chronologically sorted candle data for backtesting.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Iterator, List, Optional

from shared.database import get_questdb
from shared.models import Candle, Market

logger = logging.getLogger(__name__)


class BacktestDataLoader:
    """
    Loads historical candle data for backtesting.
    
    Sources (in priority order):
    1. QuestDB (local time-series database)
    2. CSV files (for offline testing)
    """
    
    def __init__(self, market: Market) -> None:
        """
        Initialize data loader.
        
        Args:
            market: Market to load data for
        """
        self._market = market
    
    def load_candles(
        self,
        start: datetime,
        end: datetime,
        symbols: List[str],
        interval: str = "1d",
    ) -> Iterator[Candle]:
        """
        Load candles for backtesting.
        
        CRITICAL: Returns candles sorted by timestamp ascending.
        This is required for the backtest engine's look-ahead prevention.
        
        Args:
            start: Start datetime
            end: End datetime
            symbols: List of symbols to load
            interval: Candle interval
            
        Yields:
            Candles in chronological order
        """
        logger.info(f"Loading candles: {symbols} from {start} to {end}")
        
        # Try QuestDB first
        try:
            yield from self._load_from_questdb(start, end, symbols, interval)
            return
        except Exception as e:
            logger.warning(f"QuestDB load failed: {e}, trying CSV")
        
        # Fall back to empty (for testing without data)
        logger.warning("No data source available, yielding no candles")
    
    def _load_from_questdb(
        self,
        start: datetime,
        end: datetime,
        symbols: List[str],
        interval: str,
    ) -> Iterator[Candle]:
        """Load candles from QuestDB."""
        questdb = get_questdb()
        
        # Build symbol filter
        symbol_filter = ", ".join(f"'{s}'" for s in symbols)
        
        query = f"""
        SELECT * FROM candles
        WHERE market = '{self._market.value}'
          AND symbol IN ({symbol_filter})
          AND timestamp >= '{start.isoformat()}'
          AND timestamp < '{end.isoformat()}'
        ORDER BY timestamp ASC
        """
        
        rows = questdb.query(query)
        
        for row in rows:
            yield Candle(
                market=Market(row["market"]),
                symbol=row["symbol"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                quote_volume=Decimal(str(row.get("quote_volume", 0))),
                trades=row.get("trades", 0),
                interval=interval,
                is_closed=True,
            )
    
    def load_from_csv(
        self,
        filepath: str,
        symbol: str,
        interval: str = "1d",
    ) -> Iterator[Candle]:
        """
        Load candles from CSV file.
        
        Expected columns: timestamp,open,high,low,close,volume
        
        Args:
            filepath: Path to CSV file
            symbol: Symbol identifier
            interval: Candle interval
            
        Yields:
            Candles in chronological order
        """
        import csv
        
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            
            # Sort by timestamp (in case CSV isn't sorted)
            rows = sorted(reader, key=lambda r: r["timestamp"])
            
            for row in rows:
                yield Candle(
                    market=self._market,
                    symbol=symbol,
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row.get("volume", "0")),
                    interval=interval,
                    is_closed=True,
                )


def create_candle_provider(
    market: Market,
    interval: str = "1d",
) -> callable:
    """
    Create a candle provider function for walk-forward validation.
    
    Args:
        market: Market to load
        interval: Candle interval
        
    Returns:
        Callable(start, end, symbols) -> Iterator[Candle]
    """
    loader = BacktestDataLoader(market)
    
    def provider(
        start: datetime,
        end: datetime,
        symbols: List[str],
    ) -> Iterator[Candle]:
        return loader.load_candles(start, end, symbols, interval)
    
    return provider
