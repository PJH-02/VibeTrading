"""Compatibility adapter for the canonical legacy candle strategy."""

import sys

from vibetrading.strategies.legacy.candle import turtle_breakout as _canonical

sys.modules[__name__] = _canonical
