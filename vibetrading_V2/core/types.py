"""Core domain types for V2 runner/strategy contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    quantity: float


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str
    quantity: float
    price: float
    timestamp: datetime

