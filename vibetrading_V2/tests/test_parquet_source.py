from __future__ import annotations

from datetime import datetime, timezone

import pytest

from vibetrading_V2.data.parquet_source import ParquetDataSource


class _Frame:
    def __init__(self, rows):
        self._rows = list(rows)
        self.columns = tuple(self._rows[0].keys()) if self._rows else tuple()

    def sort_values(self, key):
        return _Frame(sorted(self._rows, key=lambda row: row[key]))

    def to_dict(self, orient="records"):
        assert orient == "records"
        return list(self._rows)

    def reset_index(self):
        return self


def test_parquet_source_reads_and_sorts_rows() -> None:
    rows = [
        {
            "timestamp": datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
            "open": 102,
            "high": 103,
            "low": 100,
            "close": 101,
            "volume": 10,
        },
        {
            "timestamp": datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 5,
        },
    ]

    source = ParquetDataSource(
        root="data/curated",
        read_parquet=lambda _path: _Frame(rows),
    )
    bars = source.get_bars("BTC-USD")

    assert len(bars) == 2
    assert bars[0].timestamp < bars[1].timestamp
    assert bars[0].symbol == "BTC-USD"


def test_parquet_source_requires_ohlcv_columns() -> None:
    rows = [
        {
            "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
        }
    ]
    source = ParquetDataSource(read_parquet=lambda _path: _Frame(rows))

    with pytest.raises(ValueError, match="Missing required columns"):
        source.get_bars("BTC-USD")
