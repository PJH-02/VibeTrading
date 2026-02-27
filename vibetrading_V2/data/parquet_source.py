"""Parquet-backed read-only DataSource adapter for curated runtime data."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from vibetrading_V2.core.ports import DataSource
from vibetrading_V2.core.types import Bar
from vibetrading_V2.data.catalog import DataCatalog


class ParquetDataSource(DataSource):
    """Load symbol bars from <root>/<symbol>.parquet in read-only mode."""

    REQUIRED_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

    def __init__(
        self,
        root: str | Path = "data/curated",
        *,
        read_parquet: Callable[[Path], object] | None = None,
    ) -> None:
        self.catalog = DataCatalog(root)
        if read_parquet is not None:
            self._read_parquet = read_parquet
        else:
            # Lazily import pandas to keep core/runtime contracts independent.
            import pandas as pd

            self._read_parquet = lambda path: pd.read_parquet(path)

    def get_bars(self, symbol: str) -> Sequence[Bar]:
        path = self.catalog.path_for_symbol(symbol)
        frame = self._read_parquet(path)
        return self._frame_to_bars(symbol, frame)

    def _frame_to_bars(self, symbol: str, frame: object) -> list[Bar]:
        if not hasattr(frame, "columns"):
            raise TypeError("read_parquet must return a dataframe-like object with columns")

        columns = set(getattr(frame, "columns"))
        missing = [column for column in self.REQUIRED_COLUMNS if column not in columns]
        if missing:
            raise ValueError(f"Missing required columns for {symbol}: {missing}")

        if "timestamp" not in columns:
            if not hasattr(frame, "reset_index"):
                raise ValueError("Dataframe must provide a timestamp column or index")
            frame = frame.reset_index()
            columns = set(getattr(frame, "columns"))
        if "timestamp" not in columns:
            raise ValueError("Dataframe must contain timestamp values")

        if hasattr(frame, "sort_values"):
            frame = frame.sort_values("timestamp")

        bars: list[Bar] = []
        records = frame.to_dict(orient="records")
        for row in records:
            ts = row["timestamp"]
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            if not isinstance(ts, datetime):
                raise TypeError(f"timestamp must be datetime-like for {symbol}")
            bars.append(
                Bar(
                    symbol=symbol,
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
        return bars
