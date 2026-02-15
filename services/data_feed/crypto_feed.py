"""
Binance Crypto Data Feed Provider
WebSocket-based market data collection for crypto markets.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator, Dict, List, Optional, cast

from shared.config import get_settings
from shared.database import get_questdb
from shared.models import Candle, Market

from .base import DataFeedError, DataFeedProvider

logger = logging.getLogger(__name__)

try:
    from shared.messaging import Subjects, ensure_connected
except ImportError:
    Subjects = None  # type: ignore
    ensure_connected = None  # type: ignore

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
except ImportError:
    websockets = None  # type: ignore
    WebSocketClientProtocol = None  # type: ignore


class CryptoDataFeed(DataFeedProvider):
    """
    Crypto WebSocket data feed provider for Binance and Bybit.
    """

    BYBIT_TO_INTERNAL_INTERVAL: Dict[str, str] = {
        "1": "1m",
        "3": "3m",
        "5": "5m",
        "15": "15m",
        "30": "30m",
        "60": "1h",
        "120": "2h",
        "240": "4h",
        "360": "6h",
        "720": "12h",
        "D": "1d",
        "W": "1w",
        "M": "1M",
    }
    INTERNAL_TO_BYBIT_INTERVAL: Dict[str, str] = {
        value: key for key, value in BYBIT_TO_INTERNAL_INTERVAL.items()
    }

    def __init__(
        self,
        exchange: Optional[str] = None,
        ws_url: Optional[str] = None,
    ) -> None:
        super().__init__(Market.CRYPTO)
        settings = get_settings()
        self._exchange = (exchange or settings.crypto_exchange).lower()
        self._ws_url_override = ws_url or settings.crypto_ws_url or None
        self._ws: Optional[WebSocketClientProtocol] = None
        self._subscriptions: set[str] = set()
        self._candle_queue: asyncio.Queue[Candle] = asyncio.Queue()
        self._receive_task: Optional[asyncio.Task] = None

        if self._exchange not in {"binance", "bybit"}:
            raise DataFeedError(
                f"Unsupported crypto exchange '{self._exchange}'",
                self.market,
            )

    @property
    def exchange(self) -> str:
        """Selected crypto exchange."""
        return self._exchange

    @property
    def ws_url(self) -> str:
        """Resolved WebSocket URL."""
        if self._ws_url_override:
            return self._ws_url_override

        settings = get_settings()
        if self.exchange == "bybit":
            return settings.bybit.ws_public_url
        return settings.binance.ws_stream_url
    
    async def connect(self) -> None:
        """Connect to exchange WebSocket."""
        if websockets is None:
            raise DataFeedError(
                "websockets library not installed",
                self.market
            )
        
        try:
            self._ws = await websockets.connect(self.ws_url, ping_interval=20, ping_timeout=10)
            logger.info(
                "Connected to %s WebSocket at %s",
                self.exchange,
                self.ws_url,
            )
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
        
        logger.info("Disconnected from %s WebSocket", self.exchange)
    
    async def subscribe_candles(
        self,
        symbols: List[str],
        interval: str = "1m",
    ) -> None:
        """Subscribe to kline streams for symbols."""
        if not self._ws:
            raise DataFeedError("Not connected", self.market)
        
        streams = self._build_subscriptions(symbols=symbols, interval=interval)
        self._subscriptions.update(streams)
        subscribe_msg = self._build_subscribe_message(streams)
        
        await self._ws.send(json.dumps(subscribe_msg))
        logger.info("Subscribed to %d %s kline streams", len(streams), self.exchange)
        
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
        
        streams: List[str] = []
        for symbol in symbols:
            to_remove = [s for s in self._subscriptions if self._is_symbol_subscription(s, symbol)]
            for stream in to_remove:
                self._subscriptions.discard(stream)
                streams.append(stream)
        
        if streams:
            unsubscribe_msg = self._build_unsubscribe_message(streams)
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
            
            if self._is_subscription_confirmation(data):
                return

            candles = self._extract_candles_from_message(data)
            for candle in candles:
                await self._candle_queue.put(candle)
                await self._persist_candle(candle)
                await self._publish_candle(candle)
        
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON message: {raw_message[:100]}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def _parse_binance_kline(self, data: dict) -> Optional[Candle]:
        """Parse Binance kline payload to Candle."""
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

    def _parse_bybit_kline(self, data: dict) -> Optional[Candle]:
        """Parse Bybit kline payload to Candle."""
        try:
            interval_raw = str(data.get("interval", "1"))
            interval = self.BYBIT_TO_INTERNAL_INTERVAL.get(interval_raw, f"{interval_raw}m")
            start_ms = int(data.get("start", 0))
            if start_ms == 0:
                return None

            return Candle(
                market=Market.CRYPTO,
                symbol=str(data.get("symbol", "")),
                timestamp=datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
                open=Decimal(str(data.get("open", "0"))),
                high=Decimal(str(data.get("high", "0"))),
                low=Decimal(str(data.get("low", "0"))),
                close=Decimal(str(data.get("close", "0"))),
                volume=Decimal(str(data.get("volume", "0"))),
                quote_volume=Decimal(str(data.get("turnover", "0"))),
                interval=interval,
                is_closed=bool(data.get("confirm", False)),
            )
        except Exception as e:
            logger.error(f"Error parsing Bybit kline: {e}")
            return None

    def _build_subscriptions(self, symbols: List[str], interval: str) -> List[str]:
        """Build exchange-native kline subscriptions."""
        if self.exchange == "bybit":
            bybit_interval = self.INTERNAL_TO_BYBIT_INTERVAL.get(interval, interval)
            return [f"kline.{bybit_interval}.{symbol.upper()}" for symbol in symbols]
        return [f"{symbol.lower()}@kline_{interval}" for symbol in symbols]

    def _build_subscribe_message(self, streams: List[str]) -> dict:
        """Build exchange-native subscribe message."""
        if self.exchange == "bybit":
            return {"op": "subscribe", "args": streams}
        return {"method": "SUBSCRIBE", "params": streams, "id": 1}

    def _build_unsubscribe_message(self, streams: List[str]) -> dict:
        """Build exchange-native unsubscribe message."""
        if self.exchange == "bybit":
            return {"op": "unsubscribe", "args": streams}
        return {"method": "UNSUBSCRIBE", "params": streams, "id": 2}

    def _is_subscription_confirmation(self, data: dict) -> bool:
        """Return True for exchange subscription acknowledgements."""
        if self.exchange == "bybit":
            return bool(data.get("success")) or data.get("op") == "pong"
        return "result" in data

    def _is_symbol_subscription(self, subscription: str, symbol: str) -> bool:
        """Check whether a subscription belongs to a symbol."""
        if self.exchange == "bybit":
            return subscription.endswith(f".{symbol.upper()}")
        return subscription.startswith(symbol.lower())

    def _extract_candles_from_message(self, data: dict) -> List[Candle]:
        """Extract candles from raw websocket message."""
        candles: List[Candle] = []

        if self.exchange == "bybit":
            topic = str(data.get("topic", ""))
            if not topic.startswith("kline."):
                return candles
            payload = data.get("data", [])
            if isinstance(payload, list):
                for row in payload:
                    candle = self._parse_bybit_kline(cast(dict, row))
                    if candle:
                        candles.append(candle)
            return candles

        if "stream" in data and "data" in data:
            stream = str(data["stream"])
            if "@kline_" in stream:
                candle = self._parse_binance_kline(cast(dict, data["data"]))
                if candle:
                    candles.append(candle)
        return candles
    
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
        if ensure_connected is None or Subjects is None:
            return

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
