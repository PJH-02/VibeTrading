"""Bounded paper-only observations plus an explicit native execution coordinator."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import hashlib
import json
import time
from types import MappingProxyType
from collections.abc import Mapping

from .execution_ledger import (
    AccountScope, ExecutionLedger, canonical, exact_difference, exact_product,
    exact_sum, positive_decimal,
)
from core.models import OrderIntent, Side
from .trading_contracts import (
    ActionKind, ExecutionAction, GroupSubmitMode, ProductSpec, StrategyActionPlan,
    StrategyOrderContext, TradingMode, bounded_identity, validate_scope,
)
from runtime.runtime_control import GateBlocked


@dataclass(frozen=True)
class GroupLeg:
    leg_id: str
    scope: AccountScope
    strategy_id: str
    config_version: str
    intent_id: str
    side: Side
    quantity: Decimal
    rules: ProductSpec
    base_asset: str | None = None
    base_mapping_provenance: str | None = None

    def __post_init__(self):
        validate_scope(self.scope, TradingMode.PAPER)
        for name in ("leg_id", "strategy_id", "config_version", "intent_id"):
            bounded_identity(getattr(self, name), name)
        object.__setattr__(self, "side", Side(self.side))
        if self.side not in {Side.BUY, Side.SELL}:
            raise ValueError("Group leg requires an explicit buy/sell side")
        object.__setattr__(self, "quantity", positive_decimal(self.quantity, "leg quantity"))
        self.rules.supported()
        if self.scope.venue != self.rules.instrument.venue.value:
            raise ValueError("Group leg account and instrument venue differ")
        for name in ("base_asset", "base_mapping_provenance"):
            if getattr(self, name) is not None:
                bounded_identity(getattr(self, name), name)

    def to_document(self):
        return {"leg_id": self.leg_id, "scope": asdict(self.scope), "strategy_id": self.strategy_id,
                "config_version": self.config_version, "intent_id": self.intent_id, "side": self.side.value,
                "quantity": str(self.quantity), "rules": self.rules.to_document(), "base_asset": self.base_asset,
                "base_mapping_provenance": self.base_mapping_provenance}

    @classmethod
    def from_document(cls, document):
        body = dict(document)
        body["scope"], body["rules"] = AccountScope(**body["scope"]), ProductSpec.from_document(body["rules"])
        return cls(**body)


@dataclass(frozen=True)
class ExecutionGroupSpec:
    group_id: str
    strategy_id: str
    config_version: str
    created_ns: int
    deadline_ns: int
    legs: tuple[GroupLeg, ...]
    base_asset: str | None = None
    timeout_plan: tuple[str, ...] = ()
    timeout_plan_provenance: str | None = None

    def __post_init__(self):
        for name in ("group_id", "strategy_id", "config_version"):
            bounded_identity(getattr(self, name), name)
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (self.created_ns, self.deadline_ns)) or not 0 <= self.created_ns < self.deadline_ns <= 2**63-1:
            raise ValueError("Group requires explicit increasing bounded timestamps")
        object.__setattr__(self, "legs", tuple(self.legs))
        object.__setattr__(self, "timeout_plan", tuple(self.timeout_plan))
        if not self.legs or len({leg.leg_id for leg in self.legs}) != len(self.legs):
            raise ValueError("Group requires distinct explicit legs")
        if len({(leg.scope.key, leg.intent_id) for leg in self.legs}) != len(self.legs):
            raise ValueError("An owner intent cannot be counted twice in a group")
        if any(leg.strategy_id != self.strategy_id or leg.config_version != self.config_version for leg in self.legs):
            raise ValueError("Group strategy/config identity differs from a child leg")
        if self.base_asset is not None:
            bounded_identity(self.base_asset, "base_asset")
        if self.timeout_plan:
            bounded_identity(self.timeout_plan_provenance, "timeout_plan_provenance")
            for action in self.timeout_plan:
                bounded_identity(action, "fixed timeout action")

    def to_document(self):
        return {"group_id": self.group_id, "strategy_id": self.strategy_id, "config_version": self.config_version,
                "created_ns": self.created_ns, "deadline_ns": self.deadline_ns,
                "legs": [leg.to_document() for leg in self.legs], "base_asset": self.base_asset,
                "timeout_plan": list(self.timeout_plan), "timeout_plan_provenance": self.timeout_plan_provenance}

    @classmethod
    def from_document(cls, document):
        body = dict(document)
        body["legs"] = tuple(GroupLeg.from_document(leg) for leg in body["legs"])
        return cls(**body)


class PaperExecutionGroups:
    """Plans and observations in one existing ledger, with serial child-owner reads.

    Limits include terminal groups and every historical observation for the run.
    Account reads are not a simultaneous distributed snapshot. This class has
    no submit, retry, hedge, unwind, or reserve-release operation.
    """

    def __init__(self, ledger, owners, *, max_groups: int, max_observations: int, max_legs: int):
        if ledger.mode != TradingMode.PAPER:
            raise ValueError("Execution group coordinator is paper only")
        for value in (max_groups, max_observations, max_legs):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("Execution group lifetime capacities must be explicit positive integers")
        if not owners or len(owners) > max_legs:
            raise ValueError("Execution group owner inventory exceeds its explicit capacity")
        for key, owner in owners.items():
            if owner.mode != TradingMode.PAPER or key != owner.scope.key:
                raise ValueError("Group child owner differs from its paper account scope")
        self.ledger, self.owners = ledger, MappingProxyType(dict(owners))
        self.max_groups, self.max_observations, self.max_legs = max_groups, max_observations, max_legs
        facts = ledger.group_facts()
        if sum(f["kind"] == "plan" for f in facts) > max_groups or sum(f["kind"] == "observation" for f in facts) > max_observations:
            raise ValueError("Existing execution groups exceed lifetime capacities")
        for fact in facts:
            if fact['kind']=='plan':
                self._validate_spec(ExecutionGroupSpec.from_document(fact['document']['spec']))

    def _validate_spec(self, spec):
        if len(spec.legs) > self.max_legs or len(spec.timeout_plan) > self.max_legs:
            raise ValueError("Execution group leg/action-plan capacity exceeded")
        if any(leg.scope.key not in self.owners for leg in spec.legs):
            raise ValueError("Every group leg requires an explicitly registered child owner")

    def create(self, spec: ExecutionGroupSpec, evidence):
        self._validate_spec(spec)
        if not evidence:
            raise ValueError("Execution group plan requires immutable evidence")
        return self.ledger.record_group_fact(spec.group_id, "plan", "plan",
            {"spec": spec.to_document(), "evidence": evidence},
            max_groups=self.max_groups, max_observations=self.max_observations)

    def get(self, group_id):
        facts = self.ledger.group_facts(group_id)
        plans = [fact["document"] for fact in facts if fact["kind"] == "plan"]
        if not plans:
            return None
        return {"plan": plans[0], "observations": [fact for fact in facts if fact["kind"] == "observation"]}

    def _leg_fact(self, leg):
        owner = self.owners[leg.scope.key]
        try:
            before = owner.account_version()
            row = owner.get(leg.intent_id)
            after = owner.account_version()
        except Exception as exc:
            return {"leg_id": leg.leg_id, "state": "UNOBSERVED", "reason": "owner_read_unavailable:" + type(exc).__name__}
        if row is None:
            return {"leg_id": leg.leg_id, "state": "UNOBSERVED", "reason": "intent_unobserved", "account_version": after}
        if (row["strategy"] != leg.strategy_id or row["instrument"] != leg.rules.instrument.key
                or row["side"] != leg.side.value or Decimal(row["quantity"]) != leg.quantity
                or json.loads(row["rules"]) != leg.rules.to_document()):
            raise ValueError("Observed child order contradicts immutable group identity")
        proof, payload = json.loads(row["evidence"]), json.loads(row["payload"])
        versions = [value for value in (proof.get("config_version"), payload.get("metadata", {}).get("config_version")) if value is not None]
        if any(value != leg.config_version for value in versions):
            raise ValueError("Observed child config contradicts immutable group identity")
        config = versions[0] if versions else None
        return {"leg_id": leg.leg_id, "state": row["state"], "filled": row["filled"],
                "remaining": str(exact_difference(Decimal(row["quantity"]), Decimal(row["filled"]))),
                "reservation": row["reservation"], "reserve_currency": row["reserve_currency"],
                "reference_price": row["reference_price"], "margin_rate": row["margin_rate"],
                "intent_fingerprint": row["fingerprint"], "account_version": after,
                "account_read_stable": before == after, "config_binding_verified": config == leg.config_version}

    def observe(self, group_id, observation_id, *, now_ns: int, evidence):
        bounded_identity(observation_id, "observation_id")
        if isinstance(now_ns, bool) or not isinstance(now_ns, int) or not 0 <= now_ns <= 2**63-1 or not evidence:
            raise ValueError("Group observation requires bounded time and explicit evidence")
        group = self.get(group_id)
        if group is None:
            raise ValueError("Unknown execution group")
        request = {"observed_ns": now_ns, "evidence": evidence}
        for old in group["observations"]:
            if old["fact_id"] == observation_id:
                if canonical(old["document"]["request"]) != canonical(request):
                    raise ValueError("Immutable execution group observation collision")
                return False, old["document"]
        spec = ExecutionGroupSpec.from_document(group["plan"]["spec"])
        if now_ns < spec.created_ns or (group["observations"] and now_ns < group["observations"][-1]["document"]["request"]["observed_ns"]):
            raise ValueError("Execution group observation clock regression")
        document = {"request": request, "legs": [self._leg_fact(leg) for leg in spec.legs],
                    "cross_account_snapshot_atomic": False}
        created = self.ledger.record_group_fact(group_id, "observation", observation_id, document,
            max_groups=self.max_groups, max_observations=self.max_observations)
        return created, document

    def report(self, group_id, *, now_ns=None):
        group = self.get(group_id)
        if group is None:
            raise ValueError("Unknown execution group")
        spec = ExecutionGroupSpec.from_document(group["plan"]["spec"])
        observation = group["observations"][-1]["document"] if group["observations"] else None
        if now_ns is None:
            now_ns = observation["request"]["observed_ns"] if observation else spec.created_ns
        if isinstance(now_ns, bool) or not isinstance(now_ns, int) or not spec.created_ns <= now_ns <= 2**63-1:
            raise ValueError("Group report time is invalid")
        if observation and now_ns < observation["request"]["observed_ns"]:
            raise ValueError("Group report cannot use a future observation")
        known_base, unknown, reservations, facts = [], [], {}, []
        actual_notional_unknown, position_margin_unknown = [], []
        for index, leg in enumerate(spec.legs):
            fact = observation["legs"][index] if observation else {"leg_id": leg.leg_id, "state": "UNOBSERVED"}
            fact = dict(fact)
            mapping = (spec.base_asset is not None and leg.base_asset == spec.base_asset
                       and leg.rules.instrument.underlying == spec.base_asset and bool(leg.base_mapping_provenance))
            reasons = []
            if "filled" in fact:
                filled = Decimal(fact["filled"])
                if mapping:
                    value = exact_product(filled, leg.rules.multiplier)
                    value = value if leg.side == Side.BUY else value.copy_negate()
                    known_base.append(value)
                    fact["confirmed_base_delta"] = str(value)
                else:
                    reasons.append("base_mapping_unverified")
                if not fact["config_binding_verified"]:
                    reasons.append("config_binding_unverified")
                if not fact["account_read_stable"]:
                    reasons.append("account_read_changed")
                if fact["state"] not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}:
                    reasons.append("remaining_fill_or_order_state_unresolved")
                currency = fact["reserve_currency"]
                reservations.setdefault(currency, []).append(Decimal(fact["reservation"]))
                fact["admission_notional"] = {"currency": leg.rules.quote_currency,
                    "value": str(exact_product(leg.quantity, exact_product(Decimal(fact["reference_price"]), leg.rules.multiplier))),
                    "meaning": "admission_reference_only"}
            else:
                reasons.append("order_unobserved")
            if reasons:
                unknown.append({"leg_id": leg.leg_id, "reasons": reasons})
            actual_notional_unknown.append(leg.leg_id)
            position_margin_unknown.append(leg.leg_id)
            fact["actual_mark_notional"] = "UNVERIFIED"
            fact["attributed_position_margin"] = "UNVERIFIED"
            facts.append(fact)
        states = [fact["state"] for fact in facts]
        if all(state == "FILLED" for state in states) and not unknown:
            state = "COMPLETED_CONFIRMED"
        elif now_ns >= spec.deadline_ns:
            state = "WAITING_EXPLICIT_ACTION"
        elif "UNKNOWN" in states:
            state = "UNKNOWN"
        elif any("filled" in fact and Decimal(fact["filled"]) > 0 for fact in facts):
            state = "PARTIAL"
        elif all(value in {"CANCELED", "REJECTED", "EXPIRED"} for value in states):
            state = "TERMINAL_NO_CONFIRMED_FILL"
        else:
            state = "OBSERVING" if observation else "PLANNED"
        return {"group_id": group_id, "mode": "paper", "state": state, "base_asset": spec.base_asset,
                "observation_ns": observation["request"]["observed_ns"] if observation else None,
                "confirmed_base_delta": {"known_value": str(exact_sum(*known_base)) if known_base else None,
                    "unknown_legs": unknown, "complete": not unknown},
                "pending_reservation_by_currency": {key: str(exact_sum(*values)) for key, values in reservations.items()},
                "pending_reservation_unknown_legs": [fact["leg_id"] for fact in facts if "reservation" not in fact],
                "actual_mark_notional": {"known_value": None, "unknown_legs": actual_notional_unknown, "status": "UNVERIFIED"},
                "attributed_position_margin": {"known_value": None, "unknown_legs": position_margin_unknown, "status": "UNVERIFIED"},
                "legs": facts, "timeout_plan": list(spec.timeout_plan), "timeout_plan_execution": "recorded_only_not_executed",
                "cross_account_snapshot_atomic": False, "automatic_retry": False, "automatic_hedge": False,
                "production_gate": "BLOCKED", "owner_connector_integration": "MISSING_EVIDENCE",
                "lifetime_limits": {"groups": self.max_groups, "observations": self.max_observations, "terminal_included": True}}


class CrossVenueExecutionGroups:
    """Explicit two-owner submission coordinator over existing ExecutionOwners.

    The group anchor is the first owning account's native ExecutionLedger. Child
    owners keep their own ledgers/leases. There is no distributed transaction:
    each leg is independently authoritative and the report says so.
    """

    FOLLOW_UPS = frozenset({"WAIT", "CANCEL_REMAINDER", "HEDGE_BOUNDED",
                            "REDUCE_ONLY_FLATTEN"})

    def __init__(self, ledger, owners, *, max_groups: int, max_facts: int,
                 max_bytes: int, max_followup_actions: int = 4,
                 clock=time.time_ns):
        for value, name in ((max_groups, "max_groups"), (max_facts, "max_facts"),
                            (max_bytes, "max_bytes"),
                            (max_followup_actions, "max_followup_actions")):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(name + " must be an explicit positive integer")
        if not isinstance(owners, Mapping) or len(owners) != 2:
            raise ValueError("An execution group requires exactly two explicit child owners")
        seen = set()
        for key, owner in owners.items():
            if (owner.ledger is None or key != owner.ledger.scope.key
                    or not callable(getattr(owner, "prepare_group_leg", None))):
                raise ValueError("Group owner key differs from its native account scope")
            if owner.ledger.scope.key in seen:
                raise ValueError("An execution group owner is duplicated")
            seen.add(owner.ledger.scope.key)
        if any(owner.ledger.scope.venue.casefold() == other.ledger.scope.venue.casefold()
               and owner.ledger.scope.account.casefold() == other.ledger.scope.account.casefold()
               for owner in owners.values() for other in owners.values() if owner is not other):
            raise ValueError("Group owners must be distinct canonical venue/account pairs")
        self.ledger, self.owners = ledger, MappingProxyType(dict(owners))
        self.max_groups, self.max_facts, self.max_bytes = max_groups, max_facts, max_bytes
        self.max_followup_actions = max_followup_actions
        self.clock = clock
        self._lock = None

    def _async_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @staticmethod
    def _leg_value(value, scope):
        if isinstance(value, Mapping) and scope.key in value:
            return value[scope.key]
        return value

    @staticmethod
    def _plan_document(plan):
        from runtime.strategy_process import plan_document
        return plan_document(plan)

    @staticmethod
    def _fingerprint(plan, evidence):
        body = canonical({"plan": CrossVenueExecutionGroups._plan_document(plan),
                          "evidence": evidence})
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def _plan_fact(self, group_id):
        for fact in self.ledger.group_facts(group_id):
            if fact["fact_id"] == "plan":
                return fact
        return None

    def _record(self, group_id, fact_id, kind, document):
        return self.ledger.record_group_fact(group_id, fact_id, kind, document,
            max_groups=self.max_groups, max_facts=self.max_facts,
            max_bytes=self.max_bytes)

    def _definition(self, action):
        intent = action.intent
        if action.action is not ActionKind.NEW or intent is None:
            raise GateBlocked("An initial execution group consists of two NEW actions")
        if action.group_id is None:
            raise GateBlocked("Grouped action lost its explicit group_id")
        if action.expiry_ns <= self.clock():
            raise GateBlocked("Grouped action TTL is expired")
        return {
            "group_id": action.group_id, "action_id": action.action_id,
            "scope_key": action.scope.key, "venue": action.scope.venue,
            "account": action.scope.account, "intent_id": intent.intent_id,
            "client_order_id": intent.client_order_id,
            "instrument_key": intent.instrument.key,
            "side": intent.side.value, "quantity": str(intent.quantity),
            "limit_price": None if intent.limit_price is None else str(intent.limit_price),
            "reduce_only": bool(action.reduce_only), "time_in_force": action.time_in_force,
            "expiry_ns": action.expiry_ns, "attempt_id": action.action_id + ":attempt:1",
            "intent_fingerprint": self.owners[action.scope.key].ledger.fingerprint(intent),
            "config_version": action.config_version, "strategy_id": action.strategy_id,
            "source_action": action.action.value,
        }

    def _leg_document(self, state, *, result=None, error=None, token=None):
        document = {"state": state}
        if result is not None:
            document["result"] = {key: value for key, value in result.items()
                                  if key in {"status", "intent_id", "route_id", "order_id",
                                             "reason", "detail", "duplicate"}}
        if error is not None:
            document["error_type"] = type(error).__name__
            document["error_detail"] = str(error)[:200]
        if token is not None:
            document["route_id"] = token.route_id
            document["attempt_id"] = token.attempt_id
            document["reserve_unit"] = str(token.reserve_unit)
            document["reservation_state"] = "STAGED"
            document["intent_id"] = token.intent.intent_id
            document["side"] = token.intent.side.value
            document["quantity"] = str(token.intent.quantity)
            document.update(self._native_unit_facts(token))
        return document

    @staticmethod
    def _native_unit_facts(token):
        native = token.native_facts.get("native")
        product = token.native_facts.get("product")
        if not isinstance(native, Mapping):
            native = {}
        if not isinstance(product, Mapping):
            products = native.get("products")
            product = (products.get(token.intent.instrument.raw_symbol)
                       if isinstance(products, Mapping) else {})
        if not isinstance(product, Mapping):
            product = {}
        valuation = native.get("valuation_currency", native.get("currency"))
        settlement = native.get("settlement_currency",
                                product.get("settlement_currency", native.get("currency")))
        if not isinstance(valuation, str) or not valuation or not isinstance(settlement, str) or not settlement:
            raise GateBlocked("Native group unit requires explicit valuation and settlement currency labels")
        return {"contract_multiplier": str(positive_decimal(token.native_facts["multiplier"],
                                                               "native multiplier")),
                "valuation_currency": bounded_identity(valuation, "valuation_currency"),
                "settlement_currency": bounded_identity(settlement, "settlement_currency")}

    def _save_leg(self, group_id, leg_id, intent_id, scope_key, state, document):
        self.ledger.put_group_leg(group_id, leg_id, scope_key, intent_id, state,
                                  document, max_bytes=self.max_bytes)

    def _save_state(self, group_id, state, document):
        self.ledger.put_group_state(group_id, state, document, max_bytes=self.max_bytes)

    @staticmethod
    def _dec(value):
        number = Decimal(str(value))
        if not number.is_finite():
            raise GateBlocked("Group cap arithmetic requires finite native units")
        return number

    def _price_unit(self, intent: OrderIntent) -> Decimal:
        """Decisive native unit price; a reserve-only override is not a price."""
        if intent.limit_price is not None:
            return positive_decimal(intent.limit_price, "intent limit price")
        unit = intent.metadata.get("reserve_unit_price")
        if unit is None:
            raise GateBlocked("Group cost caps require a decisive native unit price")
        return positive_decimal(unit, "intent reserve unit price")

    def _validate_cost_caps_policy(self, group_id, policy, token):
        """Cumulative group notional/cost caps in one native currency per leg.

        The prepared token's ``reserve_unit`` already equals
        ``unit_price * multiplier * (1 + fee_rate)``, so the native multiplier
        and fee must never be multiplied into it a second time. The sums below
        add every other group leg's current filled notional/fee plus its
        remaining worst-case price-notional and fee, then the new candidate,
        all in that child account's exact native valuation unit.
        """
        scope = token.action.scope
        notional_cap = (policy["notional_caps"].get(scope.key)
                        or policy["notional_caps"].get(scope.account)
                        or policy["notional_caps"].get("default"))
        cost_cap = (policy["cost_caps"].get(scope.key)
                    or policy["cost_caps"].get(scope.account)
                    or policy["cost_caps"].get("default"))
        if notional_cap is None or cost_cap is None:
            raise GateBlocked("Group policy lacks this account/native-unit cap")
        intent = token.intent
        multiplier = positive_decimal(token.native_facts["multiplier"], "native multiplier")
        fee = positive_decimal(token.native_facts["taker_fee_rate"], "native taker fee", zero=True)
        price_unit = self._price_unit(intent)
        inclusive = exact_product(exact_product(price_unit, multiplier),
                                  exact_sum(Decimal(1), fee))
        if inclusive > token.reserve_unit:
            raise GateBlocked("Prepared reserve unit is below its native price/multiplier/fee facts")
        used_notional = exact_product(exact_product(
            positive_decimal(intent.quantity, "quantity"), price_unit), multiplier)
        used_cost = exact_product(used_notional, fee)
        for leg in self.ledger.group_legs(group_id):
            if leg["scope_key"] != scope.key:
                continue
            owner = self.owners.get(scope.key)
            row = None if owner is None else owner.ledger.get(leg["intent_id"])
            if row is None:
                raise GateBlocked("Cumulative group caps require current child facts for every leg")
            if leg["intent_id"] == intent.intent_id:
                continue  # this candidate is counted exactly once, above
            leg_intent = OrderIntent.model_validate_json(row["payload"])
            leg_multiplier = positive_decimal(row["contract_multiplier"], "leg multiplier")
            leg_price = self._price_unit(leg_intent)
            leg_fee = positive_decimal(token.native_facts["taker_fee_rate"],
                                       "leg native taker fee", zero=True)
            remaining = exact_sum(positive_decimal(leg_intent.quantity, "leg quantity"),
                                  Decimal(row["filled"]).copy_negate())
            pending_price = exact_product(exact_product(remaining, leg_price), leg_multiplier)
            used_notional = exact_sum(used_notional,
                exact_sum(pending_price, self._dec(row["notional"])))
            used_cost = exact_sum(used_cost,
                exact_sum(exact_product(pending_price, leg_fee), self._dec(row["fees"])))
        if used_notional > positive_decimal(notional_cap, "group notional cap"):
            raise GateBlocked("Cumulative group leg exposure exceeds its account/native notional cap")
        if used_cost > positive_decimal(cost_cap, "group cost cap", zero=True):
            raise GateBlocked("Cumulative group leg cost exceeds its account/native cost cap")

    def _validate_cost_caps(self, group_id, plan, token):
        self._validate_cost_caps_policy(group_id, plan.group_policy, token)

    def _followup_used(self, group_id):
        """Durable action count: each recorded follow-up action is one unit."""
        return sum(1 for fact in self.ledger.group_facts(group_id)
                   if fact["kind"] == "followup")

    def _followup_plan_fact(self, group_id, plan_id):
        key = "followup-plan:" + plan_id
        return next((fact for fact in self.ledger.group_facts(group_id)
                     if fact["fact_id"] == key), None)

    def _claim_followup_plan(self, group_id, plan):
        document = self._plan_document(plan)
        fingerprint = hashlib.sha256(canonical(document).encode("utf-8")).hexdigest()
        fact_id = "followup-plan:" + plan.plan_id
        existing = self._followup_plan_fact(group_id, plan.plan_id)
        claim = {"plan_id": plan.plan_id, "fingerprint": fingerprint,
                 "plan": document, "state": "CLAIMED"}
        if existing is not None:
            if canonical(existing["document"]) != canonical(claim):
                raise GateBlocked("Follow-up plan identity conflicts with its durable claim")
            return False
        action_ids = {action.action_id for action in plan.actions}
        for fact in self.ledger.group_facts(group_id):
            if fact["kind"] != "followup-plan":
                continue
            prior_ids = {action.get("action_id") for action in fact["document"].get("plan", {}).get("actions", ())}
            if action_ids & prior_ids:
                raise GateBlocked("Follow-up action identity was already claimed by another plan")
        self._record(group_id, fact_id, "followup-plan", claim)
        return True

    def _record_followup_actions(self, group_id, plan):
        used = self._followup_used(group_id)
        for offset, action in enumerate(plan.actions, 1):
            self._record(group_id, "followup:" + str(used + offset), "followup",
                         {"group_id": group_id, "index": used + offset,
                          "plan_id": plan.plan_id, "action_id": action.action_id,
                          "action": action.action.value, "scope_key": action.scope.key,
                          "intent_id": action.intent.intent_id if action.intent else None,
                          "target_order_id": action.target_order_id,
                          "plan": self._plan_document(plan), "state": "RECORDED"})

    def _record_followup_fault(self, group_id, plan, error):
        serial = sum(1 for fact in self.ledger.group_facts(group_id)
                     if fact["kind"] == "followup-fault") + 1
        self._record(group_id, "followup-fault:" + str(serial), "followup-fault",
                     {"group_id": group_id, "plan_id": plan.plan_id,
                      "fault": {"type": type(error).__name__, "detail": str(error)[:200]}})

    def _new_fence(self, group_id, *, keys=("UNKNOWN",), allow_followup_plan_id=None,
                   allow_intent_id=None):
        """NEW blocks while group or child facts carry unresolved uncertainty.

        An owned read/cancel/reconcile stays available; this fence covers only
        the send-uncertain native states ( Authorized/HANDOFF never resend),
        group recovery states, or a persisted source-followup fault. A durable
        known REJECTED child alone is not a blanket fence: the source may issue
        a bounded literal hedge for the remaining exposure.
        """
        uncertain = {"UNKNOWN", "LEGACY_UNKNOWN", "AUTHORIZED", "HANDOFF"}
        group_state = self.ledger.group_state(group_id)
        if group_state is None:
            raise GateBlocked("Group follow-up NEW requires durable group state")
        if group_state["state"] in {"UNKNOWN", "RECONCILING", "PREPARING", "PREPARED",
                                    "PREFLIGHT_FAILED"}:
            raise GateBlocked("Group follow-up NEW is fenced until group recovery completes")
        if any(fact["kind"] == "followup-fault" for fact in self.ledger.group_facts(group_id)):
            raise GateBlocked("Group follow-up NEW is fenced by a persisted source fault")
        facts = self.ledger.group_facts(group_id)
        results = {fact["fact_id"] for fact in facts if fact["kind"] == "followup-result"}
        for fact in facts:
            if fact["kind"] != "followup-plan":
                continue
            if fact["document"].get("plan_id") == allow_followup_plan_id:
                continue
            actions = fact["document"].get("plan", {}).get("actions", ())
            if any("followup-result:" + action.get("action_id", "") not in results
                   for action in actions):
                raise GateBlocked("Group follow-up NEW is fenced by an in-progress durable plan")
        for leg in self.ledger.group_legs(group_id):
            if leg["intent_id"] == allow_intent_id:
                continue
            owner = self.owners.get(leg["scope_key"])
            row = None if owner is None else owner.ledger.get(leg["intent_id"])
            if owner is None or row is None or row["state"] in uncertain:
                raise GateBlocked("Group follow-up NEW is fenced until every leg is reconciled")

    def _followup_fences(self, group_id, plan, policy):
        """Literal NEW follow-ups need a deadline and a reconciled group."""
        now = self.clock()
        if self._dec(now) >= self._dec(policy["deadline_ns"]):
            raise GateBlocked("Group follow-up NEW is past the immutable group deadline")
        self._new_fence(group_id, allow_followup_plan_id=plan.plan_id)

    def _validate_followup_plan(self, group_id, plan, policy):
        if not isinstance(plan, StrategyActionPlan) or not plan.group_followup:
            raise GateBlocked("Group follow-up requires an explicit group_followup plan")
        if plan.group_submit_mode is not None or plan.group_policy is not None:
            raise GateBlocked("A group follow-up plan cannot introduce a new mode or policy")
        if not plan.actions or any(action.group_id != group_id for action in plan.actions):
            raise GateBlocked("Follow-up actions must all target the original execution group")
        if any(action.action not in (ActionKind.NEW, ActionKind.CANCEL) for action in plan.actions):
            raise GateBlocked("Unsupported bounded group follow-up action")
        if any(action.action is ActionKind.NEW for action in plan.actions):
            self._followup_fences(group_id, plan, policy)
        limit = min(self.max_followup_actions, int(policy["max_followup_actions"]))
        used = self._followup_used(group_id)
        if used + len(plan.actions) > limit:
            raise GateBlocked("Group follow-up action budget exhausted")
        return used

    def _resolve_group_for_event(self, event):
        """Recover the ORIGINAL group from durable child intent membership.

        Late events for two groups of the same strategy must never be routed by
        the last callback closure. The child row identity (intent/scope) is
        looked up in the anchor ledger; a claimed group_id that disagrees with
        that durable membership is rejected, never honoured.
        """
        owner = self.owners.get(event.account_scope.key)
        if owner is None:
            return None
        intent_id = event.metadata.get("intent_id")
        groups = self.ledger.group_ids_for_intent(intent_id, owner.ledger.scope.key)
        if len(groups) != 1:
            return None
        group_id = groups[0]
        claimed = event.metadata.get("group_id")
        if claimed is not None and claimed != group_id:
            raise GateBlocked("Event group identity differs from its durable child membership")
        return group_id

    def _followup_guard(self, group_id, policy, on_event, queue, plan_fact):
        """Consume a RAW source follow-up into durable facts; never redispatch.

        The callback returns None so the child owner cannot re-dispatch the
        returned plan into its own single account. The original group is
        recovered from durable child membership (never the closure), the plan
        is validated and durably claimed, and the literal actions are
        dispatched by the coordinator after the initial facts are durable.
        Invalid delivers become visible persisted faults, not silent success.
        """

        async def callback(event, context):
            current = group_id
            try:
                current = self._resolve_group_for_event(event)
                if current is None:
                    raise GateBlocked("Source event has no unique durable group membership")
                resolved = self._plan_fact(current)
                if resolved is None:
                    raise GateBlocked("Source event has no durable execution group")
                if not isinstance(context, StrategyOrderContext):
                    raise GateBlocked("Group source callback requires its immutable owner context")
                view = self.report(current)
                compact_legs = [{
                    "leg_id": leg["leg_id"], "intent_id": leg["intent_id"],
                    "scope_key": leg["scope_key"], "side": leg.get("side"),
                    "quantity": leg.get("quantity"),
                    "contract_multiplier": leg.get("contract_multiplier"),
                    "valuation_currency": leg.get("valuation_currency"),
                    "settlement_currency": leg.get("settlement_currency"),
                    "current": leg.get("current", {}),
                } for leg in view["legs"]]
                group_context = {
                    "group_id": current,
                    "strategy_id": view["strategy_id"],
                    "config_version": view["config_version"],
                    "deadline_ns": resolved["document"]["plan"]["group_policy"]["deadline_ns"],
                    "state": view["state"], "legs": compact_legs,
                    "funding": view["funding"],
                    "cross_ledger_atomic_commit": False,
                    "economic_neutrality": "NOT_ASSESSED",
                }
                context = replace(context, execution={**dict(context.execution),
                                                       "execution_group": group_context})
                plan = on_event(event, context)
                if hasattr(plan, "__await__"):
                    plan = await plan
                if plan is None or isinstance(plan, (list, tuple, dict)) and not plan:
                    return None
                if plan is not None:
                    if (plan.strategy_id != resolved["document"]["strategy_id"]
                            or plan.config_version != resolved["document"]["config_version"]):
                        raise GateBlocked("Follow-up plan lost its immutable source strategy/config")
                    self._validate_followup_plan(current, plan,
                                                 resolved["document"]["plan"]["group_policy"])
                    if not self._claim_followup_plan(current, plan):
                        return None
                    self._record_followup_actions(current, plan)
                    source_owner = self.owners.get(event.account_scope.key)
                    task = asyncio.current_task()
                    owner_lock_held = (source_owner is not None and task is not None
                                       and source_owner._lock._owner is task)
                    if owner_lock_held:
                        # Initial send callbacks run under an owner lock. Defer
                        # until the group has persisted all leg outcomes.
                        queue.append((current, plan))
                    else:
                        dispatch = getattr(callback, "group_dispatch_context", None)
                        if dispatch is None:
                            dispatch = (source_owner._source_dispatch.get(resolved["document"]["strategy_id"])
                                        if source_owner is not None else None)
                        if dispatch is None:
                            raise GateBlocked("Late source follow-up lacks its saved dispatch context")
                        await self._dispatch_followup(
                            current, plan,
                            policy=resolved["document"]["plan"]["group_policy"],
                            registry=dispatch["registry"], readiness=dispatch["readiness"],
                            guard=dispatch["guard"], evidence=dispatch["evidence"])
            except Exception as error:
                fault = {"type": type(error).__name__, "detail": str(error)[:200],
                         "event_id": getattr(event, "event_id", None),
                         "resolve_group": current}
                try:
                    serial = sum(1 for fact in self.ledger.group_facts(current)
                                 if fact["kind"] == "followup-fault") + 1
                    self._record(current, "followup-fault:" + str(serial), "followup-fault",
                                 {"group_id": current, "fault": fault})
                except Exception:
                    pass
                return None
            return None

        return callback

    async def _dispatch_followup(self, group_id, plan, *, policy, registry,
                                 readiness, guard, evidence):
        """Route every literal follow-up action to its exact existing child owner."""
        parent = self._plan_fact(group_id)
        if parent is None:
            raise GateBlocked("Follow-up dispatch requires an immutable parent group")
        immutable = parent["document"]
        if (plan.strategy_id != immutable["strategy_id"]
                or plan.config_version != immutable["config_version"]):
            raise GateBlocked("Follow-up dispatch strategy/config differs from its parent group")
        # Never trust the caller's policy argument. Queue drains may occur while
        # another same-strategy group is executing and must retain this group's
        # immutable caps and deadline.
        policy = immutable["plan"]["group_policy"]
        results = {}
        for action in plan.actions:
            owner = self.owners.get(action.scope.key)
            if owner is None:
                raise GateBlocked("Follow-up action has no explicitly registered child owner")
            if action.action is ActionKind.NEW:
                self._followup_fences(group_id, plan, policy)
            owner_readiness = self._leg_value(readiness, action.scope) if readiness is not None else None
            owner_guard = self._leg_value(guard, action.scope) if guard is not None else None
            if action.action is ActionKind.CANCEL:
                target = owner.ledger.find(action.target_order_id)
                if target is None or group_id not in self.ledger.group_ids_for_intent(
                        target["intent_id"], action.scope.key):
                    raise GateBlocked("Cancel target is not a durable child of this exact group")
                outcome = await owner.cancel_action(action, registry=registry,
                                                    readiness=owner_readiness,
                                                    guard=owner_guard)
            else:
                owner_evidence = self._leg_value(evidence, action.scope) if evidence is not None else None
                if owner_evidence is None:
                    raise GateBlocked("Follow-up NEW requires explicit durable source evidence")
                token = await owner.prepare_group_leg(
                    action, group_id=group_id, registry=registry,
                    readiness=owner_readiness, evidence=owner_evidence)
                try:
                    self._validate_cost_caps_policy(group_id, policy, token)
                except BaseException:
                    owner.abandon_prepared(token, reason="FOLLOWUP_RISK_REJECTED")
                    raise
                try:
                    definition = self._definition(action)
                    self._record(group_id, "followup-definition:" + action.action_id,
                                 "followup-definition", definition)
                    self._save_leg(group_id, action.action_id, action.intent.intent_id,
                                   action.scope.key, "PREPARED",
                                   self._leg_document("PREPARED", token=token))
                    self._record(group_id, "followup-reservation:" + action.action_id,
                                 "followup-reservation", self._reservation_document(token))
                except BaseException:
                    owner.abandon_prepared(token, reason="FOLLOWUP_DURABLE_PREPARE_FAILED")
                    raise
                def validate_followup_send(active_token):
                    if self.clock() >= policy["deadline_ns"]:
                        raise GateBlocked("Execution group deadline expired before follow-up send")
                    self._new_fence(group_id, allow_followup_plan_id=plan.plan_id,
                                    allow_intent_id=active_token.intent.intent_id)
                    evidence_map = immutable.get("evidence", {})
                    for member in self.ledger.group_legs(group_id):
                        peer_owner = self.owners.get(member["scope_key"])
                        peer_row = None if peer_owner is None else peer_owner.ledger.get(member["intent_id"])
                        if peer_owner is None or peer_row is None:
                            raise GateBlocked("Follow-up peer owner/order truth is unavailable")
                        peer_evidence = evidence_map.get(member["scope_key"], {})
                        epochs = peer_evidence.get("expected_epochs")
                        if not isinstance(epochs, Mapping):
                            raise GateBlocked("Follow-up peer epoch evidence is unavailable")
                        peer_owner.control.validate(plan.strategy_id, epochs)
                        peer_readiness = self._leg_value(readiness, peer_owner.ledger.scope)
                        peer_owner._readiness(peer_readiness)
                        peer_guard = self._leg_value(guard, peer_owner.ledger.scope) if guard is not None else None
                        if peer_guard:
                            failures = peer_guard()
                            if failures:
                                raise GateBlocked("Follow-up peer guard changed before send")
                token.group_permit = validate_followup_send
                outcome = await owner.dispatch_prepared(token)
                state = self._status_state(outcome)
                self._save_leg(group_id, action.action_id, action.intent.intent_id,
                               action.scope.key, state,
                               self._leg_document(state, result=outcome, token=token))
            results[action.action_id] = {key: value for key, value in outcome.items()
                                         if key in {"status", "intent_id", "route_id",
                                                    "order_id", "reason", "acknowledged",
                                                    "terminal", "duplicate"}}
            self._record(group_id, "followup-result:" + action.action_id, "followup-result",
                         {"group_id": group_id, "action_id": action.action_id,
                          "outcome": results[action.action_id]})
        return results

    async def execute_followup(self, plan: StrategyActionPlan, *, registry,
                               readiness, evidence=None, guard=None) -> dict:
        """Explicit source follow-up entrypoint over one existing group."""
        if not isinstance(plan, StrategyActionPlan) or not plan.group_followup:
            raise GateBlocked("execute_followup requires an explicit group_followup plan")
        group_id = plan.actions[0].group_id
        prior = self._plan_fact(group_id)
        if prior is None:
            raise GateBlocked("Follow-up references an unknown execution group")
        parent = prior["document"]
        if plan.strategy_id != parent["strategy_id"] or plan.config_version != parent["config_version"]:
            raise GateBlocked("Follow-up strategy/config differs from the immutable parent group")
        policy = parent["plan"]["group_policy"]
        existing_claim = self._followup_plan_fact(group_id, plan.plan_id)
        if existing_claim is not None:
            self._claim_followup_plan(group_id, plan)
            report = self.report(group_id)
            report["duplicate"] = True
            return report
        async with self._async_lock():
            self._validate_followup_plan(group_id, plan, policy)
            if not self._claim_followup_plan(group_id, plan):
                report = self.report(group_id)
                report["duplicate"] = True
                return report
            self._record_followup_actions(group_id, plan)
        try:
            results = await self._dispatch_followup(group_id, plan, policy=policy,
                                                    registry=registry, readiness=readiness,
                                                    guard=guard, evidence=evidence)
        except Exception as error:
            self._record_followup_fault(group_id, plan, error)
            raise
        report = self.report(group_id)
        report["followup_results"] = results
        return report

    def _reservation_document(self, token):
        row = token.owner.ledger.get(token.intent.intent_id)
        if row is None:
            raise GateBlocked("Prepared leg lacks its durable reservation row")
        return {"leg_id": token.action.action_id, "intent_id": token.intent.intent_id,
                "scope_key": token.action.scope.key, "route_id": token.route_id,
                "attempt_id": token.attempt_id, "state": row["state"],
                "reservation": row["reservation"],
                "reserve_unit": str(token.reserve_unit),
                "reservation_key": "scope:" + token.action.scope.key,
                "fingerprint": row["fingerprint"],
                **self._native_unit_facts(token),
                "native_fee_rate": str(token.native_facts["taker_fee_rate"])}

    @staticmethod
    def _status_state(result):
        if result is None:
            return "UNKNOWN"
        status = result.get("status")
        if status in {"SUBMITTED"}:
            return "ACKNOWLEDGED"
        if status in {"UNKNOWN"}:
            return "UNKNOWN"
        if status in {"BLOCKED", "PRE_SEND_FAILED", "REJECTED"}:
            return "NOT_SENT"
        return "UNKNOWN" if status is None else status

    async def execute(self, plan: StrategyActionPlan, *, registry, readiness,
                      evidence, reserve_units=None, guard=None, on_event=None) -> dict:
        """Prepare both legs, then submit exactly once under the opt-in mode."""
        if not isinstance(plan, StrategyActionPlan):
            raise GateBlocked("Only an original StrategyActionPlan is accepted")
        if plan.group_followup:
            raise GateBlocked("Initial group execution does not accept a follow-up plan; "
                              "use execute_followup for explicit owned follow-ups")
        if plan.group_submit_mode is None or plan.group_policy is None:
            raise GateBlocked("Cross-venue execution requires an explicit group policy")
        if len(plan.actions) != 2:
            raise GateBlocked("An initial execution group requires exactly two legs")
        if type(plan.event_ts_ns) is not int or plan.event_ts_ns > self.clock():
            raise GateBlocked("Group event time cannot be in the future")
        group_id = plan.actions[0].group_id
        policy = plan.group_policy
        for action in plan.actions:
            parents = self.ledger.group_ids_for_intent(action.intent.intent_id, action.scope.key)
            if any(parent != group_id for parent in parents):
                raise GateBlocked("Native child identity already belongs to a different execution group")
        if self.clock() >= policy["deadline_ns"]:
            raise GateBlocked("Execution group deadline has expired")
        if any(action.expiry_ns < policy["deadline_ns"] for action in plan.actions):
            raise GateBlocked("Every group leg TTL must cover the explicit group deadline")

        evidence_by_scope = {action.scope.key: self._leg_value(evidence, action.scope)
                             for action in plan.actions}
        fingerprint = self._fingerprint(plan, {key: value for key, value in evidence_by_scope.items()})
        async with self._async_lock():
            prior = self._plan_fact(group_id)
            if prior is not None:
                if prior["document"].get("fingerprint") != fingerprint:
                    raise GateBlocked("Immutable execution group identity collision")
                report = self.report(group_id)
                report["duplicate"] = True
                return report
            if self._plan_fact(group_id) is None and self.ledger.group_state(group_id) is None:
                pass
            plan_fact = {"group_id": group_id, "fingerprint": fingerprint,
                         "plan": self._plan_document(plan),
                         "evidence": evidence_by_scope,
                         "strategy_id": plan.strategy_id,
                         "config_version": plan.config_version,
                         "mode": plan.group_submit_mode.value,
                         "created_ns": self.clock()}
            self._record(group_id, "plan", "plan", plan_fact)
            definitions = []
            for action in plan.actions:
                definition = self._definition(action)
                self._record(group_id, "leg-definition:" + action.action_id,
                             "leg-definition", definition)
                self._save_leg(group_id, action.action_id, action.intent.intent_id,
                               action.scope.key, "DEFINED", {"definition": definition})
                definitions.append(definition)
        self._save_state(group_id, "PREPARING", {"group_id": group_id,
            "fingerprint": fingerprint, "plan": plan_fact,
            "legs": [{"leg_id": d["action_id"], "state": "DEFINED"} for d in definitions]})

        tokens, failures = {}, {}
        group_policy = plan.group_policy
        for action in plan.actions:
            leg_id = action.action_id
            owner = self.owners.get(action.scope.key)
            if owner is None:
                failures[leg_id] = GateBlocked("Group leg has no explicitly registered owner")
                continue
            owner_readiness = self._leg_value(readiness, action.scope)
            owner_evidence = evidence_by_scope[action.scope.key]
            owner_guard = self._leg_value(guard, action.scope) if guard is not None else None
            owner_reserves = self._leg_value(reserve_units, action.scope) if reserve_units is not None else None
            try:
                token = await owner.prepare_group_leg(action, group_id=group_id,
                    registry=registry, readiness=owner_readiness, evidence=owner_evidence,
                    reserve_units=owner_reserves, guard=owner_guard, on_event=on_event)
                self._validate_cost_caps(group_id, plan, token)
                self._record(group_id, "reservation:" + leg_id, "reservation",
                             self._reservation_document(token))
                tokens[leg_id] = token
                self._save_leg(group_id, leg_id, action.intent.intent_id, action.scope.key,
                               "PREPARED", self._leg_document("PREPARED", token=token))
            except BaseException as error:
                failures[leg_id] = error
                self._save_leg(group_id, leg_id, action.intent.intent_id, action.scope.key,
                               "PREFLIGHT_FAILED", self._leg_document("PREFLIGHT_FAILED", error=error))
                if not isinstance(error, Exception):
                    raise

        if failures:
            for action in plan.actions:
                if action.action_id not in tokens and action.action_id not in failures:
                    self._save_leg(group_id, action.action_id, action.intent.intent_id,
                                   action.scope.key, "NOT_SENT",
                                   self._leg_document("NOT_SENT", error=GateBlocked(
                                       "A sibling preflight failure blocked submission")))
                elif action.action_id in tokens:
                    try:
                        tokens[action.action_id].owner.abandon_prepared(tokens[action.action_id])
                    except Exception:
                        # Keep any reservation that may have crossed the send seam.
                        pass
                    self._save_leg(group_id, action.action_id, action.intent.intent_id,
                                   action.scope.key, "RESERVED_NOT_SENT",
                                   self._leg_document("RESERVED_NOT_SENT", token=tokens[action.action_id]))
            self._save_state(group_id, "PREFLIGHT_FAILED", {"group_id": group_id,
                "fingerprint": fingerprint,
                "failures": {leg: type(error).__name__ for leg, error in failures.items()},
                "mutations_attempted": 0, "reservations_may_remain": True})
            report = self.report(group_id)
            report["mutations_attempted"] = 0
            return report

        self._save_state(group_id, "PREPARED", {"group_id": group_id,
            "fingerprint": fingerprint, "mutations_attempted": 0})
        followup_queue = []
        def validate_group_send_permit(active_token):
            # Runs inside each concrete adapter immediately before its native
            # transport mutation, revalidating both owners at that boundary.
            if self.clock() >= group_policy["deadline_ns"]:
                raise GateBlocked("Execution group deadline expired before native send")
            for candidate in tokens.values():
                inventory = candidate.owner._prepared_inventory.get(id(candidate))
                row = candidate.owner.ledger.get(candidate.intent.intent_id)
                if candidate is active_token:
                    if (inventory is None or inventory[0] is not candidate
                            or inventory[1] != candidate.owner._prepared_fingerprint(candidate)):
                        raise GateBlocked("Active group admission identity changed before native send")
                elif inventory is None:
                    if row is None or row["state"] not in {"SUBMITTED", "UNKNOWN", "REJECTED"}:
                        raise GateBlocked("Group peer has neither a live admission nor durable send outcome")
                elif (inventory[0] is not candidate
                      or inventory[1] != candidate.owner._prepared_fingerprint(candidate)):
                    raise GateBlocked("Group peer admission identity changed before native send")
                current_native = candidate.owner._native_facts(candidate.binding, candidate.action)
                current_identity = current_native.get("native", {})
                prepared_identity = candidate.native_facts.get("native", {})
                current_products = current_identity.get("products", {})
                prepared_products = prepared_identity.get("products", {})
                current_product = (current_products.get(candidate.intent.instrument.raw_symbol)
                                   if isinstance(current_products, Mapping) else None)
                prepared_product = (prepared_products.get(candidate.intent.instrument.raw_symbol)
                                    if isinstance(prepared_products, Mapping) else None)
                if current_product is None:
                    current_product = current_native.get("product")
                if prepared_product is None:
                    prepared_product = candidate.native_facts.get("product")
                if (Decimal(str(current_native.get("multiplier")))
                        != Decimal(str(candidate.native_facts.get("multiplier")))
                        or Decimal(str(current_native.get("taker_fee_rate")))
                        != Decimal(str(candidate.native_facts.get("taker_fee_rate")))
                        or current_identity.get("scope_key") != prepared_identity.get("scope_key")
                        or current_identity.get("native_account_id") != prepared_identity.get("native_account_id")
                        or canonical(current_product) != canonical(prepared_product)):
                    raise GateBlocked("Group peer native product/fee facts changed before send")
                if candidate.action.expiry_ns <= candidate.owner.clock():
                    raise GateBlocked("Group peer action expired before native send")
                candidate.owner._readiness(candidate.readiness)
                if candidate.guard:
                    failures = candidate.guard()
                    if failures:
                        raise GateBlocked("Group peer guard changed before native send")
                if row is None or row["fingerprint"] != candidate.owner.ledger.fingerprint(candidate.intent):
                    raise GateBlocked("Group peer durable reservation identity changed before native send")
                if candidate is not active_token:
                    # A sibling already handed to the transport is part of this
                    # concurrent dispatch; its UNKNOWN does not suppress the
                    # other pre-authorized leg. All other peers retain epochs.
                    if not (candidate.used and row["state"] in {"UNKNOWN", "LEGACY_UNKNOWN"}):
                        candidate.owner.control.validate(candidate.action.strategy_id,
                                                         candidate.epochs)
        for candidate in tokens.values():
            candidate.group_permit = validate_group_send_permit
        callback = (self._followup_guard(group_id, policy, on_event, followup_queue, plan_fact)
                    if on_event is not None else None)
        if callback is not None:
            callback.group_dispatch_context = {"registry": registry, "readiness": readiness,
                                               "guard": guard, "evidence": evidence}
        results = {}
        if plan.group_submit_mode is GroupSubmitMode.CONCURRENT:
            tasks = {leg_id: asyncio.create_task(
                token.owner.dispatch_prepared(token, on_event=callback))
                for leg_id, token in tokens.items()}
            try:
                outcomes = await asyncio.gather(*tasks.values(), return_exceptions=True)
            except asyncio.CancelledError:
                outcomes = await asyncio.gather(*tasks.values(), return_exceptions=True)
                final_cancel = {}
                for action in plan.actions:
                    owner = self.owners[action.scope.key]
                    row = owner.ledger.get(action.intent.intent_id)
                    state = "UNKNOWN" if row is None or row["state"] in {
                        "UNKNOWN", "LEGACY_UNKNOWN", "HANDOFF", "AUTHORIZED"} else self._status_state(
                            {"status": row["state"]})
                    if row is not None and row["state"] in {"HANDOFF", "AUTHORIZED"}:
                        owner.ledger.outcome(action.intent.intent_id, "UNKNOWN",
                                             {"reason": "GROUP_TASK_CANCELLED_DURING_HANDOFF"})
                        owner.control.halt(action.strategy_id)
                    final_cancel[action.action_id] = state
                    self._save_leg(group_id, action.action_id, action.intent.intent_id,
                                   action.scope.key, state,
                                   self._leg_document(state, error=asyncio.CancelledError(),
                                                      token=tokens[action.action_id]))
                self._save_state(group_id, "UNKNOWN" if "UNKNOWN" in final_cancel.values()
                                 else "BLOCKED", {"group_id": group_id,
                                 "fingerprint": fingerprint, "leg_states": final_cancel,
                                 "mutations_attempted": sum(v in {"UNKNOWN", "ACKNOWLEDGED", "REJECTED"}
                                                             for v in final_cancel.values()),
                                 "cancelled": True})
                raise
            for leg_id, outcome in zip(tasks, outcomes):
                results[leg_id] = outcome
        else:
            first, second = plan.actions
            first_token, second_token = tokens[first.action_id], tokens[second.action_id]
            first_result = await first_token.owner.dispatch_prepared(first_token, on_event=callback)
            results[first.action_id] = first_result
            first_state = self._status_state(first_result)
            if first_state != "ACKNOWLEDGED":
                results[second.action_id] = {"status": "NOT_SENT", "intent_id":
                    second.intent.intent_id, "reason": "PREDECESSOR_BLOCKED"}
            else:
                results[second.action_id] = await second_token.owner.dispatch_prepared(
                    second_token, on_event=callback)

        final = {}
        for action in plan.actions:
            outcome = results[action.action_id]
            if isinstance(outcome, BaseException):
                state, document = "UNKNOWN", self._leg_document("UNKNOWN", error=outcome,
                                                                 token=tokens[action.action_id])
            else:
                state = self._status_state(outcome)
                document = self._leg_document(state, result=outcome,
                                              token=tokens[action.action_id])
            self._save_leg(group_id, action.action_id, action.intent.intent_id,
                           action.scope.key, state, document)
            final[action.action_id] = state
        states = set(final.values())
        if all(state == "ACKNOWLEDGED" for state in states):
            group_state = "ACKNOWLEDGED"
        elif "UNKNOWN" in states:
            group_state = "UNKNOWN"
        else:
            group_state = "BLOCKED"
        self._save_state(group_id, group_state, {"group_id": group_id,
            "fingerprint": fingerprint, "leg_states": final,
            "mutations_attempted": sum(1 for state in final.values()
                                       if state in {"ACKNOWLEDGED", "UNKNOWN", "REJECTED"}),
            "not_sent": [leg for leg, state in final.items() if state == "NOT_SENT"]})
        report = self.report(group_id)
        # Source follow-up plans consumed during the owner callbacks are
        # dispatched here, after the initial dispatch facts are durable and
        # outside every owner callback. A requested follow-up failure is a
        # visible persisted fault, never a swallowed success.
        followup_results, followup_faults = {}, {}
        for queued_group, queued in followup_queue:
            try:
                followup_results.update(await self._dispatch_followup(
                    queued_group, queued, policy=policy, registry=registry,
                    readiness=readiness, guard=guard, evidence=evidence))
            except Exception as error:
                fault = {"type": type(error).__name__, "detail": str(error)[:200],
                         "plan_id": queued.plan_id, "group_id": queued_group}
                followup_faults[queued.plan_id] = fault
                self._record(queued_group, "followup-fault:" + queued.plan_id, "followup-fault",
                             {"group_id": queued_group, "plan_id": queued.plan_id, "fault": fault})
        if followup_results:
            report["followup_results"] = followup_results
        if followup_faults:
            report["followup_faults"] = followup_faults
        return report

    # --------------------------------------------------- native reconciliation
    def _leg_query_action(self, owner, row, leg_id):
        environment = owner.ledger.scope.environment
        mode = TradingMode(environment) if environment in {"sandbox", "live"} else None
        if mode is None:
            raise GateBlocked("Native group reconciliation requires a sandbox/live account")
        return ExecutionAction(
            action_id="query:" + leg_id, action=ActionKind.CANCEL, scope=owner.ledger.scope,
            strategy_id=row["strategy_id"], config_version=row["config_version"], mode=mode,
            expiry_ns=row["expiry_ns"],
            target_order_id=row["target_order_id"] or row["client_order_id"])

    @staticmethod
    def _money(value, name):
        number = Decimal(str(value))
        if not number.is_finite():
            raise GateBlocked(name + " must be finite and exact")
        return number

    async def _apply_native_fills(self, owner, action, row, *, registry, start_ns, end_ns,
                                  document):
        """Apply exact native fill records once; never from cumulative quantity."""
        intent = OrderIntent.model_validate_json(row["payload"])
        route_id, _ = owner._resolve_cancel_alias(registry, action)
        fact_quality = ("SYNTHETIC"
                        if registry.binding(route_id).capabilities.synthetic_fixture else "VALID")
        native_order_id = row["target_order_id"]
        client_order_id = row["client_order_id"]
        cursor, pages, incomplete = None, 0, False
        while pages < 8:
            page = await asyncio.wait_for(
                registry.invoke(route_id, "get_fills", intent.instrument.raw_symbol,
                                start_ns=start_ns, end_ns=end_ns,
                                **({"cursor": cursor} if cursor is not None else {})),
                owner.budgets.prepare_timeout_seconds)
            pages += 1
            complete = bool(page.get("complete"))
            for fill_row in page.get("rows", ()):
                if not isinstance(fill_row, Mapping):
                    continue
                if (fill_row.get("symbol") != intent.instrument.raw_symbol
                        or (str(fill_row.get("order_id", "")) != str(native_order_id or "")
                            and str(fill_row.get("client_order_id", "")) != client_order_id)):
                    continue
                try:
                    if fill_row.get("side") != intent.side.value:
                        raise GateBlocked("Native fill side contradicts its owned order identity")
                    if (not isinstance(document.get("valuation_currency"), str)
                            or fill_row.get("fee_currency") != document["valuation_currency"]):
                        raise GateBlocked("Native fill fee currency lacks the exact valuation-unit proof")
                    applied = owner.apply_fill(
                        action, fill_id=fill_row["event_id"],
                        quantity=self._money(fill_row["quantity"], "native fill quantity"),
                        price=self._money(fill_row["price"], "native fill price"),
                        fee=self._money(fill_row.get("fee", "0"), "native fill fee"),
                        order_id=client_order_id,
                        fact_quality=fact_quality)
                    document["applied_fills"] = document.get("applied_fills", 0) + (1 if applied else 0)
                except Exception as error:  # visible persisted per-leg fault
                    document["fill_fault"] = str(error)[:200]
                    document["resolved"] = False
                    return document
            if complete:
                break
            if not page.get("cursor"):
                incomplete = True
                break
            cursor = page["cursor"]
        if not complete:
            incomplete = True
        document["fill_pages"] = pages
        if incomplete:
            document["history_incomplete"] = True
            document["resolved"] = False
        return document

    async def reconcile(self, group_id: str, *, registry, start_ns: int, end_ns: int,
                        on_event=None) -> dict:
        """Recover current child truth through the real owner query/fill paths.

        Native partial history, cursor exhaustion or any gap keeps the group
        explicitly RECONCILING and fences NEW; owned read/cancel/reconcile stays
        available after the deadline. Fills are acknowledged only as exact
        per-fill receipts through the once-only owner fill/outbox path.
        """
        facts = self.ledger.group_facts(group_id)
        plan_fact = next((fact["document"] for fact in facts if fact["fact_id"] == "plan"), None)
        state_row = self.ledger.group_state(group_id)
        if plan_fact is None or state_row is None:
            raise ValueError("Unknown execution group")
        if (isinstance(start_ns, bool) or isinstance(end_ns, bool)
                or not isinstance(start_ns, int) or not isinstance(end_ns, int)
                or not 0 <= start_ns <= end_ns or end_ns > 2**63 - 1 or start_ns < plan_fact["created_ns"] - 10**9
                or end_ns - start_ns > 366 * 86400 * 10**9):
            raise ValueError("Reconciliation requires an explicit bounded history interval")
        legs = self.ledger.group_legs(group_id)
        documents, unresolved = [], False
        prior_kind = sum(1 for fact in facts if fact["kind"] == "reconcile")
        for index, leg in enumerate(legs, 1):
            leg_id = leg["leg_id"]
            owner = self.owners.get(leg["scope_key"])
            document = {"leg_id": leg_id, "scope_key": leg["scope_key"],
                        "intent_id": leg["intent_id"], "resolved": True,
                        "valuation_currency": leg["document"].get("valuation_currency")}
            if owner is None:
                document.update(state="UNKNOWN", resolved=False,
                                reason="missing_child_owner")
                unresolved = True
                documents.append(document)
                self._record(group_id, "reconcile:" + str(prior_kind + index), "reconcile",
                             {"group_id": group_id, "legs": [document]})
                continue
            row = owner.ledger.get(leg["intent_id"])
            if row is None:
                document.update(state="UNKNOWN", resolved=False, reason="missing_child_row")
                unresolved = True
                documents.append(document)
                self._record(group_id, "reconcile:" + str(prior_kind + index), "reconcile",
                             {"group_id": group_id, "legs": [document]})
                continue
            action = self._leg_query_action(owner, row, leg_id)
            try:
                facts_local = await owner.query_order(action, registry=registry)
            except Exception as error:
                document.update(state="RECONCILING", resolved=False,
                                query_fault=type(error).__name__, reason=str(error)[:120])
                unresolved = True
                documents.append(document)
                self._record(group_id, "reconcile:" + str(prior_kind + index), "reconcile",
                             {"group_id": group_id, "legs": [document]})
                continue
            status = facts_local.get("status")
            if (row["state"] in {"CANCEL_PENDING", "UNKNOWN", "LEGACY_UNKNOWN"}
                    and isinstance(status, str)
                    and status.casefold() in {"canceled", "cancelled", "expired", "rejected"}):
                try:
                    row = owner.confirm_cancel(action)
                except Exception as error:
                    document.update(cancel_confirm_fault=type(error).__name__,
                                    reason=str(error)[:120], resolved=False)
                    unresolved = True
            elif (row["state"] in {"UNKNOWN", "LEGACY_UNKNOWN"}
                    and isinstance(status, str)
                    and status.casefold() in {"new", "submitted", "accepted", "acknowledged"}):
                # A proven native open order repairs the crash-instigated
                # UNKNOWN authoritative state to its truthful ACKNOWLEDGED
                # lifecycle step; no fact is manufactured beyond the query.
                owner.ledger.outcome(row["intent_id"], "SUBMITTED",
                                     {"probe": "native_open_query", "status": status})
                row = owner.ledger.get(row["intent_id"])
            document.update(status=status, terminal=bool(facts_local.get("terminal")),
                            native_cumulative=str(facts_local.get("cumulative_quantity", "0")))
            try:
                await self._apply_native_fills(owner, action, owner.ledger.get(leg["intent_id"]),
                                               registry=registry, start_ns=start_ns,
                                               end_ns=end_ns, document=document)
            except Exception as error:
                document.update(state="RECONCILING", resolved=False, reason=str(error)[:160])
                unresolved = True
            updated = owner.ledger.get(leg["intent_id"])
            document.update(state=updated["state"], filled=updated["filled"],
                            fees=updated["fees"], notional=updated["notional"],
                            reservation=updated["reservation"],
                            local_cumulative=updated["filled"])
            native_cum = self._money(facts_local.get("cumulative_quantity", "0"),
                                     "native cumulative fill")
            if Decimal(updated["filled"]) != native_cum:
                # Any disagreement is a reconciliation gap; neither side is
                # allowed to overwrite or manufacture the other.
                document["fill_gap"] = str(exact_sum(native_cum, Decimal(updated["filled"]).copy_negate()))
                document["resolved"] = False
                document["unresolved"] = True
                unresolved = True
            known_open = (isinstance(status, str) and status.casefold() in
                          {"new", "submitted", "accepted", "acknowledged"})
            if (row["state"] in {"UNKNOWN", "LEGACY_UNKNOWN", "AUTHORIZED", "HANDOFF",
                                  "CANCEL_PENDING"}
                    or row["state"] == "SUBMITTED" and not known_open):
                document["resolved"] = False
            if not document["resolved"]:
                document["unresolved"] = True
                unresolved = True
            documents.append(document)
            self._record(group_id, "reconcile:" + str(prior_kind + index), "reconcile",
                         {"group_id": group_id, "legs": [document]})
        prior_state = state_row["state"]
        if unresolved:
            new_state = "RECONCILING"
        elif prior_state in {"UNKNOWN", "RECONCILING", "PREPARING"}:
            new_state = "RECONCILED"
        else:
            new_state = prior_state
        if new_state != prior_state:
            self._save_state(group_id, new_state, {"group_id": group_id,
                "fingerprint": plan_fact["fingerprint"], "reconciled": not unresolved})
        followup_queue = []
        callback = self._followup_guard(group_id, plan_fact["plan"]["group_policy"],
                                        on_event, followup_queue, plan_fact) if on_event is not None else None
        for leg in legs:
            owner = self.owners.get(leg["scope_key"])
            dispatch = owner._source_dispatch.get(plan_fact["strategy_id"]) if owner else None
            if dispatch is not None and callback is not None:
                await owner.deliver_feedback(plan_fact["strategy_id"], callback)
        for queued_group, queued_plan in followup_queue:
            dispatch = next((owner._source_dispatch.get(plan_fact["strategy_id"])
                             for owner in self.owners.values()
                             if owner._source_dispatch.get(plan_fact["strategy_id"])), None)
            if dispatch is None:
                self._record(queued_group, "followup-fault:" + queued_plan.plan_id,
                             "followup-fault", {"group_id": queued_group,
                             "plan_id": queued_plan.plan_id,
                             "fault": {"type": "GateBlocked", "detail": "source dispatch context unavailable"}})
                continue
            try:
                await self._dispatch_followup(
                    queued_group, queued_plan,
                    policy=plan_fact["plan"]["group_policy"],
                    registry=dispatch["registry"], readiness=dispatch["readiness"],
                    guard=dispatch["guard"], evidence=dispatch["evidence"])
            except Exception as error:
                self._record(queued_group, "followup-fault:" + queued_plan.plan_id,
                             "followup-fault", {"group_id": queued_group,
                             "plan_id": queued_plan.plan_id,
                             "fault": {"type": type(error).__name__, "detail": str(error)[:200]}})
        return {"group_id": group_id, "state": new_state, "reconciled": not unresolved,
                "legs": documents, "report": self.report(group_id)}

    # ------------------------------------------------- explicit funding facts
    def record_group_funding(self, group_id: str, receipts) -> dict:
        """Attribute explicit native funding receipts to group allocations.

        Bounded native period/receipt identity, exact currency/amount/version
        facts and source-declared per-group allocations. An identical replay is
        a durable no-op; a changed duplicate is an immutable-fact fault. Total
        conservation per receipt across groups plus the explicit unallocated
        remainder is enforced at record time; nothing is invented or netted.
        """
        if self._plan_fact(group_id) is None or self.ledger.group_state(group_id) is None:
            raise GateBlocked("Funding receipt attribution requires an existing execution group")
        if not isinstance(receipts, (list, tuple)) or not receipts or len(receipts) > 64:
            raise ValueError("Funding receipts require a bounded explicit sequence")
        recorded, replayed = [], []
        allowed = {"receipt_id", "period_ns", "currency", "amount", "version",
                   "raw", "event_id", "allocations", "scope_key", "native_account_id", "source"}
        for receipt in receipts:
            if (not isinstance(receipt, Mapping)
                    or not {"receipt_id", "period_ns", "currency", "amount", "version",
                            "scope_key", "native_account_id", "source", "raw", "allocations"}.issubset(set(receipt))
                    or not set(receipt).issubset(allowed)):
                raise ValueError("Funding receipt requires native period/receipt/"
                                 "currency/amount identity and bounded fields")
            receipt_id = bounded_identity(str(receipt["receipt_id"]), "funding receipt_id")
            scope_key = bounded_identity(str(receipt["scope_key"]), "funding scope_key")
            owner = self.owners.get(scope_key)
            if (owner is None or str(receipt["native_account_id"]) != owner.ledger.native_account_id
                    or receipt["source"] != "native_funding_receipt"
                    or not isinstance(receipt["raw"], Mapping)):
                raise GateBlocked("Funding receipt lacks matching native child-owner attribution")
            currency = bounded_identity(str(receipt["currency"]), "funding currency")
            if len(currency) > 16:
                raise ValueError("Funding currency requires a bounded native identity")
            period = receipt["period_ns"]
            if isinstance(period, bool) or not isinstance(period, int) or not 0 < period <= 2**63 - 1:
                raise ValueError("Funding receipt requires an exact native period")
            if isinstance(receipt["amount"], (bool, float)):
                raise ValueError("Funding amount must be an exact decimal, never a float or bool")
            amount = self._money(receipt["amount"], "funding amount")
            allocation_input = receipt.get("allocations")
            if allocation_input is not None and not isinstance(allocation_input, Mapping):
                raise ValueError("Funding allocations must be an explicit bounded mapping")
            allocations = {}
            for key, value in (allocation_input or {}).items():
                target = bounded_identity(str(key), "funding allocation group")
                target_fact = self._plan_fact(target)
                source_fact = self._plan_fact(group_id)
                if (target_fact is None or source_fact is None
                        or target_fact["document"].get("strategy_id") != source_fact["document"].get("strategy_id")
                        or target_fact["document"].get("config_version") != source_fact["document"].get("config_version")):
                    raise GateBlocked("Funding allocation references an unknown execution group")
                if isinstance(value, (bool, float)):
                    raise ValueError("Funding allocation must be an exact decimal, never a float or bool")
                allocations[target] = self._money(value, "funding allocation")
            allocated_total = exact_sum(*allocations.values()) if allocations else Decimal(0)
            unallocated = exact_sum(amount, allocated_total.copy_negate())
            version = receipt.get("version")
            if isinstance(version, bool) or not isinstance(version, int) or not 0 <= version <= 2**63 - 1:
                raise ValueError("Funding receipt version must be an exact native version integer")
            if (amount >= 0 and any(value < 0 for value in allocations.values())
                    or amount < 0 and any(value > 0 for value in allocations.values())
                    or abs(allocated_total) > abs(amount)):
                raise GateBlocked("Funding allocation exceeds or contradicts its signed native receipt")
            document = {"receipt_id": receipt_id, "scope_key": scope_key,
                        "native_account_id": owner.ledger.native_account_id,
                        "source": "native_funding_receipt", "period_ns": period,
                        "currency": currency, "amount": str(amount),
                        "version": version,
                        "allocations": {key: str(value) for key, value in allocations.items()},
                        "unallocated": str(unallocated),
                        "raw": dict(receipt["raw"]) if isinstance(receipt.get("raw"), Mapping) else None,
                        "source_allocation": True}
            # Version, period, currency, amount and allocations are immutable
            # contents of one payment identity, not separate payment keys.
            identity = canonical([scope_key, owner.ledger.native_account_id, receipt_id])
            key = "funding-receipt:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
            receipt_store = owner.ledger
            for old_group in receipt_store.group_facts():
                if old_group["kind"] == "funding-receipt" and old_group["fact_id"] == key:
                    if canonical(old_group["document"]) != canonical(document):
                        raise ValueError("Immutable native funding receipt collision with its original contents")
                    replayed.append(receipt_id)
                    break
            else:
                if receipt_store is self.ledger:
                    created = self._record(group_id, key, "funding-receipt", document)
                else:
                    created = receipt_store.record_group_fact(
                        group_id, key, "funding-receipt", document,
                        max_groups=self.max_groups, max_facts=self.max_facts,
                        max_bytes=self.max_bytes)
                    # This anchor copy is a cache only; child-native receipt
                    # remains authoritative if the mirror write is interrupted.
                    if created:
                        self._record(group_id, key, "funding-receipt", document)
                if created:
                    recorded.append(receipt_id)
                else:
                    replayed.append(receipt_id)
        result = self.report(group_id)
        result["funding_receipts"] = {"group_id": group_id, "recorded": recorded,
                                      "replayed": replayed}
        return result

    def report(self, group_id: str) -> dict:
        state_row = self.ledger.group_state(group_id)
        if state_row is None:
            raise ValueError("Unknown execution group")
        facts = self.ledger.group_facts(group_id)
        plan_fact = next((fact["document"] for fact in facts if fact["fact_id"] == "plan"), None)
        if plan_fact is None:
            raise ValueError("Execution group lacks its immutable plan")
        legs = self.ledger.group_legs(group_id)
        # Reservations and states come from the CURRENT child owner ledgers,
        # never the immutable initial reservation snapshots (historical only).
        # Account reads are non-atomic serial facts; a missing/unreadable child
        # row stays explicitly unknown and is never coerced to zero.
        reservations = {}
        unknown_legs = []
        current_by_leg = {}
        current_states = []
        for leg in legs:
            owner = self.owners.get(leg["scope_key"])
            row = None
            if owner is not None:
                try:
                    row = owner.ledger.get(leg["intent_id"])
                    account_version = owner.ledger.account_version()
                except Exception:
                    row, account_version = None, None
            if row is None:
                unknown_legs.append(leg["leg_id"])
                current_states.append(None)
                current_by_leg[leg["leg_id"]] = {
                    "state": "UNOBSERVED", "unresolved": True,
                    "reason": "current child ledger row is missing or unreadable"}
                continue
            current_states.append(row["state"])
            key = "scope:" + leg["scope_key"]
            reservations[key] = exact_sum(
                reservations.get(key, Decimal(0)),
                positive_decimal(row["reservation"], "current reservation", zero=True))
            current_by_leg[leg["leg_id"]] = {
                "state": row["state"], "filled": row["filled"],
                "fees": row["fees"], "notional": row["notional"],
                "reservation": row["reservation"],
                "reserve_unit": row["reserve_unit"],
                "native_order_id": row["target_order_id"] or None,
                "cancel_state": row["cancel_state"] or None,
                "account_version": account_version,
                "unresolved": row["state"] in set(ExecutionLedger.UNRESOLVED),
            }
        legs_report = []
        definition_by_leg = {fact["fact_id"].split(":", 1)[1]: fact["document"]
                             for fact in facts if fact["kind"] in {"leg-definition", "followup-definition"}
                             and ":" in fact["fact_id"]}
        for leg in legs:
            entry = dict(leg)
            entry["current"] = current_by_leg.get(leg["leg_id"], {"state": "UNOBSERVED",
                                                                  "unresolved": True})
            definition = definition_by_leg.get(leg["leg_id"], {})
            entry["side"] = definition.get("side")
            entry["quantity"] = definition.get("quantity")
            native_facts = entry["document"]
            entry["contract_multiplier"] = native_facts.get("contract_multiplier")
            entry["valuation_currency"] = native_facts.get("valuation_currency")
            entry["settlement_currency"] = native_facts.get("settlement_currency")
            entry["current"].update({
                "side": definition.get("side"),
                "quantity": definition.get("quantity"),
                "contract_multiplier": entry["contract_multiplier"],
                "valuation_currency": entry["valuation_currency"],
                "settlement_currency": entry["settlement_currency"],
            })
            legs_report.append(entry)
        state = state_row["state"]
        if any(value in {"UNKNOWN", "LEGACY_UNKNOWN"} for value in current_states if value):
            # A native UNKNOWN child can never leave the group looking
            # ACKNOWLEDGED; it requires exact native reconciliation.
            if state in {"ACKNOWLEDGED", "UNKNOWN"}:
                state = "UNKNOWN"
        elif state == "ACKNOWLEDGED" and any(value is None for value in current_states):
            state = "RECONCILING"
        return {
            "group_id": group_id, "mode": plan_fact["mode"],
            "state": state, "plan_fingerprint": plan_fact["fingerprint"],
            "strategy_id": plan_fact["strategy_id"],
            "config_version": plan_fact["config_version"],
            "base_asset": plan_fact["plan"]["group_policy"]["base_asset"],
            "legs": legs_report, "facts": [{key: fact[key] for key in ("seq", "fact_id", "kind", "sha256")}
                                    for fact in facts],
            "pending_reservation_by_currency": {key: str(value) for key, value in reservations.items()},
            "pending_reservation_unknown_legs": unknown_legs,
            "initial_reservation_facts_are_historical": True,
            "funding": self._funding_report(self._funding_facts(group_id), group_id),
            "followup_faults": [fact["fact_id"] for fact in facts
                                if fact["kind"] in {"followup-fault"}],
            "reconciliation_facts": [fact["fact_id"] for fact in facts
                                     if fact["kind"] in {"reconcile"}],
            "cross_ledger_atomic_commit": False,
            "distributed_atomic_fill": False,
            "automatic_retry": False, "automatic_hedge": False,
            "economic_neutrality": "NOT_ASSESSED",
            "duplicate": False,
        }

    def _funding_facts(self, group_id):
        """Read authoritative native-child receipt facts and dedupe mirrors."""
        result = {}
        for owner in self.owners.values():
            for fact in owner.ledger.group_facts():
                document = fact["document"]
                if (fact["kind"] != "funding-receipt"
                        or document.get("scope_key") != owner.ledger.scope.key
                        or document.get("native_account_id") != owner.ledger.native_account_id
                        or document.get("source") != "native_funding_receipt"
                        or group_id not in document.get("allocations", {})):
                    continue
                key = fact["fact_id"]
                old = result.get(key)
                if old is not None and canonical(old["document"]) != canonical(document):
                    raise ValueError("Native funding source and anchor mirror facts conflict")
                result[key] = fact
        return list(result.values())

    def _funding_report(self, facts, group_id):
        """Explicit attributed funding facts only; absent receipts stay UNKNOWN.

        Receipts are shared across groups via their source-declared
        allocations; a group report attributes exactly its own declared share.
        """
        receipts = [fact["document"] for fact in facts
                    if fact["kind"] == "funding-receipt"
                    and isinstance(fact["document"].get("allocations"), Mapping)
                    and group_id in fact["document"]["allocations"]]
        if not receipts:
            return {"status": "UNKNOWN", "reason": "no explicit native funding receipt supplied"}
        attributed, unallocated = {}, {}
        for document in receipts:
            currency = document["currency"]
            attributed[currency] = exact_sum(attributed.get(currency, Decimal(0)),
                Decimal(document["allocations"].get(group_id, "0")))
            unallocated[currency] = exact_sum(unallocated.get(currency, Decimal(0)),
                                              Decimal(document["unallocated"]))
        return {"status": "ATTRIBUTED", "receipts": [document["receipt_id"] for document in receipts],
                "attributed_by_currency": {key: str(value) for key, value in attributed.items()},
                "unallocated_by_currency": {key: str(value) for key, value in unallocated.items()},
                "cross_group_conservation": "explicit declarations only; no invented allocation"}
