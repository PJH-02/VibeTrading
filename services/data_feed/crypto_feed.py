"""
Binance Crypto Data Feed Provider
WebSocket-based market data collection for crypto markets.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator, Dict, List, Optional

from shared.config import get_settings
from shared.database import get_questdb
from shared.messaging import Subjects, ensure_connected
from shared.models import Candle, Market

from .base import DataFeedError, DataFeedProvider

logger = logging.getLogger(__name__)

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
except ImportError:
    websockets = None  # type: ignore
    WebSocketClientProtocol = None  # type: ignore


class CryptoDataFeed(DataFeedProvider):
    """
    Binance WebSocket data feed provider.
    
    Connects to Binance WebSocket streams for real-time
    candle (kline) data.
    """
    
    def __init__(self) -> None:
        super().__init__(Market.CRYPTO)
        self._ws: Optional[WebSocketClientProtocol] = None
        self._subscriptions: set[str] = set()
        self._candle_queue: asyncio.Queue[Candle] = asyncio.Queue()
        self._receive_task: Optional[asyncio.Task] = None
    
    @property
    def ws_url(self) -> str:
        """Get WebSocket URL."""
        settings = get_settings()
        return settings.binance.ws_url
    
    async def connect(self) -> None:
        """Connect to Binance WebSocket."""
        if websockets is None:
            raise DataFeedError(
                "websockets library not installed",
                self.market
            )
        
        try:
            self._ws = await websockets.connect(
                f"{self.ws_url}/stream",
                ping_interval=20,
                ping_timeout=10,
            )
            logger.info(f"Connected to Binance WebSocket at {self.ws_url}")
        except Exception as e:
            raise DataFeedError(f"Failed to connect: {e}", self.market)
    
    async def disconnect(self) -> None:
        """Disconnect from Binance WebSocket."""
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        logger.info("Disconnected from Binance WebSocket")
    
    async def subscribe_candles(
        self,
        symbols: List[str],
        interval: str = "1m",
    ) -> None:
        """Subscribe to kline streams for symbols."""
        if not self._ws:
            raise DataFeedError("Not connected", self.market)
        
        # Build stream names
        streams = []
        for symbol in symbols:
            stream_name = f"{symbol.lower()}@kline_{interval}"
            streams.append(stream_name)
            self._subscriptions.add(stream_name)
        
        # Send subscribe message
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": 1,
        }
        
        await self._ws.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to {len(streams)} kline streams")
        
        # Start receive task if not running
        if self._receive_task is None or self._receive_task.done():
            self._receive_task = asyncio.create_task(self._receive_loop())
    
    async def unsubscribe_candles(
        self,
        symbols: List[str],
    ) -> None:
        """Unsubscribe from kline streams."""
        if not self._ws:
            return
        
        streams = []
        for symbol in symbols:
            # Remove all interval subscriptions for this symbol
            to_remove = [s for s in self._subscriptions if s.startswith(symbol.lower())]
            for stream in to_remove:
                self._subscriptions.discard(stream)
                streams.append(stream)
        
        if streams:
            unsubscribe_msg = {
                "method": "UNSUBSCRIBE",
                "params": streams,
                "id": 2,
            }
            await self._ws.send(json.dumps(unsubscribe_msg))
    
    async def _receive_loop(self) -> None:
        """Background task to receive WebSocket messages."""
        if not self._ws:
            return
        
        try:
            async for message in self._ws:
                await self._process_message(message)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"WebSocket receive error: {e}")
    
    async def _process_message(self, raw_message: str) -> None:
        """Process incoming WebSocket message."""
        try:
            data = json.loads(raw_message)
            
            # Skip subscription confirmations
            if "result" in data:
                return
            
            # Handle stream data
            if "stream" in data and "data" in data:
                stream = data["stream"]
                kline_data = data["data"]
                
                if "@kline_" in stream:
                    candle = self._parse_kline(kline_data)
                    if candle:
                        await self._candle_queue.put(candle)
                        
                        # Persist to QuestDB
                        await self._persist_candle(candle)
                        
                        # Publish to NATS
                        await self._publish_candle(candle)
        
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON message: {raw_message[:100]}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def _parse_kline(self, data: dict) -> Optional[Candle]:
        """Parse Binance kline message to Candle."""
        try:
            k = data.get("k", {})
            
            return Candle(
                market=Market.CRYPTO,
                symbol=k.get("s", ""),
                timestamp=datetime.fromtimestamp(k.get("t", 0) / 1000, tz=timezone.utc),
                open=Decimal(str(k.get("o", "0"))),
                high=Decimal(str(k.get("h", "0"))),
                low=Decimal(str(k.get("l", "0"))),
                close=Decimal(str(k.get("c", "0"))),
                volume=Decimal(str(k.get("v", "0"))),
                quote_volume=Decimal(str(k.get("q", "0"))),
                trades=k.get("n", 0),
                interval=k.get("i", "1m"),
                is_closed=k.get("x", False),
            )
        except Exception as e:
            logger.error(f"Error parsing kline: {e}")
            return None
    
    async def _persist_candle(self, candle: Candle) -> None:
        """Persist candle to QuestDB."""
        if not candle.is_closed:
            return  # Only persist closed candles
        
        try:
            questdb = get_questdb()
            questdb.write_line(
                table="candles",
                tags={
                    "market": candle.market.value,
                    "symbol": candle.symbol,
                },
                fields={
                    "open": float(candle.open),
                    "high": float(candle.high),
                    "low": float(candle.low),
                    "close": float(candle.close),
                    "volume": float(candle.volume),
                    "quote_volume": float(candle.quote_volume) if candle.quote_volume else 0,
                    "trades": candle.trades or 0,
                },
                timestamp_ns=int(candle.timestamp.timestamp() * 1_000_000_000),
            )
        except Exception as e:
            logger.error(f"Error persisting candle: {e}")
    
    async def _publish_candle(self, candle: Candle) -> None:
        """Publish candle to NATS."""
        try:
            messaging = await ensure_connected()
            await messaging.publish(
                Subjects.candles(self.market.value),
                candle,
            )
        except Exception as e:
            logger.error(f"Error publishing candle: {e}")
    
    async def stream_candles(self) -> AsyncIterator[Candle]:
        """Stream candles from queue."""
        while self._running:
            try:
                candle = await asyncio.wait_for(
                    self._candle_queue.get(),
                    timeout=1.0,
                )
                yield candle
            except asyncio.TimeoutError:
                continue
    
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Candle]:
        """
        Fetch historical candles from Binance REST API.
        
        Note: In production, this should use the REST API.
        For now, returns data from QuestDB if available.
        """
        questdb = get_questdb()
        
        end_time = end_time or datetime.utcnow()
        
        query = f"""
        SELECT * FROM candles
        WHERE symbol = '{symbol}'
          AND market = 'crypto'
          AND timestamp >= '{start_time.isoformat()}'
          AND timestamp <= '{end_time.isoformat()}'
        ORDER BY timestamp
        LIMIT {limit}
        """
        
        rows = questdb.query(query)
        
        candles = []
        for row in rows:
            candles.append(Candle(
                market=Market.CRYPTO,
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
            ))
        
        return candles
