"""Compatibility adapter for the explicit legacy candle loader.

The canonical implementation and cache live in
``vibetrading.strategies.legacy.candle_loader``.
"""

import sys

from vibetrading.strategies.legacy import candle_loader as _canonical

sys.modules[__name__] = _canonical
