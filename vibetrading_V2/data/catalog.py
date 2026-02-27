"""Read-only data catalog utilities for curated parquet datasets."""

from __future__ import annotations

from pathlib import Path


class DataCatalog:
    """Resolve symbol -> parquet file paths under a curated root directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for_symbol(self, symbol: str) -> Path:
        return self.root / f"{symbol}.parquet"
