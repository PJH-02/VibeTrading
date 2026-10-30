"""Compatibility adapter for the canonical legacy candle strategy."""

import sys

from strategies.legacy.candle import turtle_breakout as _canonical

sys.modules[__name__] = _canonical
