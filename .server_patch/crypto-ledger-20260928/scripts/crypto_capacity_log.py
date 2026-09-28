"""Record bounded, read-only server metrics for later crypto capacity review."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DATA = Path("/srv/vibetrading-data/runtime_data")
PROVIDERS = ("binance-perp", "bitget-perp", "bybit-perp")


def _health(path: Path) -> dict:
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"missing": True}
    keep = ("state", "phase", "ts_ns", "last_event_ts_ns", "published_count",
            "published_bytes", "pending", "buffer_bytes", "rss_bytes",
            "cgroup_used_bytes", "messages", "acked", "flushes", "files",
            "reason", "archive")
    result = {key: item[key] for key in keep if key in item}
    if "archive" in result:
        archive = result["archive"]
        result["archive"] = {key: archive.get(key) for key in ("uploaded", "upload_candidates", "ran")}
    return result


def _pressure(resource:str)->dict:
    result={}
    try:
        for line in Path(f"/proc/pressure/{resource}").read_text().splitlines():
            kind,*items=line.split()
            result[kind]={key:float(value) if key!="total" else int(value)
                          for key,value in (item.split("=",1) for item in items)}
    except (OSError,ValueError):pass
    return result


def _block_device(name:str="vdb")->dict:
    try:
        fields=next(line.split() for line in Path("/proc/diskstats").read_text().splitlines() if line.split()[2]==name)
    except (OSError,StopIteration):return {}
    values=[int(value) for value in fields[3:]]
    keys=("reads","reads_merged","read_sectors","read_ms","writes","writes_merged","write_sectors","write_ms",
          "io_in_progress","io_ms","weighted_io_ms","discards","discards_merged","discard_sectors","discard_ms","flushes","flush_ms")
    return dict(zip(keys,values))


def sample() -> dict:
    output = {"time_utc": datetime.now(timezone.utc).isoformat()}
    disk = shutil.disk_usage(DATA)
    output["disk"] = {"total": disk.total, "used": disk.used, "free": disk.free}
    output["crypto_sqlite_bytes"] = (DATA / "crypto_state/vibetrading.sqlite3").stat().st_size
    wal=DATA / "crypto_state/vibetrading.sqlite3-wal"
    output["crypto_sqlite_wal_bytes"] = wal.stat().st_size if wal.exists() else 0
    output["pressure"]={"io":_pressure("io"),"memory":_pressure("memory"),"cpu":_pressure("cpu")}
    output["data_device"]=_block_device()
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"MemTotal", "MemAvailable", "SwapFree"}:
            memory[key] = int(value.strip().split()[0]) * 1024
    output["host_memory"] = memory
    with urllib.request.urlopen("http://127.0.0.1:8222/jsz?streams=true&consumers=true", timeout=5) as response:
        nats = json.load(response)
    output["nats"] = {
        stream["name"]: {
            "messages": stream["state"].get("messages"),
            "bytes": stream["state"].get("bytes"),
            "consumers": {
                consumer.get("name"): {
                    "num_pending": consumer.get("num_pending"),
                    "num_ack_pending": consumer.get("num_ack_pending"),
                    "num_redelivered": consumer.get("num_redelivered"),
                }
                for consumer in stream.get("consumer_detail", [])
            },
        }
        for account in nats.get("account_details", [])
        for stream in account.get("stream_detail", [])
    }
    stats = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
        check=True, text=True, capture_output=True, timeout=20,
    )
    output["containers"] = [json.loads(line) for line in stats.stdout.splitlines() if line.strip()]
    output["health"] = {
        **{provider: _health(DATA / "state/health" / f"collectors-{provider}.json") for provider in PROVIDERS},
        "storage": _health(DATA / "crypto_state/health/crypto-storage.json"),
        "maintenance": _health(DATA / "crypto_state/health/maintenance.json"),
    }
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-sec", type=int, default=172800)
    parser.add_argument("--interval-sec", type=int, default=60)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.duration_sec
    while time.monotonic() < deadline:
        try:
            row = sample()
        except Exception as exc:
            row = {"time_utc": datetime.now(timezone.utc).isoformat(), "error": f"{type(exc).__name__}: {exc}"}
        with args.output.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(max(1, args.interval_sec), remaining))


if __name__ == "__main__":
    main()
