from __future__ import annotations

from decimal import Decimal

from vibetrading.hot_path import StrategyDataView
from core.models import Bar1mEvent, BaseMarketEvent, EventType, Signal, SignalAction, utc_now_ns


STRATEGY_ID = "example_signal"
SUBSCRIPTIONS = [{"venue": "binance", "symbol": "BTCUSDT", "event_type": "bar_1m"}]
WARMUP = {"bars": 30}


class ExampleSignalStrategy:
    def on_event(self, event: BaseMarketEvent, data: StrategyDataView) -> list[Signal]:
        if event.event_type != EventType.BAR_1M:
            return []
        bar = event
        if not isinstance(bar, Bar1mEvent) or not bar.closed or not data.ready:
            return []
        bars = data.closed_bars(bar.instrument.key, limit=4)
        if len(bars) < 4:
            return []
        prev = bars[-2]
        if bar.close > prev.high:
            return [
                Signal(
                    strategy_id=STRATEGY_ID,
                    instrument=bar.instrument,
                    action=SignalAction.ENTER_LONG,
                    event_ts_ns=bar.close_ts_ns,
                    reference_price=bar.close,
                    valid_until_ts_ns=utc_now_ns() + 60 * 1_000_000_000,
                    source_event_id=bar.event_id,
                    metadata={"trigger": "close_above_prev_high"},
                )
            ]
        if bar.close < prev.low:
            return [
                Signal(
                    strategy_id=STRATEGY_ID,
                    instrument=bar.instrument,
                    action=SignalAction.EXIT_LONG,
                    event_ts_ns=bar.close_ts_ns,
                    reference_price=bar.close,
                    valid_until_ts_ns=utc_now_ns() + 60 * 1_000_000_000,
                    source_event_id=bar.event_id,
                    metadata={"trigger": "close_below_prev_low"},
                )
            ]
        return []


def create_strategy() -> ExampleSignalStrategy:
    return ExampleSignalStrategy()
