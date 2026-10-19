"""S2: same-asset Binance USDT perpetual anchor with frozen log basis."""
from decimal import Decimal as D
from vibetrading.fv_mm.types import Signal, quote_intents

STRATEGY_ID = "S2"


class Strategy:
    def __init__(self, coefficient=None, basis=None, max_reference_age=3, **kwargs):
        self.coefficient, self.basis = coefficient, basis
        self.max_reference_age = max_reference_age

    def on_book(self, quote, reset=False):
        pass

    def on_trade(self, side, quantity, received):
        pass

    def signal(self, quote, now, reference=None):
        if reference is None or now - reference.received > self.max_reference_age:
            return Signal(STRATEGY_ID, D(0), None, self.coefficient, 0, "stale_or_missing_binance_reference")
        if self.basis is None:
            return Signal(STRATEGY_ID, D(0), None, self.coefficient, 0, "unfitted_basis")
        anchor = reference.mid * self.basis.exp()
        x = anchor - quote.mid
        valid = self.coefficient is not None and D(0) < self.coefficient <= D(1)
        return Signal(STRATEGY_ID, x, quote.mid + self.coefficient * x if valid else None,
                      self.coefficient, 0, "ok" if valid else "convergence_coefficient_out_of_range_or_unfitted")

    def order_intents(self, signal, quote, **risk):
        return quote_intents(signal, quote, **risk)
