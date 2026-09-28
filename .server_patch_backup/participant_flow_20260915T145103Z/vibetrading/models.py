from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = 2
UTC = timezone.utc


class EventType(str, Enum):
    TRADE = "trade"
    ORDER_BOOK = "order_book"
    BAR_1M = "bar_1m"
    FUNDING_RATE = "funding_rate"
    OPEN_INTEREST = "open_interest"
    INDEX_QUOTE = "index_quote"
    DATA_GAP = "data_gap"
    SIGNAL = "signal"


class AssetClass(str, Enum):
    INDEX = "index"
    FUTURE = "future"
    OPTION = "option"
    BOND_YIELD = "bond_yield"
    FX = "fx"
    EQUITY = "equity"
    ETF = "etf"
    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_PERP = "crypto_perp"
    TOKENIZED_EQUITY = "tokenized_equity"


class Venue(str, Enum):
    KIS = "kis"
    KRX = "krx"
    TOSS = "toss"
    KIWOOM = "kiwoom"
    BINANCE = "binance"
    HYPERLIQUID = "hyperliquid"
    COINGECKO = "coingecko"
    TIMEFOLIO = "timefolio"
    NASDAQ = "nasdaq"
    CME = "cme"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class BookUpdateType(str, Enum):
    SNAPSHOT = "snapshot"
    DELTA = "delta"


class SignalAction(str, Enum):
    ENTER_LONG = "enter_long"
    EXIT_LONG = "exit_long"
    ENTER_SHORT = "enter_short"
    EXIT_SHORT = "exit_short"


class OrderDestination(str, Enum):
    REAL = "real"
    TIMEFOLIO = "timefolio"


class OrderIntentStatus(str, Enum):
    CREATED = "created"
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    FILLED = "filled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


def utc_now_ns() -> int:
    return time.time_ns()


def ns_to_datetime(value: int) -> datetime:
    seconds, nanos = divmod(int(value), 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=nanos // 1000)


def trading_date_from_ns(value: int, offset_hours: int = 9) -> str:
    dt = ns_to_datetime(value) + timedelta(hours=offset_hours)
    return dt.strftime("%Y-%m-%d")


def stable_hash(parts: list[Any]) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class InstrumentId(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    venue: Venue
    asset_class: AssetClass
    raw_symbol: str
    std_symbol: str
    contract_month: str | None = None
    expiry: str | None = None
    strike: Decimal | None = None
    option_right: str | None = None
    underlying: str | None = None
    # Provider-independent product classification.  In particular this keeps a
    # tokenized security distinct from a stock/index derivative even when both
    # are quoted by Hyperliquid.
    security_kind: str | None = None
    currency: str | None = None
    exchange: str | None = None

    @field_validator("raw_symbol", "std_symbol", mode="before")
    @classmethod
    def normalize_symbol_text(cls, value: Any) -> str:
        return str(value or "").strip().upper()

    @property
    def key(self) -> str:
        base = f"{self.venue.value}:{self.asset_class.value}:{self.std_symbol}"
        if self.contract_month:
            base += f":{self.contract_month}"
        if self.expiry:
            base += f":{self.expiry}"
        if self.option_right:
            base += f":{self.option_right}"
        if self.strike is not None:
            base += f":{self.strike}"
        return base


class BaseMarketEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    event_id: str = ""
    event_type: EventType
    instrument: InstrumentId
    # source_ts_ns is the vendor timestamp.  It remains null when the vendor
    # does not supply one; wallclock_ts_ns is never fabricated as source time.
    source_ts_ns: int | None = None
    source_timestamp_kind: str = "provider_event"
    wallclock_ts_ns: int | None = None
    receive_ts_ns: int | None = None
    receive_monotonic_ns: int | None = None
    collector_run_id: str | None = None
    connection_id: str | None = None
    receive_sequence: int | None = None
    record_index: int | None = None
    native_event_id: str | None = None
    collection_start_ts_ns: int | None = None
    ingest_mode: Literal["realtime", "backfill"] = "realtime"
    trading_date: str = ""
    session: str = "unknown"
    venue: Venue
    provider: str
    sequence: int | str | None = None

    @model_validator(mode="after")
    def fill_common_fields(self) -> "BaseMarketEvent":
        wallclock = self.wallclock_ts_ns or self.receive_ts_ns or utc_now_ns()
        if self.wallclock_ts_ns is None:
            object.__setattr__(self, "wallclock_ts_ns", wallclock)
        if self.receive_ts_ns is None:
            object.__setattr__(self, "receive_ts_ns", wallclock)
        if not self.trading_date:
            object.__setattr__(self, "trading_date", trading_date_from_ns(self.source_ts_ns or wallclock))
        if not self.event_id:
            identity = self.native_event_id or getattr(self, "trade_id", None) or [
                self.connection_id,
                self.receive_sequence,
                self.record_index,
                self.sequence,
                self.source_ts_ns,
            ]
            object.__setattr__(
                self,
                "event_id",
                stable_hash(
                    [
                        self.schema_version,
                        self.event_type.value,
                        self.provider,
                        self.instrument.key,
                        identity,
                    ]
                ),
            )
        return self


class TradeEvent(BaseMarketEvent):
    event_type: Literal[EventType.TRADE] = EventType.TRADE
    price: Decimal
    quantity: Decimal
    aggressor_side: Side = Side.UNKNOWN
    trade_id: str | None = None

class OrderBookLevel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    price: Decimal
    quantity: Decimal
    order_count: int | None = None


class OrderBookEvent(BaseMarketEvent):
    event_type: Literal[EventType.ORDER_BOOK] = EventType.ORDER_BOOK
    update_type: BookUpdateType = BookUpdateType.SNAPSHOT
    depth: int
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)
    is_full_depth: bool = False
    # End of the sampling interval.  source_ts_ns remains the provider's
    # original timestamp and receive_ts_ns remains the actual receive time.
    sampled_at_ns: int | None = None
    channel: str | None = None
    provider_depth: int | None = None
    first_update_id: int | None = None
    final_update_id: int | None = None
    previous_update_id: int | None = None
    sync_generation: int | None = None
    fast: bool | None = None

    @model_validator(mode="after")
    def validate_depth(self) -> "OrderBookEvent":
        actual_depth = max(len(self.bids), len(self.asks))
        if self.depth != actual_depth:
            object.__setattr__(self, "depth", actual_depth)
        return self


class Bar1mEvent(BaseMarketEvent):
    """A completed provider-native one-minute OHLCV candle."""

    event_type: Literal[EventType.BAR_1M] = EventType.BAR_1M
    interval: Literal["1m"] = "1m"
    open_ts_ns: int
    close_ts_ns: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int | None = None
    closed: Literal[True] = True


class FundingRateEvent(BaseMarketEvent):
    """An hourly funding rate published by a perpetual market."""

    event_type: Literal[EventType.FUNDING_RATE] = EventType.FUNDING_RATE
    funding_rate: Decimal
    premium: Decimal | None = None
    funding_interval_hours: int = 1


class OpenInterestEvent(BaseMarketEvent):
    """Provider-native open interest without inferred unit conversion."""

    event_type: Literal[EventType.OPEN_INTEREST] = EventType.OPEN_INTEREST
    open_interest: Decimal
    open_interest_unit: str
    open_interest_value: Decimal | None = None
    value_currency: str | None = None
    mark_price: Decimal | None = None
    observation_method: str = "provider"


class IndexQuoteEvent(BaseMarketEvent):
    event_type: Literal[EventType.INDEX_QUOTE] = EventType.INDEX_QUOTE
    value: Decimal
    unit: str = "point"
    currency: str | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    change: Decimal | None = None
    change_pct: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataGapEvent(BaseMarketEvent):
    event_type: Literal[EventType.DATA_GAP] = EventType.DATA_GAP
    missing_from_sequence: int | str | None = None
    missing_to_sequence: int | str | None = None
    reason: str
    resync_status: str = "pending"


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    event_type: Literal[EventType.SIGNAL] = EventType.SIGNAL
    signal_id: str = ""
    strategy_id: str
    instrument: InstrumentId
    action: SignalAction
    event_ts_ns: int
    reference_price: Decimal
    valid_until_ts_ns: int
    source_event_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_signal_id(self) -> "Signal":
        if not self.signal_id:
            object.__setattr__(
                self,
                "signal_id",
                stable_hash(
                    [
                        self.schema_version,
                        self.strategy_id,
                        self.instrument.key,
                        self.action.value,
                        self.event_ts_ns,
                        self.source_event_id,
                    ]
                ),
            )
        return self


class OrderIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_id: str
    job_id: str
    signal_id: str
    destination: OrderDestination
    instrument: InstrumentId
    side: Side
    status: OrderIntentStatus = OrderIntentStatus.CREATED
    created_ts_ns: int = Field(default_factory=utc_now_ns)
    client_order_id: str
    quantity: Decimal | None = None
    weight_pct: Decimal | None = None
    limit_price: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def event_from_dict(data: dict[str, Any]) -> BaseMarketEvent:
    event_type = EventType(data["event_type"])
    mapping: dict[EventType, type[BaseMarketEvent]] = {
        EventType.TRADE: TradeEvent,
        EventType.ORDER_BOOK: OrderBookEvent,
        EventType.BAR_1M: Bar1mEvent,
        EventType.FUNDING_RATE: FundingRateEvent,
        EventType.OPEN_INTEREST: OpenInterestEvent,
        EventType.INDEX_QUOTE: IndexQuoteEvent,
        EventType.DATA_GAP: DataGapEvent,
    }
    if event_type not in mapping:
        raise ValueError(f"Unsupported market event type: {event_type}")
    return mapping[event_type].model_validate(data)
