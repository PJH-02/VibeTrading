from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

import time
from decimal import Decimal
from .models import AssetClass, BaseMarketEvent, BookUpdateType, EventType, IndexQuoteEvent, OrderBookEvent, OrderBookLevel, TradeEvent, Venue, event_from_dict
from .dedup import BoundedDedup


class HotPathStore:
    def __init__(
        self,
        max_trades: int = 2048,
        max_books: int = 512,
        max_index_quotes: int = 2048,
    ) -> None:
        self._trades: dict[str, deque[TradeEvent]] = defaultdict(lambda: deque(maxlen=max_trades))
        self._books: dict[str, deque[OrderBookEvent]] = defaultdict(lambda: deque(maxlen=max_books))
        self._index_quotes: dict[str, deque[IndexQuoteEvent]] = defaultdict(lambda: deque(maxlen=max_index_quotes))
        self._seen = BoundedDedup(max_entries=max(max_trades * 8, 16_384))
        self._crypto_books: dict[str, tuple[dict[Decimal, Decimal], dict[Decimal, Decimal], int, int]] = {}
        self._crypto_first: set[str] = set()

    def apply(self, event: BaseMarketEvent | dict) -> bool:
        if isinstance(event, dict):
            event = event_from_dict(event)
        if not self._seen.add_if_new(event.event_id):
            return False
        key = event.instrument.key
        if event.event_type == EventType.TRADE:
            self._trades[key].append(event)  # type: ignore[arg-type]
        elif event.event_type == EventType.DATA_GAP:
            self._crypto_books.pop(key, None)
            self._crypto_first.discard(key)
            self._books.pop(key, None)
            return False
        elif event.event_type == EventType.ORDER_BOOK:
            book = event  # type: ignore[assignment]
            if book.instrument.asset_class == AssetClass.CRYPTO_PERP:
                if book.fast is False:
                    return False
                reconstructed = self._apply_crypto_book(book)
                if reconstructed is not None:
                    self._books[key].append(reconstructed)
                else:
                    return False
            else:
                self._books[key].append(book)
        elif event.event_type == EventType.INDEX_QUOTE:
            self._index_quotes[key].append(event)  # type: ignore[arg-type]
        return True

    def _apply_crypto_book(self, event: OrderBookEvent) -> OrderBookEvent | None:
        key = event.instrument.key
        current = self._crypto_books.get(key)
        if event.update_type == BookUpdateType.SNAPSHOT:
            bids: dict[Decimal, Decimal] = {}
            asks: dict[Decimal, Decimal] = {}
            self._crypto_first.add(key)
        elif current is not None and current[2] == event.sync_generation and event.final_update_id is not None and event.final_update_id > current[3]:
            previous=event.previous_update_id
            if previous is not None and previous != current[3]:
                if key not in self._crypto_first or not previous <= current[3] <= event.final_update_id:
                    self._crypto_books.pop(key,None)
                    self._crypto_first.discard(key)
                    return None
            if event.instrument.venue == Venue.BYBIT and event.final_update_id != current[3]+1:
                self._crypto_books.pop(key,None)
                self._crypto_first.discard(key)
                return None
            bids, asks = current[0], current[1]
        else:
            self._crypto_books.pop(key, None)
            self._crypto_first.discard(key)
            return None
        for target, levels in ((bids, event.bids), (asks, event.asks)):
            for level in levels:
                if level.quantity == 0:
                    target.pop(level.price, None)
                else:
                    target[level.price] = level.quantity
        if len(bids) > 1000:
            for price in sorted(bids)[:-1000]: bids.pop(price,None)
        if len(asks) > 1000:
            for price in sorted(asks)[1000:]: asks.pop(price,None)
        if not bids or not asks:
            self._crypto_books.pop(key, None)
            self._crypto_first.discard(key)
            return None
        self._crypto_books[key] = (bids, asks, int(event.sync_generation or 0), int(event.final_update_id or 0))
        top_bids = sorted(bids, reverse=True)[:50]
        top_asks = sorted(asks)[:50]
        if top_bids[0] >= top_asks[0]:
            self._crypto_books.pop(key, None)
            self._crypto_first.discard(key)
            return None
        if event.update_type == BookUpdateType.DELTA:
            self._crypto_first.discard(key)
        return event.model_copy(update={"update_type": BookUpdateType.SNAPSHOT,
            "bids": [OrderBookLevel(price=p, quantity=bids[p]) for p in top_bids],
            "asks": [OrderBookLevel(price=p, quantity=asks[p]) for p in top_asks],
            "depth": max(len(top_bids), len(top_asks)), "is_full_depth": False})

    def latest_trade(self, instrument_key: str) -> TradeEvent | None:
        values = self._trades.get(instrument_key)
        return values[-1] if values else None

    def latest_book(self, instrument_key: str) -> OrderBookEvent | None:
        values = self._books.get(instrument_key)
        event = values[-1] if values else None
        if event is not None and event.instrument.asset_class == AssetClass.CRYPTO_PERP:
            if time.time_ns() - int(event.receive_ts_ns or 0) > 1_000_000_000:
                return None
        return event

    def latest_index_quote(self, instrument_key: str) -> IndexQuoteEvent | None:
        values = self._index_quotes.get(instrument_key)
        return values[-1] if values else None

    def instruments(self) -> list[str]:
        return sorted(set(self._trades) | set(self._books) | set(self._index_quotes))


@dataclass(frozen=True)
class StrategyDataView:
    hot_path: HotPathStore
    history_reader: object | None = None

    def history(self, instrument_key: str, event_type: str, limit: int = 1000) -> list[dict]:
        if self.history_reader is None:
            return []
        reader = getattr(self.history_reader, "history", None)
        if reader is None:
            return []
        return list(reader(instrument_key=instrument_key, event_type=event_type, limit=limit))

    def latest_trade(self, instrument_key: str) -> TradeEvent | None:
        return self.hot_path.latest_trade(instrument_key)

    def latest_book(self, instrument_key: str) -> OrderBookEvent | None:
        return self.hot_path.latest_book(instrument_key)

    def latest_index_quote(self, instrument_key: str) -> IndexQuoteEvent | None:
        return self.hot_path.latest_index_quote(instrument_key)

    def subscribe_keys(self, subscriptions: Iterable[object]) -> list[str]:
        out: list[str] = []
        for item in subscriptions:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and item.get("instrument_key"):
                out.append(str(item["instrument_key"]))
        return out
