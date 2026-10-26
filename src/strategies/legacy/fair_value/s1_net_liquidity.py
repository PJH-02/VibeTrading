"""S1: price-aware net BBO flow. No trade, OBI or anchor approval filter."""
from decimal import Decimal as D
from strategies.legacy.fair_value.contracts import ActivityWindow, Signal, quote_intents

STRATEGY_ID = "S1"


class Strategy:
    def __init__(self, coefficient=None, nu=D(1), max_window_age=120, **kwargs):
        self.coefficient, self.nu = coefficient, nu
        self.window = ActivityWindow(max_window_age)
        self.previous = None

    def on_book(self, quote, reset=False):
        if reset:
            self.window.reset()
            self.previous = None
        old, self.previous = self.previous, quote
        if old is None:
            return
        u = (quote.bid_qty if quote.bid >= old.bid else D(0)) - (old.bid_qty if quote.bid <= old.bid else D(0))
        v = (quote.ask_qty if quote.ask <= old.ask else D(0)) - (old.ask_qty if quote.ask >= old.ask else D(0))
        self.window.add(quote.received, u - v, abs(u) + abs(v))

    def on_trade(self, side, quantity, received):
        pass  # Already present in book changes; never double-count.

    def signal(self, quote, now, reference=None):
        signed, age = self.window.value(now, self.nu * quote.depth)
        if signed is None:
            return Signal(STRATEGY_ID, D(0), None, self.coefficient, age, "insufficient_activity_history")
        x = quote.spread * signed / quote.depth
        fv = quote.mid + self.coefficient * x if self.coefficient is not None else None
        return Signal(STRATEGY_ID, x, fv, self.coefficient, age, "ok" if fv is not None else "unfitted_coefficient")

    def order_intents(self, signal, quote, **risk):
        return quote_intents(signal, quote, **risk)
