"""Explicit local trading contracts; legacy Signal/OrderIntent wire stays v2."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum

from .execution_ledger import AccountScope, positive_decimal
from core.models import AssetClass, InstrumentId, OrderIntent


class TradingMode(str, Enum):
    PAPER = "paper"
    SANDBOX = "sandbox"
    LIVE = "live"


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


def validate_scope(scope: AccountScope, mode: TradingMode | str) -> TradingMode:
    mode = TradingMode(mode)
    if scope.venue.strip().casefold() == "toss":
        raise ValueError("TOSS accounts cannot register in any trading mode")
    if scope.environment != mode.value:
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
