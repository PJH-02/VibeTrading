from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

from .models import BaseMarketEvent, EventType, IndexQuoteEvent, OrderBookEvent, TradeEvent, event_from_dict
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

    def apply(self, event: BaseMarketEvent | dict) -> bool:
        if isinstance(event, dict):
            event = event_from_dict(event)
        if not self._seen.add_if_new(event.event_id):
            return False
        key = event.instrument.key
        if event.event_type == EventType.TRADE:
            self._trades[key].append(event)  # type: ignore[arg-type]
        elif event.event_type == EventType.ORDER_BOOK:
            self._books[key].append(event)  # type: ignore[arg-type]
        elif event.event_type == EventType.INDEX_QUOTE:
            self._index_quotes[key].append(event)  # type: ignore[arg-type]
        return True

    def latest_trade(self, instrument_key: str) -> TradeEvent | None:
        values = self._trades.get(instrument_key)
        return values[-1] if values else None

    def latest_book(self, instrument_key: str) -> OrderBookEvent | None:
        values = self._books.get(instrument_key)
        return values[-1] if values else None

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

