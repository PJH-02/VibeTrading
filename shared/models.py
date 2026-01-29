"""
Shared Models for Trading System
Pydantic schemas for all core data structures.

This module is the SINGLE SOURCE OF TRUTH for all data models.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ConfigDict


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Enums
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Market(str, Enum):
    """Market scope enum - first-class execution scopes."""
    KR = "kr"
    US = "us"
    CRYPTO = "crypto"


class TradingMode(str, Enum):
    """Trading mode enum."""
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class OrderSide(str, Enum):
    """Order side enum."""
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """Order type enum."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    """Order status enum."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class SignalAction(str, Enum):
    """Signal action enum."""
    ENTER_LONG = "enter_long"
    EXIT_LONG = "exit_long"
    ENTER_SHORT = "enter_short"
    EXIT_SHORT = "exit_short"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Base Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BaseEvent(BaseModel):
    """Base class for all event models."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    
    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    market: Market
    

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Market Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Candle(BaseEvent):
    """OHLCV candle data."""
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Optional[Decimal] = None
    trades: Optional[int] = None
    interval: str = "1m"  # 1m, 5m, 15m, 1h, 4h, 1d, etc.
    is_closed: bool = True  # False for live streaming candles


class Tick(BaseEvent):
    """Raw tick data."""
    symbol: str
    price: Decimal
    quantity: Decimal
    side: OrderSide
    trade_id: Optional[str] = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Signal Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Signal(BaseEvent):
    """Trading signal from a strategy."""
    symbol: str
    action: SignalAction
    strength: Decimal = Field(ge=0, le=1, default=Decimal("1.0"))
    strategy_name: str
    price_at_signal: Decimal
    mode: TradingMode
    metadata: dict[str, Any] = Field(default_factory=dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Order Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Order(BaseModel):
    """Order model - mutable during lifecycle."""
    model_config = ConfigDict(extra="forbid")
    
    id: UUID = Field(default_factory=uuid4)
    external_id: Optional[str] = None
    market: Market
    mode: TradingMode
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    price: Optional[Decimal] = None  # None for market orders
    stop_price: Optional[Decimal] = None
    status: OrderStatus = OrderStatus.PENDING
    strategy_name: str
    signal_id: Optional[UUID] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    @property
    def remaining_quantity(self) -> Decimal:
        """Calculate remaining quantity to fill."""
        return self.quantity - self.filled_quantity
    
    @property
    def is_terminal(self) -> bool:
        """Check if order is in a terminal state."""
        return self.status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}


class OrderEvent(BaseEvent):
    """Order event for NATS publishing."""
    order: Order
    event_type: str  # created, submitted, partial, filled, cancelled, rejected


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fill Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Fill(BaseEvent):
    """Fill/execution record."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    
    order_id: UUID
    external_id: Optional[str] = None
    mode: TradingMode
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal("0")
    commission_asset: Optional[str] = None
    slippage_bps: Decimal = Decimal("0")
    latency_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Position Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Position(BaseModel):
    """Position model - mutable."""
    model_config = ConfigDict(extra="forbid")
    
    id: UUID = Field(default_factory=uuid4)
    market: Market
    mode: TradingMode
    symbol: str
    side: OrderSide
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Optional[Decimal] = None
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    strategy_name: str
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    @property
    def is_open(self) -> bool:
        """Check if position is still open."""
        return self.closed_at is None
    
    @property
    def notional_value(self) -> Decimal:
        """Calculate notional value of position."""
        price = self.current_price or self.avg_entry_price
        return self.quantity * price


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Account Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AccountSnapshot(BaseEvent):
    """Account state snapshot."""
    mode: TradingMode
    balance: Decimal
    equity: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    daily_pnl: Decimal = Decimal("0")
    drawdown_pct: Decimal = Decimal("0")
    peak_equity: Optional[Decimal] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Risk Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RiskAlert(BaseEvent):
    """Risk alert event."""
    mode: TradingMode
    event_type: str  # drawdown_breach, daily_loss_breach, position_limit, etc.
    severity: str  # info, warning, critical
    message: str
    triggered_value: Optional[Decimal] = None
    threshold_value: Optional[Decimal] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KillSwitchEvent(BaseEvent):
    """Kill switch activation event."""
    mode: TradingMode
    reason: str
    triggered_by: str  # manual, drawdown, daily_loss, etc.
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# System Health Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HealthStatus(BaseModel):
    """Service health status."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    
    service_name: str
    status: str  # healthy, degraded, unhealthy
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    uptime_seconds: int = 0
    last_activity: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Strategy Interface
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class StrategyContext(BaseModel):
    """Context passed to strategy for signal generation."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    
    market: Market
    mode: TradingMode
    symbol: str
    current_time: datetime
    current_price: Decimal
    position: Optional[Position] = None


class StrategyResult(BaseModel):
    """Result from strategy signal generation."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    
    signals: list[Signal] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
