"""Compatibility adapter for the canonical modern example strategy."""

import sys

from vibetrading.strategies import example_signal as _canonical

sys.modules[__name__] = _canonical
