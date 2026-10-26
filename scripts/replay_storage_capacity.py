"""Replay a captured public feed into an isolated Parquet writer at 2x rate.

This checks writer throughput/RSS; a production gate also needs NATS, archive,
and a 48-hour live soak. Never point output-root at production storage.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path

from storage.parquet_store import ParquetBatchWriter
from vibetrading.settings import get_settings
from storage.sqlite_store import RuntimeStore


def rss() -> int:
    try:
        import psutil
        return int(psutil.Process().memory_info().rss)
    except ImportError:
        return 0


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--observed-events-per-sec",type=float,required=True)
    parser.add_argument("--multiplier",type=float,default=2)
    parser.add_argument("--duration-sec",type=float,default=60)
    parser.add_argument("--output-root",type=Path,required=True)
    args=parser.parse_args()
    if args.observed_events_per_sec<=0 or args.multiplier<=0 or args.duration_sec<=0:
        raise ValueError("rates and duration must be positive")
    target=args.output_root.resolve()
    if target.exists() and any(target.iterdir()):
        raise RuntimeError("output-root must be empty; production data cannot be overwritten")
    target.mkdir(parents=True,exist_ok=True)
    settings=get_settings()
    settings=replace(settings,state_dir=target/"state",data_root=target,
                     parquet=replace(settings.parquet,data_root=target/"parquet"))
    store=RuntimeStore(settings)
    writer=ParquetBatchWriter(settings,store)
    file=args.input.open("r",encoding="utf-8")
    rate=args.observed_events_per_sec*args.multiplier
    started=time.monotonic(); count=0; flushes=0; max_rss=rss(); max_buffer=0; max_flush_sec=0.0
    try:
        while time.monotonic()-started<args.duration_sec:
            line=file.readline()
            if not line:
                file.seek(0)
                line=file.readline()
                if not line:raise RuntimeError("input capture is empty")
            event=json.loads(line)
            if writer.add(event):count+=1
            if count and (count%16384==0 or writer.buffer_bytes>=64*1024*1024 or writer.should_flush()):
                before=time.monotonic(); writer.flush_all();max_flush_sec=max(max_flush_sec,time.monotonic()-before);flushes+=1
            max_rss=max(max_rss,rss());max_buffer=max(max_buffer,writer.buffer_bytes)
            due=started+count/rate
            delay=due-time.monotonic()
            if delay>0:time.sleep(min(.1,delay))
        before=time.monotonic();writer.flush_all();max_flush_sec=max(max_flush_sec,time.monotonic()-before);flushes+=1
    finally:
        file.close();store.close()
    elapsed=time.monotonic()-started
    compressed=sum(p.stat().st_size for p in (target/"parquet").rglob("*.parquet"))
    print(json.dumps({"events":count,"elapsed_seconds":round(elapsed,3),"events_per_sec":round(count/elapsed,2),
                      "target_events_per_sec":rate,"max_rss_bytes":max_rss,"max_buffer_bytes":max_buffer,
                      "max_flush_seconds":round(max_flush_sec,3),"flushes":flushes,
                      "parquet_bytes":compressed,"output_root":str(target)},ensure_ascii=False))


if __name__ == "__main__":
    main()
