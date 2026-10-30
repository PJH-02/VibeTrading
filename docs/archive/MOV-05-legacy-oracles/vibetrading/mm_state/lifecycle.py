"""Shared paper/live quote retention rules, including pre-ACK reservations."""
from dataclasses import dataclass
from decimal import Decimal as D


@dataclass(frozen=True)
class LifecyclePolicy:
    minimum_lifetime: float = 1.5
    maximum_ttl: float = 15.
    reprice_ticks: int = 2
    resize_fraction: D = D("0.20")

    def reason(self, working, target, now, tick, *, hard_risk=False, state_flip=False):
        if working.done:
            return None
        if hard_risk:
            return "RISK_CANCEL"
        if state_flip or (target is not None and target.side != working.intent.side):
            return "DIRECTION_FLIP"
        if target is not None and target.reduce_only != working.intent.reduce_only:
            return "EXPOSURE_ROLE_CHANGED"
        # A pending intent remains reserved through latency. Small updates neither
        # undo an in-flight create nor send a replacement before terminal cancel.
        active_ts = getattr(working, "active_ts", None)
        if active_ts is None:
            active_ts = getattr(working, "arrival", working.born) if getattr(working, "active", getattr(working, "live", False)) else None
        if active_ts is None:
            return None
        age = now-active_ts
        if age >= self.maximum_ttl:
            return "TTL_EXPIRED"
        if age < self.minimum_lifetime:
            return None
        if target is None:
            return "STATE_INACTIVE"
        if abs(target.price-working.intent.price) >= tick*self.reprice_ticks:
            return "PRICE_STALE"
        if working.leaves and abs(target.quantity-working.leaves)/working.leaves >= self.resize_fraction:
            return "MATERIAL_RESIZE"
        return None
