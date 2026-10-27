"""KP ownership: pure adapter plus real file-plugin contract."""
from __future__ import annotations

from . import _etf_plugin
from ._intraday import intraday_decision

STRATEGY_ID = "KP"
STRATEGY_VERSION = "str01-kp-1"
SUBSCRIPTIONS = [
    {"instrument_key": "kiwoom:etf:KIWOOM:KRX:069500", "event_type": "bar_1m"},
    {"instrument_key": "kiwoom:etf:KIWOOM:KRX:069500", "event_type": "order_book"},
]
WARMUP = {"bars": 0}


def decide(feature: dict, book: dict) -> tuple[int, float, str]:
    return intraday_decision('KP', feature, book)


class KPStrategy(_etf_plugin.FileEtfStrategy):
    STRATEGY_ID = STRATEGY_ID

    def on_event(self, event, data):
        return _etf_plugin.intraday_on_event(self, STRATEGY_ID, event, data, decide)


def create_strategy():
    return KPStrategy()


def action_builder(signal, view, config):
    return _etf_plugin.build_actions(STRATEGY_ID, signal, view, config)
