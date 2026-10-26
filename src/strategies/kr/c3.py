"""C3 ownership: pure open-regression guard and sizing decision.

The historical regression, calendar and readiness checks stay in
EtfHistory.c3; only its final guard/sizing/return calculation lives here.
No auto-discovery or registration side effects.
"""
from __future__ import annotations

import numpy as np


def open_decision(z, mis, sigma):
    if not np.isfinite([z, mis, sigma]).all() or sigma <= 0:
        raise RuntimeError('C3 regression/volatility incomplete')
    return (min(2., .30 / sigma) if z >= 1 and mis <= 0 else 0.), {'z':z, 'mis':mis, 'sigma':sigma}
