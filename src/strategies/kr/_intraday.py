"""Shared pure KP/KD intraday stop, sizing and one-entry decisions.

Owned by the KR strategy family. This module intentionally performs no
discovery or registration; callers import the helper explicitly.
"""
from __future__ import annotations


def intraday_decision(strategy: str, feature: dict, book: dict) -> tuple[int, float, str]:
    """Frozen M1-P stops/sizing and one-entry-per-day selections."""
    direction = int(book['direction']) if book['positions'] else 0
    if direction:
        stop = max(feature['upper_band'],feature['vwap_used']) if direction == 1 else min(feature['lower_band'],feature['vwap_used'])
        if feature['price'] <= stop if direction == 1 else feature['price'] >= stop:
            return 0, 0., 'paper_stop'
        return direction, 0., 'hold'
    if not feature.get('usable') or book['entries']:
        return 0, 0., 'no_entry'
    side = int(feature['entry_signal'])
    if side == 0 or (strategy == 'KP' and side != 1):
        return 0, 0., 'no_entry'
    leverage = min(2. if strategy == 'KP' else 1., .02 / feature['vol14'] * feature['signal_strength'])
    return side, leverage, 'entry'
