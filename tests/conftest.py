"""Test-only access to frozen CLN-02 fair-value historical oracles.

The retired S1-S4 modules live in the archive and are unavailable to ordinary
Python imports and built distributions. Tests may resolve the real archived
modules through this narrowly scoped package path extension.
"""
from pathlib import Path

import strategies.legacy.fair_value as fair_value

_ARCHIVED_FAIR_VALUE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "archive"
    / "CLN-02-legacy-oracles"
    / "fair_value"
)
if _ARCHIVED_FAIR_VALUE.is_dir():
    fair_value.__path__.append(str(_ARCHIVED_FAIR_VALUE))
