"""Compatibility adapter for the canonical research state S2 strategy."""

import sys

from vibetrading.strategies.research.state import s2_external_anchor as _canonical

sys.modules[__name__] = _canonical
