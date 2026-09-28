from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a bounded JSONL replay capture from Parquet")
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-events", type=int, default=10_000)
    parser.add_argument("--providers", default="binance-perp,bitget-perp,bybit-perp")
    args = parser.parse_args()
    if args.max_events <= 0:
        raise ValueError("max-events must be positive")
    if args.output.exists():
        raise RuntimeError("output already exists")

    import pyarrow.parquet as pq

    providers = {item.strip() for item in args.providers.split(",") if item.strip()}
    files = sorted(
        args.input_root.rglob("*.parquet"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    used_files = 0
    with args.output.open("x", encoding="utf-8") as output:
        for path in files:
            file_used = False
            try:
                for batch in pq.ParquetFile(path).iter_batches(batch_size=2048):
                    for row in batch.to_pylist():
                        if providers and str(row.get("provider") or "") not in providers:
                            continue
                        output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                        written += 1
                        file_used = True
                        if written >= args.max_events:
                            break
                    if written >= args.max_events:
                        break
            except (OSError, FileNotFoundError):
                continue
            if file_used:
                used_files += 1
            if written >= args.max_events:
                break
    if written == 0:
        args.output.unlink(missing_ok=True)
        raise RuntimeError("no matching Parquet rows were available")
    print(json.dumps({"events": written, "files": used_files, "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
