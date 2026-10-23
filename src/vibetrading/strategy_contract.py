"""Compatibility adapter for the canonical modern strategy contract.

Importing this historical path aliases ``vibetrading.strategies.contract`` so
classes, functions, caches, and monkeypatched globals keep their identity.
"""

import sys

from vibetrading.strategies import contract as _canonical

sys.modules[__name__] = _canonical
