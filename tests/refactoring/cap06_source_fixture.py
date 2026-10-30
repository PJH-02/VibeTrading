"""Credential-free canonical loader fixture for selected native routing."""
from cap05_source_fixture import CONFIG_VERSION, STRATEGY_ID

ACTION_PROTOCOL_VERSION = 1
SUBSCRIPTIONS = [{"venue": "binance", "symbol": "BTCUSDT", "event_type": "trade"}]
WARMUP = {"bars": 0}
PLAN = None


class SelectedSource:
    STRATEGY_ID = STRATEGY_ID
    ACTION_PROTOCOL_VERSION = 1

    def __init__(self):
        self.config_version = CONFIG_VERSION
        self.calls = 0

    def configure(self, config):
        self.config_version = config["config_version"]

    def validate_registration(self, facts):
        if not facts["accounts"]:
            raise ValueError("account facts required")

    def on_event(self, event, data):
        self.calls += 1
        return PLAN

    def on_order_event(self, event, context):
        return []


def create_strategy():
    return SelectedSource()
