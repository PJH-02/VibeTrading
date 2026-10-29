"""Local fake-adapter execution owner. Existing live adapters cannot opt in.

The permit callback is synchronous and one-use at the mutation seam. This is a
testable handoff contract, not permission to submit to a production exchange.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import math
import inspect
import json
import sqlite3
import time
from typing import Callable
from datetime import datetime, timezone

from .execution_ledger import (ExecutionLedger, canonical, exact_difference,
                               exact_product, exact_sum, positive_decimal)
from .trading_contracts import (ActionKind, ExecutionAction, StrategyActionPlan,
                                StrategyOrderContext, StrategyOrderEvent, LiveExecutionAuthorization,
                                validate_native_account_facts)
from .venues.registry import (
    AdapterProtocolError, ForeignOrderError, RouteStateError, UnsupportedOperation,
    cancel_arguments, query_arguments, submit_candidate,
)
from .perp_execution import VenueRejected
from core.models import AssetClass, OrderDestination, OrderIntent, Side
from runtime.runtime_control import GateBlocked, RuntimeControl


MAX_JOURNAL_CASH_FACT_ROWS = 4096
MAX_JOURNAL_CASH_FACT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ExecutionBudgets:
    max_order_notional: Decimal
    max_account_notional: Decimal
    max_pending: int
    max_ttl_ns: int
    prepare_timeout_seconds: float
    submit_timeout_seconds: float

    def __post_init__(self):
        object.__setattr__(self, "max_order_notional", positive_decimal(self.max_order_notional, "max order notional"))
        object.__setattr__(self, "max_account_notional", positive_decimal(self.max_account_notional, "max account notional"))
        for number in (self.max_pending, self.max_ttl_ns):
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                raise ValueError("Execution counts/TTL must be explicit positive integers")
        for number in (self.prepare_timeout_seconds, self.submit_timeout_seconds):
            if not math.isfinite(number) or number <= 0:
                raise ValueError("Execution timeouts must be positive and finite")


@dataclass(frozen=True)
class ExecutionReadiness:
    scope_key: str
    account_verified: bool
    risk_verified: bool
    feed_ready: bool
    lease_until_ns: int
    position_notional: Decimal
    available_notional: Decimal
    # These are explicit facts from the trusted account/risk reconciliation
    # boundary, never defaults inferred from a signal or a product alias.
    account_version: int | None = None
    allowed_instruments: tuple[str, ...] = ()
    market_price_caps: tuple[tuple[str, Decimal], ...] = ()


class _OwnerAsyncLock:
    """Task-reentrant lock for source plans dispatched from owner callbacks."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._owner = None
        self._depth = 0

    async def __aenter__(self):
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Execution owner requires an asyncio task")
        if self._owner is task:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner, self._depth = task, 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._owner is not asyncio.current_task() or self._depth <= 0:
            raise RuntimeError("Execution owner lock ownership was lost")
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()
        return False


class _SubmitPermit:
    def __init__(self, owner, intent, expiry_ns, epochs, reserve_unit, readiness, guard):
        self.owner, self.intent, self.expiry_ns = owner, intent, expiry_ns
        self.epochs, self.reserve_unit = epochs, reserve_unit
        self.readiness, self.guard, self.used = readiness, guard, False

    def __call__(self):
        if self.used:
            raise GateBlocked("Submit permit is single-use")
        self.owner._validate(self.intent, self.expiry_ns, self.reserve_unit,
                             self.readiness, self.guard, self.epochs, already_reserved=True)
        try:
            self.owner.ledger.mark_handoff(self.intent.intent_id)
        except BaseException:
            self.owner.control.fail_closed()
            raise
        self.used = True


class _PreparedAction:
    """Opaque, one-use owner handoff between durable prepare and transport send."""

    def __init__(self, owner, action, registry, readiness, evidence, reserve_units, guard,
                 on_event, route_id, binding, intent, epochs, native_facts, reserve_unit,
                 candidate, attempt_id, duplicate=None):
        self.owner = owner
        self.action = action
        self.registry, self.readiness, self.evidence = registry, readiness, evidence
        self.reserve_units, self.guard, self.on_event = reserve_units, guard, on_event
        self.route_id, self.binding, self.intent = route_id, binding, intent
        self.epochs, self.native_facts = epochs, native_facts
        self.reserve_unit, self.candidate = reserve_unit, candidate
        self.attempt_id, self.duplicate = attempt_id, duplicate
        self.group_permit = None
        self.used = False
        self._identity = object()
        self.admission_held = True


class ExecutionOwner:
    def __init__(self, ledger: ExecutionLedger, control: RuntimeControl, adapter, budgets: ExecutionBudgets,
                 *, clock: Callable[[], int] = time.time_ns,
                 live_authorization: LiveExecutionAuthorization | None = None,
                 live_activation_guard: Callable[[], bool] | None = None):
        if control.ledger is not ledger:
            raise ValueError("Owner/control must share one ledger writer")
        self.ledger, self.control, self.adapter, self.budgets = ledger, control, adapter, budgets
        self.clock = clock
        if live_authorization is not None and not isinstance(live_authorization, LiveExecutionAuthorization):
            raise TypeError("LIVE authorization must use the explicit scoped authorization contract")
        if live_activation_guard is not None and not callable(live_activation_guard):
            raise TypeError("LIVE activation guard must be an explicit trusted injected provider")
        self.live_authorization = live_authorization
        self.live_activation_guard = live_activation_guard
        self._lock = _OwnerAsyncLock()
        self._pending = 0
        self._connected = False
        self._closing = False
        self._terminal_proofs = {}
        self._source_dispatch = {}
        self._prepared_inventory = {}
        self._restored_registry_rows = set()

    @staticmethod
    def _strategy(intent: OrderIntent) -> str:
        return intent.metadata.get("strategy_id", intent.job_id)

    def supports_offline_handoff(self) -> bool:
        declared = type(self.adapter).__dict__
        return (self.ledger.scope.environment == "offline"
                and declared.get("execution_handoff_protocol") == "offline-permit-v1"
                and declared.get("offline_only") is True
                and callable(declared.get("submit_order_with_permit")))

    def _readiness(self, provider: Callable[[], ExecutionReadiness]) -> ExecutionReadiness:
        state = provider()
        if inspect.iscoroutine(state): state.close()
        now = self.clock()
        if (not isinstance(state, ExecutionReadiness) or state.scope_key != self.ledger.scope.key
                or state.account_verified is not True or state.risk_verified is not True
                or state.feed_ready is not True or type(state.lease_until_ns) is not int
                or type(now) is not int or not 0 <= now < state.lease_until_ns <= 2**63-1):
            raise GateBlocked("Verified account/risk/feed readiness is missing or expired")
        positive_decimal(state.position_notional, "position notional", zero=True)
        positive_decimal(state.available_notional, "available notional", zero=True)
        if (isinstance(state.account_version, bool) or not isinstance(state.account_version, int)
                or state.account_version != self.ledger.account_version()):
            raise GateBlocked("Readiness account version is missing or stale")
        if (not isinstance(state.allowed_instruments, tuple) or not state.allowed_instruments
                or len(state.allowed_instruments) > 256
                or any(not isinstance(key, str) or not key or len(key) > 512 for key in state.allowed_instruments)
                or len(set(state.allowed_instruments)) != len(state.allowed_instruments)):
            raise GateBlocked("An explicit bounded instrument scope is required")
        if not isinstance(state.market_price_caps, tuple) or len(state.market_price_caps) > 256:
            raise GateBlocked("Market price caps must be an explicit bounded immutable snapshot")
        seen = set()
        for entry in state.market_price_caps:
            if (not isinstance(entry, tuple) or len(entry) != 2 or not isinstance(entry[0], str)
                    or entry[0] not in state.allowed_instruments or entry[0] in seen):
                raise GateBlocked("Market price caps must match the explicit instrument scope")
            try:
                positive_decimal(entry[1], "market price cap")
            except (ValueError, ArithmeticError) as error:
                raise GateBlocked("Market price cap must be a finite positive exact value") from error
            seen.add(entry[0])
        return state

    def _validate(self, intent, expiry_ns, reserve_unit, readiness, guard, epochs, *, already_reserved=False):
        if self._closing or not self.supports_offline_handoff():
            raise GateBlocked("Only an explicit offline fake mutation-permit adapter is enabled")
        if intent.instrument.venue.value != self.ledger.scope.venue:
            raise GateBlocked("Intent venue differs from account scope")
        if intent.destination != OrderDestination.REAL:
            raise GateBlocked("Generic owner supports only the REAL intent destination")
        if intent.side not in (Side.BUY, Side.SELL):
            raise GateBlocked("Unsupported side")
        if intent.instrument.asset_class not in {AssetClass.EQUITY, AssetClass.ETF, AssetClass.CRYPTO_SPOT}:
            raise GateBlocked("Only cash/spot economics are supported; derivative risk is unverified")
        for key, expected in (("account_id", self.ledger.scope.account), ("environment", self.ledger.scope.environment)):
            if intent.metadata.get(key, expected) != expected:
                raise GateBlocked("Intent account/environment differs from owner")
        now = self.clock()
        if (isinstance(expiry_ns, bool) or not isinstance(expiry_ns, int)
                or expiry_ns <= now or expiry_ns - now > self.budgets.max_ttl_ns):
            raise GateBlocked("Intent TTL is expired or outside the explicit budget")
        self.control.validate(self._strategy(intent), epochs)
        if guard:
            failures = guard()
            if failures:
                raise GateBlocked("External execution guard: " + ",".join(failures))
        state = self._readiness(readiness)
        if intent.instrument.key not in state.allowed_instruments:
            raise GateBlocked("Intent is outside the verified account instrument scope")
        unit = positive_decimal(reserve_unit, "reservation unit")
        if intent.limit_price is not None:
            if unit < positive_decimal(intent.limit_price, "limit price"):
                raise GateBlocked("Order reservation unit is below its limit price")
        else:
            caps = dict(state.market_price_caps)
            cap = caps.get(intent.instrument.key)
            if cap is None:
                raise GateBlocked("A verified market price cap is required")
            if unit < positive_decimal(cap, "market price cap"):
                raise GateBlocked("Order reservation unit is below its verified market price cap")
        notional = exact_product(positive_decimal(intent.quantity, "quantity"), unit)
        if notional > self.budgets.max_order_notional:
            raise GateBlocked("Order notional exceeds configured limit")
        total = exact_sum(self.ledger.reserved(), Decimal(0) if already_reserved else notional)
        if exact_sum(Decimal(str(state.position_notional)), total) > self.budgets.max_account_notional or total > Decimal(str(state.available_notional)):
            raise GateBlocked("Worst-case reservation exceeds verified account capacity")

    def _evidence(self, intent, evidence, environment):
        if not isinstance(evidence, dict):
            raise GateBlocked("Explicit DecisionEvidence is required")
        for key in ("contract_version", "config_version", "engine_incarnation"):
            if not isinstance(evidence.get(key), str) or not evidence[key] or len(evidence[key]) > 128:
                raise GateBlocked("DecisionEvidence is incomplete")
        if evidence.get("mode") != environment or evidence.get("quality") != "LIVE":
            raise GateBlocked("DecisionEvidence mode/quality does not authorize new risk")
        epochs = evidence.get("expected_epochs")
        keys = {"global", "account:" + self.ledger.scope.account, "strategy:" + self._strategy(intent)}
        if (not isinstance(epochs, dict) or set(epochs) != keys
                or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in epochs.values())):
            raise GateBlocked("DecisionEvidence must preserve the producer's original epoch vector")
        sequence = evidence.get("apply_seq")
        ids, versions = evidence.get("event_ids"), evidence.get("input_versions")
        if (isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0
                or not isinstance(ids, list) or not ids or len(ids) > 256
                or any(not isinstance(x, str) or not x or len(x) > 512 for x in ids)
                or not isinstance(versions, dict) or not versions or len(versions) > 256
                or any(not isinstance(k, str) or not k or not isinstance(v, int) or isinstance(v, bool) or v < 0 for k, v in versions.items())):
            raise GateBlocked("DecisionEvidence cursor/input versions are incomplete")

    def resume(self, strategy, *, expected_epochs, readiness, scope='strategy'):
        if not self.supports_offline_handoff():
            raise GateBlocked("Production activation is unavailable in this implementation")
        self._readiness(readiness)
        return self.control.resume_if_ready(strategy, expected_epochs=expected_epochs, readiness_verified=True,scope=scope)

    def apply_command(self, request: dict, *, readiness) -> dict:
        if self._closing or not self.supports_offline_handoff():
            raise GateBlocked('Production activation is unavailable in this implementation')
        if type(request) is not dict or set(request) != {
                'command_id','action','strategy','scope','expires_ns','expected_epochs'}:
            raise ValueError('Invalid owner command fields')
        def verify():
            self._readiness(readiness)
            return True
        return self.control.apply_command(**request, readiness_verified=request['action']=='resume',
                                          verify_readiness=verify)

    async def dispatch(self, intent: OrderIntent, *, expiry_ns: int, evidence: dict,
                       reserve_unit: Decimal, readiness: Callable[[], ExecutionReadiness],
                       guard: Callable[[], list[str]] | None = None, legacy_present: bool = False) -> dict:
        if self._pending >= self.budgets.max_pending:
            raise GateBlocked("Execution admission budget exhausted")
        self._pending += 1
        try:
            async with self._lock:
                self._evidence(intent, evidence, self.ledger.scope.environment)
                # Detach nested caller input before any preparation await.
                intent = OrderIntent.model_validate_json(intent.model_dump_json())
                evidence = json.loads(canonical(evidence))
                old = self.ledger.existing(intent)
                if old:
                    if old["evidence"] != canonical(evidence) or old["expiry_ns"] != expiry_ns:
                        raise GateBlocked("Intent identity conflicts with its original evidence/expiry")
                    return {"status": old["state"], "intent_id": intent.intent_id, "duplicate": True}
                epochs = dict(evidence["expected_epochs"])
                self._validate(intent, expiry_ns, reserve_unit, readiness, guard, epochs)
                try:
                    _, row = self.ledger.authorize(intent, evidence=evidence, expiry_ns=expiry_ns,
                        epochs=epochs, reserve_unit=reserve_unit, legacy=legacy_present)
                except BaseException:
                    self.control.fail_closed()
                    raise
                if legacy_present:
                    self.control.halt(self._strategy(intent))
                    return {"status": row["state"], "intent_id": intent.intent_id, "duplicate": True}
                permit = _SubmitPermit(self, intent, expiry_ns, epochs, reserve_unit, readiness, guard)
                try:
                    if not self._connected:
                        await asyncio.wait_for(self.adapter.connect(), self.budgets.prepare_timeout_seconds)
                        self._connected = True
                    self._validate(intent, expiry_ns, reserve_unit, readiness, guard, epochs, already_reserved=True)
                    response = await asyncio.wait_for(self.adapter.submit_order_with_permit(intent, permit),
                                                      self.budgets.submit_timeout_seconds)
                    if not permit.used:
                        raise GateBlocked("Adapter returned without the required mutation permit")
                    state = ("SUBMITTED" if isinstance(response, dict) and response.get("status") == "ACCEPTED"
                             and response.get("order_id") else "UNKNOWN")
                    self.ledger.outcome(intent.intent_id, state, response if isinstance(response, dict) else {})
                    if state == "UNKNOWN":
                        self.control.halt(self._strategy(intent))
                    return {"status": self.ledger.get(intent.intent_id)["state"], "intent_id": intent.intent_id}
                except BaseException as error:
                    state = "UNKNOWN" if permit.used else ("BLOCKED" if isinstance(error, GateBlocked) else "PRE_SEND_FAILED")
                    try:
                        self.ledger.outcome(intent.intent_id, state, {"error_type": type(error).__name__})
                        if permit.used:
                            self.control.halt(self._strategy(intent))
                    except BaseException:
                        self.control.fail_closed()
                        raise
                    if not isinstance(error, Exception):
                        raise
                    return {"status": state, "intent_id": intent.intent_id, "reason": type(error).__name__}
        except (sqlite3.Error, OSError):
            self.control.fail_closed()
            raise
        finally:
            self._pending -= 1

    # ------------------------------------------------ strict canonical routes
    def _aliases(self, registry, strategy_id):
        method = getattr(registry, "aliases_for_strategy", None)
        if method is None:
            raise GateBlocked("Registry cannot resolve explicit strategy aliases")
        return tuple(method(strategy_id))

    def _resolve_new_alias(self, registry, action: ExecutionAction):
        intent = action.intent
        symbols = {intent.instrument.raw_symbol.casefold(), intent.instrument.std_symbol.casefold()}
        matches = [item for item in self._aliases(registry, action.strategy_id)
                   if item.symbol.casefold() in symbols]
        if len(matches) != 1:
            raise GateBlocked("Exactly one explicit strategy alias must resolve this action")
        alias = matches[0]
        route = registry.route(alias.route_id)
        instrument_venue = intent.instrument.venue.value.casefold()
        explicit_execution_venue = intent.metadata.get("execution_venue_id")
        if (route.account_scope.key != action.scope.key
                or route.venue.casefold() != action.scope.venue.casefold()
                or route.environment != action.scope.environment
                or (instrument_venue != route.venue.casefold()
                    and explicit_execution_venue != route.venue)):
            raise GateBlocked("Alias route differs from the explicit instrument/account execution binding")
        return alias.route_id

    def _resolve_cancel_alias(self, registry, action: ExecutionAction):
        row = self.ledger.find(action.target_order_id)
        if row is None:
            raise GateBlocked("Cancel target is not an owned durable order")
        if row["strategy_id"] and row["strategy_id"] != action.strategy_id:
            raise ForeignOrderError("Order belongs to another strategy")
        route_id = row["route_id"]
        if not route_id:
            raise GateBlocked("Original durable order has no route identity")
        route = registry.route(route_id)
        if route.account_scope.key != action.scope.key:
            raise ForeignOrderError("Cancel scope differs from the original order account")
        return route_id, row

    def _restore_registry_owned(self, registry):
        """Rebuild exact adapter ownership handles from this account ledger."""
        restore = getattr(registry, "restore_owned", None)
        if not callable(restore):
            raise GateBlocked("Registry cannot restore native ownership after restart")
        grouped, markers_by_route = {}, {}
        for row in self.ledger.owned():
            route_id = row.get("route_id")
            if not isinstance(route_id, str) or not route_id:
                continue
            marker = (id(registry), route_id, row["intent_id"])
            if marker in self._restored_registry_rows:
                continue
            intent = OrderIntent.model_validate_json(row["payload"])
            if (intent.metadata.get("account_id", self.ledger.scope.account) != self.ledger.scope.account
                    or (intent.instrument.venue.value.casefold() != self.ledger.scope.venue.casefold()
                        and intent.metadata.get("execution_venue_id") != self.ledger.scope.venue)):
                raise GateBlocked("Durable native ownership row differs from the child account scope")
            grouped.setdefault(route_id, []).append({
                "identities": [identity for identity in
                               (row["client_order_id"], row.get("target_order_id"))
                               if isinstance(identity, str) and identity],
                "strategy_id": row["strategy_id"], "account_scope": self.ledger.scope,
            })
            markers_by_route.setdefault(route_id, []).append(marker)
        for route_id, rows in grouped.items():
            restore(route_id, rows)
            self._restored_registry_rows.update(markers_by_route[route_id])

    def _binding(self, registry, route_id, action):
        binding = registry.binding(route_id)
        route = binding.route
        if self.ledger.scope.key != route.account_scope.key:
            raise GateBlocked("Owner ledger belongs to a different physical account")
        if route.environment == "paper":
            raise GateBlocked("PAPER uses the local matcher and never an external mutation client")
        if route.environment == "live":
            self._check_live_activation(action, route)
        if not route.native_account_id or route.native_account_id != self.ledger.native_account_id:
            raise GateBlocked("Route lacks the independently bound physical native account identity")
        return binding

    def _check_live_activation(self, action, route):
        auth = self.live_authorization
        now = self.clock()
        if (not isinstance(auth, LiveExecutionAuthorization)
                or auth.strategy_id != action.strategy_id
                or auth.config_version != action.config_version
                or auth.scope_key != route.account_scope.key
                or auth.native_account_id != route.native_account_id
                or auth.issued_ns > now or now >= auth.expires_ns
                or (getattr(action, "action", None) is not ActionKind.CANCEL
                    and not callable(self.live_activation_guard))):
            raise GateBlocked("Separately injected exact scoped LIVE authorization is missing or expired")
        # Fresh account-promotion proof governs new risk. It must not strand an
        # already-owned order from its scoped cancellation path.
        if getattr(action, "action", None) is ActionKind.CANCEL:
            return
        try:
            valid = self.live_activation_guard()
        except Exception as error:
            raise GateBlocked("LIVE activation state is unavailable") from error
        if valid is not True:
            raise GateBlocked("LIVE environment/account-arm/native promotion proof is not current")

    def _native_facts(self, binding, action):
        provider = binding.native_facts_provider
        if provider is None:
            raise GateBlocked("Native account/product facts provider is unavailable")
        facts = provider(action.intent.instrument.raw_symbol)
        if inspect.isawaitable(facts):
            if inspect.iscoroutine(facts):
                facts.close()
            raise GateBlocked("Native fact provider must supply a synchronous bounded snapshot at the permit")
        if not isinstance(facts, Mapping):
            raise GateBlocked("Native account/product facts are unavailable")
        account = facts.get("account_facts")
        products = facts.get("product_specs")
        fees = facts.get("taker_fee_rate")
        if isinstance(fees, (bool, float)) or not isinstance(fees, (str, int, Decimal)):
            raise GateBlocked("An immutable exact configured/native taker fee fact is required")
        fee_rate = positive_decimal(fees, "configured taker fee rate", zero=True)
        if fee_rate > 1:
            raise GateBlocked("Configured taker fee rate exceeds its bound")
        if binding.capabilities.synthetic_fixture:
            route = binding.route
            if route.environment not in {"sandbox", "offline"}:
                raise GateBlocked("Synthetic facts are restricted to sandbox/offline routes")
            account_facts = account if isinstance(account, Mapping) else {}
            observed = account_facts.get("observed_ns")
            max_age = facts.get("max_age_ns")
            version = account_facts.get("account_version")
            currency = account_facts.get("currency")
            try:
                positive_decimal(account_facts.get("equity"), "synthetic account equity")
                positive_decimal(account_facts.get("available"), "synthetic available balance", zero=True)
            except (ValueError, ArithmeticError) as error:
                raise GateBlocked("Synthetic account capacity facts are invalid") from error
            if (facts.get("quality") != "SYNTHETIC"
                    or facts.get("fee_model") != "configured_taker_rate"
                    or account_facts.get("scope_key") != route.account_scope.key
                    or account_facts.get("native_account_id") != route.native_account_id
                    or account_facts.get("reconciliation_state") != "RECONCILED"
                    or account_facts.get("unknown") is not False
                    or not isinstance(account_facts.get("raw"), Mapping)
                    or isinstance(version, bool) or not isinstance(version, int)
                    or version != self.ledger.account_version()
                    or not isinstance(currency, str) or not currency or len(currency) > 16
                    or isinstance(observed, bool) or not isinstance(observed, int)
                    or observed > self.clock()
                    or isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0
                    or self.clock() - observed > max_age):
                raise GateBlocked("Bounded synthetic fixture account facts are missing, stale or unversioned")
            row = products.get(action.intent.instrument.raw_symbol) if isinstance(products, Mapping) else None
            if (not isinstance(row, Mapping)
                    or row.get("scope_key") != route.account_scope.key
                    or row.get("venue") != route.venue
                    or row.get("product") != route.product
                    or row.get("environment") != route.environment
                    or row.get("symbol") != action.intent.instrument.raw_symbol
                    or row.get("kind") != "cash"
                    or row.get("settlement_currency") != currency):
                raise GateBlocked("Synthetic fixture product facts do not match the selected route/currency")
            try:
                for field in ("tick_size", "lot_size", "min_notional"):
                    positive_decimal(row.get(field), "synthetic product " + field)
            except (ValueError, ArithmeticError) as error:
                raise GateBlocked("Synthetic product minimums and execution costs are incomplete") from error
            return {"quality": "SYNTHETIC", "multiplier": Decimal(1),
                    "taker_fee_rate": fee_rate, "native": dict(account_facts),
                    "product": dict(row),
                    "physical_positions": account_facts.get("physical_positions")}
        if binding.route.mode_family == "linear":
            result = validate_native_account_facts(
                account, products, getattr(binding.adapter, "scope", None),
                account_version=(account.get("account_version") if isinstance(account, Mapping) else None),
                expected_native_account_id=binding.route.native_account_id,
                now_ns=self.clock(), max_age_ns=facts.get("max_age_ns"))
            if result["quality"] != "VALID":
                raise GateBlocked("Native linear risk facts unavailable: " + str(result["reason"]))
            local_cut = result["facts"].get("local_ledger_version")
            if (local_cut is not None
                    and (type(local_cut) is not int or local_cut != self.ledger.account_version())):
                raise GateBlocked("Native linear account snapshot is stale against local fill version")
            product = result["facts"]["products"].get(action.intent.instrument.raw_symbol)
            if product is None:
                raise GateBlocked("Native product facts do not cover the selected symbol")
            return {"multiplier": product["multiplier"], "taker_fee_rate": fee_rate,
                    "native": result["facts"]}
        # Cash account proof remains factual and does not invent a margin model.
        if not isinstance(account, Mapping) or not isinstance(products, Mapping):
            raise GateBlocked("Native cash account/product facts are unavailable")
        normalized = account.get("normalized")
        identity = account.get("native_identity")
        now, observed, max_age = self.clock(), account.get("observed_ns"), facts.get("max_age_ns")
        if (not isinstance(normalized, Mapping) or not isinstance(identity, Mapping)
                or normalized.get("scope_key") != binding.route.account_scope.key
                or normalized.get("venue") != binding.route.venue
                or normalized.get("product") != binding.route.product
                or normalized.get("environment") != binding.route.environment
                or not isinstance(normalized.get("raw"), Mapping) or not normalized.get("raw")
                or identity.get("native_account_id") != binding.route.native_account_id
                or identity.get("scope_key") != binding.route.account_scope.key
                or identity.get("source") != "authenticated_native_session"
                or not {"account_read", "trade"}.issubset(set(identity.get("permissions", ())))
                or account.get("reconciliation_state") != "RECONCILED"
                or account.get("unknown") is not False
                or isinstance(observed, bool) or not isinstance(observed, int)
                or isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0
                or observed > now or now - observed > max_age):
            raise GateBlocked("Native cash identity, permissions, reconciliation or freshness proof is missing")
        row = products.get(action.intent.instrument.raw_symbol)
        if (not isinstance(row, Mapping) or row.get("scope_key") != binding.route.account_scope.key
                or row.get("symbol") != action.intent.instrument.raw_symbol
                or row.get("venue") != binding.route.venue or row.get("product") != binding.route.product
                or row.get("environment") != binding.route.environment
                or row.get("kind") != "cash"):
            raise GateBlocked("Native cash product facts do not cover this account/symbol")
        return {"multiplier": Decimal(1), "taker_fee_rate": fee_rate,
                "native": normalized,
                "physical_positions": account.get("physical_positions")}

    def _validate_route(self, action, binding, *, expiry_ns, readiness, guard, epochs,
                        native_facts,
                        already_reserved=False):
        if self._closing:
            raise GateBlocked("Execution owner is closing")
        if not isinstance(action, ExecutionAction):
            raise GateBlocked("Only original StrategyActionPlan actions are accepted")
        route = binding.route
        if route.environment == "live":
            self._check_live_activation(action, route)
        if action.scope.key != route.account_scope.key:
            raise GateBlocked("Action account scope differs from the selected route")
        now = self.clock()
        if (isinstance(expiry_ns, bool) or not isinstance(expiry_ns, int)
                or expiry_ns <= now or expiry_ns - now > self.budgets.max_ttl_ns):
            raise GateBlocked("Action TTL is expired or outside the explicit budget")
        self.control.validate(action.strategy_id, epochs)
        if guard:
            failures = guard()
            if failures:
                raise GateBlocked("External execution guard: " + ",".join(failures))
        state = self._readiness(readiness)
        intent = action.intent
        if intent is None:
            raise GateBlocked("NEW/REPLACE requires an original OrderIntent")
        if intent.destination != OrderDestination.REAL:
            raise GateBlocked("Generic route owner supports only the REAL destination")
        instrument_venue = intent.instrument.venue.value.casefold()
        if (instrument_venue != route.venue.casefold()
                and intent.metadata.get("execution_venue_id") != route.venue):
            raise GateBlocked("Intent venue differs without the exact explicit execution venue binding")
        if intent.side not in (Side.BUY, Side.SELL):
            raise GateBlocked("Unsupported side")
        cash = {"cash", "spot", "crypto_spot", "krx_cash"}
        if route.mode_family == "cash":
            if intent.instrument.asset_class not in {AssetClass.EQUITY, AssetClass.ETF, AssetClass.CRYPTO_SPOT}:
                raise GateBlocked("Cash route requires cash/spot/equity economics")
        elif intent.instrument.asset_class is not AssetClass.CRYPTO_PERP:
            raise GateBlocked("Linear route requires an explicit crypto perpetual instrument")
        for key, expected in (("account_id", action.scope.account),
                              ("environment", action.scope.environment)):
            if intent.metadata.get(key, expected) != expected:
                raise GateBlocked("Intent account/environment differs from the selected route")
        if intent.instrument.key not in state.allowed_instruments:
            raise GateBlocked("Intent is outside the verified account instrument scope")
        unit = positive_decimal(action.intent.metadata.get("reserve_unit_price", 0),
                                "reservation unit") if "reserve_unit_price" in intent.metadata else None
        if unit is None:
            if intent.limit_price is None:
                raise GateBlocked("An explicit strategy reserve unit is required")
            unit = positive_decimal(intent.limit_price, "limit price")
        if intent.limit_price is not None and unit < positive_decimal(intent.limit_price, "limit price"):
            raise GateBlocked("Order reservation unit is below its limit price")
        multiplier = positive_decimal(native_facts["multiplier"], "native multiplier")
        fee_rate = positive_decimal(native_facts["taker_fee_rate"], "configured taker fee rate", zero=True)
        unit = exact_product(exact_product(unit, multiplier), exact_sum(Decimal(1), fee_rate))
        if action.reduce_only:
            caps = binding.capabilities
            if (route.mode_family != "cash" or not caps.owner_enforced_reduce_only
                    or caps.reduce_only):
                raise GateBlocked("Cash reduce-only requires the explicit owner-enforced capability")
            positions = native_facts.get("physical_positions")
            key = intent.instrument.key
            if not isinstance(positions, Mapping):
                raise GateBlocked("Fresh native physical holdings are unavailable for reduce-only")
            physical = positions.get(key, positions.get(intent.instrument.raw_symbol))
            if isinstance(physical, (bool, float)) or not isinstance(physical, (str, int, Decimal)):
                raise GateBlocked("Exact native physical holdings are required for reduce-only")
            physical = positive_decimal(physical, "native physical holdings", zero=True)
            pending = self.ledger.pending_sell_quantity(key)
            strategy_held = self.ledger.strategy_position(action.strategy_id, key)
            strategy_pending = Decimal(0)
            for pending_row in self.ledger.unresolved():
                pending_intent = OrderIntent.model_validate_json(pending_row["payload"])
                if (pending_row["strategy_id"] == action.strategy_id and pending_intent.side == Side.SELL
                        and pending_intent.instrument.key == key):
                    strategy_pending = exact_sum(strategy_pending,
                        exact_difference(pending_intent.quantity, Decimal(pending_row["filled"])))
            if (intent.side != Side.SELL or intent.quantity > exact_difference(strategy_held, strategy_pending)
                    or intent.quantity > exact_difference(physical, pending)):
                raise GateBlocked("Reduce-only quantity exceeds confirmed strategy-unreserved or physical holdings")
        notional = exact_product(positive_decimal(intent.quantity, "quantity"), unit)
        if notional > self.budgets.max_order_notional:
            raise GateBlocked("Order notional exceeds configured limit")
        total = exact_sum(self.ledger.reserved(), Decimal(0) if already_reserved else notional)
        if (exact_sum(Decimal(str(state.position_notional)), total) > self.budgets.max_account_notional
                or total > Decimal(str(state.available_notional))):
            raise GateBlocked("Worst-case reservation exceeds verified account capacity")
        if route.mode_family == "linear":
            native = native_facts.get("native")
            reported = native.get("reported") if isinstance(native, Mapping) else None
            native_available = reported.get("available") if isinstance(reported, Mapping) else None
            if (isinstance(native_available, (bool, float))
                    or not isinstance(native_available, (str, int, Decimal))):
                raise GateBlocked("Verified native linear available balance is missing")
            try:
                native_available = positive_decimal(native_available,
                    "verified native linear available balance", zero=True)
            except (ValueError, ArithmeticError) as error:
                raise GateBlocked("Verified native linear available balance is invalid") from error
            if total > native_available:
                raise GateBlocked("Worst-case reservation exceeds verified native available balance")
        return unit

    @staticmethod
    def _response_order_id(response):
        if not isinstance(response, dict):
            return None
        for key in ("order_id", "native_order_id", "orderId", "ODNO", "odno", "ord_no",
                    "client_order_id", "clientOrderId"):
            value = response.get(key)
            if isinstance(value, str) and value:
                return value
            if type(value) is int and value >= 0:
                return str(value)
        return None

    def _feedback(self, action, *, status, order_id, intent=None, fill=None, provenance,
                  cumulative_filled=None, fact_quality="VALID"):
        intent = intent or action.intent
        quantity = positive_decimal(intent.quantity, "quantity")
        filled = (positive_decimal(cumulative_filled, "cumulative filled quantity", zero=True)
                  if cumulative_filled is not None else
                  (Decimal(str(fill.get("quantity"))) if fill else Decimal(0)))
        event = StrategyOrderEvent(
            event_id=action.action_id + ":" + status + (":" + fill["fill_id"] if fill else ""),
            strategy_id=action.strategy_id, account_scope=action.scope,
            order_id=order_id, status=status, event_ts_ns=None,
            quantity=quantity, filled_quantity=filled,
            remaining_quantity=exact_difference(quantity, filled),
            fill_id=fill["fill_id"] if fill else None,
            fill_quantity=Decimal(str(fill["quantity"])) if fill else None,
            fill_price=Decimal(str(fill["price"])) if fill else None,
            fee=Decimal(str(fill.get("fee", 0))) if fill else None,
            metadata={"provenance": provenance, "config_version": action.config_version,
                      "intent_id": intent.intent_id, "client_order_id": intent.client_order_id,
                      "fact_quality": fact_quality,
                      **({"group_id": action.group_id} if action.group_id is not None else {})},
        )
        return event

    def _record_feedback(self, action, event):
        document = self._feedback_document(event)
        return self.ledger.record_feedback(action.strategy_id, event.event_id, document)

    @staticmethod
    def _feedback_document(event):
        document = {
            "event_id": event.event_id, "strategy_id": event.strategy_id,
            "account_scope": event.account_scope.key, "order_id": event.order_id,
            "status": event.status, "quantity": str(event.quantity),
            "filled_quantity": str(event.filled_quantity),
            "remaining_quantity": str(event.remaining_quantity),
            "fill_id": event.fill_id, "fill_quantity": None if event.fill_quantity is None else str(event.fill_quantity),
            "fill_price": None if event.fill_price is None else str(event.fill_price),
            "fee": None if event.fee is None else str(event.fee),
            "metadata": dict(event.metadata),
        }
        return document

    async def deliver_feedback(self, strategy_id, callback, *, limit=64):
        """Claim durably, then deliver typed immutable facts to the source once."""
        if not callable(callback):
            raise TypeError("A factual feedback callback is required")
        delivered = []
        for row in self.ledger.claim_feedback(strategy_id, limit=limit):
            delivered.append(row)
            raw = row["event"]
            scope_doc = raw.get("account_scope")
            from .execution_ledger import AccountScope
            if isinstance(scope_doc, str):
                try:
                    scope_doc = json.loads(scope_doc)
                except (TypeError, ValueError):
                    continue
            if isinstance(scope_doc, (list, tuple)) and len(scope_doc) == 4:
                scope = AccountScope(*scope_doc)
            elif isinstance(scope_doc, dict):
                scope = AccountScope(**scope_doc)
            else:
                continue
            event = StrategyOrderEvent(
                raw["event_id"], raw["strategy_id"], scope,
                raw["order_id"], raw["status"], raw.get("event_ts_ns"),
                Decimal(raw["quantity"]), Decimal(raw["filled_quantity"]),
                Decimal(raw["remaining_quantity"]), raw.get("fill_id"),
                None if raw.get("fill_quantity") is None else Decimal(raw["fill_quantity"]),
                None if raw.get("fill_price") is None else Decimal(raw["fill_price"]),
                None if raw.get("fee") is None else Decimal(raw["fee"]),
                raw.get("fee_currency"), raw.get("metadata", {}))
            durable = self.ledger.find(raw["order_id"])
            execution = {
                "owner_scope": self.ledger.scope.key, "strategy_id": strategy_id,
                "order_state": None if durable is None else durable["state"],
                "filled_quantity": None if durable is None else durable["filled"],
                "remaining_quantity": None if durable is None else str(exact_difference(
                    Decimal(OrderIntent.model_validate_json(durable["payload"]).quantity),
                    Decimal(durable["filled"]))),
                "strategy_position": None if durable is None else str(self.ledger.strategy_position(
                    strategy_id, OrderIntent.model_validate_json(durable["payload"]).instrument.key)),
                "account_journal_position": None if durable is None else str(self.ledger.position(
                    OrderIntent.model_validate_json(durable["payload"]).instrument.key)),
                "native_physical_position": None,
                "native_physical_position_quality": "UNAVAILABLE",
                "unknown_or_unreconciled": durable is None or durable["state"] in {"UNKNOWN", "LEGACY_UNKNOWN"},
                "account_version": self.ledger.account_version(),
                "fee_provenance": "explicit fill fact; configured fee policy remains separate",
                "broker_fee_amount_received": False,
            }
            quality = raw.get("metadata", {}).get("fact_quality", "VALID")
            context = StrategyOrderContext(None, execution, self.clock(),
                                           (row["seq"], row["event_id"]),
                                           "UNKNOWN" if execution["unknown_or_unreconciled"] else quality)
            try:
                plan = callback(event, context)
                if inspect.isawaitable(plan):
                    plan = await plan
                if plan is not None:
                    dispatch = self._source_dispatch.get(strategy_id)
                    if not isinstance(plan, StrategyActionPlan) or plan.strategy_id != strategy_id or dispatch is None:
                        continue
                    await self.dispatch_plan(plan, registry=dispatch["registry"],
                        readiness=dispatch["readiness"], evidence=dispatch["evidence"],
                        reserve_units=dispatch["reserve_units"], guard=dispatch["guard"],
                        on_event=dispatch["callback"])
            except Exception:
                # A committed fact and durable claim are never relabelled due
                # to source callback or follow-up-plan failure.
                continue
        return tuple(delivered)

    async def dispatch_plan(self, plan: StrategyActionPlan, *, registry, readiness,
                            evidence: dict, reserve_units=None, guard=None,
                            on_event=None) -> dict:
        if not isinstance(plan, StrategyActionPlan):
            raise GateBlocked("Only an original StrategyActionPlan is accepted")
        if plan.group_submit_mode is not None or any(a.group_id is not None for a in plan.actions):
            raise GateBlocked("Grouped plans require the explicit cross-venue group coordinator")
        if type(plan.event_ts_ns) is not int or plan.event_ts_ns > self.clock():
            raise GateBlocked("Plan event time cannot be in the future")
        results = []
        for action in plan.actions:
            if action.strategy_id != plan.strategy_id or action.config_version != plan.config_version:
                raise GateBlocked("Plan action lost its original strategy/config attribution")
            if action.action in (ActionKind.NEW, ActionKind.REPLACE):
                results.append(await self.dispatch_action(
                    action, registry=registry, readiness=readiness, evidence=evidence,
                    reserve_units=reserve_units, guard=guard, on_event=on_event))
            elif action.action is ActionKind.CANCEL:
                results.append(await self.cancel_action(
                    action, registry=registry, readiness=readiness, guard=guard, on_event=on_event))
            else:
                raise GateBlocked("Unsupported plan action")
        return {"plan_id": plan.plan_id, "strategy_id": plan.strategy_id,
                "results": tuple(results)}

    async def prepare_action(self, action: ExecutionAction, *, registry, readiness,
                             evidence: dict, reserve_units=None, guard=None,
                             on_event=None):
        """Durably validate/reserve one ungrouped NEW/REPLACE without sending."""
        if not isinstance(action, ExecutionAction) or action.group_id is not None:
            raise GateBlocked("Grouped NEW requires the explicit group coordinator")
        return await self._prepare_action(action, registry=registry, readiness=readiness,
            evidence=evidence, reserve_units=reserve_units, guard=guard, on_event=on_event)

    async def prepare_group_leg(self, action: ExecutionAction, *, group_id, registry,
                                readiness, evidence: dict, reserve_units=None, guard=None,
                                on_event=None):
        """Coordinator-only prepare seam. Plain dispatch_action/plan stay closed."""
        if (not isinstance(action, ExecutionAction) or not isinstance(group_id, str)
                or not group_id or action.group_id != group_id):
            raise GateBlocked("Prepared group leg differs from its explicit group identity")
        return await self._prepare_action(action, registry=registry, readiness=readiness,
            evidence=evidence, reserve_units=reserve_units, guard=guard, on_event=on_event,
            allow_grouped=True)

    async def _prepare_action(self, action: ExecutionAction, *, registry, readiness,
                              evidence: dict, reserve_units=None, guard=None,
                              on_event=None, allow_grouped=False):
        if self._pending >= self.budgets.max_pending:
            raise GateBlocked("Execution admission budget exhausted")
        self._pending += 1
        try:
            async with self._lock:
                if action.action not in (ActionKind.NEW, ActionKind.REPLACE):
                    raise GateBlocked("prepare_action accepts NEW/REPLACE only")
                if action.action is ActionKind.REPLACE:
                    raise GateBlocked("REPLACE requires a proven adapter replace path")
                if action.group_id is not None and not allow_grouped:
                    raise GateBlocked("Grouped NEW requires the explicit group coordinator")
                intent = action.intent
                route_id = self._resolve_new_alias(registry, action)
                binding = self._binding(registry, route_id, action)
                self._evidence(intent, evidence, action.scope.environment)
                evidence = json.loads(canonical(evidence))
                old = self.ledger.existing(intent)
                duplicate = None
                if old:
                    if old["evidence"] != canonical(evidence) or old["expiry_ns"] != action.expiry_ns:
                        raise GateBlocked("Intent identity conflicts with its original evidence/expiry")
                    duplicate = {"status": old["state"], "intent_id": intent.intent_id,
                                 "duplicate": True, "route_id": route_id}
                else:
                    for row in self.ledger.unresolved():
                        if row["state"] in ("UNKNOWN", "LEGACY_UNKNOWN"):
                            raise GateBlocked("Account UNKNOWN order blocks blind resubmit and new risk")
                epochs = dict(evidence["expected_epochs"])
                unit = None
                if reserve_units is not None:
                    unit = reserve_units.get(intent.intent_id, reserve_units.get("default"))
                if unit is None:
                    unit = intent.metadata.get("reserve_unit_price")
                if unit is None:
                    unit = intent.limit_price
                if unit is None:
                    raise GateBlocked("An explicit strategy reserve unit is required")
                unit = positive_decimal(unit, "reservation unit")
                native_facts = self._native_facts(binding, action)
                reserve_unit = self._validate_route(
                    action, binding, expiry_ns=action.expiry_ns, readiness=readiness,
                    guard=guard, epochs=epochs, native_facts=native_facts)
                attempt_id = action.action_id + ":attempt:1"
                if duplicate is None:
                    try:
                        _, row = self.ledger.authorize(
                            intent, evidence=evidence, expiry_ns=action.expiry_ns, epochs=epochs,
                            reserve_unit=reserve_unit, route_id=route_id,
                            strategy_id=action.strategy_id, config_version=action.config_version,
                            attempt_id=attempt_id, action=action.action.value,
                            contract_multiplier=native_facts["multiplier"])
                    except BaseException:
                        self.control.fail_closed()
                        raise
                candidate = submit_candidate(binding, action, intent)
                prepared = _PreparedAction(self, action, registry, readiness, evidence,
                    reserve_units, guard, on_event, route_id, binding, intent, epochs,
                    native_facts, reserve_unit, candidate, attempt_id, duplicate)
                self._prepared_inventory[id(prepared)] = (prepared, self._prepared_fingerprint(prepared), False)
                return prepared
        except BaseException:
            self._pending -= 1
            raise

    async def dispatch_prepared(self, prepared, *, on_event=None) -> dict:
        """Send one prepared owner action; the token is opaque and single-use."""
        inventory = self._prepared_inventory.get(id(prepared)) if isinstance(prepared, _PreparedAction) else None
        if (inventory is None or inventory[0] is not prepared or prepared.owner is not self
                or prepared.used or inventory[2] or inventory[1] != self._prepared_fingerprint(prepared)):
            raise GateBlocked("Prepared owner handoff is foreign or already used")
        self._prepared_inventory[id(prepared)] = (prepared, inventory[1], True)
        prepared.used = True
        action, intent = prepared.action, prepared.intent
        try:
            if prepared.duplicate is not None:
                return dict(prepared.duplicate)
            async with self._lock:
                sent = {"value": False}

                def source():
                    if prepared.group_permit is not None:
                        prepared.group_permit(prepared)
                    current_native = self._native_facts(prepared.binding, action)
                    self._validate_route(action, prepared.binding, expiry_ns=action.expiry_ns,
                                         readiness=prepared.readiness, guard=prepared.guard,
                                         epochs=prepared.epochs, native_facts=current_native,
                                         already_reserved=True)
                    if (Decimal(str(current_native["multiplier"])) != Decimal(str(prepared.native_facts["multiplier"]))
                            or current_native["taker_fee_rate"] != prepared.native_facts["taker_fee_rate"]):
                        raise GateBlocked("Native product/fee facts changed after durable authorization")
                    self.ledger.mark_handoff(intent.intent_id)
                    sent["value"] = True

                callback = on_event if on_event is not None else prepared.on_event
                try:
                    response = await asyncio.wait_for(
                        prepared.registry.invoke(prepared.route_id, "submit", prepared.candidate,
                            permit=source, owner_strategy_id=action.strategy_id,
                            owner_account_scope=action.scope),
                        self.budgets.submit_timeout_seconds)
                    order_id = self._response_order_id(response)
                    if order_id is None:
                        raise GateBlocked("Adapter ACK lacked an authoritative order identity")
                    self.ledger.bind_native_order(intent.intent_id, order_id)
                    self.ledger.outcome(intent.intent_id, "SUBMITTED",
                                        response if isinstance(response, dict) else {})
                    event = self._feedback(action, status="ACCEPTED", order_id=order_id,
                                           intent=intent,
                                           provenance="SYNTHETIC_ACK" if prepared.native_facts.get("quality") == "SYNTHETIC" else "NATIVE_ACK",
                                           fact_quality=prepared.native_facts.get("quality", "VALID"))
                    self._record_feedback(action, event)
                    if callback is not None:
                        self._source_dispatch[action.strategy_id] = {
                            "registry": prepared.registry, "readiness": prepared.readiness,
                            "evidence": prepared.evidence,
                            "reserve_units": prepared.reserve_units,
                            "guard": prepared.guard, "callback": callback}
                        await self.deliver_feedback(action.strategy_id, callback)
                    return {"status": self.ledger.get(intent.intent_id)["state"],
                            "intent_id": intent.intent_id, "route_id": prepared.route_id,
                            "order_id": order_id}
                except BaseException as error:
                    cause = error
                    explicit_rejection = False
                    root_cause = error
                    while cause is not None:
                        root_cause = cause
                        if isinstance(cause, VenueRejected) or type(cause).__name__ == "VenueRejected":
                            explicit_rejection = True
                            break
                        cause = cause.__cause__
                    state = ("REJECTED" if sent["value"] and explicit_rejection else
                             "UNKNOWN" if sent["value"] else
                             ("BLOCKED" if isinstance(error, (GateBlocked, ValueError,
                                                              UnsupportedOperation,
                                                              ForeignOrderError,
                                                              RouteStateError)) else "PRE_SEND_FAILED"))
                    try:
                        self.ledger.outcome(intent.intent_id, state,
                                            {"error_type": type(root_cause).__name__,
                                             "error_detail": str(root_cause)[:200]})
                        if state == "UNKNOWN":
                            self.control.halt(action.strategy_id)
                        event_status = "UNKNOWN" if state == "UNKNOWN" else "REJECTED"
                        event = self._feedback(action, status=event_status,
                                               order_id=intent.client_order_id, intent=intent,
                                               provenance="NATIVE_UNKNOWN" if state == "UNKNOWN" else "LOCAL_NOT_SENT")
                        self._record_feedback(action, event)
                        if callback is not None:
                            self._source_dispatch[action.strategy_id] = {
                                "registry": prepared.registry, "readiness": prepared.readiness,
                                "evidence": prepared.evidence,
                                "reserve_units": prepared.reserve_units,
                                "guard": prepared.guard, "callback": callback}
                            await self.deliver_feedback(action.strategy_id, callback)
                    except BaseException:
                        self.control.fail_closed()
                        raise
                    if not isinstance(error, Exception):
                        raise
                    return {"status": state, "intent_id": intent.intent_id,
                            "route_id": prepared.route_id, "reason": type(error).__name__,
                            "detail": str(error)[:200]}
        except (sqlite3.Error, OSError):
            self.control.fail_closed()
            raise
        finally:
            if prepared.admission_held:
                prepared.admission_held = False
                self._pending -= 1
            self._prepared_inventory.pop(id(prepared), None)

    def abandon_prepared(self, prepared, *, reason="GROUP_PREFLIGHT_FAILED"):
        """Release a durable admission only while its native send is provably unsent."""
        inventory = self._prepared_inventory.get(id(prepared)) if isinstance(prepared, _PreparedAction) else None
        if (inventory is None or inventory[0] is not prepared or prepared.owner is not self
                or prepared.used or inventory[2] or inventory[1] != self._prepared_fingerprint(prepared)):
            raise GateBlocked("Only an untouched owned admission can be released")
        row = self.ledger.get(prepared.intent.intent_id)
        if row is None or row["state"] not in {"AUTHORIZED", "PREPARED"}:
            raise GateBlocked("Potentially sent reservation cannot be released")
        self.ledger.outcome(prepared.intent.intent_id, "BLOCKED", {"reason": reason})
        self._prepared_inventory[id(prepared)] = (prepared, inventory[1], True)
        if prepared.admission_held:
            prepared.admission_held = False
            self._pending -= 1
        self._prepared_inventory.pop(id(prepared), None)

    @staticmethod
    def _prepared_fingerprint(prepared):
        """Bind an in-memory handoff to every mutation-relevant prepared fact."""
        return canonical({"scope": prepared.action.scope.key, "action": prepared.action.action_id,
            "intent": prepared.intent.intent_id, "fingerprint": prepared.owner.ledger.fingerprint(prepared.intent),
            "route": prepared.route_id, "attempt": prepared.attempt_id,
            "reserve_unit": str(prepared.reserve_unit), "native": prepared.native_facts,
            "epochs": prepared.epochs, "candidate": repr(prepared.candidate)})

    async def dispatch_action(self, action: ExecutionAction, *, registry, readiness,
                              evidence: dict, reserve_units=None, guard=None,
                              on_event=None) -> dict:
        if not isinstance(action, ExecutionAction) or action.group_id is not None:
            raise GateBlocked("Grouped NEW requires the explicit group coordinator")
        prepared = await self.prepare_action(action, registry=registry, readiness=readiness,
            evidence=evidence, reserve_units=reserve_units, guard=guard, on_event=on_event)
        return await self.dispatch_prepared(prepared, on_event=on_event)

    async def cancel_action(self, action: ExecutionAction, *, registry, readiness,
                            guard=None, on_event=None) -> dict:
        if not isinstance(action, ExecutionAction) or action.action is not ActionKind.CANCEL:
            raise GateBlocked("cancel_action requires a CANCEL ExecutionAction")
        async with self._lock:
            route_id, row = self._resolve_cancel_alias(registry, action)
            binding = self._binding(registry, route_id, action)
            self._restore_registry_owned(registry)
            if binding.route.environment == "paper":
                raise GateBlocked("PAPER cancellation uses the local matcher")
            intent = OrderIntent.model_validate_json(row["payload"])
            if action.scope.key != binding.route.account_scope.key:
                raise GateBlocked("Cancel scope differs from the selected route")
            # Durable cancel attempt precedes the native permit.
            self.ledger.outcome(row["intent_id"], "CANCEL_PENDING",
                                {"target": action.target_order_id})
            event = self._feedback(action, status="CANCEL_PENDING", order_id=action.target_order_id,
                                   intent=intent, provenance="LOCAL_CANCEL_REQUEST",
                                   fact_quality="SYNTHETIC" if binding.capabilities.synthetic_fixture else "VALID")
            self._record_feedback(action, event)
            symbol = intent.instrument.raw_symbol
            response_body = json.loads(row["response"] or "{}")
            organization = response_body.get("KRX_FWDG_ORD_ORGNO",
                            response_body.get("krx_fwdg_ord_orgno", ""))
            args, kwargs = cancel_arguments(binding, symbol=symbol,
                                            target_order_id=action.target_order_id,
                                            strategy_id=action.strategy_id, scope=action.scope,
                                            organization=organization)
            sent = {"value": False}

            def source():
                sent["value"] = True

            try:
                response = await asyncio.wait_for(
                    registry.invoke(route_id, "cancel", *args, permit=source,
                                    owner_strategy_id=action.strategy_id,
                                    owner_account_scope=action.scope,
                                    owner_target_order_id=action.target_order_id, **kwargs),
                    self.budgets.submit_timeout_seconds)
                self.ledger.outcome(row["intent_id"], "CANCEL_PENDING",
                                    response if isinstance(response, dict) else {})
                if on_event is not None:
                    await self.deliver_feedback(action.strategy_id, on_event)
                return {"status": "CANCEL_PENDING", "intent_id": row["intent_id"],
                        "route_id": route_id, "acknowledged": True, "terminal": False}
            except (GateBlocked, ForeignOrderError, RouteStateError, UnsupportedOperation):
                raise
            except BaseException as error:
                # A cancel ACK is never terminal; an ambiguous cancel stays pending.
                state = "UNKNOWN" if sent["value"] else "CANCEL_PENDING"
                if state == "UNKNOWN":
                    self.control.halt(action.strategy_id)
                if not isinstance(error, Exception):
                    raise
                return {"status": state, "intent_id": row["intent_id"],
                        "route_id": route_id, "acknowledged": False,
                        "terminal": False, "reason": type(error).__name__}

    async def query_order(self, action: ExecutionAction, *, registry):
        """Read one order through its original registered owner, even if disabled."""
        if not isinstance(action, ExecutionAction) or action.action is not ActionKind.CANCEL:
            raise GateBlocked("Order query requires its original cancel/order identity")
        route_id, row = self._resolve_cancel_alias(registry, action)
        binding = registry.binding(route_id)
        if (binding.route.account_scope.key != action.scope.key
                or binding.route.native_account_id != self.ledger.native_account_id):
            raise ForeignOrderError("Query route differs from the independently owned physical account")
        intent = OrderIntent.model_validate_json(row["payload"])
        if action.strategy_id != row["strategy_id"] or action.config_version != row["config_version"]:
            raise ForeignOrderError("Query action differs from the durable source owner")
        self._restore_registry_owned(registry)
        order_id = row["target_order_id"] or row["client_order_id"]
        day = datetime.fromtimestamp(intent.created_ts_ns / 1_000_000_000,
                                     tz=timezone.utc).strftime("%Y%m%d")
        args, kwargs = query_arguments(binding, symbol=intent.instrument.raw_symbol,
            order_id=order_id, client_order_id=row["client_order_id"], day=day)
        result = await asyncio.wait_for(registry.invoke(
            route_id, "query_order", *args, owner_strategy_id=action.strategy_id,
            owner_account_scope=action.scope, owner_target_order_id=order_id,
            **kwargs), self.budgets.prepare_timeout_seconds)
        if not isinstance(result, Mapping):
            raise GateBlocked("Native order query returned no normalized facts")
        if result.get("account_scope", action.scope.key) != action.scope.key:
            raise ForeignOrderError("Native query response belongs to a different account")
        observed_id = self._response_order_id(result)
        if observed_id not in {order_id, row["client_order_id"], row["target_order_id"]}:
            # A crash between the submit and its durable ACK binding can leave
            # the native venue holding the order under an id the local ledger
            # never learned.  The query is addressed by the exact durable
            # client order id; when the venue row echoes it under the same
            # symbol, binding that native id is the idempotent recovery step.
            if (observed_id and result.get("symbol") == intent.instrument.raw_symbol
                    and result.get("client_order_id") == row["client_order_id"]):
                self.ledger.bind_native_order(row["intent_id"], observed_id)
            else:
                raise GateBlocked("Native order query identity differs from its durable target")
        if result.get("identity_proven") is not True and (
                result.get("symbol") != intent.instrument.raw_symbol or
                result.get("client_order_id") != row["client_order_id"]):
            raise GateBlocked("Native query lacks a scoped exact client/order identity proof")
        status = result.get("normalized_status", result.get("status"))
        raw = result.get("raw") if isinstance(result.get("raw"), Mapping) else result
        total = result.get("quantity", raw.get("origQty", raw.get("qty",
                     raw.get("size", raw.get("originalSize")))))
        filled = result.get("filled_quantity", result.get("cumulative_quantity",
                     raw.get("executedQty", raw.get("cumExecQty", raw.get("filled", 0)))))
        try:
            total, filled = positive_decimal(total, "native original quantity"), positive_decimal(filled, "native filled quantity", zero=True)
        except (TypeError, ValueError, ArithmeticError) as error:
            raise GateBlocked("Native query lacks exact original/fill quantity facts") from error
        if total != Decimal(OrderIntent.model_validate_json(row["payload"]).quantity) or filled > total:
            raise GateBlocked("Native order query quantity differs from the durable order")
        if (isinstance(status, str)
                and status.casefold() in {"canceled", "cancelled", "expired", "rejected"}):
            proof = {"route_id": route_id, "scope": action.scope.key,
                     "strategy_id": action.strategy_id, "intent_id": row["intent_id"],
                     "native_order_id": observed_id, "status": status,
                     "quantity": str(total), "filled": str(filled),
                     "observed_ns": self.clock(), "response": dict(result)}
            self._terminal_proofs[row["intent_id"]] = proof
        result = dict(result)
        result["fact_quality"] = "SYNTHETIC" if binding.capabilities.synthetic_fixture else "VALID"
        return result

    async def reconcile_order(self, action: ExecutionAction, *, registry):
        facts = await self.query_order(action, registry=registry)
        row = self.ledger.find(action.target_order_id)
        if row is not None and row["intent_id"] in self._terminal_proofs:
            self.confirm_cancel(action)
            intent = OrderIntent.model_validate_json(row["payload"])
            event = self._feedback(action, status="CANCELLED", order_id=action.target_order_id,
                                   intent=intent, provenance="NATIVE_TERMINAL_QUERY",
                                   fact_quality=facts.get("fact_quality", "UNKNOWN"))
            self._record_feedback(action, event)
            dispatch = self._source_dispatch.get(action.strategy_id)
            if dispatch is not None:
                await self.deliver_feedback(action.strategy_id, dispatch["callback"])
        return facts

    def confirm_cancel(self, action: ExecutionAction, *, evidence: dict | None = None):
        if not isinstance(action, ExecutionAction) or action.action is not ActionKind.CANCEL:
            raise GateBlocked("Cancel confirmation requires the original CANCEL action")
        row = self.ledger.find(action.target_order_id)
        if row is None:
            raise GateBlocked("Cancel target is not an owned durable order")
        if (row["strategy_id"] != action.strategy_id or row["config_version"] != action.config_version
                or action.scope.key != self.ledger.scope.key):
            raise ForeignOrderError("Cancel confirmation differs from the durable order owner")
        proof = self._terminal_proofs.pop(row["intent_id"], None)
        if proof is None or (evidence is not None and canonical(evidence) != canonical(proof)):
            raise GateBlocked("Terminal cancellation requires a fresh same-owner native query proof")
        self.ledger.outcome(row["intent_id"], "CANCELLED", proof)
        return self.ledger.get(row["intent_id"])

    def apply_fill(self, action: ExecutionAction, *, fill_id: str, quantity, price, fee=None,
                   order_id=None, fact_quality="VALID") -> bool:
        row = self.ledger.find(order_id or action.target_order_id or
                               (action.intent.client_order_id if action.intent else ""))
        if row is None:
            raise GateBlocked("Fill target is not an owned durable order")
        if (not isinstance(action, ExecutionAction) or action.strategy_id != row["strategy_id"]
                or action.config_version != row["config_version"]
                or action.scope.key != self.ledger.scope.key
                or fee is None):
            raise GateBlocked("Fill attribution and explicit native fee must match the durable order")
        intent = OrderIntent.model_validate_json(row["payload"])
        if action.intent is not None and self.ledger.fingerprint(action.intent) != row["fingerprint"]:
            raise GateBlocked("Fill action intent differs from its durable order")
        delta = positive_decimal(quantity, "fill quantity")
        receipt_identity = canonical([
            self.ledger.scope.venue.casefold(), self.ledger.native_account_id,
            self.ledger.scope.product.casefold(), intent.instrument.raw_symbol, fill_id,
        ])
        native_receipt_key = "native:" + hashlib.sha256(
            receipt_identity.encode("utf-8")).hexdigest()
        if self.ledger.fill_receipt_status(native_receipt_key, identity=row["intent_id"],
                                           quantity=quantity, price=price, fee=fee):
            return False
        cumulative = exact_sum(Decimal(row["filled"]), delta)
        event_status = "FILLED" if cumulative == intent.quantity else "PARTIAL_FILL"
        event = self._feedback(action, status=event_status,
                               order_id=order_id or row["client_order_id"], intent=intent,
                               fill={"fill_id": fill_id, "quantity": quantity,
                                     "price": price, "fee": fee},
                               cumulative_filled=cumulative,
                               provenance="SYNTHETIC_FILL" if fact_quality == "SYNTHETIC" else "NATIVE_FILL",
                               fact_quality=fact_quality)
        feedback = (row["strategy_id"], event.event_id, self._feedback_document(event))
        applied = self.ledger.apply_fill_once(row["intent_id"], fill_id, quantity=quantity,
                                              price=price, fee=fee, feedback=feedback,
                                              receipt_key=native_receipt_key)
        if not applied:
            return False
        updated = self.ledger.get(row["intent_id"])
        return True

    @staticmethod
    async def dispatch_journal_intent(*, journal, registry, route_id, intent,
                                      verify, timeout_seconds):
        """Guard an existing EtfJournal PREPARED attempt without a shadow ledger."""
        if (not isinstance(intent, OrderIntent) or not callable(verify)
                or isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("Journal handoff requires an original intent, verifier and bounded timeout")
        binding = registry.binding(route_id)
        if binding.route.environment == "live":
            raise GateBlocked("LIVE journal handoff remains disabled")
        if not binding.route.native_account_id:
            raise GateBlocked("Journal route lacks independently bound physical native identity")
        row = journal.db.execute("SELECT payload,status,account_scope,strategy FROM orders WHERE intent_id=?",
                                 (intent.intent_id,)).fetchone()
        if row is None or journal.dispatch_state(intent.intent_id) != "PREPARED":
            raise GateBlocked("Existing journal intent is not durably PREPARED")
        if (OrderIntent.model_validate_json(row["payload"]).model_dump(mode="json") !=
                intent.model_dump(mode="json") or row["status"] not in {"submitting", "pending"}
                or row["account_scope"] != binding.route.environment
                or not row["strategy"]):
            raise ForeignOrderError("Journal intent/scope differs from its original selected route")

        def journal_facts():
            # Bind the fresh native cash observation to every local journal
            # order fact, including terminal rows that can receive a late fill.
            # This is a local journal version, never a broker account version.
            rows = journal.db.execute(
                "SELECT intent_id,side,status,quantity,filled,notional,reserve,"
                "cancel_requested,cancel_confirmed FROM orders WHERE account_scope=? "
                "ORDER BY intent_id LIMIT ?",
                (row["account_scope"], MAX_JOURNAL_CASH_FACT_ROWS + 1)).fetchall()
            if len(rows) > MAX_JOURNAL_CASH_FACT_ROWS:
                raise GateBlocked("Local journal fact version exceeds its explicit row budget")
            body = [{
                "intent_id": str(item["intent_id"]),
                "side": str(item["side"]),
                "status": str(item["status"]),
                "quantity": int(item["quantity"]),
                "filled": int(item["filled"] or 0),
                "notional": str(item["notional"] or "0"),
                "reserve": str(item["reserve"] or "0"),
                "cancel_requested": int(item["cancel_requested"] or 0),
                "cancel_confirmed": int(item["cancel_confirmed"] or 0),
            } for item in rows]
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False).encode("utf-8")
            if len(encoded) > MAX_JOURNAL_CASH_FACT_BYTES:
                raise GateBlocked("Local journal fact version exceeds its explicit byte budget")
            return body, hashlib.sha256(encoded).hexdigest()

        def verify_order():
            proof = verify()
            observed = proof.get("observed_ns") if isinstance(proof, Mapping) else None
            now_ns = time.time_ns()
            if (not isinstance(proof, Mapping) or proof.get("quality") != "VALID"
                    or proof.get("account_scope") != binding.route.account_scope.key
                    or isinstance(observed, bool) or not isinstance(observed, int)
                    or observed <= 0 or observed > now_ns or now_ns - observed > 5_000_000_000):
                raise GateBlocked("Fresh account-scope-bound native dispatch facts are unavailable")
            if journal.db.execute(
                    "SELECT 1 FROM orders WHERE account_scope=? AND status='unknown' LIMIT 1",
                    (row["account_scope"],)).fetchone():
                raise GateBlocked("Account UNKNOWN order blocks new journal risk")

            if intent.side == Side.BUY:
                if (binding.route.mode_family != "cash"
                        or binding.capabilities.synthetic_fixture):
                    # Native cash routes must use authenticated KIS account facts;
                    # the unrelated generic synthetic route cannot authorize this journal.
                    raise GateBlocked("Journal BUY requires an authenticated native cash route")
                currency = proof.get("currency")
                product_currency = proof.get("product_currency")
                cash = proof.get("orderable_cash")
                if (not isinstance(currency, str) or not currency
                        or currency != product_currency
                        or isinstance(cash, (bool, float))
                        or not isinstance(cash, (str, int, Decimal))):
                    raise GateBlocked("Fresh orderable cash/currency facts are incomplete")
                available_cash = positive_decimal(cash, "native orderable cash", zero=True)
                all_rows, version = journal_facts()
                if proof.get("journal_fact_version") != version:
                    raise GateBlocked("Local journal facts changed after the native cash observation")
                active_buy_statuses = {"submitting", "unknown", "pending", "cancel_pending"}
                reservations = [item for item in all_rows
                                if item["side"] == "buy" and item["status"] in active_buy_statuses]
                if sum(item["intent_id"] == intent.intent_id for item in reservations) != 1:
                    raise GateBlocked("Current staged BUY reserve is missing or duplicated")
                try:
                    reserved_cash = exact_sum(*(positive_decimal(item["reserve"],
                        "journal BUY reserve", zero=True) for item in reservations))
                except (ValueError, ArithmeticError) as error:
                    raise GateBlocked("Journal BUY reserves are invalid") from error
                if reserved_cash > available_cash:
                    raise GateBlocked("Shared account BUY reservations exceed fresh orderable cash")
                return proof

            # A source SELL is always a reduction. Validate against the exact
            # instrument key and exclude this staged row from pre-existing sells;
            # the new quantity is compared separately below.
            if (binding.route.mode_family != "cash"
                    or not binding.capabilities.owner_enforced_reduce_only
                    or binding.capabilities.reduce_only or intent.side != Side.SELL):
                raise GateBlocked("Journal SELL requires explicit owner-enforced cash reduction")
            holdings = proof.get("physical_holdings")
            pending = proof.get("aggregate_pending_sell")
            key = intent.instrument.key
            if not isinstance(holdings, Mapping) or not isinstance(pending, Mapping):
                raise GateBlocked("Fresh native physical holdings proof is incomplete")
            physical = holdings.get(key)
            account_pending = pending.get(key)
            if (isinstance(physical, (bool, float)) or not isinstance(physical, (str, int, Decimal))
                    or isinstance(account_pending, (bool, float))
                    or not isinstance(account_pending, (str, int, Decimal))):
                raise GateBlocked("Native physical holdings quantities must be exact and key-matched")
            physical = positive_decimal(physical, "native physical holdings", zero=True)
            account_pending = positive_decimal(account_pending, "aggregate pending sell", zero=True)
            qty = positive_decimal(intent.quantity, "reduce-only quantity")
            strategy = row["strategy"]
            strategy_position = Decimal(journal.book(strategy)["positions"].get(intent.instrument.raw_symbol, 0))
            rows = journal.db.execute(
                "SELECT intent_id,quantity,filled,status FROM orders "
                "WHERE strategy=? AND account_scope=? AND symbol=? AND side='sell'",
                (strategy, row["account_scope"], intent.instrument.raw_symbol)).fetchall()
            active = {"submitting", "pending", "unknown", "cancel_pending"}
            prior_reservations = []
            for item in rows:
                if item["intent_id"] == intent.intent_id or item["status"] not in active:
                    continue
                remaining = exact_difference(Decimal(item["quantity"]),
                                             Decimal(item["filled"] or 0))
                prior_reservations.append(remaining)
            strategy_pending = exact_sum(*prior_reservations)
            if (qty > exact_difference(strategy_position, strategy_pending)
                    or qty > exact_difference(physical, account_pending)):
                raise GateBlocked("Journal SELL exceeds strategy-unreserved or native physical holdings")
            return proof

        verify_order()
        consumed = {"value": False}

        def permit():
            if consumed["value"]:
                raise GateBlocked("Journal submit permit is single-use")
            verify_order()
            journal.dispatch_handoff(intent.intent_id)
            try:
                verify_order()
            except BaseException as error:
                journal.dispatch_not_sent(intent.intent_id, str(error), before_post=True)
                raise
            consumed["value"] = True

        try:
            result = await asyncio.wait_for(registry.invoke(
                route_id, "submit", intent, permit=permit,
                owner_strategy_id=row["strategy"], owner_account_scope=binding.route.account_scope),
                timeout_seconds)
            if not consumed["value"] or journal.dispatch_state(intent.intent_id) != "HANDOFF":
                raise GateBlocked("Selected adapter returned without consuming the durable journal permit")
            return result
        except BaseException as error:
            state = journal.dispatch_state(intent.intent_id)
            if state == "HANDOFF":
                journal.ambiguous(intent.intent_id)
            elif state == "PREPARED":
                journal.dispatch_not_sent(intent.intent_id, str(error) or type(error).__name__)
            raise

    @staticmethod
    async def dispatch_journal_cancel(*, journal, registry, route_id, order,
                                      verify, timeout_seconds):
        """Guard a cancel through the same existing journal/registered route."""
        if (not isinstance(order, Mapping) or not callable(verify)
                or isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("Journal cancel requires an original order, verifier and bounded timeout")
        binding = registry.binding(route_id)
        if binding.route.environment == "live":
            raise GateBlocked("LIVE journal cancellation remains disabled")
        if not binding.route.native_account_id:
            raise GateBlocked("Journal route lacks independently bound physical native identity")
        identity = order.get("intent_id")
        original = journal.db.execute("SELECT * FROM orders WHERE intent_id=?", (identity,)).fetchone()
        if original is None:
            raise ForeignOrderError("Cancel row is not owned by the existing journal")
        original = dict(original)
        if (order.get("strategy") != original["strategy"]
                or order.get("account_scope") != original["account_scope"]
                or order.get("order_number") != original["order_number"]
                or order.get("organization") != original["organization"]
                or original["account_scope"] != binding.route.environment
                or not original["order_number"] or not original["organization"]):
            raise ForeignOrderError("Cancel identity/strategy/account differs from the durable journal order")
        verify()
        args, kwargs = cancel_arguments(binding, symbol=original["symbol"],
            target_order_id=original["order_number"], strategy_id=original["strategy"],
            scope=binding.route.account_scope, organization=original["organization"])
        consumed = {"value": False}

        def permit():
            if consumed["value"]:
                raise GateBlocked("Journal cancel permit is single-use")
            verify()
            consumed["value"] = True

        result = await asyncio.wait_for(registry.invoke(
            route_id, "cancel", *args, permit=permit,
            owner_strategy_id=original["strategy"],
            owner_account_scope=binding.route.account_scope,
            owner_target_order_id=original["order_number"], **kwargs), timeout_seconds)
        if not consumed["value"]:
            raise GateBlocked("Selected adapter returned without consuming the journal cancel permit")
        return result

    async def close(self):
        self._closing = True
        self.control.fail_closed()
        async with self._lock:
            self.ledger.close()
