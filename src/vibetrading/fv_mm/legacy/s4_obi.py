"""Compatibility adapter for the canonical frozen fair-value S4 strategy."""

import sys

from vibetrading.strategies.legacy.fair_value import s4_obi as _canonical

sys.modules[__name__] = _canonical
