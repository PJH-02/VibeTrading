"""Audit per-instrument collection gaps from local and verified remote Parquet.

The script reads only active catalogue entries.  It opens local files when
available and downloads a verified remote object to a temporary file otherwise;
remote objects are never modified.  A five-minute bin is considered covered
when a continuous event type (order book, one-minute bar, or open interest)
exists in that bin.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import sqlite3
import tempfile
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pyarrow.parquet as pq

from vibetrading.settings import get_settings
from storage.storage_backend import archive_backend


UTC = timezone.utc
KST = timezone(timedelta(hours=9))
BIN_SECONDS = 5 * 60
NS_PER_SECOND = 1_000_000_000
NS_PER_BIN = BIN_SECONDS * NS_PER_SECOND
VALID_TIMESTAMP_START = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * NS_PER_SECOND)
VALID_TIMESTAMP_END = int(datetime(2028, 1, 1, tzinfo=UTC).timestamp() * NS_PER_SECOND)
CONTINUOUS_EVENT_PRIORITY = ("order_book", "bar_1m", "open_interest")

# The audited interval is September 2026.  KRX is closed for Chuseok on the
# 24th and 25th; Saturday the 26th is already excluded by the weekday rule.
KRX_HOLIDAYS = {date(2026, 9, 24), date(2026, 9, 25)}
US_EQUITY_HOLIDAYS: set[date] = set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("/data/state/vibetrading.sqlite3"))
    parser.add_argument("--output-dir", type=Path, default=Path("/data/reports/market-coverage"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--progress-every", type=int, default=100)
    return parser.parse_args()


def read_manifest(database: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT file_id, event_type, provider, venue, path, remote_key,
                       upload_status, local_present, rows, min_ts_ns, max_ts_ns
                FROM parquet_manifest
                WHERE lifecycle_state='active'
                ORDER BY created_ts_ns, file_id
                """
            )
        ]
    finally:
        connection.close()


def valid_timestamp(value: Any) -> int | None:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return None
    return timestamp if VALID_TIMESTAMP_START <= timestamp < VALID_TIMESTAMP_END else None


def event_timestamp(values: dict[str, list[Any]], index: int) -> int | None:
    for name in ("source_ts_ns", "sampled_at_ns", "wallclock_ts_ns", "receive_ts_ns"):
        if name in values:
            timestamp = valid_timestamp(values[name][index])
            if timestamp is not None:
                return timestamp
    return None


def text(values: dict[str, list[Any]], name: str, index: int, fallback: str = "") -> str:
    value = values.get(name, [fallback])[index] if name in values else fallback
    return str(value or fallback).strip()


def instrument_key(meta: dict[str, Any], values: dict[str, list[Any]], index: int) -> tuple[str, ...] | None:
    standard = text(values, "instrument_std_symbol", index)
    raw = text(values, "instrument_raw_symbol", index)
    symbol = standard or raw
    if not symbol:
        return None
    return (
        str(meta["provider"]),
        str(meta["venue"]),
        symbol,
        text(values, "instrument_exchange", index, "unknown"),
        text(values, "instrument_asset_class", index, "unknown"),
        text(values, "instrument_contract_month", index),
        text(values, "instrument_expiry", index),
    )


_thread_state = threading.local()


def remote_backend() -> Any:
    backend = getattr(_thread_state, "backend", None)
    if backend is None:
        backend = archive_backend(get_settings())
        _thread_state.backend = backend
    return backend


def source_path(meta: dict[str, Any], temporary_dir: Path) -> tuple[Path, bool]:
    local = Path(str(meta["path"]))
    if bool(meta["local_present"]) and local.is_file():
        return local, False
    if meta.get("upload_status") != "remote_verified" or not meta.get("remote_key"):
        raise FileNotFoundError(f"active file unavailable locally and remotely: {meta['file_id']}")
    descriptor, name = tempfile.mkstemp(prefix=f"coverage-{meta['file_id']}-", suffix=".parquet", dir=temporary_dir)
    os.close(descriptor)
    destination = Path(name)
    destination.unlink(missing_ok=True)
    remote_backend().download_file(str(meta["remote_key"]), destination)
    return destination, True


def scan_file(meta: dict[str, Any], temporary_dir: Path) -> dict[str, Any]:
    path, downloaded = source_path(meta, temporary_dir)
    assets: dict[tuple[str, ...], dict[str, Any]] = {}
    try:
        parquet = pq.ParquetFile(path)
        wanted = [
            "instrument_std_symbol", "instrument_raw_symbol", "instrument_exchange",
            "instrument_asset_class", "instrument_contract_month", "instrument_expiry",
            "source_ts_ns", "sampled_at_ns", "wallclock_ts_ns", "receive_ts_ns",
        ]
        names = set(parquet.schema_arrow.names)
        columns = [name for name in wanted if name in names]
        for batch in parquet.iter_batches(batch_size=65_536, columns=columns):
            values = batch.to_pydict()
            for index in range(batch.num_rows):
                key = instrument_key(meta, values, index)
                timestamp = event_timestamp(values, index)
                if key is None or timestamp is None:
                    continue
                bucket = timestamp // NS_PER_BIN
                item = assets.setdefault(
                    key,
                    {"event_minutes": defaultdict(set), "first_ts_ns": timestamp, "last_ts_ns": timestamp, "rows": 0},
                )
                item["event_minutes"][str(meta["event_type"])].add(bucket)
                item["first_ts_ns"] = min(item["first_ts_ns"], timestamp)
                item["last_ts_ns"] = max(item["last_ts_ns"], timestamp)
                item["rows"] += 1
    finally:
        if downloaded:
            path.unlink(missing_ok=True)
    return {"file_id": str(meta["file_id"]), "downloaded": downloaded, "assets": assets}


def merge_assets(target: dict[tuple[str, ...], dict[str, Any]], scanned: dict[str, Any]) -> None:
    for key, source in scanned["assets"].items():
        item = target.setdefault(
            key,
            {"event_minutes": defaultdict(set), "first_ts_ns": source["first_ts_ns"], "last_ts_ns": source["last_ts_ns"], "rows": 0},
        )
        item["first_ts_ns"] = min(item["first_ts_ns"], source["first_ts_ns"])
        item["last_ts_ns"] = max(item["last_ts_ns"], source["last_ts_ns"])
        item["rows"] += source["rows"]
        for event_type, minutes in source["event_minutes"].items():
            item["event_minutes"][event_type].update(minutes)


def time_label(bucket: int) -> str:
    return datetime.fromtimestamp(bucket * BIN_SECONDS, tz=UTC).astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


def market_kind(key: tuple[str, ...]) -> str:
    provider, _venue, _symbol, exchange, asset_class, _month, _expiry = key
    exchange = exchange.upper()
    asset_class = asset_class.upper()
    if provider in {"binance", "hyperliquid"}:
        return "24x7"
    if exchange == "US" or exchange.startswith(("NYSE", "NASDAQ", "AMEX", "ARCA")):
        return "US_EQUITY"
    if asset_class in {"FUTURE", "OPTION"}:
        return "KR_DERIVATIVES"
    return "KR_EQUITY"


def session_ranges(kind: str, start_bucket: int, end_bucket: int) -> Iterable[range]:
    start_utc = datetime.fromtimestamp(start_bucket * BIN_SECONDS, tz=UTC)
    end_utc = datetime.fromtimestamp((end_bucket + 1) * BIN_SECONDS, tz=UTC)
    if kind == "24x7":
        yield range(start_bucket, end_bucket + 1)
        return
    first_day = (start_utc.astimezone(KST) - timedelta(days=1)).date()
    last_day = (end_utc.astimezone(KST) + timedelta(days=1)).date()
    day = first_day
    while day <= last_day:
        if kind == "KR_EQUITY":
            if day.weekday() < 5 and day not in KRX_HOLIDAYS:
                begin = datetime.combine(day, clock_time(9, 0), tzinfo=KST)
                finish = datetime.combine(day, clock_time(15, 30), tzinfo=KST)
            else:
                day += timedelta(days=1)
                continue
        elif kind == "KR_DERIVATIVES":
            if day.weekday() < 5 and day not in KRX_HOLIDAYS:
                begin = datetime.combine(day, clock_time(8, 45), tzinfo=KST)
                finish = datetime.combine(day, clock_time(15, 45), tzinfo=KST)
            else:
                day += timedelta(days=1)
                continue
        else:  # US_EQUITY, expressed in KST while US daylight saving is active.
            if day.weekday() < 5 and day not in US_EQUITY_HOLIDAYS:
                begin = datetime.combine(day, clock_time(22, 30), tzinfo=KST)
                finish = datetime.combine(day + timedelta(days=1), clock_time(5, 0), tzinfo=KST)
            else:
                day += timedelta(days=1)
                continue
        lower = max(start_bucket, int(begin.astimezone(UTC).timestamp()) // BIN_SECONDS)
        upper = min(end_bucket, (int(finish.astimezone(UTC).timestamp()) - 1) // BIN_SECONDS)
        if lower <= upper:
            yield range(lower, upper + 1)
        day += timedelta(days=1)


def gaps(expected: list[int], covered: set[int]) -> list[list[int]]:
    result: list[list[int]] = []
    current: list[int] = []
    for bucket in expected:
        if bucket not in covered:
            current.append(bucket)
        elif current:
            result.append(current)
            current = []
    if current:
        result.append(current)
    return result


def write_reports(assets: dict[tuple[str, ...], dict[str, Any]], output_dir: Path, now_ns: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    end_bucket = now_ns // NS_PER_BIN - 1  # Do not judge the in-progress bin.
    prepared: list[dict[str, Any]] = []
    provider_coverage: dict[tuple[str, str], dict[str, Any]] = {}
    for key, item in sorted(assets.items()):
        minutes_by_event: dict[str, set[int]] = item["event_minutes"]
        selected = next((event for event in CONTINUOUS_EVENT_PRIORITY if minutes_by_event.get(event)), None)
        covered = minutes_by_event.get(selected, set()) if selected else set()
        kind = market_kind(key)
        first_bucket = min(covered) if covered else int(item["first_ts_ns"]) // NS_PER_BIN
        last_bucket = max(covered) if covered else first_bucket
        expected = [bucket for segment in session_ranges(kind, first_bucket, last_bucket) for bucket in segment]
        trailing_expected = [bucket for segment in session_ranges(kind, last_bucket + 1, end_bucket) for bucket in segment]
        group_key = (key[0], kind)
        group = provider_coverage.setdefault(group_key, {"covered": set(), "first": first_bucket, "last": last_bucket})
        group["covered"].update(covered)
        group["first"] = min(group["first"], first_bucket)
        group["last"] = max(group["last"], last_bucket)
        prepared.append({"key": key, "item": item, "selected": selected, "covered": covered, "kind": kind, "first": first_bucket, "last": last_bucket, "expected": expected, "trailing": trailing_expected, "group_key": group_key})

    provider_missing: dict[tuple[str, str], set[int]] = {}
    provider_outages: list[dict[str, Any]] = []
    for (provider, kind), group in provider_coverage.items():
        expected = [bucket for segment in session_ranges(kind, group["first"], group["last"]) for bucket in segment]
        outage_groups = [gap for gap in gaps(expected, group["covered"]) if len(gap) >= 2]
        provider_missing[(provider, kind)] = {bucket for gap in outage_groups for bucket in gap}
        for gap in outage_groups:
            provider_outages.append({"provider": provider, "market_schedule": kind, "gap_start_kst": time_label(gap[0]), "gap_end_kst": time_label(gap[-1] + 1), "lost_active_minutes": len(gap) * BIN_SECONDS // 60, "lost_active_hours": round(len(gap) * BIN_SECONDS / 3600, 2)})

    summaries: list[dict[str, Any]] = []
    gap_rows: list[dict[str, Any]] = []
    for entry in prepared:
        key, item = entry["key"], entry["item"]
        provider, venue, symbol, exchange, asset_class, contract_month, expiry = key
        selected, covered, kind = entry["selected"], entry["covered"], entry["kind"]
        first_bucket, last_bucket = entry["first"], entry["last"]
        expected, trailing_expected = entry["expected"], entry["trailing"]
        symbol_groups = [group for group in gaps(expected, covered) if len(group) >= 2] if selected else []
        confirmed_missing = set(expected).intersection(provider_missing[entry["group_key"]]) if selected else set()
        error_groups = gaps(expected, set(expected).difference(confirmed_missing)) if confirmed_missing else []
        missing_bins = sum(len(group) for group in error_groups)
        status = "assessed" if selected and len(expected) >= 24 else "not_assessed_sparse_event"
        summaries.append(
            {
                "provider": provider,
                "venue": venue,
                "symbol": symbol,
                "exchange": exchange,
                "asset_class": asset_class,
                "contract_month": contract_month,
                "expiry": expiry,
                "market_schedule": kind,
                "coverage_event_type": selected or "",
                "status": status,
                "first_observed_kst": time_label(first_bucket),
                "last_observed_kst": time_label(last_bucket),
                "source_rows": item["rows"],
                "expected_5m_bins": len(expected),
                "covered_5m_bins": len(covered.intersection(expected)),
                "symbol_gap_5m_bins": sum(len(group) for group in symbol_groups),
                "error_gap_5m_bins": missing_bins,
                "error_loss_hours": round(missing_bins * BIN_SECONDS / 3600, 2),
                "gap_count_10m_or_more": len(error_groups),
                "unassessed_trailing_active_hours": round(len(trailing_expected) * BIN_SECONDS / 3600, 2),
            }
        )
        for group in error_groups:
            gap_rows.append(
                {
                    "provider": provider,
                    "venue": venue,
                    "symbol": symbol,
                    "exchange": exchange,
                    "asset_class": asset_class,
                    "coverage_event_type": selected,
                    "gap_start_kst": time_label(group[0]),
                    "gap_end_kst": time_label(group[-1] + 1),
                    "lost_active_minutes": len(group) * BIN_SECONDS // 60,
                    "lost_active_hours": round(len(group) * BIN_SECONDS / 3600, 2),
                }
            )
    fields = list(summaries[0]) if summaries else []
    with (output_dir / "coverage_by_symbol.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)
    gap_fields = list(gap_rows[0]) if gap_rows else ["provider", "venue", "symbol", "gap_start_kst", "gap_end_kst", "lost_active_hours"]
    with (output_dir / "error_gaps_10m_or_more.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=gap_fields)
        writer.writeheader()
        writer.writerows(gap_rows)
    with (output_dir / "provider_outages_10m_or_more.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fields = list(provider_outages[0]) if provider_outages else ["provider", "market_schedule", "gap_start_kst", "gap_end_kst", "lost_active_hours"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(provider_outages)
    assessed = [row for row in summaries if row["status"] == "assessed"]
    largest = sorted(assessed, key=lambda row: (-float(row["error_loss_hours"]), row["provider"], row["symbol"]))
    report = {
        "generated_at_kst": datetime.now(KST).isoformat(),
        "bin_minutes": 5,
        "loss_definition": "two or more consecutive five-minute bins with no continuous event from any instrument in the provider/session group; allocated only within each symbol's observed coverage span",
        "excluded": {"krx_holidays": sorted(str(day) for day in KRX_HOLIDAYS), "weekends": True, "in_progress_bin": True},
        "assets_total": len(summaries),
        "assets_assessed": len(assessed),
        "assets_not_assessed_sparse_event": len(summaries) - len(assessed),
        "largest_losses": largest[:30],
    }
    (output_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    settings = get_settings()
    if not settings.archive.enabled:
        raise RuntimeError("run inside the maintenance container with archive credentials")
    manifests = read_manifest(args.database)
    temporary_dir = args.output_dir / ".download-tmp"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    assets: dict[tuple[str, ...], dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    downloaded = 0
    local = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(scan_file, row, temporary_dir): row for row in manifests}
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                row = futures[future]
                try:
                    scanned = future.result()
                    merge_assets(assets, scanned)
                    downloaded += int(scanned["downloaded"])
                    local += int(not scanned["downloaded"])
                except Exception as exc:  # retain every unavailable object in the report
                    failures.append({"file_id": str(row["file_id"]), "error": f"{type(exc).__name__}: {exc}"})
                if index % max(1, args.progress_every) == 0 or index == len(manifests):
                    print(f"coverage audit files={index}/{len(manifests)} local={local} remote={downloaded} failures={len(failures)}", flush=True)
    finally:
        for path in temporary_dir.glob("*"):
            path.unlink(missing_ok=True)
        temporary_dir.rmdir()
    if failures:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "unavailable_files.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    write_reports(assets, args.output_dir, time.time_ns())
    print(json.dumps({"assets": len(assets), "files": len(manifests), "local": local, "remote": downloaded, "failures": len(failures), "output": str(args.output_dir)}, ensure_ascii=False))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
