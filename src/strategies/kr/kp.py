"""KP ownership: pure adapter plus real file-plugin contract."""
from __future__ import annotations

from . import _etf_plugin
from ._intraday import intraday_decision

STRATEGY_ID = "KP"
STRATEGY_VERSION = "str02-kp-1"
ACTION_PROTOCOL_VERSION = 1
SUBSCRIPTIONS = [
    {"instrument_key": "kiwoom:etf:KIWOOM:KRX:069500", "event_type": "bar_1m"},
    {"instrument_key": "kiwoom:etf:KIWOOM:KRX:069500", "event_type": "order_book"},
]
WARMUP = {"bars": 0}


def decide(feature: dict, book: dict) -> tuple[int, float, str]:
    return intraday_decision('KP', feature, book)


def on_order_event(event, context, instance=None):
    if context.data is not None:
        if instance is None:
            return []
        return _etf_plugin.file_strategy_order_event(instance, event, context)
    if event.account_scope.environment != 'mock' and event.account_scope.environment != 'mock2':
        return []
    return _etf_plugin.kis_order_event_plan(STRATEGY_ID, event, context)


class KPStrategy(_etf_plugin.FileEtfStrategy):
    STRATEGY_ID = STRATEGY_ID

    def on_event(self, event, data):
        return _etf_plugin.intraday_on_event(self, STRATEGY_ID, event, data, decide)


def create_strategy():
    return KPStrategy()


def action_builder(signal, view, config):
    return _etf_plugin.build_actions(STRATEGY_ID, signal, view, config)
