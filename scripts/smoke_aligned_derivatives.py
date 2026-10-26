from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from feeds.collection_timing import MINUTE_NS
from core.models import EventType
from feeds.providers.binance import BinanceCollector
from feeds.providers.hyperliquid import HyperliquidCollector
from feeds.universe_config import configured_binance_securities, configured_hyperliquid_securities


class CountingBus:
    def __init__(self) -> None:
        self.counts: Counter[tuple[str, str]] = Counter()
        self.starts: dict[str, int] = {}
        self.errors: list[str] = []

    async def publish_market_event(self, event) -> None:
        self.counts[(event.provider, event.event_type.value)] += 1
        self.starts.setdefault(event.provider, event.collection_start_ts_ns)
        if event.wallclock_ts_ns < event.collection_start_ts_ns:
            self.errors.append(f"pre-boundary event: {event.provider} {event.event_id}")
        if event.event_type == EventType.BAR_1M and event.open_ts_ns % MINUTE_NS:
            self.errors.append(f"unaligned bar: {event.provider} {event.open_ts_ns}")


async def main(duration: float, provider: str, stream_only: bool) -> None:
    bus = CountingBus()
    collectors = []
    if provider in {"all", "hyperliquid"}:
        collectors.append(HyperliquidCollector(configured_hyperliquid_securities(), enable_rest=not stream_only))
    if provider in {"all", "binance"}:
        collectors.append(BinanceCollector(configured_binance_securities()))
    await asyncio.gather(*(collector.run(bus, duration_sec=duration) for collector in collectors))
    if bus.errors:
        raise RuntimeError("; ".join(bus.errors))
    print({"counts": {f"{provider}:{kind}": count for (provider, kind), count in sorted(bus.counts.items())}, "starts": bus.starts})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=75.0)
    parser.add_argument("--provider", choices=("all", "hyperliquid", "binance"), default="all")
    parser.add_argument("--stream-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.duration, args.provider, args.stream_only))
