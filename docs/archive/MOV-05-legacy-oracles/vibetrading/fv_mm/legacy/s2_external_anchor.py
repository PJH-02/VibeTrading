"""Compatibility adapter for the canonical frozen fair-value S2 strategy."""

import sys

from strategies.legacy.fair_value import s2_external_anchor as _canonical

sys.modules[__name__] = _canonical
