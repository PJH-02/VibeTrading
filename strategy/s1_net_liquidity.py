"""Compatibility adapter for the canonical research state S1 strategy."""

import sys

from strategies.research.state import s1_net_liquidity as _canonical

sys.modules[__name__] = _canonical
