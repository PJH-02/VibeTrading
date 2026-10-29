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
        # `mock` is the shared domestic mock-environment vocabulary; `mock2`
        # remains the KIS-only VTS mode.  Both require cash economics.
        venues = {"kis"} if mode is TradingMode.KIS_MOCK2 else {"kis", "kiwoom"}
        if (scope.venue.casefold() not in venues or scope.product != "cash"
                or scope.environment != mode.value):
            raise ValueError("Domestic mock modes require the exact cash account environment")
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


_NATIVE_LINEAR_VENUES = frozenset({"binance", "bybit", "grvt", "arcus", "hyperliquid"})
_NATIVE_PRODUCT_FIELDS = frozenset({
    "scope_key", "venue", "product", "environment", "symbol", "kind", "multiplier",
    "tick_size", "lot_size", "min_quantity", "min_notional", "base_currency", "quote_currency",
    "settlement_currency", "collateral_currency", "raw",
})


def _native_exact_decimal(value, name, *, nonnegative=False):
    """Parse adapter decimal strings/integers without accepting floats or bools."""
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(name + " requires an exact decimal string/integer")
    return positive_decimal(value, name, zero=nonnegative)


def _native_result(reason, *, facts=None):
    return _freeze_bounded({
        "quality": "UNAVAILABLE" if reason else "VALID",
        "reason": reason,
        "risk_ready": reason is None,
        "basis": "NATIVE_REPORTED_FACTS",
        "facts": facts or {},
    }, name="native risk result")


def validate_native_account_facts(account_facts, product_specs, current_scope, *,
                                  account_version, expected_native_account_id,
                                  now_ns, max_age_ns):
    """Validate an explicit, read-only native account/product evidence envelope.

    ``account_facts`` must contain ``normalized`` (the adapter's account_state
    row), ``observed_ns``, ``account_version``, ``reconciliation_state`` equal
    to ``RECONCILED``, ``unknown`` equal to False, explicit ``position_mode``
    and ``margin_mode``, and ``native_identity``. Identity evidence must name
    the exact scope and native account/session, match
    ``expected_native_account_id`` (the independently bound physical account
    identity), identify its source as an authenticated native session, and list
    both ``account_read`` and ``trade`` permissions. A caller alias or isolated
    boolean is never sufficient.

    ``product_specs`` is a bounded mapping of native metadata rows with the
    same shape emitted by ``_Adapter._metadata_row``. The function only
    validates declared facts; it performs no requests, permission grants, FX,
    margin calculations, or authentication. Its result is a frozen mapping.
    """
    try:
        frozen = _freeze_bounded(account_facts, name="native account facts")
        specs = _freeze_bounded(product_specs, name="native product specs")
        if not isinstance(account_version, str) or not account_version or len(account_version) > 256:
            raise ValueError("expected account version is missing")
        if (not isinstance(expected_native_account_id, str) or not expected_native_account_id.strip()
                or len(expected_native_account_id.encode("utf-8")) > 256):
            raise ValueError("independently expected native account identity is missing")
        if (isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns <= 0
                or isinstance(max_age_ns, bool) or not isinstance(max_age_ns, int) or max_age_ns <= 0):
            raise ValueError("explicit positive freshness bounds are required")
        venue = getattr(current_scope, "venue", None)
        product = getattr(current_scope, "product", None)
        environment = getattr(current_scope, "environment", None)
        scope_key = getattr(current_scope, "key", None)
        settlement_currency = getattr(current_scope, "settlement_currency", None)
        if (venue not in _NATIVE_LINEAR_VENUES or product != "linear_perpetual"
                or environment not in {"sandbox", "live"} or not isinstance(scope_key, str)
                or not isinstance(settlement_currency, str) or not settlement_currency):
            raise ValueError("scope is outside the selected linear perpetual boundary")
        if not isinstance(frozen, Mapping) or not isinstance(specs, Mapping) or not specs:
            raise ValueError("native account and product evidence must be nonempty mappings")
        normalized = frozen.get("normalized")
        if not isinstance(normalized, Mapping):
            raise ValueError("normalized adapter account row is missing")
        for key, expected in (("scope_key", scope_key), ("venue", venue),
                              ("product", product), ("environment", environment)):
            if normalized.get(key) != expected:
                raise ValueError("normalized account scope mismatch")
        raw_account = normalized.get("raw")
        if not isinstance(raw_account, Mapping) or not raw_account:
            raise ValueError("native raw account source evidence is missing")
        observed_ns = frozen.get("observed_ns")
        if isinstance(observed_ns, bool) or not isinstance(observed_ns, int) or observed_ns <= 0:
            raise ValueError("native observation timestamp is missing")
        if observed_ns > now_ns:
            raise ValueError("native account observation is from the future")
        if now_ns - observed_ns > max_age_ns:
            raise ValueError("native account observation is stale")
        if frozen.get("account_version") != account_version:
            raise ValueError("native account version mismatch")
        if frozen.get("reconciliation_state") != "RECONCILED" or frozen.get("unknown") is not False:
            raise ValueError("native account state is unreconciled or UNKNOWN")
        if frozen.get("position_mode") != "net":
            raise ValueError("native account position mode is not net")
        margin_mode = frozen.get("margin_mode")
        if margin_mode not in {"cross", "isolated"}:
            raise ValueError("native account margin mode is missing or unsupported")

        identity = frozen.get("native_identity")
        if not isinstance(identity, Mapping):
            raise ValueError("authenticated native identity evidence is missing")
        for key in ("native_account_id", "authenticated_session_id"):
            value = identity.get(key)
            if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 256:
                raise ValueError("authenticated native identity/session is missing")
        if identity.get("native_account_id") != expected_native_account_id:
            raise ValueError("native account identity differs from the independently bound physical account")
        # Some existing adapters expose an independently observed native ID in
        # the normalized row (notably Hyperliquid's account_address). Bind such
        # fields when present; do not infer a native ID from arbitrary raw keys.
        for key in ("account_address", "native_account_id"):
            if key in normalized and normalized[key] != expected_native_account_id:
                raise ValueError("normalized native account identity differs from the independently bound physical account")
        if identity.get("scope_key") != scope_key or identity.get("source") != "authenticated_native_session":
            raise ValueError("native identity evidence does not bind the authenticated scope")
        permissions = identity.get("permissions")
        if (not isinstance(permissions, tuple)
                or not {"account_read", "trade"}.issubset(permissions)):
            raise ValueError("native read/trade permission provenance is incomplete")

        # Arcus is the sole explicit native quote/settlement exception. The
        # public read snapshots are recomputed here; a caller-supplied decoded
        # result or legacy account flags cannot establish this proof.
        if venue == "arcus":
            proof_input = frozen.get("arcus_quote_proof")
            if not isinstance(proof_input, Mapping):
                raise ValueError("cross-currency, FX, or wrong-collateral Arcus quote lacks native USD valuation proof")
            if margin_mode not in {"cross", "isolated"}:
                raise ValueError("Arcus native margin mode is unsupported")
            from .arcus_risk import decode_arcus_quote_proof
            computed = decode_arcus_quote_proof(
                proof_input.get("account"), proof_input.get("markets"), proof_input.get("leverages"),
                address=identity["native_account_id"], account_index=int(getattr(current_scope, "subaccount", "-1")),
                symbol=next(iter(specs), ""),
                local_ledger_version=frozen.get("local_ledger_version"))
            if frozen.get("account_version") != "arcus:" + str(computed["sequence"]):
                raise ValueError("Arcus account sequence/version mismatch")
            if normalized.get("raw") != proof_input.get("account") or normalized.get("account_sequence") != computed["sequence"]:
                raise ValueError("Arcus normalized account raw/sequence differs from native quote proof")
            if margin_mode != computed["margin_mode"]:
                raise ValueError("Arcus native mode differs from its quote proof")
            if (normalized.get("currency") != "USD" or normalized.get("settlement_currency") != "USDG"
                    or getattr(current_scope, "settlement_currency", None) != "USDG"):
                raise ValueError("Arcus quote USD and settlement USDG must remain distinct")
            expected_nums = {"equity": computed["equityUSD"], "available": computed["availableUSD"],
                             "initial_margin": computed["initialMarginUSD"],
                             "maintenance_margin": computed["maintenanceMarginUSD"]}
            for key, expected in expected_nums.items():
                if _native_exact_decimal(normalized.get(key), key, nonnegative=True) != expected:
                    raise ValueError("Arcus normalized " + key + " differs from recomputed native USD proof")
            if computed["availableUSD"] > computed["equityUSD"]:
                raise ValueError("Arcus available USD exceeds equity USD")
            checked_specs = {}
            if len(specs) > 64:
                raise ValueError("native product set exceeds the bounded product count")
            for symbol, row in specs.items():
                if not isinstance(row, Mapping) or not _NATIVE_PRODUCT_FIELDS.issubset(row):
                    raise ValueError("Arcus native product metadata is incomplete")
                for key, expected in (("scope_key", scope_key), ("venue", "arcus"),
                                      ("product", product), ("environment", environment),
                                      ("symbol", symbol), ("kind", "linear_perpetual")):
                    if row.get(key) != expected:
                        raise ValueError("Arcus native product scope/kind mismatch")
                if (row.get("quote_currency") != "USD" or row.get("settlement_currency") != "USDG"
                        or row.get("collateral_currency") != "USDG" or row.get("margin_mode") != computed["margin_mode"]
                        or row.get("position_mode") != "net" or not isinstance(row.get("raw"), Mapping)):
                    raise ValueError("Arcus native product quote/settlement/mode proof mismatch")
                native_market = next((m for m in computed["raw_markets"]
                                      if m.get("marketDisplayName") == symbol), None)
                if native_market is None or row.get("raw") != native_market:
                    raise ValueError("Arcus native product metadata differs from full market snapshot")
                from .arcus_risk import dec as arcus_decimal
                if (row.get("base_currency") != native_market.get("baseAsset")
                        or row.get("quote_currency") != native_market.get("quoteAsset")
                        or arcus_decimal(row.get("multiplier"), "Arcus product multiplier") != Decimal(1)
                        or arcus_decimal(row.get("tick_size"), "Arcus product tick") != arcus_decimal(native_market.get("tickSize"), "Arcus native tick")
                        or arcus_decimal(row.get("lot_size"), "Arcus product lot") != arcus_decimal(native_market.get("stepSize"), "Arcus native step")
                        or arcus_decimal(row.get("min_quantity"), "Arcus product minimum") != arcus_decimal(native_market.get("minOrderSize"), "Arcus native minimum")
                        or arcus_decimal(row.get("min_notional"), "Arcus product notional") != arcus_decimal(native_market.get("minOrderNotional"), "Arcus native notional minimum")):
                    raise ValueError("Arcus normalized contract fields differ from native market metadata")
                nums = {}
                for key, nonnegative in (("multiplier", False), ("tick_size", False),
                                         ("lot_size", False), ("min_quantity", False),
                                         ("min_notional", True)):
                    nums[key] = _native_exact_decimal(row.get(key), key, nonnegative=nonnegative)
                    if key != "min_notional" and nums[key] <= 0:
                        raise ValueError("Arcus native product " + key + " must be positive")
                checked_specs[symbol] = {**dict(row), **nums}
            facts = {"scope_key": scope_key, "venue": venue, "product": product,
                     "environment": environment, "account_version": account_version,
                     "local_ledger_version": computed["local_ledger_version"],
                     "observed_ns": observed_ns, "native_account_id": identity["native_account_id"],
                     "position_mode": "net", "margin_mode": computed["margin_mode"],
                     "basis": "ARCUS_NATIVE_QUOTE_USD", "currency": "USD",
                     "settlement_currency": "USDG", "collateral_currency": "USDG",
                     "valuation_currency": "USD", "initial_margin_basis": computed["initial_margin_basis"],
                     "maintenance_margin_basis": computed["maintenance_margin_basis"],
                     "native_margin_state": computed["margin_state"],
                     "reported": expected_nums, "products": checked_specs,
                     "arcus_quote_proof": computed}
            return _native_result(None, facts=facts)

        account_currency = normalized.get("currency")
        settlement = normalized.get("settlement_currency", account_currency)
        if account_currency != settlement_currency or settlement != settlement_currency:
            raise ValueError("native account currency does not exactly match settlement currency")
        numbers = {}
        for key, nonnegative in (("equity", True), ("available", True),
                                 ("initial_margin", True), ("maintenance_margin", True)):
            value = normalized.get(key)
            if value is None:
                raise ValueError("native reported " + key + " is unavailable")
            numbers[key] = _native_exact_decimal(value, key, nonnegative=nonnegative)
        if numbers["available"] > numbers["equity"]:
            raise ValueError("native available balance exceeds reported equity")

        checked_specs = {}
        if len(specs) > 64:
            raise ValueError("native product set exceeds the bounded product count")
        for symbol, row in specs.items():
            if not isinstance(symbol, str) or not symbol or len(symbol.encode("utf-8")) > 256:
                raise ValueError("native product symbol is invalid")
            if not isinstance(row, Mapping) or not _NATIVE_PRODUCT_FIELDS.issubset(row):
                raise ValueError("native product metadata is incomplete")
            for key, expected in (("scope_key", scope_key), ("venue", venue),
                                  ("product", product), ("environment", environment),
                                  ("symbol", symbol), ("kind", "linear_perpetual")):
                if row.get(key) != expected:
                    raise ValueError("native product metadata scope/kind mismatch")
            raw_product = row.get("raw")
            if not isinstance(raw_product, Mapping) or not raw_product:
                raise ValueError("native product source metadata is missing")
            base_currency = row.get("base_currency")
            if (not isinstance(base_currency, str) or not base_currency.strip()
                    or len(base_currency.encode("utf-8")) > 256):
                raise ValueError("native product base currency is missing")
            for key, expected_mode in (("position_mode", "net"), ("margin_mode", margin_mode)):
                reported_mode = row.get(key)
                if reported_mode is not None and reported_mode != expected_mode:
                    raise ValueError("native product " + key + " contradicts authenticated account mode")
            currencies = (row.get("quote_currency"), row.get("settlement_currency"),
                          row.get("collateral_currency"))
            if any(currency != settlement_currency for currency in currencies):
                raise ValueError("cross-currency, FX, or wrong-collateral product is unsupported")
            spec_numbers = {}
            for key, nonnegative in (("multiplier", False), ("tick_size", False),
                                     ("lot_size", False), ("min_quantity", False),
                                     ("min_notional", True)):
                spec_numbers[key] = _native_exact_decimal(row.get(key), key, nonnegative=nonnegative)
                if key != "min_notional" and spec_numbers[key] <= 0:
                    raise ValueError("native product " + key + " must be positive")
            checked_specs[symbol] = {**dict(row), **spec_numbers}
        facts = {
            "scope_key": scope_key, "venue": venue, "product": product,
            "environment": environment, "account_version": account_version,
            "observed_ns": observed_ns, "native_account_id": identity["native_account_id"],
            "position_mode": "net", "margin_mode": margin_mode,
            "basis": "NATIVE_REPORTED_FACTS",
            "currency": settlement_currency, "reported": numbers,
            "products": checked_specs,
        }
        return _native_result(None, facts=facts)
    except (TypeError, ValueError, ArithmeticError) as error:
        # Errors are converted to a closed, immutable result so callers can use
        # one gate contract without accidentally treating absent facts as zero.
        return _native_result(str(error)[:256])


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
            # A source may explicitly select a separate execution venue while
            # retaining its market/listing instrument identity. Route aliases
            # still bind that exact venue, account, product and environment.
            selected = self.intent.metadata.get("execution_venue_id")
            if not isinstance(selected, str) or selected != self.scope.venue:
                raise ValueError("Action instrument venue differs without an explicit execution_venue_id")
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
