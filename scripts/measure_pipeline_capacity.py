"""Sample server queue, storage and container pressure without enabling feeds.

Run on the Docker host with the deployment environment loaded. Output is JSONL
so a 48-hour observation can be retained and checked for rising backlog.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from runtime.bus import NatsJetStreamBus
from runtime.settings import get_settings


def _tree_bytes(root: Path, suffix: str) -> tuple[int, int]:
    count = total = 0
    if root.exists():
        for path in root.rglob("*" + suffix):
            try:
                total += path.stat().st_size
                count += 1
            except OSError:
                continue
    return count, total


def _health(root: Path) -> dict:
    out = {}
    if root.exists():
        for path in root.glob("*.json"):
            try:
                item=json.loads(path.read_text(encoding="utf-8"))
                out[path.stem]={key:item.get(key) for key in ("state","phase","published_count","published_bytes","pending","buffer_bytes","rss_bytes","cgroup_used_bytes","cgroup_limit_bytes","messages","files","flushes") if key in item}
            except (OSError,ValueError):
                pass
    return out


def _docker_stats() -> list[dict]:
    if shutil.which("docker") is None:
        return []
    result=subprocess.run(["docker","stats","--no-stream","--format","{{json .}}"],
                          capture_output=True,text=True,timeout=30,check=False)
    if result.returncode:
        return []
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


async def sample() -> dict:
    settings=get_settings()
    bus=NatsJetStreamBus(settings)
    streams={}
    try:
        address=urlparse(settings.nats.url)
        with socket.create_connection((address.hostname or "localhost", address.port or 4222),timeout=1):
            pass
        await bus.connect()
        for name, method in (("market",bus.market_stream_status),("crypto",bus.crypto_stream_status)):
            try:
                streams[name]=await method()
            except Exception as exc:
                streams[name]={"error":type(exc).__name__}
        for stream,durable in ((bus.market_stream_name,"parquet-storage"),(bus.crypto_stream_name,"crypto-parquet-storage")):
            try:
                info=await bus._js.consumer_info(stream,durable)
                streams["crypto" if stream==bus.crypto_stream_name else "market"].update(
                    {"consumer_pending":int(info.num_pending),"ack_pending":int(info.num_ack_pending)})
            except Exception:
                pass
    except Exception as exc:
        streams={"error":type(exc).__name__}
    finally:
        await bus.close()
    disk=shutil.disk_usage(settings.data_root)
    roots={"market":settings.parquet.data_root,"crypto":settings.data_root/"crypto_market_data"}
    storage={name:{"parquet_files":count,"parquet_bytes":size}
             for name,root in roots.items() for count,size in [_tree_bytes(root,".parquet")]}
    sqlite_files,sqlite_bytes=_tree_bytes(settings.data_root,".sqlite3")
    return {"ts_ns":time.time_ns(),"streams":streams,"disk":{"total":disk.total,"used":disk.used,"free":disk.free},
            "storage":storage,"sqlite":{"files":sqlite_files,"bytes":sqlite_bytes},
            "health":_health(settings.state_dir/"health")|_health(settings.data_root/"crypto_state"/"health"),
            "containers":_docker_stats()}


async def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--duration-sec",type=float,default=0)
    parser.add_argument("--interval-sec",type=float,default=60)
    args=parser.parse_args()
    deadline=time.monotonic()+args.duration_sec
    while True:
        print(json.dumps(await sample(),ensure_ascii=False,separators=(",", ":")),flush=True)
        if args.duration_sec <= 0 or time.monotonic() >= deadline:
            return
        await asyncio.sleep(max(1,args.interval_sec))


if __name__ == "__main__":
    asyncio.run(main())
