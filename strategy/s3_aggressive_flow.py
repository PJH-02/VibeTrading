"""Compatibility adapter for the canonical research state S3 strategy."""

import sys

from vibetrading.strategies.research.state import s3_aggressive_flow as _canonical

sys.modules[__name__] = _canonical
