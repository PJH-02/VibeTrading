"""S4: current BBO imbalance only, with its own future-move coefficient."""
from decimal import Decimal as D
from strategies.legacy.fair_value.contracts import Signal, quote_intents

STRATEGY_ID = "S4"


class Strategy:
    def __init__(self, coefficient=None, **kwargs):
        self.coefficient = coefficient

    def on_book(self, quote, reset=False):
        pass

    def on_trade(self, side, quantity, received):
        pass

    def signal(self, quote, now, reference=None):
        total = quote.bid_qty + quote.ask_qty
        if total <= 0:
            return Signal(STRATEGY_ID, D(0), None, self.coefficient, 0, "invalid_depth")
        x = quote.spread * (quote.bid_qty - quote.ask_qty) / total
        fv = quote.mid + self.coefficient * x if self.coefficient is not None else None
        return Signal(STRATEGY_ID, x, fv, self.coefficient, 0, "ok" if fv is not None else "unfitted_coefficient")

    def order_intents(self, signal, quote, **risk):
        return quote_intents(signal, quote, **risk)
