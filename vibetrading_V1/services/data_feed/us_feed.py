"""
US Stock Market Data Feed Provider
Real implementation using Korea Investment & Securities (KIS) API for US stocks.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import AsyncIterator, Dict, List, Optional

import aiohttp
import websockets
from shared.config import get_settings
from shared.models import Candle, Market

from .base import DataFeedError, DataFeedProvider

logger = logging.getLogger(__name__)


class USDataFeed(DataFeedProvider):
    """
    US stock market data feed provider using KIS API.
    
    Features:
    - Real-time US stock market data via WebSocket
    - Historical OHLCV data for US stocks
    - Support for both virtual and real accounts
    """
    
    def __init__(self) -> None:
        super().__init__(Market.US)
        self._settings = get_settings().kis
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._subscribed_symbols: Dict[str, str] = {}  # symbol -> interval
        self._candle_queue: asyncio.Queue[Candle] = asyncio.Queue()
        
        if not self._settings.app_key or not self._settings.app_secret:
            logger.warning("KIS API credentials not configured - feed will not work")
    
    async def _get_access_token(self) -> str:
        """Get or refresh access token for KIS API."""
        # Check if token is still valid
        if self._access_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at - timedelta(minutes=5):
                return self._access_token
        
        # Request new token
        url = f"{self._settings.base_url}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self._settings.app_key,
            "appsecret": self._settings.app_secret,
        }
        
        async with self._session.post(url, json=body, headers=headers) as response:
            if response.status != 200:
                raise DataFeedError(
                    f"Failed to get access token: {response.status}",
                    Market.US
                )
            
            data = await response.json()
            self._access_token = data["access_token"]
            # Token expires in 24 hours
            self._token_expires_at = datetime.now() + timedelta(hours=24)
            
            logger.info("KIS API access token obtained successfully")
            return self._access_token
    
    async def connect(self) -> None:
        """Connect to KIS data source for US markets."""
        logger.info("Connecting to KIS API for US market...")
        
        self._session = aiohttp.ClientSession()
        
        try:
            # Get access token
            await self._get_access_token()
            logger.info("USDataFeed connected successfully")
        except Exception as e:
            await self.disconnect()
            raise DataFeedError(f"Failed to connect: {e}", Market.US)
    
    async def disconnect(self) -> None:
        """Disconnect from KIS data source."""
        logger.info("Disconnecting from KIS API...")
        
        # Unsubscribe from all symbols
        if self._subscribed_symbols:
            await self.unsubscribe_candles(list(self._subscribed_symbols.keys()))
        
        # Close websocket
        if self._ws and not self._ws.closed:
            await self._ws.close()
            self._ws = None
        
        # Close HTTP session
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        
        logger.info("USDataFeed disconnected")
    
    async def _connect_websocket(self) -> None:
        """Establish WebSocket connection for real-time US stock data."""
        if self._ws and not self._ws.closed:
            return
        
        ws_url = "ws://ops.koreainvestment.com:21000"
        try:
            self._ws = await websockets.connect(ws_url)
            logger.info("WebSocket connection established for US market")
            
            # Start receiving messages
            asyncio.create_task(self._ws_message_handler())
        except Exception as e:
            raise DataFeedError(f"WebSocket connection failed: {e}", Market.US)
    
    async def _ws_message_handler(self) -> None:
        """Handle incoming WebSocket messages."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    
                    # Parse US market data
                    if "header" in data and data["header"].get("tr_id") == "HDFSASP0":
                        candle = self._parse_ws_candle(data)
                        if candle:
                            await self._candle_queue.put(candle)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse WebSocket message: {message}")
                except Exception as e:
                    logger.error(f"Error processing WebSocket message: {e}")
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
            self._ws = None
        except Exception as e:
            logger.error(f"WebSocket handler error: {e}")
            self._ws = None
    
    def _parse_ws_candle(self, data: dict) -> Optional[Candle]:
        """Parse WebSocket message to Candle object."""
        try:
            body = data.get("body", {})
            
            # Extract US stock data
            symbol = body.get("SYMB")  # Symbol
            current_price = Decimal(body.get("LAST", "0"))  # Last price
            volume = Decimal(body.get("TVOL", "0"))  # Total volume
            
            # For real-time data, approximate OHLC from tick data
            return Candle(
                market=Market.US,
                symbol=self.normalize_symbol(symbol),
                timestamp=datetime.now(),
                open=current_price,
                high=current_price,
                low=current_price,
                close=current_price,
                volume=volume,
                interval="1m",
                is_closed=False,
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse candle from WebSocket data: {e}")
            return None
    
    async def subscribe_candles(
        self,
        symbols: List[str],
        interval: str = "1m",
    ) -> None:
        """Subscribe to US stock candles via WebSocket."""
        logger.info(f"Subscribing to US candles: {symbols} ({interval})")
        
        # Ensure WebSocket is connected
        await self._connect_websocket()
        
        token = await self._get_access_token()
        
        for symbol in symbols:
            normalized_symbol = self.normalize_symbol(symbol)
            
            # Build subscription message for US stocks
            subscribe_msg = {
                "header": {
                    "approval_key": token,
                    "custtype": "P",
                    "tr_type": "1",  # Subscribe
                    "content-type": "utf-8",
                },
                "body": {
                    "input": {
                        "tr_id": "HDFSASP0",  # Real-time US stock price
                        "tr_key": normalized_symbol,
                    }
                },
            }
            
            # Send subscription
            await self._ws.send(json.dumps(subscribe_msg))
            self._subscribed_symbols[normalized_symbol] = interval
            
        logger.info(f"Successfully subscribed to {len(symbols)} US symbols")
    
    async def unsubscribe_candles(
        self,
        symbols: List[str],
    ) -> None:
        """Unsubscribe from US stock candles."""
        logger.info(f"Unsubscribing from US candles: {symbols}")
        
        if not self._ws or self._ws.closed:
            return
        
        token = await self._get_access_token()
        
        for symbol in symbols:
            normalized_symbol = self.normalize_symbol(symbol)
            
            if normalized_symbol not in self._subscribed_symbols:
                continue
            
            # Build unsubscribe message
            unsubscribe_msg = {
                "header": {
                    "approval_key": token,
                    "custtype": "P",
                    "tr_type": "2",  # Unsubscribe
                    "content-type": "utf-8",
                },
                "body": {
                    "input": {
                        "tr_id": "HDFSASP0",
                        "tr_key": normalized_symbol,
                    }
                },
            }
            
            await self._ws.send(json.dumps(unsubscribe_msg))
            del self._subscribed_symbols[normalized_symbol]
    
    async def stream_candles(self) -> AsyncIterator[Candle]:
        """Stream US stock candles from queue."""
        while self._running:
            try:
                # Wait for candle with timeout
                candle = await asyncio.wait_for(
                    self._candle_queue.get(),
                    timeout=1.0
                )
                yield candle
            except asyncio.TimeoutError:
                # No data available, continue waiting
                continue
            except Exception as e:
                logger.error(f"Error in stream_candles: {e}")
                break
    
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Candle]:
        """
        Fetch historical US stock candles using KIS REST API.
        
        Uses the overseas stock daily price endpoint.
        """
        logger.info(f"Fetching historical US candles for {symbol} ({interval})")
        
        normalized_symbol = self.normalize_symbol(symbol)
        
        if end_time is None:
            end_time = datetime.now()
        
        # KIS API endpoint for US stock data
        url = f"{self._settings.base_url}/uapi/overseas-price/v1/quotations/dailyprice"
        
        token = await self._get_access_token()
        
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": self._settings.app_key,
            "appsecret": self._settings.app_secret,
            "tr_id": "HHDFS76240000",  # Overseas stock daily price inquiry
        }
        
        # Convert interval to period code
        period_code = self._interval_to_period_code(interval)
        
        params = {
            "AUTH": "",
            "EXCD": "NAS",  # NASDAQ (use "NYS" for NYSE, "AMS" for AMEX)
            "SYMB": normalized_symbol,
            "GUBN": period_code,
            "BYMD": end_time.strftime("%Y%m%d"),
            "MODP": "0",  # 0: Original price, 1: Modified price
        }
        
        try:
            async with self._session.get(url, headers=headers, params=params) as response:
                if response.status != 200:
                    raise DataFeedError(
                        f"Failed to fetch US historical data: {response.status}",
                        Market.US,
                        symbol
                    )
                
                data = await response.json()
                
                # Parse response
                candles = []
                for item in data.get("output2", [])[:limit]:
                    try:
                        # Convert YYYYMMDD to datetime
                        trade_date = datetime.strptime(item["xymd"], "%Y%m%d")
                        
                        # Filter by start_time
                        if trade_date < start_time:
                            continue
                        
                        candle = Candle(
                            market=Market.US,
                            symbol=normalized_symbol,
                            timestamp=trade_date,
                            open=Decimal(item["open"]),
                            high=Decimal(item["high"]),
                            low=Decimal(item["low"]),
                            close=Decimal(item["clos"]),
                            volume=Decimal(item["tvol"]),
                            interval=interval,
                            is_closed=True,
                        )
                        candles.append(candle)
                    except (KeyError, ValueError, TypeError) as e:
                        logger.warning(f"Failed to parse US candle: {e}")
                
                logger.info(f"Fetched {len(candles)} historical US candles for {symbol}")
                return candles
                
        except Exception as e:
            logger.error(f"Error fetching US historical candles: {e}")
            raise DataFeedError(f"Failed to fetch US historical data: {e}", Market.US, symbol)
    
    @staticmethod
    def _interval_to_period_code(interval: str) -> str:
        """Convert interval string to KIS period code for US stocks."""
        mapping = {
            "1d": "0",  # Daily
            "1D": "0",
            "1w": "1",  # Weekly
            "1W": "1",
            "1M": "2",  # Monthly
            "1m": "0",  # Minute data treated as daily
        }
        return mapping.get(interval, "0")
    
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
