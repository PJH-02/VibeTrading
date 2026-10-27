"""Explicit local venue registry: bindings, lifecycle, aliases, no discovery.

An adapter instance is the sole order/fill/account authority.  This module only
records explicit references, validates bounded route/product/account scope,
and forwards operations.  It never constructs clients, signers, SDK sessions,
feed providers, or a second execution engine/ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import inspect
import json
from types import MappingProxyType
from typing import Any, Callable, Mapping
import threading
import weakref

from execution.execution_ledger import AccountScope


SUPPORTED_OPERATIONS = (
    "submit",
    "cancel",
    "query_order",
    "list_open_orders",
    "get_fills",
    "account_state",
    "positions",
    "reconcile",
    "instrument_spec",
    "private_or_poll",
)
MUTATION_OPERATIONS = frozenset({"submit", "cancel"})
MANDATORY_REMOVAL_FACTS = (
    "open_orders",
    "unknown_orders",
    "unreconciled_fills",
    "account_dependencies",
    "strategy_aliases",
    "rollback_refs",
)
MAX_IDENTITY_BYTES = 200
MAX_ALIAS_DOCUMENT_BYTES = 65536

# A route's product and its market segment must belong to one accounting mode.
# This is a static compatibility map, never inferred from a symbol's text.
_MODE_FAMILY = {
    "cash": "cash",
    "spot": "cash",
    "crypto_spot": "cash",
    "krx_cash": "cash",
    "linear": "linear",
    "perp": "linear",
    "perpetual": "linear",
    "crypto_perp": "linear",
    "linear_perpetual": "linear",
    "usdm_perp": "linear",
}
_REAL_MODES = {
    # product -> (accepted market segments, accepted account-scope products)
    "cash": ({"cash", "spot", "crypto_spot", "krx_cash"}, {"cash", "spot", "crypto_spot", "krx_cash"}),
    "linear_perpetual": ({"linear", "perp", "perpetual", "crypto_perp", "usdm_perp"},
                         {"linear", "perp", "perpetual", "crypto_perp", "linear_perpetual", "usdm_perp"}),
}
_ALLOWED_ENVIRONMENTS = frozenset({"offline", "paper", "sandbox", "mock", "mock2", "live"})


class RouteError(RuntimeError):
    pass


class UnsupportedOperation(RouteError):
    pass


class RouteStateError(RouteError):
    pass


class PermitError(RouteError):
    pass


class AdapterProtocolError(RouteError):
    pass


class RemovalBlocked(RouteError):
    pass


class ForeignOrderError(RouteError):
    pass


class RouteState(str, Enum):
    ENABLED = "ENABLED"
    QUARANTINED = "QUARANTINED"
    DISABLED = "DISABLED"
    REMOVED = "REMOVED"


def _bounded(value, name, limit=MAX_IDENTITY_BYTES):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " requires a bounded explicit identity")
    if len(value.encode("utf-8")) > limit:
        raise ValueError(name + " exceeds its explicit byte bound")
    return value


def _positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(name + " requires an explicit positive integer")
    return value


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _mode_family(value: str) -> str:
    family = _MODE_FAMILY.get(value.casefold())
    if family is None:
        raise ValueError("Unsupported product/market segment: " + str(value))
    return family


@dataclass(frozen=True)
class RouteSpec:
    """Immutable exact route tuple.  A symbol never selects the product."""

    route_id: str
    venue: str
    market_segment: str
    product: str
    environment: str
    account_scope: AccountScope

    def __post_init__(self):
        _bounded(self.route_id, "route_id")
        _bounded(self.venue, "venue")
        _bounded(self.market_segment, "market_segment")
        _bounded(self.product, "product")
        _bounded(self.environment, "environment")
        if not isinstance(self.account_scope, AccountScope):
            raise ValueError("route requires an immutable AccountScope")
        if self.venue.strip().casefold() == "toss" or self.account_scope.venue.strip().casefold() == "toss":
            raise ValueError("TOSS is DATA_ONLY and cannot register a trading route")
        if self.environment not in _ALLOWED_ENVIRONMENTS:
            raise ValueError("Unsupported route environment")
        product_family = _mode_family(self.product)
        segment_family = _mode_family(self.market_segment)
        scope_family = _mode_family(self.account_scope.product)
        if not (product_family == segment_family == scope_family):
            raise ValueError("Route product/market/account-scope modes are incompatible")
        accepted_segments, accepted_scope_products = _REAL_MODES[
            "linear_perpetual" if product_family == "linear" else "cash"]
        if self.market_segment not in accepted_segments:
            raise ValueError("Market segment is incompatible with the route product")
        if self.account_scope.product not in accepted_scope_products:
            raise ValueError("Account-scope product is incompatible with the route product")
        if self.account_scope.venue.casefold() != self.venue.casefold():
            raise ValueError("Route venue and account-scope venue disagree")
        if self.account_scope.environment != self.environment:
            raise ValueError("Route environment and account-scope environment disagree")
        venue = self.venue.casefold()
        if self.environment == "mock2" and venue != "kis":
            raise ValueError("mock2 is supported only for KIS cash routes")
        if self.environment == "mock2" and (self.product != "cash" or self.market_segment != "krx_cash"):
            raise ValueError("KIS mock2 supports only krx_cash")
        if venue == "kiwoom" and self.environment == "mock" and (self.product != "cash" or self.market_segment != "krx_cash"):
            raise ValueError("Kiwoom mock supports only krx_cash")
        if venue == "binance" and self.market_segment == "usdm_perp" and self.product != "linear_perpetual":
            raise ValueError("Binance usdm_perp requires linear_perpetual")

    @property
    def mode_family(self) -> str:
        return _mode_family(self.product)

    @property
    def canonical_mutation_tuple(self):
        scope = self.account_scope
        # Segment/product spellings such as Binance `linear` and `usdm_perp`
        # describe the same economic mutation family for this physical account.
        return (self.venue.casefold(), self.mode_family,
                self.environment.casefold(), scope.venue.casefold(), scope.account,
                _mode_family(scope.product), scope.environment.casefold())


@dataclass(frozen=True)
class Capabilities:
    """Frozen explicit adapter capabilities; no capability is inferred."""

    products: frozenset
    environments: frozenset
    time_in_force: frozenset
    post_only: bool
    reduce_only: bool
    funding: bool
    private_or_poll: bool = True

    def __post_init__(self):
        for name in ("products", "environments", "time_in_force"):
            values = getattr(self, name)
            if not isinstance(values, (set, frozenset, tuple, list)) or not values:
                raise ValueError(name + " requires an explicit nonempty set")
            bounded = frozenset(_bounded(item, name + " item") for item in values)
            object.__setattr__(self, name, bounded)
        for name in ("post_only", "reduce_only", "funding", "private_or_poll"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(name + " requires an explicit boolean")
        if self.private_or_poll is not True:
            raise ValueError("private_or_poll is required for every registered route")


@dataclass(frozen=True)
class AliasBinding:
    strategy_id: str
    alias: str
    route_id: str
    symbol: str
    market_segment: str
    product: str
    environment: str
    account_scope: AccountScope
    required_feeds: tuple[str, ...] = ()
    execution_enabled: bool = True

    def __post_init__(self):
        for name in ("strategy_id", "alias", "route_id", "symbol", "market_segment", "product", "environment"):
            _bounded(getattr(self, name), name)
        if not isinstance(self.account_scope, AccountScope):
            raise ValueError("alias requires an immutable AccountScope")
        feeds = tuple(_bounded(item, "required feed") for item in self.required_feeds)
        if len(set(feeds)) != len(feeds):
            raise ValueError("required_feeds must be unique")
        object.__setattr__(self, "required_feeds", feeds)
        if type(self.execution_enabled) is not bool:
            raise ValueError("execution_enabled requires an explicit boolean")

    @property
    def key(self):
        return (self.strategy_id, self.alias)

    def missing_feeds(self, available_feeds) -> tuple[str, ...]:
        available = frozenset(available_feeds or ())
        return tuple(feed for feed in self.required_feeds if feed not in available)

    def enabled_with(self, available_feeds) -> bool:
        return self.execution_enabled and not self.missing_feeds(available_feeds)


class _RouteRecord:
    __slots__ = ("binding", "state", "epoch", "in_flight", "owned", "owned_meta", "owned_primary", "reserved_owned")

    def __init__(self, binding):
        self.binding = binding
        self.state = RouteState.ENABLED
        self.epoch = 1
        self.in_flight = 0
        self.owned = set()
        self.owned_meta = {}
        self.owned_primary = set()
        self.reserved_owned = set()


@dataclass(frozen=True)
class Binding:
    route: RouteSpec
    adapter: Any
    operations: Mapping
    capabilities: Capabilities
    reference_provider: Callable
    canonical_owner: str
    gaps: tuple[str, ...] = ()


class _GuardedPermit:
    """Synchronous one-use permit checked at the adapter dispatch seam."""

    __slots__ = ("_registry", "_route_id", "_epoch", "_source", "_operation", "consumed", "dispatch_started", "__weakref__")

    def __init__(self, registry, route_id, epoch, source, operation="submit"):
        self._registry = registry
        self._route_id = route_id
        self._epoch = epoch
        self._source = source
        self._operation = operation
        self.consumed = False
        self.dispatch_started = False

    def __call__(self):
        if self.consumed:
            raise PermitError("Permit has already been consumed")
        with self._registry._lock:
            record = self._registry._records.get(self._route_id)
            allowed = record is not None and (record.state is RouteState.ENABLED or
                      (self._operation == "cancel" and record.state in
                       (RouteState.DISABLED, RouteState.QUARANTINED)))
            if not allowed:
                raise PermitError("Route state no longer permits this mutation dispatch")
            if record.epoch != self._epoch:
                raise PermitError("Registry epoch changed before mutation dispatch")
            try:
                if self._source in self._registry._used_permit_sources:
                    raise PermitError("Owner permit source has already been consumed")
                self._registry._used_permit_sources[self._source] = True
            except TypeError as error:
                raise TypeError("Owner permit source must be weak-referenceable and hashable") from error
        self.consumed = True
        result = self._source()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("Owner permit must be synchronous and one-use")
        with self._registry._lock:
            record = self._registry._records.get(self._route_id)
            allowed = record is not None and (record.state is RouteState.ENABLED or
                      (self._operation == "cancel" and record.state in
                       (RouteState.DISABLED, RouteState.QUARANTINED)))
            if not allowed or record.epoch != self._epoch:
                raise PermitError("Owner permit changed route lifecycle before adapter dispatch")
        self.dispatch_started = True
        return result


class VenueRegistry:
    """Bounded in-process registry.  No adapter is constructed or discovered."""

    def __init__(self, *, max_routes: int, max_aliases: int, max_fact_bytes: int,
                 max_owned_orders: int = 1024):
        self.max_routes = _positive_int(max_routes, "max_routes")
        self.max_aliases = _positive_int(max_aliases, "max_aliases")
        self.max_fact_bytes = _positive_int(max_fact_bytes, "max_fact_bytes")
        self.max_owned_orders = _positive_int(max_owned_orders, "max_owned_orders")
        self._lock = threading.RLock()
        self._records: dict[str, _RouteRecord] = {}
        self._canonical: dict[tuple, str] = {}
        self._aliases: dict[tuple[str, str], AliasBinding] = {}
        self._epoch = 0
        self._used_permit_sources = weakref.WeakKeyDictionary()

    # ------------------------------------------------------------------ routes
    def register(self, route: RouteSpec, adapter, operations, capabilities: Capabilities,
                 reference_provider, *, canonical_owner=None, gaps=()) -> Binding:
        if not isinstance(route, RouteSpec):
            raise TypeError("An explicit RouteSpec is required")
        if not isinstance(capabilities, Capabilities):
            raise TypeError("Frozen explicit Capabilities are required")
        if not callable(reference_provider):
            raise TypeError("A trusted owner factual reference provider is required")
        operations = self._resolve_operations(adapter, operations)
        owner = canonical_owner or (type(adapter).__module__ + "." + type(adapter).__qualname__)
        _bounded(owner, "canonical_owner", 512)
        gap_tuple = tuple(_bounded(str(gap), "gap", 512) for gap in gaps)
        self._validate_adapter_scope(adapter, route)
        binding = Binding(route, adapter, MappingProxyType(dict(operations)), capabilities,
                          reference_provider, owner, gap_tuple)
        with self._lock:
            if route.route_id in self._records:
                raise ValueError("Route ID is already registered and is never silently evicted")
            if len(self._records) >= self.max_routes:
                raise ValueError("Explicit max_routes budget exhausted; no silent eviction")
            existing = self._canonical.get(route.canonical_mutation_tuple)
            if existing is not None:
                raise ValueError("Duplicate canonical mutation tuple already owned by route " + existing)
            record = _RouteRecord(binding)
            self._records[route.route_id] = record
            self._canonical[route.canonical_mutation_tuple] = route.route_id
            self._epoch += 1
        return binding

    def bind_existing_adapter(self, route: RouteSpec, adapter, *, reference_provider,
                              capabilities: Capabilities) -> Binding:
        owner, operations, gaps = existing_adapter_contract(adapter)
        return self.register(route, adapter, operations, capabilities, reference_provider,
                             canonical_owner=owner, gaps=gaps)

    def _resolve_operations(self, adapter, operations):
        if not isinstance(operations, Mapping):
            raise TypeError("Operation mapping must be an explicit mapping")
        resolved = {}
        for name, target in operations.items():
            if name not in SUPPORTED_OPERATIONS:
                raise ValueError("Unsupported operation name: " + str(name))
            if isinstance(target, str):
                fn = getattr(adapter, target, None)
                if not callable(fn):
                    raise ValueError("Adapter lacks mapped operation " + name)
            elif callable(target):
                fn = target
            else:
                raise TypeError("Operation mapping values must be method names or callables")
            if name in MUTATION_OPERATIONS and not _accepts_permit(fn):
                raise ValueError("Mutation operation " + name + " requires a permit dispatch seam")
            resolved[name] = fn
        return resolved

    @staticmethod
    def _validate_adapter_scope(adapter, route: RouteSpec):
        scope = getattr(adapter, "scope", None)
        native_name = getattr(adapter, "name", None)
        if isinstance(native_name, str) and native_name.strip().casefold() != route.venue.casefold():
            raise ValueError("Native adapter venue name differs from the registered route")
        owner = type(adapter).__module__ + "." + type(adapter).__qualname__
        known_cash_owners = {
            "execution.kis_execution.KisCashBroker",
            "execution.brokers.KisBrokerAdapter",
            "execution.brokers.KiwoomBrokerAdapter",
            "execution.brokers.BinanceBrokerAdapter",
        }
        if owner in known_cash_owners and route.mode_family != "cash":
            raise ValueError("Native cash adapter cannot bind a linear perpetual route")
        mode = getattr(adapter, "mode", None)
        if mode is not None and mode != route.environment:
            raise ValueError("Native adapter mode differs from the registered route environment")
        use_mock = getattr(adapter, "use_mock", None)
        if use_mock is not None:
            expected_mock = route.environment == "mock"
            if type(use_mock) is not bool or use_mock is not expected_mock:
                raise ValueError("Native adapter mock state differs from the registered route")
        use_testnet = getattr(adapter, "use_testnet", None)
        if use_testnet is not None:
            expected_testnet = route.environment == "sandbox"
            if type(use_testnet) is not bool or use_testnet is not expected_testnet:
                raise ValueError("Native adapter testnet state differs from the registered route")
        if scope is None:
            return
        if isinstance(scope, AccountScope):
            if scope.key != route.account_scope.key:
                raise ValueError("Native adapter scope differs from the registered route scope")
            return
        if isinstance(scope, Mapping):
            try:
                exposed = AccountScope(scope["venue"], scope["account"],
                                       scope["product"], scope["environment"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("Exposed adapter scope is incomplete") from error
            if exposed.key != route.account_scope.key:
                raise ValueError("Native adapter scope differs from the registered route scope")
            return
        # Existing perp adapters expose a bounded PerpScope-style object.
        fields = {}
        for name in ("venue", "product", "environment"):
            if not hasattr(scope, name):
                raise ValueError("Exposed adapter scope is not a recognized bounded scope")
            fields[name] = getattr(scope, name)
        account = getattr(scope, "account", getattr(scope, "account_alias", None))
        if fields["venue"].casefold() != route.venue.casefold():
            raise ValueError("Native adapter venue differs from the registered route")
        if _mode_family(fields["product"]) != route.mode_family:
            raise ValueError("Native adapter product differs from the registered route")
        if fields["environment"] != route.environment:
            raise ValueError("Native adapter environment differs from the registered route")
        if account is not None and str(account) != route.account_scope.account:
            raise ValueError("Native adapter account differs from the registered route")

    def route(self, route_id) -> RouteSpec:
        with self._lock:
            return self._record(route_id).binding.route

    def binding(self, route_id) -> Binding:
        with self._lock:
            return self._record(route_id).binding

    def state(self, route_id) -> RouteState:
        with self._lock:
            return self._record(route_id).state

    def _record(self, route_id):
        record = self._records.get(route_id)
        if record is None:
            raise KeyError("Unknown route: " + str(route_id))
        return record

    def snapshot(self):
        with self._lock:
            return {
                "epoch": self._epoch,
                "routes": tuple(
                    (route_id, record.state.value, record.epoch, record.in_flight)
                    for route_id, record in self._records.items()),
            }

    def route_snapshot(self, route_id):
        with self._lock:
            record = self._record(route_id)
            return {"route_id": route_id, "state": record.state.value, "epoch": record.epoch,
                    "in_flight": record.in_flight, "owned_orders": tuple(sorted(record.owned))}

    def enable(self, route_id):
        with self._lock:
            record = self._record(route_id)
            if record.state is RouteState.REMOVED:
                raise RouteStateError("Removed routes cannot be enabled")
            if record.state is not RouteState.ENABLED:
                record.state = RouteState.ENABLED
                record.epoch += 1
                self._epoch += 1
            return record.state

    def disable(self, route_id):
        with self._lock:
            record = self._record(route_id)
            if record.state is RouteState.REMOVED:
                raise RouteStateError("Removed routes cannot be disabled")
            if record.state is not RouteState.DISABLED:
                record.state = RouteState.DISABLED
                record.epoch += 1
                self._epoch += 1
            return record.state

    def quarantine(self, route_id):
        with self._lock:
            record = self._record(route_id)
            if record.state is RouteState.REMOVED:
                raise RouteStateError("Removed routes cannot be quarantined")
            if record.state is not RouteState.QUARANTINED:
                record.state = RouteState.QUARANTINED
                record.epoch += 1
                self._epoch += 1
            return record.state

    # ---------------------------------------------------------------- aliases
    def register_aliases(self, bindings) -> tuple[AliasBinding, ...]:
        items = tuple(bindings)
        for item in items:
            if not isinstance(item, AliasBinding):
                raise TypeError("Aliases must be immutable AliasBinding instances")
        with self._lock:
            seen = set()
            ambiguity: dict[tuple, str] = {}
            physical: dict[tuple, tuple] = {}
            for item in self._aliases.values():
                ambiguity[(item.strategy_id, item.symbol.casefold(), item.market_segment.casefold(),
                           item.product.casefold())] = item.route_id
                physical[(item.account_scope.venue.casefold(), item.account_scope.account,
                          item.environment.casefold())] = (
                              _mode_family(item.product), item.route_id)
            for item in items:
                if item.key in seen or item.key in self._aliases:
                    raise ValueError("Duplicate alias binding")
                seen.add(item.key)
                record = self._records.get(item.route_id)
                if record is None:
                    raise ValueError("Alias references an unregistered route")
                if record.state is RouteState.REMOVED:
                    raise RouteStateError("Alias cannot bind to a REMOVED route")
                route = record.binding.route
                if (item.market_segment != route.market_segment or item.product != route.product
                        or item.environment != route.environment
                        or item.account_scope.key != route.account_scope.key):
                    raise ValueError("Alias does not match the registered exact route tuple")
                amb = (item.strategy_id, item.symbol.casefold(), item.market_segment.casefold(),
                       item.product.casefold())
                if amb in ambiguity and ambiguity[amb] != item.route_id:
                    raise ValueError("Ambiguous alias resolves the same product/symbol to another route")
                ambiguity[amb] = item.route_id
                phys = (item.account_scope.venue.casefold(), item.account_scope.account,
                        item.environment.casefold())
                family = _mode_family(item.product)
                if phys in physical and physical[phys][0] != family:
                    raise ValueError("Incompatible product/mode declarations for one physical account")
                physical[phys] = (family, item.route_id)
            if len(self._aliases) + len(items) > self.max_aliases:
                raise ValueError("Explicit max_aliases budget exhausted; no silent eviction")
            for item in items:
                self._aliases[item.key] = item
            self._epoch += 1
        return items

    def alias(self, strategy_id, alias) -> AliasBinding:
        with self._lock:
            try:
                return self._aliases[(strategy_id, alias)]
            except KeyError as error:
                raise KeyError("Unknown alias") from error

    def aliases_for_route(self, route_id):
        with self._lock:
            return tuple(item for item in self._aliases.values() if item.route_id == route_id)

    def unbind_alias(self, strategy_id, alias):
        with self._lock:
            removed = self._aliases.pop((strategy_id, alias), None)
            if removed is None:
                raise KeyError("Unknown alias")
            self._epoch += 1
            return removed

    def unbind_strategy(self, strategy_id):
        with self._lock:
            keys = [key for key in self._aliases if key[0] == strategy_id]
            removed = tuple(self._aliases.pop(key) for key in keys)
            if removed:
                self._epoch += 1
            return removed

    def alias_status(self, available_feeds=()):
        with self._lock:
            return tuple({
                "strategy_id": item.strategy_id,
                "alias": item.alias,
                "route_id": item.route_id,
                "enabled": (self._records[item.route_id].state is RouteState.ENABLED
                            and item.enabled_with(available_feeds)),
                "missing_feeds": item.missing_feeds(available_feeds),
            } for item in self._aliases.values())

    # ----------------------------------------------------------------- invoke
    def issue_permit(self, route_id, source) -> _GuardedPermit:
        with self._lock:
            record = self._record(route_id)
            if record.state is not RouteState.ENABLED:
                raise RouteStateError("Mutation permits require an ENABLED route")
            if not callable(source) or inspect.iscoroutinefunction(source) or inspect.iscoroutinefunction(
                    getattr(source, "__call__", None)):
                raise TypeError("Owner permit must be synchronous and callable")
            return _GuardedPermit(self, route_id, record.epoch, source)

    async def invoke(self, route_id, operation, *args, permit=None, **kwargs):
        if operation not in SUPPORTED_OPERATIONS:
            raise UnsupportedOperation("Unsupported operation: " + str(operation))
        with self._lock:
            record = self._record(route_id)
            if record.state is RouteState.REMOVED:
                raise RouteStateError("Removed route cannot be invoked")
            binding = record.binding
            if operation not in binding.operations:
                raise UnsupportedOperation("Operation is unsupported by this binding: " + operation)
            mutation = operation in MUTATION_OPERATIONS
            action = _action_kind(operation, args, kwargs)
            if mutation:
                if action in ("NEW", "REPLACE") and record.state is not RouteState.ENABLED:
                    raise RouteStateError("NEW/REPLACE requires an ENABLED route")
                if action == "CANCEL":
                    target = _cancel_target(args, kwargs)
                    if target is None or target not in record.owned:
                        raise ForeignOrderError("Only originally owned orders can be cancelled")
                    candidate = _extract_candidate(args, kwargs)
                    strategy = kwargs.get("strategy_id", _field(candidate, "strategy_id"))
                    metadata = record.owned_meta.get(target)
                    if metadata is not None and metadata[0] is not None and strategy != metadata[0]:
                        raise ForeignOrderError("Order belongs to another strategy")
                _validate_request(binding, operation, action, args, kwargs)
                _require_sync_permit(permit)
                if operation == "submit":
                    identities = _identity_candidates(args, kwargs)
                    if not identities:
                        raise ValueError("Submit requires a bounded immutable order identity")
                    if any(len(value.encode("utf-8")) > MAX_IDENTITY_BYTES for value in identities):
                        raise ValueError("Order identity exceeds its explicit byte bound")
                    if identities & record.owned:
                        raise ForeignOrderError("Order identity is immutable and already owned")
                    candidate = _extract_candidate(args, kwargs)
                    primary = _field(candidate, "client_order_id")
                    if not isinstance(primary, str):
                        raise ValueError("Submit requires an explicit bounded client_order_id")
                    if primary in record.reserved_owned:
                        raise ForeignOrderError("Order identity is already reserved in flight")
                    if len(record.owned_primary) + len(record.reserved_owned) >= self.max_owned_orders:
                        raise ValueError("Explicit max_owned_orders budget exhausted before dispatch")
                    record.reserved_owned.add(primary)
            else:
                _validate_request(binding, operation, action, args, kwargs)
                if operation == "query_order" and record.state in (RouteState.DISABLED, RouteState.QUARANTINED):
                    target = _query_target(args, kwargs)
                    if target is not None and target not in record.owned:
                        raise ForeignOrderError("Only originally owned orders can be queried after disable")
            epoch = record.epoch
            record.in_flight += 1
        guarded = _GuardedPermit(self, route_id, epoch, permit, operation) if mutation else None
        try:
            fn = binding.operations[operation]
            if mutation:
                result = fn(*args, permit=guarded, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                if not guarded.consumed:
                    raise AdapterProtocolError("Adapter returned without consuming the one-use permit")
            else:
                result = fn(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
            with self._lock:
                if self._records[route_id].state is RouteState.REMOVED:
                    raise RouteStateError("Route was removed while the adapter call was in flight")
                if operation == "submit":
                    self._remember_owned(record, args, kwargs, result)
            return result
        except BaseException as error:
            if mutation and operation == "submit" and guarded is not None and guarded.dispatch_started:
                with self._lock:
                    self._remember_owned(record, args, kwargs, None)
            if mutation and guarded is not None and guarded.consumed:
                # A permit was consumed at the seam, so the outcome may have
                # reached the venue.  Never relabel it as a definitive rejection.
                if isinstance(error, (PermitError, AdapterProtocolError)):
                    raise
                raise AdapterProtocolError("Adapter invocation after permit is not a definitive rejection") from error
            raise
        finally:
            with self._lock:
                if mutation and operation == "submit" and guarded is not None and not guarded.dispatch_started:
                    record.reserved_owned.discard(_primary_identity(args, kwargs))
                self._records[route_id].in_flight = max(0, self._records[route_id].in_flight - 1)

    def _remember_owned(self, record, args, kwargs, result):
        candidate = _extract_candidate(args, kwargs)
        strategy = _field(candidate, "strategy_id")
        scope = _field(candidate, "account_scope")
        primary = _field(candidate, "client_order_id")
        if isinstance(primary, str):
            record.owned_primary.add(primary)
            record.reserved_owned.discard(primary)
        for value in _identity_candidates(args, kwargs):
            _bounded(value, "owned order identity", MAX_IDENTITY_BYTES)
            record.owned.add(value)
            record.owned_meta[value] = (strategy, scope.key if isinstance(scope, AccountScope) else None)
        record.reserved_owned.difference_update(_identity_candidates(args, kwargs))
        if isinstance(result, Mapping):
            for key in ("client_order_id", "order_id", "target_order_id"):
                value = result.get(key)
                if value is not None:
                    _bounded(value, "adapter returned order identity", MAX_IDENTITY_BYTES)
                if isinstance(value, str) and value:
                    if value in record.owned and value not in _identity_candidates(args, kwargs):
                        raise AdapterProtocolError("Adapter returned an identity already owned by another order")
                    record.owned.add(value)
                    record.owned_meta.setdefault(value, (strategy, scope.key if isinstance(scope, AccountScope) else None))
                    record.reserved_owned.discard(value)

    # ---------------------------------------------------------------- removal
    def remove(self, route_id):
        with self._lock:
            record = self._record(route_id)
            if record.state is RouteState.REMOVED:
                raise RouteStateError("Route is already removed")
            if record.in_flight:
                raise RemovalBlocked("Route has in-flight adapter calls")
            aliases = [item for item in self._aliases.values() if item.route_id == route_id]
            if aliases:
                raise RemovalBlocked("Route still has registered config aliases")
            starting_epoch = self._epoch
            route_epoch = record.epoch
            document = record.binding.reference_provider()
            if inspect.isawaitable(document):
                if inspect.iscoroutine(document):
                    document.close()
                raise RemovalBlocked("Owner reference facts must be synchronous")
            if not isinstance(document, Mapping):
                raise RemovalBlocked("Owner reference facts are missing or unbounded")
            for key in MANDATORY_REMOVAL_FACTS:
                if key not in document:
                    raise RemovalBlocked("Owner reference fact is missing: " + key)
                value = document[key]
                if type(value) is not int or value < 0:
                    raise RemovalBlocked("Owner reference fact is not a nonnegative integer: " + key)
                if value != 0:
                    raise RemovalBlocked("Owner reference fact is nonzero: " + key)
            try:
                encoded = _canonical(dict(document)).encode("utf-8")
            except (TypeError, ValueError) as error:
                raise RemovalBlocked("Owner reference facts are not finite canonical JSON") from error
            if len(encoded) > self.max_fact_bytes:
                raise RemovalBlocked("Owner reference facts exceed max_fact_bytes")
            if (self._epoch != starting_epoch or record.epoch != route_epoch
                    or record.in_flight or any(item.route_id == route_id for item in self._aliases.values())):
                raise RemovalBlocked("Registry changed while trusted owner references were sampled")
            record.state = RouteState.REMOVED
            record.epoch += 1
            self._epoch += 1
            return True


# --------------------------------------------------------------- validation
def _accepts_permit(fn) -> bool:
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "permit" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


def _require_sync_permit(permit):
    if permit is None:
        raise TypeError("Mutation requires a synchronous one-use owner permit")
    if not callable(permit) or inspect.iscoroutinefunction(permit) or inspect.iscoroutinefunction(
            getattr(permit, "__call__", None)):
        raise TypeError("Mutation requires a synchronous one-use owner permit")


def _extract_candidate(args, kwargs):
    for key in ("order", "action", "request"):
        if key in kwargs:
            return kwargs[key]
    return args[0] if args else None


def _action_kind(operation, args, kwargs) -> str:
    candidate = _extract_candidate(args, kwargs)
    if candidate is not None:
        action = getattr(candidate, "action", None)
        if action is not None:
            return getattr(action, "value", str(action))
        if isinstance(candidate, Mapping) and "action" in candidate:
            return str(candidate["action"])
    if operation == "cancel":
        return "CANCEL"
    return "NEW"


def _field(candidate, name, default=None):
    if candidate is None:
        return default
    if isinstance(candidate, Mapping):
        return candidate.get(name, default)
    return getattr(candidate, name, default)


def _validate_request(binding: Binding, operation, action, args, kwargs):
    candidate = _extract_candidate(args, kwargs)
    route, caps = binding.route, binding.capabilities
    if action not in ("NEW", "REPLACE", "CANCEL"):
        raise ValueError("Unsupported order action")
    product = _field(candidate, "product", route.product)
    venue = _field(candidate, "venue", route.venue)
    environment = _field(candidate, "environment", route.environment)
    if product is not None and product != route.product:
        raise ValueError("Order product does not match the registered route")
    if venue is not None and str(venue).casefold() != route.venue.casefold():
        raise ValueError("Order venue does not match the registered route")
    if environment is not None and environment != route.environment:
        raise ValueError("Order environment does not match the registered route")
    if route.product not in caps.products or route.environment not in caps.environments:
        raise ValueError("Route product/environment is not an explicit adapter capability")
    if operation == "submit":
        strategy_id = _field(candidate, "strategy_id")
        _bounded(strategy_id, "strategy_id", 128)
    scope = kwargs.get("account_scope", _field(candidate, "account_scope", None))
    if scope is None:
        scope = kwargs.get("scope", getattr(candidate, "scope", None))
    if scope is not None:
        if isinstance(scope, AccountScope):
            if scope.key != route.account_scope.key:
                raise ValueError("Order account scope does not match the registered route")
        elif isinstance(scope, Mapping):
            try:
                exposed = AccountScope(scope["venue"], scope["account"], scope["product"], scope["environment"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("Presented account scope is malformed") from error
            if exposed.key != route.account_scope.key:
                raise ValueError("Order account scope does not match the registered route")
        else:
            raise ValueError("Presented account scope is malformed")
    if operation != "submit" or not isinstance(candidate, Mapping):
        # ExecutionAction was already validated by trading_contracts.  Read and
        # cancel calls carry no order fields to reinterpret.
        if operation != "submit" or candidate is None or not hasattr(candidate, "intent"):
            return
        tif, post_only, reduce_only = candidate.time_in_force, candidate.post_only, candidate.reduce_only
    else:
        tif = candidate.get("time_in_force", "GTC")
        post_only = candidate.get("post_only", False)
        reduce_only = candidate.get("reduce_only", False)
        for key in ("funding", "funding_enabled", "fund"):
            value = candidate.get(key, None)
            if value not in (None, False, 0, "", "none", "NONE"):
                if not caps.funding:
                    raise ValueError("Funding is unsupported for this route")
    if type(post_only) is not bool or type(reduce_only) is not bool:
        raise ValueError("post_only/reduce_only must be explicit booleans")
    if tif not in caps.time_in_force:
        raise ValueError("Unsupported time_in_force for this route")
    if post_only and not caps.post_only:
        raise ValueError("post_only is unsupported for this route")
    if reduce_only and not caps.reduce_only:
        raise ValueError("reduce_only is unsupported for this route")
    if post_only and tif == "FOK":
        raise ValueError("post_only is incompatible with FOK")


def _cancel_target(args, kwargs):
    for key in ("client_order_id", "order_id", "target_order_id"):
        if key in kwargs and isinstance(kwargs[key], str):
            return kwargs[key]
    candidate = _extract_candidate(args, kwargs)
    if isinstance(candidate, Mapping):
        for key in ("client_order_id", "order_id", "target_order_id"):
            if isinstance(candidate.get(key), str):
                return candidate[key]
    target = getattr(candidate, "target_order_id", None)
    if isinstance(target, str):
        return target
    # Common existing adapter signatures: (symbol, client_order_id[, permit]).
    strings = [value for value in args if isinstance(value, str)]
    return strings[-1] if strings else None


def _query_target(args, kwargs):
    return _cancel_target(args, kwargs)


def _identity_candidates(args, kwargs):
    candidate = _extract_candidate(args, kwargs)
    values = set()
    if isinstance(candidate, Mapping):
        for key in ("client_order_id", "order_id", "target_order_id"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                values.add(value)
    else:
        intent = getattr(candidate, "intent", None)
        for source in (candidate, intent):
            if source is None:
                continue
            for key in ("client_order_id", "order_id", "target_order_id"):
                value = getattr(source, key, None)
                if isinstance(value, str) and value:
                    values.add(value)
        target = getattr(candidate, "target_order_id", None)
        if isinstance(target, str) and target:
            values.add(target)
    for key in ("client_order_id", "order_id", "target_order_id"):
        value = kwargs.get(key)
        if isinstance(value, str) and value:
            values.add(value)
    return values


def _primary_identity(args, kwargs):
    return _field(_extract_candidate(args, kwargs), "client_order_id")


# ------------------------------------------------- existing adapter binding
_PERP_OPERATIONS = {
    "submit": "submit_with_permit",
    "cancel": "cancel_with_permit",
    "query_order": "order_status",
    "list_open_orders": "working_orders",
    "get_fills": "fills",
    "account_state": "account_state",
    "positions": "positions",
    "reconcile": "reconcile",
    "instrument_spec": "instrument_metadata",
    "private_or_poll": "normalize_private_event",
}
_KIS_OPERATIONS = {
    "submit": "submit_order_with_permit",
    "query_order": "order_status",
    "get_fills": "daily_fills",
    "account_state": "account_state",
}
_SPOT_OPERATIONS = {
    "query_order": "order_status",
    "account_state": "account_state",
}
_KIWOOM_OPERATIONS = {
    "account_state": "account_state",
}

EXISTING_ADAPTER_MAPS = {
    "execution.perp_execution.BinancePerpAdapter": (_PERP_OPERATIONS, ()),
    "execution.perp_execution.BybitPerpAdapter": (_PERP_OPERATIONS, ()),
    "execution.perp_execution.GrvtPerpAdapter": (_PERP_OPERATIONS, ()),
    "execution.perp_execution.ArcusPerpAdapter": (_PERP_OPERATIONS, ()),
    "execution.perp_execution.HyperliquidPerpAdapter": (_PERP_OPERATIONS, ()),
    "execution.kis_execution.KisCashBroker": (_KIS_OPERATIONS, (
        "cancel_order has no synchronous permit seam and is unsupported",
        "list_open_orders, positions, reconcile, instrument_spec and private_or_poll are absent",
    )),
    "execution.brokers.KisBrokerAdapter": (_KIS_OPERATIONS, (
        "cancel_order has no synchronous permit seam and is unsupported",
        "list_open_orders, positions, reconcile, instrument_spec and private_or_poll are absent",
    )),
    "execution.brokers.BinanceBrokerAdapter": (_SPOT_OPERATIONS, (
        "submit_order has no synchronous permit seam and is unsupported",
        "cancel, list_open_orders, get_fills, positions, reconcile, instrument_spec and private_or_poll are absent",
    )),
    "execution.brokers.KiwoomBrokerAdapter": (_KIWOOM_OPERATIONS, (
        "submit_order has no synchronous permit seam and is unsupported",
        "order_status returns NOT_IMPLEMENTED and is unsupported",
        "cancel, list_open_orders, get_fills, positions, reconcile, instrument_spec and private_or_poll are absent",
    )),
}


def existing_adapter_contract(adapter):
    """Return (canonical_owner, proven operation methods, explicit gaps).

    Only exact proven classes are recognized.  No module is imported here and
    no missing method is replaced by a placeholder success.
    """
    owner = type(adapter).__module__ + "." + type(adapter).__qualname__
    selected = EXISTING_ADAPTER_MAPS.get(owner)
    if selected is None:
        raise ValueError("No proven existing adapter mapping for " + owner)
    template, template_gaps = selected
    operations = {}
    gaps = list(template_gaps)
    for operation, method_name in template.items():
        method = getattr(adapter, method_name, None)
        if callable(method):
            operations[operation] = method
        else:
            gaps.append("Missing proven method for " + operation + ": " + method_name)
    return owner, operations, tuple(gaps)


def content_digest(value) -> str:
    """Bounded helper used by tests/integrations without importing a client."""
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
