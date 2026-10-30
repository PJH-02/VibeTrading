"""Read-only public-feed smoke check; does not write or submit orders."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from core.models import Venue
from market.hot_path import HotPathStore
from feeds.providers import create_perp_collector


class CountingBus:
    def __init__(self, capture_path: Path | None = None) -> None:
        self.counts: Counter[str] = Counter()
        self.bytes: Counter[str] = Counter()
        self.valid_books: Counter[str] = Counter()
        self.first_invalid_book: dict[str,dict] = {}
        self.hot=HotPathStore()
        self.capture = capture_path.open("w",encoding="utf-8") if capture_path else None

    async def publish_market_event(self, event) -> None:
        key=f"{event.instrument.std_symbol}:{event.event_type.value}"
        self.counts[key] += 1
        self.bytes[key] += len(event.model_dump_json())
        accepted=self.hot.apply(event)
        if event.event_type.value == "order_book" and accepted and self.hot.latest_book(event.instrument.key) is not None:
            self.valid_books[event.instrument.std_symbol]+=1
        elif event.event_type.value == "order_book" and not accepted and event.instrument.std_symbol not in self.first_invalid_book:
            self.first_invalid_book[event.instrument.std_symbol]={"update_type":event.update_type.value,
                "first":event.first_update_id,"final":event.final_update_id,"previous":event.previous_update_id,
                "generation":event.sync_generation,"fast":event.fast}
        if self.capture:
            self.capture.write(event.model_dump_json()+"\n")

    def close(self) -> None:
        if self.capture:self.capture.close()


async def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--venue",choices=["binance","bitget","bybit"],required=True)
    parser.add_argument("--duration-sec",type=float,default=5)
    parser.add_argument("--capture-path",type=Path)
    args=parser.parse_args()
    bus=CountingBus(args.capture_path)
    try:
        await create_perp_collector(Venue(args.venue)).run(bus,duration_sec=args.duration_sec)
        status="ok"
    except Exception as exc:
        status=f"{type(exc).__name__}: {exc}"
    finally:
        bus.close()
    print(json.dumps({"venue":args.venue,"status":status,"counts":dict(bus.counts),
                      "bytes":dict(bus.bytes),"total_bytes":sum(bus.bytes.values()),
                      "valid_books":dict(bus.valid_books),"first_invalid_book":bus.first_invalid_book},sort_keys=True))
    return 0 if status == "ok" and all(bus.valid_books[symbol] > 0 for symbol in ("BTCUSDT","ETHUSDT","SOLUSDT")) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
