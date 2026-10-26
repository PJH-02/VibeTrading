"""Compatibility adapter for the canonical frozen fair-value S3 strategy."""

import sys

from strategies.legacy.fair_value import s3_aggressive_flow as _canonical

sys.modules[__name__] = _canonical
