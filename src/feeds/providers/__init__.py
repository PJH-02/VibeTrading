"""Market-data provider implementations for the operational runtime."""
from __future__ import annotations

from core.models import Venue

from .arcus import ArcusPerpCollector
from .binance import BinancePerpCollector
from .bitget import BitgetPerpCollector
from .bybit import BybitPerpCollector
from .grvt import GrvtPerpCollector
from .perp_common import SYMBOLS

_PERP_COLLECTORS = {
    Venue.BYBIT: BybitPerpCollector,
    Venue.BITGET: BitgetPerpCollector,
    Venue.BINANCE: BinancePerpCollector,
    Venue.ARCUS: ArcusPerpCollector,
    Venue.GRVT: GrvtPerpCollector,
}


def create_perp_collector(venue: Venue, symbols: tuple[str, ...] = SYMBOLS):
    """Construct the explicit public perpetual collector for a venue."""
    try:
        collector = _PERP_COLLECTORS[venue]
    except (KeyError, TypeError):
        raise ValueError(f"unsupported crypto perpetual venue: {venue}") from None
    return collector(symbols)


__all__ = [
    "SYMBOLS",
    "ArcusPerpCollector",
    "BinancePerpCollector",
    "BitgetPerpCollector",
    "BybitPerpCollector",
    "GrvtPerpCollector",
    "create_perp_collector",
]
