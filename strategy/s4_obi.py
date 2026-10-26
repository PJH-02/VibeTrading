"""Compatibility adapter for the canonical research state S4 strategy."""

import sys

from strategies.research.state import s4_obi as _canonical

sys.modules[__name__] = _canonical
