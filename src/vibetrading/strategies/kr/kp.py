"""KP ownership: pure adapter over the shared frozen intraday decision.

No auto-discovery or registration side effects.
"""
from __future__ import annotations

from ._intraday import intraday_decision


def decide(feature: dict, book: dict) -> tuple[int, float, str]:
    return intraday_decision('KP', feature, book)
