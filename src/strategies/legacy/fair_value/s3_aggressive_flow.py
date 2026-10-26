"""S3: signed actual aggressor quantity in a depth-scaled volume window."""
from decimal import Decimal as D
from strategies.legacy.fair_value.contracts import ActivityWindow, Signal, quote_intents

STRATEGY_ID = "S3"


class Strategy:
    def __init__(self, coefficient=None, nu=D(1), max_window_age=120, **kwargs):
        self.coefficient, self.nu = coefficient, nu
        self.window = ActivityWindow(max_window_age)

    def on_book(self, quote, reset=False):
        if reset:
            self.window.reset()

    def on_trade(self, side, quantity, received):
        if side in ("Buy", "Sell"):
            self.window.add(received, quantity if side == "Buy" else -quantity, quantity)

    def signal(self, quote, now, reference=None):
        signed, age = self.window.value(now, self.nu * quote.depth)
        if signed is None:
            return Signal(STRATEGY_ID, D(0), None, self.coefficient, age, "insufficient_trade_volume_history")
        x = quote.spread * signed / quote.depth
        fv = quote.mid + self.coefficient * x if self.coefficient is not None else None
        return Signal(STRATEGY_ID, x, fv, self.coefficient, age, "ok" if fv is not None else "unfitted_coefficient")

    def order_intents(self, signal, quote, **risk):
        return quote_intents(signal, quote, **risk)
