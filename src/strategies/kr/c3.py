"""C3 ownership: pure open-regression guard and real file-plugin contract.

The historical regression, calendar and readiness checks stay in
EtfHistory.c3; only its final guard/sizing/return calculation lives here.
No auto-discovery, broker, or optional-sibling import side effects.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np

from . import _etf_plugin

STRATEGY_ID = "C3"
STRATEGY_VERSION = "str01-c3-1"
SUBSCRIPTIONS = [
    {"instrument_key": "kiwoom:etf:KIWOOM:KRX:069500", "event_type": "bar_1m"},
    {"instrument_key": "kiwoom:etf:KIWOOM:KRX:069500", "event_type": "order_book"},
    {"instrument_key": "kis:index:KOSPI200", "event_type": "index_quote"},
]
WARMUP = {"bars": 0}


def open_decision(z, mis, sigma):
    if not np.isfinite([z, mis, sigma]).all() or sigma <= 0:
        raise RuntimeError('C3 regression/volatility incomplete')
    return (min(2., .30 / sigma) if z >= 1 and mis <= 0 else 0.), {'z':z, 'mis':mis, 'sigma':sigma}


def native_open_decision(history, etf_open, index_open):
    """Shared C3 open decision: legacy evaluate_c3 and the plugin both use it."""
    leverage, detail = history.c3(float(etf_open), float(index_open))
    identity = hashlib.sha256(json.dumps(
        ["C3", float(etf_open), float(index_open), detail], sort_keys=True,
        default=str).encode("utf-8")).hexdigest()
    return {"action": "entry" if leverage > 0 else "no_entry",
            "direction": 1 if leverage > 0 else 0,
            "leverage": float(leverage), "detail": dict(detail),
            "identity": identity}


class C3Strategy(_etf_plugin.FileEtfStrategy):
    STRATEGY_ID = STRATEGY_ID

    def on_event(self, event, data):
        return _etf_plugin.c3_on_event(self, event, data)


def create_strategy():
    return C3Strategy()


def action_builder(signal, view, config):
    return _etf_plugin.build_actions(STRATEGY_ID, signal, view, config)
