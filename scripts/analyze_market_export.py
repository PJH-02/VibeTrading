"""Merge an exported market-data snapshot by asset and run data-quality checks.

The input is the directory produced by ``export_market_snapshot.py`` after the
``.tar.gz`` file has been extracted.  Consolidation is streaming and therefore
does not require loading the full export into RAM.  Quality analysis loads one
asset/event pair at a time.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


NANOSECONDS = 1_000_000_000
SESSION_BREAK_SECONDS = 15 * 60


def safe_name(value: Any) -> str:
    text = str(value or "unknown").strip()
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "unknown"


def first_value(table: pa.Table, name: str, fallback: str = "unknown") -> str:
    if name not in table.column_names or table.num_rows == 0:
        return fallback
    values = table[name].to_pylist()
    return str(next((value for value in values if value not in (None, "")), fallback))


def align_schema(table: pa.Table, schema: pa.Schema) -> pa.Table:
    arrays = []
    for field in schema:
        if field.name not in table.column_names:
            arrays.append(pa.nulls(table.num_rows, type=field.type))
            continue
        column = table[field.name]
        if column.type != field.type:
            try:
                column = pc.cast(column, field.type, safe=False)
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                column = pa.array(column.to_pylist(), type=field.type)
        arrays.append(column)
    return pa.Table.from_arrays(arrays, schema=schema)


@dataclass(frozen=True)
class AssetFile:
    asset_key: str
    provider: str
    venue: str
    symbol: str
    event_type: str
    path: Path


def discover_input_files(export_root: Path) -> list[Path]:
    data_root = export_root / "market_data"
    if not data_root.is_dir():
        raise FileNotFoundError(f"market_data directory not found under {export_root}")
    files = sorted(path for path in data_root.glob("**/*.parquet") if path.is_file())
    if not files:
        raise FileNotFoundError(f"no Parquet files found under {data_root}")
    return files


def consolidate_by_asset(export_root: Path, output_root: Path, *, overwrite: bool = False) -> list[AssetFile]:
    if output_root.exists():
        if not overwrite:
            raise FileExistsError(f"output exists; use --overwrite: {output_root}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    writers: dict[tuple[str, str, str, str], pq.ParquetWriter] = {}
    schemas: dict[tuple[str, str, str, str], pa.Schema] = {}
    paths: dict[tuple[str, str, str, str], Path] = {}
    row_counts: Counter[tuple[str, str, str, str]] = Counter()

    try:
        for input_path in discover_input_files(export_root):
            parquet = pq.ParquetFile(input_path)
            for batch in parquet.iter_batches(batch_size=8192):
                table = pa.Table.from_batches([batch])
                symbol_column = "instrument_std_symbol" if "instrument_std_symbol" in table.column_names else "symbol"
                if symbol_column not in table.column_names:
                    raise ValueError(f"instrument symbol column missing: {input_path}")
                symbols = sorted({str(value) for value in table[symbol_column].to_pylist() if value not in (None, "")})
                for symbol in symbols:
                    # Parquet dictionary encodings can differ by input file.  Cast only
                    # the predicate column to text so filtering remains independent of
                    # the physical dictionary type while the written payload stays intact.
                    symbol_values = pc.cast(table[symbol_column], pa.string(), safe=False)
                    subset = table.filter(pc.equal(symbol_values, pa.scalar(symbol)))
                    if subset.num_rows == 0:
                        continue
                    provider = first_value(subset, "provider", "unknown")
                    venue = first_value(subset, "venue", first_value(subset, "instrument_venue", "unknown"))
                    event_type = first_value(subset, "event_type", "unknown")
                    key = (provider, venue, symbol, event_type)
                    if key not in writers:
                        asset_directory = output_root / f"{safe_name(provider)}__{safe_name(venue)}__{safe_name(symbol)}"
                        asset_directory.mkdir(parents=True, exist_ok=True)
                        path = asset_directory / f"{safe_name(event_type)}.parquet"
                        schemas[key] = subset.schema
                        paths[key] = path
                        writers[key] = pq.ParquetWriter(
                            path,
                            subset.schema,
                            compression="zstd",
                            compression_level=3,
                            use_dictionary=True,
                            write_statistics=True,
                        )
                    writers[key].write_table(align_schema(subset, schemas[key]), row_group_size=8192)
                    row_counts[key] += subset.num_rows
    finally:
        for writer in writers.values():
            writer.close()

    assets = [
        AssetFile(
            asset_key=f"{provider}|{venue}|{symbol}",
            provider=provider,
            venue=venue,
            symbol=symbol,
            event_type=event_type,
            path=paths[(provider, venue, symbol, event_type)],
        )
        for provider, venue, symbol, event_type in sorted(paths)
    ]
    pd.DataFrame(
        [
            {
                "asset_key": item.asset_key,
                "provider": item.provider,
                "venue": item.venue,
                "symbol": item.symbol,
                "event_type": item.event_type,
                "rows": row_counts[(item.provider, item.venue, item.symbol, item.event_type)],
                "path": str(item.path.relative_to(output_root)),
            }
            for item in assets
        ]
    ).to_csv(output_root / "assets.csv", index=False, encoding="utf-8-sig")
    return assets


def numeric(values: Iterable[Any]) -> np.ndarray:
    return pd.to_numeric(pd.Series(list(values)), errors="coerce").to_numpy(dtype=float)


def timestamp_column(table: pa.Table, event_type: str) -> np.ndarray:
    candidates = {
        "order_book": ("sampled_at_ns", "source_ts_ns"),
        "bar_1m": ("close_ts_ns", "source_ts_ns"),
    }.get(event_type, ("source_ts_ns",))
    for name in candidates:
        if name in table.column_names:
            values = pd.to_numeric(table[name].to_pandas(), errors="coerce")
            if values.notna().any():
                return values.fillna(0).to_numpy(dtype=np.int64)
    return np.zeros(table.num_rows, dtype=np.int64)


def percentile(values: np.ndarray, value: float) -> float | None:
    finite = values[np.isfinite(values)]
    return float(np.percentile(finite, value)) if finite.size else None


def book_levels(table: pa.Table, side: str) -> list[list[dict[str, Any]]]:
    if side in table.column_names:
        return [list(value or []) for value in table[side].to_pylist()]
    price_names = [f"{side}_{index}_price" for index in range(1, 11)]
    quantity_names = [f"{side}_{index}_quantity" for index in range(1, 11)]
    rows: list[list[dict[str, Any]]] = []
    columns = {
        name: table[name].to_pylist() if name in table.column_names else [None] * table.num_rows
        for name in price_names + quantity_names
    }
    for row_index in range(table.num_rows):
        levels = []
        for price_name, quantity_name in zip(price_names, quantity_names):
            price = columns[price_name][row_index]
            quantity = columns[quantity_name][row_index]
            if price is not None:
                levels.append({"price": price, "quantity": quantity})
        rows.append(levels)
    return rows


def gap_metrics(timestamps_ns: np.ndarray, expected_seconds: float) -> dict[str, Any]:
    valid = np.sort(timestamps_ns[timestamps_ns > 0])
    if valid.size == 0:
        return {}
    unique_seconds, counts = np.unique(valid // NANOSECONDS, return_counts=True)
    deltas = np.diff(unique_seconds).astype(float)
    # A 15-minute boundary is appropriate for one-second books and one-minute
    # bars, but would classify every normal hourly funding update as a session
    # break.  Keep at least six expected intervals in the same active segment.
    session_break_seconds = max(float(SESSION_BREAK_SECONDS), expected_seconds * 6)
    active_deltas = deltas[deltas <= session_break_seconds]
    segment_breaks = int(np.sum(deltas > session_break_seconds))
    expected_active = int(segment_breaks + 1 + np.floor(active_deltas / expected_seconds).sum())
    missing_candidates = int(np.maximum(np.floor(active_deltas / expected_seconds) - 1, 0).sum())
    return {
        "start_utc": pd.to_datetime(int(valid[0]), unit="ns", utc=True).isoformat(),
        "end_utc": pd.to_datetime(int(valid[-1]), unit="ns", utc=True).isoformat(),
        "duplicate_timestamp_rows": int(valid.size - np.unique(valid).size),
        "duplicate_sample_seconds": int(np.maximum(counts - 1, 0).sum()),
        "interval_p50_seconds": percentile(active_deltas, 50),
        "interval_p95_seconds": percentile(active_deltas, 95),
        "interval_p99_seconds": percentile(active_deltas, 99),
        "interval_max_active_seconds": float(active_deltas.max()) if active_deltas.size else None,
        "candidate_gap_count_over_2x_expected": int(np.sum(active_deltas > expected_seconds * 2)),
        "candidate_gap_count_over_10x_expected": int(np.sum(active_deltas > expected_seconds * 10)),
        "candidate_missing_intervals_in_active_segments": missing_candidates,
        "active_segment_coverage_ratio": float(unique_seconds.size / expected_active) if expected_active else None,
        "segment_breaks": segment_breaks,
        "segment_break_threshold_seconds": session_break_seconds,
        "note": "Gaps are candidates, not proven loss: snapshots are emitted only after a provider update.",
    }


def analyse_asset_file(item: AssetFile) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    parquet = pq.ParquetFile(item.path)
    timestamps: list[np.ndarray] = []
    source_receive_latency: list[np.ndarray] = []
    event_ids: set[str] = set()
    duplicate_event_ids = 0
    counters: Counter[str] = Counter()
    series: dict[str, list[np.ndarray]] = defaultdict(list)

    for batch in parquet.iter_batches(batch_size=8192):
        table = pa.Table.from_batches([batch])
        event_timestamps = timestamp_column(table, item.event_type)
        timestamps.append(event_timestamps)
        if "event_id" in table.column_names:
            for event_id in table["event_id"].to_pylist():
                text = str(event_id or "")
                if text and text in event_ids:
                    duplicate_event_ids += 1
                elif text:
                    event_ids.add(text)
        if "source_ts_ns" in table.column_names and "receive_ts_ns" in table.column_names:
            source = pd.to_numeric(table["source_ts_ns"].to_pandas(), errors="coerce").fillna(0).to_numpy(dtype=np.int64)
            receive = pd.to_numeric(table["receive_ts_ns"].to_pandas(), errors="coerce").fillna(0).to_numpy(dtype=np.int64)
            latency = (receive - source) / NANOSECONDS
            source_receive_latency.append(latency)
            counters["negative_receive_latency"] += int(np.sum(latency < 0))
            counters["future_source_over_5m"] += int(np.sum(latency < -300))

        if item.event_type == "order_book":
            bids = book_levels(table, "bids")
            asks = book_levels(table, "asks")
            best_bid = np.full(table.num_rows, np.nan)
            best_ask = np.full(table.num_rows, np.nan)
            depths = table["depth"].to_pylist() if "depth" in table.column_names else [None] * table.num_rows
            for index, (bid_levels, ask_levels) in enumerate(zip(bids, asks)):
                bid_prices = [float(level["price"]) for level in bid_levels if level.get("price") is not None]
                ask_prices = [float(level["price"]) for level in ask_levels if level.get("price") is not None]
                quantities = [float(level.get("quantity") or 0) for level in bid_levels + ask_levels]
                if not bid_prices:
                    counters["empty_bid"] += 1
                else:
                    best_bid[index] = bid_prices[0]
                    counters["bid_order_invalid"] += int(any(left < right for left, right in zip(bid_prices, bid_prices[1:])))
                if not ask_prices:
                    counters["empty_ask"] += 1
                else:
                    best_ask[index] = ask_prices[0]
                    counters["ask_order_invalid"] += int(any(left > right for left, right in zip(ask_prices, ask_prices[1:])))
                counters["nonpositive_level_quantity"] += sum(quantity <= 0 for quantity in quantities)
                actual_depth = max(len(bid_levels), len(ask_levels))
                if depths[index] is not None and int(depths[index]) != actual_depth:
                    counters["depth_mismatch"] += 1
            valid = np.isfinite(best_bid) & np.isfinite(best_ask)
            counters["crossed_book"] += int(np.sum(valid & (best_bid > best_ask)))
            counters["locked_book"] += int(np.sum(valid & (best_bid == best_ask)))
            spread = np.where(valid, best_ask - best_bid, np.nan)
            midpoint = np.where(valid, (best_ask + best_bid) / 2, np.nan)
            spread_bps = np.where(valid & (midpoint != 0), spread / midpoint * 10_000, np.nan)
            series["timestamp_ns"].append(event_timestamps)
            series["best_bid"].append(best_bid)
            series["best_ask"].append(best_ask)
            series["spread_bps"].append(spread_bps)

        elif item.event_type == "trade":
            prices = numeric(table["price"].to_pylist()) if "price" in table.column_names else np.full(table.num_rows, np.nan)
            quantities = numeric(table["quantity"].to_pylist()) if "quantity" in table.column_names else np.full(table.num_rows, np.nan)
            counters["nonpositive_price"] += int(np.sum(np.isfinite(prices) & (prices <= 0)))
            counters["nonpositive_quantity"] += int(np.sum(np.isfinite(quantities) & (quantities <= 0)))
            series["timestamp_ns"].append(event_timestamps)
            series["price"].append(prices)

        elif item.event_type == "bar_1m":
            values = {
                name: numeric(table[name].to_pylist()) if name in table.column_names else np.full(table.num_rows, np.nan)
                for name in ("open", "high", "low", "close", "volume")
            }
            counters["invalid_ohlc"] += int(np.sum(
                (values["high"] < np.fmax(values["open"], values["close"]))
                | (values["low"] > np.fmin(values["open"], values["close"]))
                | (values["high"] < values["low"])
            ))
            counters["negative_volume"] += int(np.sum(values["volume"] < 0))
            if "closed" in table.column_names:
                counters["unclosed_bar"] += sum(value is not True for value in table["closed"].to_pylist())
            series["timestamp_ns"].append(event_timestamps)
            series["price"].append(values["close"])

        elif item.event_type == "funding_rate":
            rates = numeric(table["funding_rate"].to_pylist()) if "funding_rate" in table.column_names else np.full(table.num_rows, np.nan)
            counters["missing_funding_rate"] += int(np.sum(~np.isfinite(rates)))
            series["timestamp_ns"].append(event_timestamps)
            series["funding_rate"].append(rates)

    timestamps_array = np.concatenate(timestamps) if timestamps else np.array([], dtype=np.int64)
    latency_array = np.concatenate(source_receive_latency) if source_receive_latency else np.array([], dtype=float)
    expected_seconds = 60.0 if item.event_type == "bar_1m" else 3600.0 if item.event_type == "funding_rate" else 1.0
    report = {
        "asset_key": item.asset_key,
        "provider": item.provider,
        "venue": item.venue,
        "symbol": item.symbol,
        "event_type": item.event_type,
        "rows": int(parquet.metadata.num_rows),
        "duplicate_event_ids": duplicate_event_ids,
        "receive_latency_p50_seconds": percentile(latency_array, 50),
        "receive_latency_p95_seconds": percentile(latency_array, 95),
        "receive_latency_p99_seconds": percentile(latency_array, 99),
        **dict(counters),
        **gap_metrics(timestamps_array, expected_seconds),
    }
    compact_series = {name: np.concatenate(chunks) for name, chunks in series.items() if chunks}
    return report, compact_series


def sample_for_plot(x: np.ndarray, *values: np.ndarray, maximum: int = 100_000) -> tuple[np.ndarray, ...]:
    if x.size <= maximum:
        return (x, *values)
    indices = np.linspace(0, x.size - 1, maximum, dtype=np.int64)
    return (x[indices], *(value[indices] for value in values))


def create_plots(asset_key: str, event_series: dict[str, dict[str, np.ndarray]], output_directory: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("matplotlib is required for plots; install requirements-analysis.txt") from exc

    output_directory.mkdir(parents=True, exist_ok=True)
    for event_type in ("trade", "bar_1m"):
        series = event_series.get(event_type)
        if not series or "price" not in series:
            continue
        timestamps, prices = sample_for_plot(series["timestamp_ns"], series["price"])
        order = np.argsort(timestamps)
        fig, axis = plt.subplots(figsize=(14, 5))
        axis.plot(pd.to_datetime(timestamps[order], unit="ns", utc=True), prices[order], linewidth=0.7)
        axis.set(title=f"{asset_key} — {event_type} price", xlabel="UTC", ylabel="price")
        axis.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(output_directory / f"{event_type}_price.png", dpi=140)
        plt.close(fig)

    book = event_series.get("order_book")
    if book:
        timestamps, bid, ask, spread = sample_for_plot(
            book["timestamp_ns"], book["best_bid"], book["best_ask"], book["spread_bps"]
        )
        order = np.argsort(timestamps)
        dates = pd.to_datetime(timestamps[order], unit="ns", utc=True)
        fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
        axes[0].plot(dates, bid[order], label="best bid", linewidth=0.6)
        axes[0].plot(dates, ask[order], label="best ask", linewidth=0.6)
        axes[0].legend()
        axes[0].grid(alpha=0.25)
        axes[1].plot(dates, spread[order], linewidth=0.6)
        axes[1].set(ylabel="spread (bps)", xlabel="UTC")
        axes[1].grid(alpha=0.25)
        fig.suptitle(f"{asset_key} — order book")
        fig.tight_layout()
        fig.savefig(output_directory / "order_book.png", dpi=140)
        plt.close(fig)


def trade_quote_accuracy(event_series: dict[str, dict[str, np.ndarray]]) -> dict[str, Any]:
    trades = event_series.get("trade")
    books = event_series.get("order_book")
    if not trades or not books:
        return {}
    trade_frame = pd.DataFrame({"ts": trades["timestamp_ns"], "price": trades["price"]}).sort_values("ts")
    book_frame = pd.DataFrame({
        "ts": books["timestamp_ns"],
        "bid": books["best_bid"],
        "ask": books["best_ask"],
    }).dropna().sort_values("ts")
    if trade_frame.empty or book_frame.empty:
        return {}
    aligned = pd.merge_asof(trade_frame, book_frame, on="ts", direction="backward", tolerance=5 * NANOSECONDS)
    matched = aligned[aligned["bid"].notna() & aligned["ask"].notna()]
    outside = matched[(matched["price"] < matched["bid"]) | (matched["price"] > matched["ask"])]
    return {
        "trades_with_quote_within_5s": int(len(matched)),
        "trades_outside_latest_quote": int(len(outside)),
        "trades_outside_latest_quote_ratio": float(len(outside) / len(matched)) if len(matched) else None,
        "note": "Outside-quote checks are diagnostic; provider timestamp differences can create false positives.",
    }


def load_asset_dataframe(output_root: Path, asset_directory: str, event_type: str) -> pd.DataFrame:
    """Convenience function for notebooks and interactive inspection."""
    path = output_root / asset_directory / f"{safe_name(event_type)}.parquet"
    frame = pd.read_parquet(path)
    timestamp_name = "sampled_at_ns" if event_type == "order_book" and "sampled_at_ns" in frame else "source_ts_ns"
    if timestamp_name in frame:
        frame["timestamp_utc"] = pd.to_datetime(frame[timestamp_name], unit="ns", utc=True, errors="coerce")
        frame = frame.sort_values(timestamp_name, kind="stable")
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge an export by asset and run market-data QA")
    parser.add_argument("export_root", type=Path, help="Extracted vibetrading-market-* directory")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--asset", action="append", default=[], help="Optional symbol filter; repeatable")
    args = parser.parse_args()

    export_root = args.export_root.resolve()
    output_root = (args.output or export_root.with_name(export_root.name + "-analysis")).resolve()
    selected = {value.upper() for value in args.asset}
    assets = consolidate_by_asset(export_root, output_root / "by_asset", overwrite=args.overwrite)
    if selected:
        assets = [item for item in assets if item.symbol.upper() in selected]

    reports: list[dict[str, Any]] = []
    grouped_series: dict[str, dict[str, dict[str, np.ndarray]]] = defaultdict(dict)
    grouped_directories: dict[str, str] = {}
    for item in assets:
        report, series = analyse_asset_file(item)
        reports.append(report)
        grouped_series[item.asset_key][item.event_type] = series
        grouped_directories[item.asset_key] = item.path.parent.name
        first_batch = next(pq.ParquetFile(item.path).iter_batches(batch_size=100), None)
        head = pa.Table.from_batches([first_batch]).to_pandas() if first_batch is not None else pd.DataFrame()
        head.to_csv(item.path.with_suffix(".head.csv"), index=False, encoding="utf-8-sig")

    asset_reports = []
    for asset_key, event_series in grouped_series.items():
        combined = {"asset_key": asset_key, **trade_quote_accuracy(event_series)}
        asset_reports.append(combined)
        if not args.no_plots:
            create_plots(asset_key, event_series, output_root / "plots" / grouped_directories[asset_key])

    summary = pd.DataFrame(reports)
    summary.to_csv(output_root / "qa_summary.csv", index=False, encoding="utf-8-sig")
    payload = {
        "export_root": str(export_root),
        "output_root": str(output_root),
        "asset_event_reports": reports,
        "cross_event_checks": asset_reports,
        "interpretation": {
            "order_book_gaps": "Candidate gaps include quiet seconds because the collector does not synthesize unchanged books.",
            "price_accuracy": "Internal consistency is checked; absolute exchange-price accuracy requires an independent reference feed.",
            "timestamps": "Plots use UTC. trading_date partitions may use the venue/session policy.",
        },
    }
    (output_root / "qa_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"assets": len(grouped_series), "asset_event_files": len(assets), "output": str(output_root)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
