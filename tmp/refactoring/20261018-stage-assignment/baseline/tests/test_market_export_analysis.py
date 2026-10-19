from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from scripts.analyze_market_export import (
    analyse_asset_file,
    consolidate_by_asset,
    create_plots,
    load_asset_dataframe,
)


BASE_NS = 1_800_000_000_000_000_000


def _write_export(export_root: Path) -> None:
    trade_directory = export_root / "market_data" / "event_type=trade" / "trading_date=2027-01-15"
    book_directory = export_root / "market_data" / "event_type=order_book" / "trading_date=2027-01-15"
    trade_directory.mkdir(parents=True)
    book_directory.mkdir(parents=True)

    # Dictionary encoding exercises the predicate path used by production files.
    symbols = pa.array(["AAA", "BBB", "AAA"], type=pa.dictionary(pa.int8(), pa.string()))
    trade = pa.table(
        {
            "provider": ["test"] * 3,
            "venue": ["X"] * 3,
            "instrument_std_symbol": symbols,
            "event_type": ["trade"] * 3,
            "event_id": ["a", "b", "c"],
            "source_ts_ns": [BASE_NS, BASE_NS, BASE_NS + 2_000_000_000],
            "receive_ts_ns": [BASE_NS + 1_000_000, BASE_NS + 1_000_000, BASE_NS + 2_001_000_000],
            "price": [100.0, 200.0, 101.0],
            "quantity": [2.0, 3.0, 1.0],
        }
    )
    pq.write_table(trade.slice(0, 2), trade_directory / "part-1.parquet", use_dictionary=True)
    pq.write_table(trade.slice(2), trade_directory / "part-2.parquet", use_dictionary=True)

    level_type = pa.list_(pa.struct([pa.field("price", pa.float64()), pa.field("quantity", pa.float64())]))
    book = pa.table(
        {
            "provider": ["test"] * 3,
            "venue": ["X"] * 3,
            "instrument_std_symbol": ["AAA"] * 3,
            "event_type": ["order_book"] * 3,
            "event_id": ["q1", "q2", "q3"],
            "source_ts_ns": [BASE_NS, BASE_NS + 1_000_000_000, BASE_NS + 3_000_000_000],
            "sampled_at_ns": [BASE_NS, BASE_NS + 1_000_000_000, BASE_NS + 3_000_000_000],
            "receive_ts_ns": [BASE_NS + 1_000_000, BASE_NS + 1_001_000_000, BASE_NS + 3_001_000_000],
            "depth": [1, 1, 1],
            "bids": pa.array(
                [[{"price": 99.0, "quantity": 1.0}], [{"price": 100.0, "quantity": 1.0}], [{"price": 100.0, "quantity": 1.0}]],
                type=level_type,
            ),
            "asks": pa.array(
                [[{"price": 101.0, "quantity": 1.0}], [{"price": 102.0, "quantity": 1.0}], [{"price": 102.0, "quantity": 1.0}]],
                type=level_type,
            ),
        }
    )
    pq.write_table(book, book_directory / "part-1.parquet", use_dictionary=True)


def test_consolidate_analyse_load_and_plot(tmp_path: Path) -> None:
    export_root = tmp_path / "snapshot"
    _write_export(export_root)
    by_asset = tmp_path / "analysis" / "by_asset"

    assets = consolidate_by_asset(export_root, by_asset)
    assert {(item.symbol, item.event_type) for item in assets} == {
        ("AAA", "trade"),
        ("AAA", "order_book"),
        ("BBB", "trade"),
    }
    aaa_book = next(item for item in assets if item.symbol == "AAA" and item.event_type == "order_book")
    report, series = analyse_asset_file(aaa_book)
    assert report["rows"] == 3
    assert report["candidate_missing_intervals_in_active_segments"] == 1
    assert report["active_segment_coverage_ratio"] == 0.75
    assert report.get("crossed_book", 0) == 0

    frame = load_asset_dataframe(by_asset, aaa_book.path.parent.name, "order_book")
    assert isinstance(frame, pd.DataFrame)
    assert frame["timestamp_utc"].is_monotonic_increasing

    create_plots(aaa_book.asset_key, {"order_book": series}, tmp_path / "plots")
    assert (tmp_path / "plots" / "order_book.png").is_file()
