from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


PROVIDERS = ("binance-perp", "bitget-perp", "bybit-perp")


def _maximum(values: list[float | int]) -> float | int | None:
    return max(values) if values else None


def _minimum(values: list[float | int]) -> float | int | None:
    return min(values) if values else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize crypto capacity JSONL")
    parser.add_argument("input", type=Path)
    args = parser.parse_args()

    rows = []
    for line in args.input.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            row["_time"] = datetime.fromisoformat(row["time_utc"]).timestamp()
            if "error" not in row:
                rows.append(row)
        except (ValueError, KeyError, TypeError):
            continue

    rates: list[float] = []
    byte_rates: list[float] = []
    for previous, current in zip(rows, rows[1:]):
        elapsed = current["_time"] - previous["_time"]
        if elapsed <= 0:
            continue
        event_delta = 0
        byte_delta = 0
        valid = True
        for provider in PROVIDERS:
            before = (previous.get("health", {}).get(provider) or {})
            after = (current.get("health", {}).get(provider) or {})
            events_before = before.get("published_count")
            events_after = after.get("published_count")
            bytes_before = before.get("published_bytes")
            bytes_after = after.get("published_bytes")
            if events_before is None or events_after is None or events_after < events_before:
                valid = False
                break
            event_delta += events_after - events_before
            if bytes_before is not None and bytes_after is not None and bytes_after >= bytes_before:
                byte_delta += bytes_after - bytes_before
        if valid:
            rates.append(event_delta / elapsed)
            byte_rates.append(byte_delta / elapsed)

    nats = [(row.get("nats", {}).get("CRYPTO_DATA") or {}) for row in rows]
    consumers = [
        (item.get("consumers", {}).get("crypto-parquet-storage") or {}) for item in nats
    ]
    io_full = [
        float(row.get("pressure", {}).get("io", {}).get("full", {}).get("avg10", 0))
        for row in rows
    ]
    result = {
        "samples": len(rows),
        "start_utc": rows[0]["time_utc"] if rows else None,
        "end_utc": rows[-1]["time_utc"] if rows else None,
        "observed_combined_events_per_sec": {
            "max": round(float(_maximum(rates) or 0), 2),
            "average": round(sum(rates) / len(rates), 2) if rates else 0,
        },
        "observed_combined_bytes_per_sec": {
            "max": round(float(_maximum(byte_rates) or 0), 2),
            "average": round(sum(byte_rates) / len(byte_rates), 2) if byte_rates else 0,
        },
        "nats": {
            "max_messages": _maximum([int(item.get("messages") or 0) for item in nats]),
            "max_bytes": _maximum([int(item.get("bytes") or 0) for item in nats]),
            "max_pending": _maximum([int(item.get("num_pending") or 0) for item in consumers]),
            "max_ack_pending": _maximum([int(item.get("num_ack_pending") or 0) for item in consumers]),
            "max_redelivered": _maximum([int(item.get("num_redelivered") or 0) for item in consumers]),
        },
        "host": {
            "min_memory_available_bytes": _minimum([
                int(row.get("host_memory", {}).get("MemAvailable") or 0) for row in rows
            ]),
            "min_disk_free_bytes": _minimum([int(row.get("disk", {}).get("free") or 0) for row in rows]),
            "max_sqlite_bytes": _maximum([int(row.get("crypto_sqlite_bytes") or 0) for row in rows]),
            "max_sqlite_wal_bytes": _maximum([int(row.get("crypto_sqlite_wal_bytes") or 0) for row in rows]),
            "max_io_full_avg10": round(float(_maximum(io_full) or 0), 2),
        },
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
