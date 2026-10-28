"""Explicit local trading contracts; legacy Signal/OrderIntent wire stays v2."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .execution_ledger import AccountScope, exact_sum, positive_decimal
from core.models import AssetClass, InstrumentId, OrderIntent
from market.hot_path import StrategyDataView


class TradingMode(str, Enum):
    PAPER = "paper"
    SANDBOX = "sandbox"
    LIVE = "live"
    KIS_MOCK = "mock"
    KIS_MOCK2 = "mock2"


class ProductKind(str, Enum):
    CASH = "cash"
    LINEAR_PERPETUAL = "linear_perpetual"
    INVERSE_PERPETUAL = "inverse_perpetual"


class ActionKind(str, Enum):
    NEW = "NEW"
    CANCEL = "CANCEL"
    REPLACE = "REPLACE"


def bounded_identity(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 512:
        raise ValueError(name + " requires a bounded explicit identity")
    return value


def _freeze_bounded(value, *, name, max_bytes=16_384):
    if not isinstance(value, Mapping):
        raise TypeError(name + " must be a mapping of immutable facts")
    count = 0
    def walk(item, depth=0):
        nonlocal count
        count += 1
        if count > 2048 or depth > 8:
            raise ValueError(name + " exceeds its structural bound")
        if isinstance(item, Mapping):
            if len(item) > 256:
                raise ValueError(name + " mapping exceeds its entry bound")
            out = {}
            for key, child in item.items():
                if not isinstance(key, str) or len(key.encode("utf-8")) > 256:
                    raise ValueError(name + " keys must be bounded strings")
                out[key] = walk(child, depth + 1)
            return MappingProxyType(out)
        if isinstance(item, (tuple, list)):
            if len(item) > 512:
                raise ValueError(name + " sequence exceeds its entry bound")
            return tuple(walk(child, depth + 1) for child in item)
        if isinstance(item, float):
            import math
            if not math.isfinite(item):
                raise ValueError(name + " contains a non-finite float")
            return item
        if isinstance(item, Decimal) and not item.is_finite():
            raise ValueError(name + " contains a non-finite decimal")
        if item is None or isinstance(item, (str, int, float, bool, Decimal)):
            return item
        raise TypeError(name + " cannot contain actor references")
    frozen = walk(value)
    from .execution_ledger import canonical
    if len(canonical(frozen).encode("utf-8")) > max_bytes:
        raise ValueError(name + " exceeds its byte bound")
    return frozen


def validate_scope(scope: AccountScope, mode: TradingMode | str) -> TradingMode:
    mode = TradingMode(mode)
    if scope.venue.strip().casefold() == "toss":
        raise ValueError("TOSS accounts cannot register in any trading mode")
    if mode in (TradingMode.KIS_MOCK, TradingMode.KIS_MOCK2):
        if scope.venue.casefold() != "kis" or scope.product != "cash" or scope.environment != mode.value:
            raise ValueError("KIS mock modes require the exact KIS cash account environment")
    elif scope.environment != mode.value:
        raise ValueError("Account environment must explicitly match paper/sandbox/live mode")
    return mode


@dataclass(frozen=True)
class ProductSpec:
    instrument: InstrumentId
    kind: ProductKind
    multiplier: Decimal
    tick_size: Decimal
    lot_size: Decimal
    min_notional: Decimal
    quote_currency: str
    settlement_currency: str
    collateral_currency: str
    position_mode: str
    margin_mode: str
    fee_provenance: str
    funding_provenance: str
    margin_model_provenance: str
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal

    def __post_init__(self):
        object.__setattr__(self, "kind", ProductKind(self.kind))
        for name in ("multiplier", "tick_size", "lot_size", "min_notional"):
            object.__setattr__(self, name, positive_decimal(getattr(self, name), name, zero=name == "min_notional"))
        for name in ("maker_fee_rate", "taker_fee_rate"):
            rate = Decimal(str(getattr(self, name)))
            positive_decimal(rate.copy_abs(), name, zero=True)
            if rate.copy_abs() > 1:
                raise ValueError("Fee rate must be within [-1,1]")
            object.__setattr__(self, name, rate)
        for name in ("quote_currency", "settlement_currency", "collateral_currency",
                     "position_mode", "margin_mode", "fee_provenance", "funding_provenance",
                     "margin_model_provenance"):
            bounded_identity(getattr(self, name), name)
        if self.instrument.venue.value == "toss":
            raise ValueError("TOSS instruments cannot register")

    def to_document(self) -> dict:
        body = asdict(self)
        body["instrument"] = self.instrument.model_dump(mode="json")
        body["kind"] = self.kind.value
        return {key: str(value) if isinstance(value, Decimal) else value for key, value in body.items()}

    @classmethod
    def from_document(cls, document: dict) -> "ProductSpec":
        body = dict(document)
        body["instrument"] = InstrumentId.model_validate(body["instrument"])
        return cls(**body)

    def supported(self):
        allowed = ({AssetClass.EQUITY, AssetClass.ETF, AssetClass.CRYPTO_SPOT}
                   if self.kind == ProductKind.CASH else {AssetClass.CRYPTO_PERP})
        if self.instrument.asset_class not in allowed:
            raise ValueError("Product kind contradicts instrument asset class")
        if self.kind == ProductKind.INVERSE_PERPETUAL:
            raise ValueError("Inverse perpetual accounting is unsupported")
        if self.position_mode != "net":
            raise ValueError("Only explicit net position mode is supported; hedge is unsupported")
        if len({self.quote_currency, self.settlement_currency, self.collateral_currency}) != 1:
            raise ValueError("Cross-currency accounting requires an unsupported FX policy")
        if self.kind == ProductKind.CASH and self.margin_mode != "cash":
            raise ValueError("Cash products require cash margin mode")
        if self.kind == ProductKind.LINEAR_PERPETUAL and self.margin_mode != "isolated":
            raise ValueError("Only isolated simulated perpetual margin is supported")


@dataclass(frozen=True)
class ExecutionAction:
    action_id: str
    action: ActionKind
    scope: AccountScope
    strategy_id: str
    config_version: str
    mode: TradingMode
    expiry_ns: int
    intent: OrderIntent | None = None
    target_order_id: str | None = None
    post_only: bool = False
    reduce_only: bool = False
    time_in_force: str = "GTC"
    group_id: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "mode", validate_scope(self.scope, self.mode))
        object.__setattr__(self, "action", ActionKind(self.action))
        for name in ("action_id", "strategy_id", "config_version"):
            bounded_identity(getattr(self, name), name)
        if isinstance(self.expiry_ns, bool) or not isinstance(self.expiry_ns, int) or self.expiry_ns <= 0:
            raise ValueError("Action expiry must be an explicit positive nanosecond timestamp")
        if self.action in (ActionKind.NEW, ActionKind.REPLACE) and self.intent is None:
            raise ValueError("NEW/REPLACE require an OrderIntent")
        if self.action in (ActionKind.CANCEL, ActionKind.REPLACE):
            bounded_identity(self.target_order_id, "target_order_id")
        if self.action == ActionKind.NEW and self.target_order_id is not None:
            raise ValueError("NEW cannot target an existing order")
        if self.intent is not None and self.intent.instrument.venue.value != self.scope.venue:
            raise ValueError("Action instrument venue does not match account scope")
        if self.time_in_force not in ("GTC", "IOC", "FOK"):
            raise ValueError("Unsupported time in force")
        if not isinstance(self.post_only, bool) or not isinstance(self.reduce_only, bool):
            raise ValueError("Action flags must be explicit booleans")
        if self.group_id is not None:
            bounded_identity(self.group_id, "group_id")


@dataclass(frozen=True)
class StrategyActionPlan:
    plan_id: str
    strategy_id: str
    config_version: str
    event_ts_ns: int
    actions: tuple[ExecutionAction, ...]

    def __post_init__(self):
        for name in ("plan_id", "strategy_id", "config_version"):
            bounded_identity(getattr(self, name), name)
        if isinstance(self.event_ts_ns, bool) or not isinstance(self.event_ts_ns, int) or not 0 < self.event_ts_ns <= 2**63 - 1:
            raise ValueError("Plan event timestamp must be a bounded positive nanosecond timestamp")
        if not isinstance(self.actions, tuple) or len(self.actions) > 256 or any(not isinstance(a, ExecutionAction) for a in self.actions):
            raise ValueError("Plan actions must be a bounded immutable tuple of ExecutionAction")
        if any(a.strategy_id != self.strategy_id or a.config_version != self.config_version for a in self.actions):
            raise ValueError("Plan actions must retain the exact source strategy/config")
        if any(a.expiry_ns < self.event_ts_ns for a in self.actions):
            raise ValueError("Action expiry cannot precede its source event")


@dataclass(frozen=True)
class StrategyOrderEvent:
    event_id: str
    strategy_id: str
    account_scope: AccountScope
    order_id: str
    status: str
    event_ts_ns: int | None
    quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    fill_id: str | None = None
    fill_quantity: Decimal | None = None
    fill_price: Decimal | None = None
    fee: Decimal | None = None
    fee_currency: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    STATUSES = frozenset({"ACCEPTED", "REJECTED", "PARTIAL_FILL", "FILLED", "CANCEL_PENDING", "CANCELLED", "UNKNOWN", "EXPIRED"})

    def __post_init__(self):
        for name in ("event_id", "strategy_id", "order_id"):
            bounded_identity(getattr(self, name), name)
        if not isinstance(self.account_scope, AccountScope):
            raise TypeError("Order event requires an immutable AccountScope")
        if self.status not in self.STATUSES:
            raise ValueError("Unsupported factual order event status")
        if self.event_ts_ns is not None and (isinstance(self.event_ts_ns, bool) or not isinstance(self.event_ts_ns, int) or not 0 < self.event_ts_ns <= 2**63 - 1):
            raise ValueError("Event timestamp must be null or a bounded positive nanosecond timestamp")
        for name in ("quantity", "filled_quantity", "remaining_quantity"):
            object.__setattr__(self, name, positive_decimal(getattr(self, name), name, zero=True))
        if exact_sum(self.filled_quantity, self.remaining_quantity) != self.quantity:
            raise ValueError("Filled and remaining quantities must equal total quantity")
        if self.fill_id is not None:
            bounded_identity(self.fill_id, "fill_id")
        for name in ("fill_quantity", "fill_price", "fee"):
            value = getattr(self, name)
            if value is not None:
                value = positive_decimal(Decimal(str(value)).copy_abs() if name == "fee" else value, name, zero=True if name in ("fill_quantity", "fee") else False)
                object.__setattr__(self, name, value if name != "fee" or Decimal(str(getattr(self, name))) >= 0 else value.copy_negate())
        if self.fee_currency is not None:
            bounded_identity(self.fee_currency, "fee_currency")
        object.__setattr__(self, "metadata", _freeze_bounded(self.metadata, name="metadata"))


@dataclass(frozen=True)
class StrategyOrderContext:
    data: StrategyDataView | None
    execution: Mapping[str, Any]
    now_ns: int
    cursor: tuple | None
    quality: str

    def __post_init__(self):
        if self.data is not None and not isinstance(self.data, StrategyDataView):
            raise TypeError("Order context data must be StrategyDataView or None")
        if isinstance(self.now_ns, bool) or not isinstance(self.now_ns, int) or not 0 < self.now_ns <= 2**63 - 1:
            raise ValueError("Context observation time must be a bounded positive timestamp")
        if self.cursor is not None:
            if not isinstance(self.cursor, tuple) or len(self.cursor) > 8:
                raise ValueError("Context cursor must be a bounded tuple or None")
            def freeze_cursor(value, depth=0):
                if depth > 4:
                    raise ValueError("Context cursor exceeds its structural bound")
                if isinstance(value, (tuple, list)):
                    if len(value) > 64:
                        raise ValueError("Context cursor exceeds its entry bound")
                    return tuple(freeze_cursor(item, depth + 1) for item in value)
                if isinstance(value, float):
                    import math
                    if not math.isfinite(value):
                        raise ValueError("Context cursor contains a non-finite float")
                    return value
                if isinstance(value, Decimal) and not value.is_finite():
                    raise ValueError("Context cursor contains a non-finite decimal")
                if value is None or isinstance(value, (str, int, float, bool, Decimal)):
                    return value
                raise TypeError("Context cursor cannot contain actor references")
            frozen_cursor = freeze_cursor(self.cursor)
            from .execution_ledger import canonical
            if len(canonical(frozen_cursor).encode("utf-8")) > 4096:
                raise ValueError("Context cursor exceeds its byte bound")
            object.__setattr__(self, "cursor", frozen_cursor)
        if isinstance(self.execution, Mapping) is False:
            raise TypeError("Context execution facts must be a mapping")
        bounded_identity(self.quality, "quality")
        object.__setattr__(self, "execution", _freeze_bounded(self.execution, name="execution"))
