"""Canonical ownership for modern event strategies and isolated strategy families.

Only modern event strategies are imported here. Legacy candle and research state
packages are explicitly imported by their owners and are never discovered or
registered as a side effect of importing this package.
"""

from .contract import CompiledSubscription, StrategyInstance, StrategyModule, compile_subscriptions, compile_warmup, load_strategy

__all__ = [
    "CompiledSubscription",
    "StrategyInstance",
    "StrategyModule",
    "compile_subscriptions",
    "compile_warmup",
    "load_strategy",
]
