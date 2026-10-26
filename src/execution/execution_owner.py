"""Local fake-adapter execution owner. Existing live adapters cannot opt in.

The permit callback is synchronous and one-use at the mutation seam. This is a
testable handoff contract, not permission to submit to a production exchange.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import math
import inspect
import json
import sqlite3
import time
from typing import Callable

from .execution_ledger import ExecutionLedger, canonical, exact_product, exact_sum, positive_decimal
from core.models import AssetClass, OrderDestination, OrderIntent, Side
from vibetrading.runtime_control import GateBlocked, RuntimeControl


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


class ExecutionOwner:
    def __init__(self, ledger: ExecutionLedger, control: RuntimeControl, adapter, budgets: ExecutionBudgets,
                 *, clock: Callable[[], int] = time.time_ns):
        if control.ledger is not ledger:
            raise ValueError("Owner/control must share one ledger writer")
        self.ledger, self.control, self.adapter, self.budgets = ledger, control, adapter, budgets
        self.clock = clock
        self._lock = asyncio.Lock()
        self._pending = 0
        self._connected = False
        self._closing = False

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

    async def close(self):
        self._closing = True
        self.control.fail_closed()
        async with self._lock:
            self.ledger.close()
