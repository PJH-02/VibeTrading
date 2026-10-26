"""Bounded paper-only cross-account observations; no submission or retry authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from types import MappingProxyType

from .execution_ledger import AccountScope, canonical, exact_difference, exact_product, exact_sum, positive_decimal
from core.models import Side
from .trading_contracts import ProductSpec, TradingMode, bounded_identity, validate_scope


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
