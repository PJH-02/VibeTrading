"""KD ownership: pure adapter plus real file-plugin contract."""
from __future__ import annotations

from . import _etf_plugin
from ._intraday import intraday_decision

STRATEGY_ID = "KD"
STRATEGY_VERSION = "str01-kd-1"
SUBSCRIPTIONS = [
    {"instrument_key": "kiwoom:etf:KIWOOM:KRX:229200", "event_type": "bar_1m"},
    {"instrument_key": "kiwoom:etf:KIWOOM:KRX:229200", "event_type": "order_book"},
]
WARMUP = {"bars": 0}


def decide(feature: dict, book: dict) -> tuple[int, float, str]:
    return intraday_decision('KD', feature, book)


class KDStrategy(_etf_plugin.FileEtfStrategy):
    STRATEGY_ID = STRATEGY_ID

    def on_event(self, event, data):
        return _etf_plugin.intraday_on_event(self, STRATEGY_ID, event, data, decide)


def create_strategy():
    return KDStrategy()


def action_builder(signal, view, config):
    return _etf_plugin.build_actions(STRATEGY_ID, signal, view, config)
