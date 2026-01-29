"""
NATS JetStream Messaging Client
Publish/Subscribe helpers with explicit acknowledgment.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, List, Optional, Type, TypeVar
from uuid import UUID

import nats
from nats.aio.client import Client as NatsClient
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import ConsumerConfig, DeliverPolicy
from pydantic import BaseModel

from shared.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class MessageEncoder(json.JSONEncoder):
    """JSON encoder for Pydantic models and special types."""
    
    def default(self, obj: Any) -> Any:
        if isinstance(obj, BaseModel):
            return obj.model_dump(mode="json")
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def serialize_message(data: Any) -> bytes:
    """Serialize data to bytes for NATS."""
    return json.dumps(data, cls=MessageEncoder).encode("utf-8")


def deserialize_message(data: bytes, model_class: Optional[Type[T]] = None) -> Any:
    """Deserialize NATS message to dict or Pydantic model."""
    parsed = json.loads(data.decode("utf-8"))
    if model_class is not None:
        return model_class.model_validate(parsed)
    return parsed


class NatsMessaging:
    """NATS JetStream messaging client."""
    
    _instance: Optional["NatsMessaging"] = None
    
    def __init__(self) -> None:
        self._nc: Optional[NatsClient] = None
        self._js: Optional[JetStreamContext] = None
        self._subscriptions: List[Any] = []
        self._connected = False
    
    @classmethod
    def get_instance(cls) -> "NatsMessaging":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def connect(self) -> None:
        """Connect to NATS server."""
        if self._connected:
            return
        
        settings = get_settings()
        
        async def error_cb(e: Exception) -> None:
            logger.error(f"NATS error: {e}")
        
        async def disconnected_cb() -> None:
            logger.warning("NATS disconnected")
            self._connected = False
        
        async def reconnected_cb() -> None:
            logger.info("NATS reconnected")
            self._connected = True
        
        self._nc = await nats.connect(
            settings.nats.url,
            error_cb=error_cb,
            disconnected_cb=disconnected_cb,
            reconnected_cb=reconnected_cb,
            connect_timeout=settings.nats.connect_timeout,
            reconnect_time_wait=settings.nats.reconnect_time_wait,
            max_reconnect_attempts=settings.nats.max_reconnect_attempts,
        )
        self._js = self._nc.jetstream()
        self._connected = True
        logger.info(f"Connected to NATS at {settings.nats.url}")
    
    async def close(self) -> None:
        """Close NATS connection."""
        for sub in self._subscriptions:
            try:
                await sub.unsubscribe()
            except Exception:
                pass
        self._subscriptions.clear()
        
        if self._nc:
            await self._nc.drain()
            self._nc = None
            self._js = None
        
        self._connected = False
        NatsMessaging._instance = None
    
    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected and self._nc is not None
    
    async def publish(
        self,
        subject: str,
        data: Any,
        headers: Optional[Dict[str, str]] = None,
        msg_id: Optional[str] = None,
    ) -> None:
        """
        Publish message to a subject.
        
        Args:
            subject: NATS subject (e.g., "MARKET.CANDLES.CRYPTO")
            data: Data to publish (Pydantic model or dict)
            headers: Optional message headers
            msg_id: Optional message ID for deduplication
        """
        if not self._js:
            raise RuntimeError("Not connected to NATS")
        
        payload = serialize_message(data)
        
        nats_headers = headers or {}
        if msg_id:
            nats_headers["Nats-Msg-Id"] = msg_id
        
        ack = await self._js.publish(
            subject,
            payload,
            headers=nats_headers if nats_headers else None,
        )
        
        logger.debug(f"Published to {subject}: stream={ack.stream}, seq={ack.seq}")
    
    async def subscribe(
        self,
        subject: str,
        handler: Callable[[Msg], Coroutine[Any, Any, None]],
        durable: Optional[str] = None,
        queue: Optional[str] = None,
        deliver_policy: DeliverPolicy = DeliverPolicy.NEW,
    ) -> None:
        """
        Subscribe to a subject with a message handler.
        
        Args:
            subject: NATS subject pattern (e.g., "MARKET.CANDLES.*")
            handler: Async message handler
            durable: Durable consumer name
            queue: Queue group for load balancing
            deliver_policy: Message delivery policy
        """
        if not self._js:
            raise RuntimeError("Not connected to NATS")
        
        config = ConsumerConfig(
            durable_name=durable,
            deliver_policy=deliver_policy,
        )
        
        if queue:
            sub = await self._js.subscribe(
                subject,
                cb=handler,
                durable=durable,
                queue=queue,
                config=config,
            )
        else:
            sub = await self._js.subscribe(
                subject,
                cb=handler,
                durable=durable,
                config=config,
            )
        
        self._subscriptions.append(sub)
        logger.info(f"Subscribed to {subject} (durable={durable}, queue={queue})")
    
    async def subscribe_typed(
        self,
        subject: str,
        model_class: Type[T],
        handler: Callable[[T], Coroutine[Any, Any, None]],
        durable: Optional[str] = None,
        queue: Optional[str] = None,
    ) -> None:
        """
        Subscribe with typed message handling.
        
        Args:
            subject: NATS subject pattern
            model_class: Pydantic model class for deserialization
            handler: Async handler receiving parsed model
            durable: Durable consumer name
            queue: Queue group
        """
        async def wrapper(msg: Msg) -> None:
            try:
                parsed = deserialize_message(msg.data, model_class)
                await handler(parsed)
                await msg.ack()
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                await msg.nak(delay=5)  # Negative ack with 5s delay
        
        await self.subscribe(subject, wrapper, durable, queue)
    
    async def request(
        self,
        subject: str,
        data: Any,
        timeout: float = 5.0,
    ) -> bytes:
        """
        Send request and wait for response.
        
        Args:
            subject: NATS subject
            data: Request data
            timeout: Response timeout in seconds
            
        Returns:
            Response data bytes
        """
        if not self._nc:
            raise RuntimeError("Not connected to NATS")
        
        payload = serialize_message(data)
        response = await self._nc.request(subject, payload, timeout=timeout)
        return response.data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Subject Constants
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Subjects:
    """NATS subject constants."""
    
    # Market data
    CANDLES_CRYPTO = "MARKET.CANDLES.CRYPTO"
    CANDLES_KR = "MARKET.CANDLES.KR"
    CANDLES_US = "MARKET.CANDLES.US"
    
    # Signals
    SIGNALS_CRYPTO = "STRATEGY.SIGNALS.CRYPTO"
    SIGNALS_KR = "STRATEGY.SIGNALS.KR"
    SIGNALS_US = "STRATEGY.SIGNALS.US"
    
    # Orders
    ORDERS_CRYPTO = "TRADE.ORDERS.CRYPTO"
    ORDERS_KR = "TRADE.ORDERS.KR"
    ORDERS_US = "TRADE.ORDERS.US"
    
    # Fills
    FILLS_CRYPTO = "TRADE.FILLS.CRYPTO"
    FILLS_KR = "TRADE.FILLS.KR"
    FILLS_US = "TRADE.FILLS.US"
    
    # Risk
    RISK_ALERTS = "RISK.ALERTS.*"
    KILL_SWITCH = "RISK.KILL_SWITCH"
    
    # System
    HEALTH = "SYSTEM.HEALTH.*"
    COMMANDS = "SYSTEM.COMMANDS.*"
    
    @classmethod
    def candles(cls, market: str) -> str:
        """Get candles subject for market."""
        return f"MARKET.CANDLES.{market.upper()}"
    
    @classmethod
    def signals(cls, market: str) -> str:
        """Get signals subject for market."""
        return f"STRATEGY.SIGNALS.{market.upper()}"
    
    @classmethod
    def orders(cls, market: str) -> str:
        """Get orders subject for market."""
        return f"TRADE.ORDERS.{market.upper()}"
    
    @classmethod
    def fills(cls, market: str) -> str:
        """Get fills subject for market."""
        return f"TRADE.FILLS.{market.upper()}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Convenience Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_messaging() -> NatsMessaging:
    """Get messaging instance."""
    return NatsMessaging.get_instance()


async def ensure_connected() -> NatsMessaging:
    """Ensure messaging is connected and return instance."""
    messaging = get_messaging()
    if not messaging.is_connected:
        await messaging.connect()
    return messaging
