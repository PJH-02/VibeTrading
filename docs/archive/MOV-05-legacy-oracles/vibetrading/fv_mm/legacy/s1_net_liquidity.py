"""Compatibility adapter for the canonical frozen fair-value S1 strategy."""

import sys

from strategies.legacy.fair_value import s1_net_liquidity as _canonical

sys.modules[__name__] = _canonical
