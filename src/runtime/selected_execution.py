"""Strict configuration and bounded assembly glue for selected strategies.

PAPER delegates to the existing local matcher. Native routes delegate to the
existing RealExecutionRunner and ExecutionOwner through a bounded A1 bridge.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import asyncio
import inspect
import math
import time
from collections import deque
from types import MappingProxyType


_REGISTRATION_FIELDS = frozenset({
    "job_id", "module", "strategy_version", "config_version", "config",
    "research_receipt", "paper_permission", "required_feeds", "warmup_bars",
    "accounts", "risk",
})
_MODES = {"PAPER": "paper", "TESTNET": "sandbox", "LIVE": "live"}


def _text(value, name, limit=256):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value.encode("utf-8")) > limit):
        raise ValueError(name + " must be a bounded explicit string")
    return value


def _freeze_json(value, name):
    import json
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(name + " mapping keys must be strings")
        return MappingProxyType({key: _freeze_json(child, name) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(child, name) for child in value)
    if type(value) is float and math.isfinite(value):
        return value
    if value is None or type(value) in {str, int, bool}:
        return value
    raise ValueError(name + " must contain only finite JSON values")


def _thaw_json(value):
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


@dataclass(frozen=True)
class StrategySelection:
    mode: str
    venue: str
    account: str
    product: str
    environment: str
    registration: Mapping
    max_queue_items: int
    max_queue_bytes: int
    max_queue_age_ns: int
    snapshot_charge_bytes: int

    @property
    def job_id(self):
        return self.registration["job_id"]


def parse_strategy_selections(value):
    """Parse a bounded exact route list; no import or account access occurs."""
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("strategy_selections must be a bounded list")
    result = []
    seen = set()
    for entry in value:
        fields = {"mode", "venue", "account", "product", "environment", "registration",
                  "queue"}
        if not isinstance(entry, Mapping) or set(entry) != fields:
            raise ValueError("strategy selection requires exact route fields")
        mode = entry["mode"]
        if mode not in _MODES:
            raise ValueError("strategy selection mode must be PAPER, TESTNET, or LIVE")
        environment = _text(entry["environment"], "execution environment", 32)
        if environment != _MODES[mode]:
            raise ValueError("strategy mode and execution environment do not match")
        scope = tuple(_text(entry[name], name, 128) for name in ("venue", "account", "product"))
        registration = entry["registration"]
        if not isinstance(registration, Mapping) or set(registration) != _REGISTRATION_FIELDS:
            raise ValueError("strategy registration requires the canonical explicit fields")
        for name in ("job_id", "strategy_version", "config_version"):
            _text(registration[name], name, 128)
        job_id = _text(registration["job_id"], "job_id", 128)
        if job_id in seen:
            raise ValueError("duplicate selected strategy job_id")
        seen.add(job_id)
        from strategies.contract import _canonical_module_name
        _canonical_module_name(registration["module"])
        if (not isinstance(registration["config"], Mapping)
                or not isinstance(registration["research_receipt"], Mapping)):
            raise ValueError("strategy config and research receipt must be explicit mappings")
        if type(registration["paper_permission"]) is not bool:
            raise ValueError("paper_permission must be an exact boolean")
        feeds, accounts = registration["required_feeds"], registration["accounts"]
        if (not isinstance(feeds, (list, tuple)) or not feeds or len(feeds) > 256
                or any(not isinstance(item, str) or not item or len(item.encode("utf-8")) > 512 for item in feeds)
                or len(set(feeds)) != len(feeds)):
            raise ValueError("required_feeds must be an explicit bounded unique list")
        if (not isinstance(accounts, (list, tuple)) or not 0 < len(accounts) <= 16
                or any(not isinstance(item, str) or not item or len(item.encode("utf-8")) > 512 for item in accounts)
                or len(set(accounts)) != len(accounts)):
            raise ValueError("accounts must be an explicit bounded unique list")
        warmup = registration["warmup_bars"]
        if type(warmup) is not int or not 0 <= warmup <= 100_000:
            raise ValueError("warmup_bars must be an explicit bounded nonnegative integer")
        risk = registration["risk"]
        risk_fields = {"funding_buffer", "account_notional_limit", "strategy_notional_limit",
            "account_capital_limit", "strategy_capital_limit", "margin_rate"}
        if not isinstance(risk, Mapping) or set(risk) != set(accounts):
            raise ValueError("risk requires exact account bindings")
        from .strategy_runtime import StrategyRisk
        for account, values in risk.items():
            if not isinstance(values, Mapping) or set(values) != risk_fields:
                raise ValueError("risk requires exact StrategyRisk fields")
            StrategyRisk(**dict(values))
        import json
        try:
            encoded_registration = json.dumps(dict(registration), sort_keys=True,
                separators=(",", ":"), allow_nan=False, default=None).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("strategy registration must be finite canonical JSON") from error
        if len(encoded_registration) > 65_536:
            raise ValueError("strategy registration exceeds its 64 KiB bound")
        queue = entry["queue"]
        queue_fields = {"max_items", "max_bytes", "max_age_ns", "snapshot_charge_bytes"}
        if not isinstance(queue, Mapping) or set(queue) != queue_fields:
            raise ValueError("strategy queue requires exact bounded limits")
        limits = tuple(queue[name] for name in ("max_items", "max_bytes", "max_age_ns", "snapshot_charge_bytes"))
        if any(type(item) is not int or item <= 0 for item in limits):
            raise ValueError("strategy queue limits require positive integers")
        if (limits[0] > 4096 or limits[1] > 64 * 1024 * 1024
                or limits[2] > 300_000_000_000 or limits[3] > limits[1]):
            raise ValueError("strategy queue limits exceed explicit hard bounds")
        result.append(StrategySelection(mode, *scope, environment,
            _freeze_json(dict(registration), "strategy registration"), *limits))
    return tuple(result)


def activate_paper_selections(plan, paper_runtime, action_builders):
    """Register every configured PAPER selection through the existing host.

    Builder injection is explicit because sizing/action policy is owned by the
    caller. A nonempty configured list is never silently ignored.
    """
    selections = tuple(getattr(plan, "strategy_selections", ()))
    if not selections:
        return ()
    papers = tuple(item for item in selections if item.mode == "PAPER")
    if not papers:
        return ()
    if paper_runtime is None:
        raise ValueError("configured strategies require an injected local runtime assembly")
    if any(item.mode != "PAPER" for item in papers):
        raise ValueError("native selections require a NativeSelectionBinding")
    if not papers:
        return ()
    if not isinstance(action_builders, Mapping) or set(action_builders) != {item.job_id for item in papers}:
        raise ValueError("selected PAPER strategies require one exact injected action builder per job")
    registrations = []
    for selection in papers:
        scope = (selection.venue, selection.account, selection.product, selection.environment)
        account_keys = selection.registration["accounts"]
        if not isinstance(account_keys, (list, tuple)) or not account_keys:
            raise ValueError("selected PAPER strategy requires an explicit account binding")
        matches = []
        for key in account_keys:
            matcher = paper_runtime.accounts.get(key)
            if matcher is None:
                raise ValueError("selected PAPER account is not in the injected runtime")
            actual = matcher.ledger.scope
            if (actual.venue, actual.account, actual.product, actual.environment) != scope:
                raise ValueError("selected PAPER route differs from its durable account scope")
            matches.append(key)
        if set(account_keys) != set(matches):
            raise ValueError("selected PAPER account bindings are incomplete")
        registrations.append(paper_runtime.register_configured(
            _thaw_json(selection.registration), action_builder=action_builders[selection.job_id]))
    return tuple(registrations)


@dataclass(frozen=True)
class NativeSelectionBinding:
    """Explicit local assembly of an already constructed native owner/source."""
    runner: object
    owner: object
    source: object
    registry: object
    readiness: object
    evidence_provider: object
    registration_facts: Mapping
    route_id: str
    source_trust: str = "trusted_cooperative"


@dataclass
class _QueuedDispatch:
    result: object
    snapshot: object
    encoded_bytes: int
    queued_ns: int


class _SelectedSourceProxy:
    """Validate the original source plan against its configured route set."""
    def __init__(self, dispatcher):
        self._dispatcher = dispatcher
        self.STRATEGY_ID = dispatcher.strategy_id
        self.ACTION_PROTOCOL_VERSION = 1
        self.config_version = dispatcher.selection.registration["config_version"]

    def on_event(self, event, data):
        self._dispatcher._begin_source_dispatch()
        result = self._dispatcher.binding.source.on_event(event, data)
        return self._validate(result)

    def on_order_event(self, event, context):
        self._dispatcher._begin_source_dispatch()
        result = self._dispatcher.binding.source.on_order_event(event, context)
        return self._validate(result)

    def _validate(self, plan):
        from execution.trading_contracts import StrategyActionPlan
        if plan is None or plan == []:
            return plan
        if (not isinstance(plan, StrategyActionPlan)
                or plan.strategy_id != self.STRATEGY_ID
                or plan.config_version != self.config_version):
            raise ValueError("selected source must retain its exact strategy/config action plan")
        grouped = plan.group_submit_mode is not None or plan.group_followup or any(
            action.group_id is not None for action in plan.actions)
        if grouped and self._dispatcher.binding.runner.group_coordinator is None:
            raise ValueError("selected grouped action requires the existing injected group coordinator")
        allowed = self._dispatcher.allowed_scopes
        for action in plan.actions:
            if (action.scope.key not in allowed or action.strategy_id != self.STRATEGY_ID
                    or action.config_version != self.config_version
                    or action.mode.value != self._dispatcher.selection.environment):
                raise ValueError("source action differs from its exact selected scope/mode/config")
            declared = self._dispatcher.account_scopes[action.scope.key]
            if (action.scope.venue, action.scope.account, action.scope.product,
                    action.scope.environment) != declared:
                raise ValueError("source action escaped its declared account namespace")
        return plan


@dataclass(frozen=True)
class _PromotionReceipt:
    scope_key: str
    native_account_id: str
    strategy_id: str
    config_version: str
    ledger_version: int
    observed_ns: int
    expires_ns: int
    control_epochs: tuple[tuple[str, int], ...]
    proof_sha256: str


class NativeSelectionDispatcher:
    """Bounded apply-to-owner bridge; callback only enqueues immutable facts."""
    def __init__(self, selection, binding):
        from execution.execution_owner import ExecutionOwner
        from runtime.real_runner import RealExecutionRunner
        from strategies.contract import load_strategy, compile_subscriptions
        if not isinstance(binding, NativeSelectionBinding):
            raise TypeError("native route requires an explicit NativeSelectionBinding")
        self.selection, self.binding = selection, binding
        if not isinstance(binding.runner, RealExecutionRunner) or not isinstance(binding.owner, ExecutionOwner):
            raise TypeError("native binding requires the existing RealExecutionRunner and ExecutionOwner")
        if binding.runner.owner is not binding.owner:
            raise ValueError("native runner and injected owner must be the same owner")
        if (not callable(binding.evidence_provider) and not isinstance(binding.evidence_provider, Mapping)):
            raise ValueError("native route requires an explicit evidence provider")
        if (not callable(binding.readiness) and not isinstance(binding.readiness, Mapping)):
            raise ValueError("native route requires explicit readiness providers")
        if binding.source_trust != "trusted_cooperative":
            raise ValueError("native local source requires explicit trusted_cooperative selection")
        scope = binding.owner.ledger.scope
        if ((scope.venue, scope.account, scope.product, scope.environment) !=
                (selection.venue, selection.account, selection.product, selection.environment)):
            raise ValueError("native route differs from its durable owner account scope")
        if binding.runner.config.account_id != scope.account:
            raise ValueError("native runner account differs from its durable owner scope")
        if not isinstance(binding.route_id, str) or not binding.route_id:
            raise ValueError("native selection requires one exact existing registry route id")
        route = binding.registry.route(binding.route_id)
        if (route.account_scope.key != scope.key or route.environment != selection.environment
                or route.venue != selection.venue or route.product != selection.product
                or route.native_account_id != binding.owner.ledger.native_account_id):
            raise ValueError("native registry route differs from selected physical account scope")
        if selection.mode == "LIVE":
            self._check_live_authorization(getattr(binding.source, "STRATEGY_ID",
                getattr(type(binding.source), "STRATEGY_ID", "")))
        loaded = load_strategy(selection.registration["module"])
        module = loaded.module
        source_module = inspect.getmodule(type(binding.source))
        if source_module is not module or getattr(binding.source, "STRATEGY_ID", loaded.strategy_id) != loaded.strategy_id:
            raise ValueError("native source instance must come from the canonical configured strategy module")
        if type(getattr(module, "ACTION_PROTOCOL_VERSION", None)) is not int or module.ACTION_PROTOCOL_VERSION != 1:
            raise ValueError("native selected strategy requires ACTION_PROTOCOL_VERSION=1")
        if not callable(getattr(binding.source, "on_event", None)) or not callable(
                getattr(binding.source, "on_order_event", None)):
            raise ValueError("native selected strategy requires market and typed order callbacks")
        configure = getattr(binding.source, "configure", None)
        if callable(configure):
            configured = configure(selection.registration["config"])
            if inspect.isawaitable(configured):
                if inspect.iscoroutine(configured): configured.close()
                raise ValueError("native strategy configure hook must be synchronous")
        validate = getattr(binding.source, "validate_registration", None)
        facts = binding.registration_facts
        expected_accounts = selection.registration.get("accounts")
        if not isinstance(facts, Mapping) or not isinstance(facts.get("accounts"), Mapping):
            raise ValueError("native source registration requires explicit account-scope facts")
        if not isinstance(expected_accounts, (list, tuple)) or not 0 < len(expected_accounts) <= 2:
            raise ValueError("native selection requires one account or one existing two-leg group")
        if len(expected_accounts) > 1:
            coordinator = binding.runner.group_coordinator
            owners = getattr(coordinator, "owners", None)
            if not isinstance(owners, Mapping) or set(owners) != set(expected_accounts):
                raise ValueError("selected group scopes must exactly match the existing coordinator owners")
        account_facts_by_key = {}
        for key in expected_accounts:
            account_facts = facts["accounts"].get(key)
            account_scope = account_facts.get("scope") if isinstance(account_facts, Mapping) else None
            if (not isinstance(account_scope, Mapping)
                    or account_facts.get("mode") != selection.environment):
                raise ValueError("native source registration facts differ from selected account mode")
            scope_tuple = tuple(account_scope.get(name) for name in ("venue", "account", "product", "environment"))
            owner = binding.runner.group_coordinator.owners.get(key) if len(expected_accounts) > 1 else binding.owner
            if (owner.ledger.scope.key != key or scope_tuple != (
                    owner.ledger.scope.venue, owner.ledger.scope.account,
                    owner.ledger.scope.product, owner.ledger.scope.environment)):
                raise ValueError("selected account facts differ from their existing durable owner")
            account_facts_by_key[key] = (account_scope, owner)
        if (selection.account not in {facts["accounts"][key]["scope"]["account"] for key in expected_accounts}
                or not any(tuple(facts["accounts"][key]["scope"].get(name) for name in
                    ("venue", "account", "product", "environment")) ==
                    (selection.venue, selection.account, selection.product, selection.environment)
                    for key in expected_accounts)):
            raise ValueError("selected primary route is absent from registration account scopes")
        if callable(validate):
            validate(facts)
        if isinstance(binding.readiness, Mapping) and set(binding.readiness) != set(expected_accounts):
            raise ValueError("group readiness providers must exactly match selected owner scopes")
        if isinstance(binding.readiness, Mapping) and any(not callable(item) for item in binding.readiness.values()):
            raise ValueError("every selected group readiness binding must be callable")
        if isinstance(binding.evidence_provider, Mapping) and set(binding.evidence_provider) != set(expected_accounts):
            raise ValueError("group evidence providers must exactly match selected owner scopes")
        if isinstance(binding.evidence_provider, Mapping) and any(not callable(item) for item in binding.evidence_provider.values()):
            raise ValueError("every selected group evidence binding must be callable")
        self.subscriptions = compile_subscriptions(loaded.subscriptions)
        self.native_symbols = tuple(item.get("symbol") for item in loaded.subscriptions
            if isinstance(item, Mapping) and isinstance(item.get("symbol"), str))
        self.queue = asyncio.Queue(maxsize=selection.max_queue_items)
        self.queued_bytes = 0
        self.task = None
        self.blocked = False
        self.results = deque(maxlen=128)
        self.errors = deque(maxlen=128)
        self.strategy_id = loaded.strategy_id
        self.allowed_scopes = frozenset(expected_accounts)
        self.account_scopes = {key: tuple(facts["accounts"][key]["scope"][name]
            for name in ("venue", "account", "product", "environment")) for key in expected_accounts}
        aliases = binding.registry.aliases_for_strategy(loaded.strategy_id)
        route_scopes = {}
        for alias in aliases:
            route = binding.registry.route(alias.route_id)
            route_scopes[route.account_scope.key] = alias.route_id
        if any(route_scopes.get(key) is None for key in self.allowed_scopes):
            raise ValueError("every selected native account requires an existing named route")
        if route_scopes.get(scope.key) != binding.route_id:
            raise ValueError("selected primary owner route differs from the named strategy alias")
        self._scope_dispatchers = {scope.key: self}
        for key in expected_accounts:
            if key == scope.key:
                continue
            account_scope, account_owner = account_facts_by_key[key]
            route_id = route_scopes.get(key)
            if not route_id:
                raise ValueError("every selected native account requires an existing named route")
            account_route = binding.registry.route(route_id)
            scope_tuple = tuple(account_scope.get(name) for name in
                ("venue", "account", "product", "environment"))
            if (account_route.account_scope.key != key or account_route.environment != selection.environment
                    or (account_route.venue, account_route.product) != (scope_tuple[0], scope_tuple[2])
                    or account_route.native_account_id != account_owner.ledger.native_account_id):
                raise ValueError("selected group alias differs from its exact existing owner route")
            from dataclasses import replace
            child = object.__new__(NativeSelectionDispatcher)
            child.selection = replace(selection, venue=scope_tuple[0], account=scope_tuple[1],
                product=scope_tuple[2], environment=scope_tuple[3])
            child.binding = replace(binding, owner=account_owner, route_id=route_id)
            child.strategy_id = self.strategy_id
            child._promotion_receipt = None
            child._dispatch_active = False
            child._dispatch_base_version = None
            child._scope_dispatchers = {key: child}
            child._live_auth_checked = selection.mode == "LIVE"
            if selection.mode == "LIVE":
                child._check_live_authorization(self.strategy_id)
                account_owner.live_activation_guard = child._promotion_guard
            self._scope_dispatchers[key] = child
        self.source_proxy = _SelectedSourceProxy(self)
        self.required_feeds = tuple(selection.registration["required_feeds"])
        self.warmup_bars = selection.registration["warmup_bars"]
        self._promotion_receipt = None
        self._dispatch_active = False
        self._dispatch_base_version = None
        if selection.mode == "LIVE":
            # Replace any caller-provided boolean-style activation callback.
            binding.owner.live_activation_guard = self._promotion_guard

    def _check_live_authorization(self, strategy_id):
        owner = self.binding.owner
        auth = getattr(owner, "live_authorization", None)
        settings = getattr(self.binding.runner, "settings", None)
        env = getattr(settings, "env", None)
        arm = "LIVE_ORDER_ARMED_" + self.selection.account.upper()
        if (auth is None or auth.strategy_id != strategy_id
                or auth.config_version != self.selection.registration["config_version"]
                or auth.scope_key != owner.ledger.scope.key
                or auth.native_account_id != owner.ledger.native_account_id
                or auth.issued_ns > owner.clock() or owner.clock() >= auth.expires_ns
                or getattr(settings, "live_order_enabled", False) is not True
                or not isinstance(env, Mapping) or env.get(arm) != "1"
                ):
            raise ValueError("LIVE selection lacks exact scoped authorization and required runtime flags")

    def _promotion_guard(self):
        if len(getattr(self, "_scope_dispatchers", ())) > 1:
            return all(item._promotion_guard_one() for item in self._scope_dispatchers.values())
        return self._promotion_guard_one()

    def _promotion_guard_one(self):
        receipt = self._promotion_receipt
        owner = self.binding.owner
        now = owner.clock()
        if (not isinstance(receipt, _PromotionReceipt)
                or receipt.scope_key != owner.ledger.scope.key
                or receipt.native_account_id != owner.ledger.native_account_id
                or receipt.strategy_id != self.strategy_id
                or receipt.config_version != self.selection.registration["config_version"]
                or not receipt.observed_ns <= now < receipt.expires_ns
                or now - receipt.observed_ns > 5_000_000_000
                or tuple(sorted(owner.control.snapshot(self.strategy_id)["epochs"].items()))
                    != receipt.control_epochs):
            return False
        current_version = owner.ledger.account_version()
        if self._dispatch_active:
            base = self._dispatch_base_version
            if type(base) is not int or current_version != base:
                return False
        elif receipt.ledger_version != current_version:
            return False
        if self.selection.mode == "LIVE":
            try:
                self._check_live_authorization(self.strategy_id)
            except (TypeError, ValueError, AttributeError):
                return False
        return True

    def _activation_context_valid(self):
        if len(getattr(self, "_scope_dispatchers", ())) > 1:
            return all(item._activation_context_valid_one() for item in self._scope_dispatchers.values())
        return self._activation_context_valid_one()

    def _activation_context_valid_one(self):
        owner = self.binding.owner
        route = self.binding.registry.route(self.binding.route_id)
        if (owner.ledger.scope.key != self.selection_scope_key
                or owner.ledger.native_account_id != route.native_account_id
                or route.account_scope.key != self.selection_scope_key
                or route.environment != self.selection.environment
                or route.venue != self.selection.venue or route.product != self.selection.product):
            return False
        if self.selection.mode == "LIVE":
            try:
                self._check_live_authorization(self.strategy_id)
            except (TypeError, ValueError, AttributeError):
                return False
        return True

    def _begin_source_dispatch(self):
        if len(getattr(self, "_scope_dispatchers", ())) > 1:
            if not all(item._promotion_guard_one() for item in self._scope_dispatchers.values()):
                raise ValueError("selected group has no fresh exact promotion proof for every owner")
            for item in self._scope_dispatchers.values():
                item._begin_source_dispatch_one()
            return
        self._begin_source_dispatch_one()

    def _begin_source_dispatch_one(self):
        owner = self.binding.owner
        base = owner.ledger.account_version()
        receipt = self._promotion_receipt
        if (not self._activation_context_valid()
                or not isinstance(receipt, _PromotionReceipt)
                or receipt.ledger_version != base or not self._promotion_guard()):
            raise ValueError("selected native source has no current exact owner-version promotion receipt")
        self._dispatch_active = True
        self._dispatch_base_version = base

    def _dispatch_guard(self):
        if len(getattr(self, "_scope_dispatchers", ())) > 1:
            return {key: (lambda item=item: [] if item._promotion_guard_one() else ["native_promotion_proof_stale"])
                    for key, item in self._scope_dispatchers.items()}
        return [] if self._promotion_guard() else ["native_promotion_proof_stale"]

    async def prepare_promotion(self):
        """Read actual scoped native reconciliation before selected startup."""
        if len(getattr(self, "_scope_dispatchers", ())) > 1:
            for key in sorted(self._scope_dispatchers):
                item = self._scope_dispatchers[key]
                if item is not self:
                    await item.prepare_promotion()
        from execution.trading_contracts import validate_native_account_facts
        from execution.execution_ledger import canonical
        from decimal import Decimal
        import hashlib
        if self.selection.mode == "LIVE":
            self._check_live_authorization(self.strategy_id)
        route_binding = self.binding.registry.binding(self.binding.route_id)
        adapter = route_binding.adapter
        reconcile = getattr(adapter, "reconcile", None)
        aliases = [item for item in self.binding.registry.aliases_for_strategy(
            self.strategy_id) if item.route_id == self.binding.route_id
            and item.account_scope.key == self.binding.owner.ledger.scope.key
            and item.execution_enabled]
        symbols = {item.symbol for item in aliases}
        if len(symbols) != 1:
            raise RuntimeError("Native promotion requires exactly one execution symbol from its configured route alias")
        symbol = next(iter(symbols))
        if not callable(reconcile):
            await self._prepare_cash_promotion(route_binding, adapter, symbol)
            return
        native_scope_key = getattr(adapter.scope, "key", None)
        if not isinstance(native_scope_key, str) or not native_scope_key:
            raise RuntimeError("Selected adapter lacks an exact native account scope identity")
        history_end_ns = self.binding.owner.clock()
        start_ns = max(0, history_end_ns - 7 * 24 * 60 * 60 * 1_000_000_000)
        timeout = self.binding.owner.budgets.prepare_timeout_seconds
        ledger_before = self.binding.owner.ledger.account_version()
        epochs_before = tuple(sorted(self.binding.owner.control.snapshot(self.strategy_id)["epochs"].items()))
        read_started_ns = history_end_ns
        fill_cursor = funding_cursor = None
        fills_complete = funding_complete = False
        pages = min(getattr(adapter, "max_pages", 1), 64)
        latest = None
        page_proofs = []
        all_fills = []
        for _ in range(pages):
            latest = await asyncio.wait_for(self.binding.registry.invoke(
                self.binding.route_id, "reconcile", symbol, start_ns=start_ns, end_ns=history_end_ns,
                fill_cursor=fill_cursor, funding_cursor=funding_cursor), timeout)
            if not isinstance(latest, Mapping) or latest.get("scope_key") != native_scope_key:
                raise RuntimeError("Native reconciliation scope proof is missing or foreign: " +
                    repr(latest.get("scope_key") if isinstance(latest, Mapping) else type(latest).__name__))
            if latest.get("absence_resolves_unknown") is not False or latest.get("cursor_requires_durable_apply") is not True:
                raise RuntimeError("Native reconciliation lacks the existing durable cursor/UNKNOWN contract")
            fills, funding = latest.get("fills"), latest.get("funding")
            if not isinstance(fills, Mapping) or not isinstance(funding, Mapping):
                raise RuntimeError("Native fill/funding page facts are incomplete")
            fill_rows, funding_rows = fills.get("rows"), funding.get("rows")
            if not isinstance(fill_rows, list) or not isinstance(funding_rows, list):
                raise RuntimeError("Native fill/funding rows must be explicit bounded lists")
            if not fills_complete:
                all_fills.extend(fill_rows)
            page_proofs.append(dict(latest))
            if len(all_fills) > 16_384 or len(page_proofs) > 64:
                raise RuntimeError("Native reconciliation exceeds its explicit page/row bound")
            fills_complete = fills_complete or fills.get("complete") is True
            funding_complete = funding_complete or funding.get("complete") is True
            if fills_complete and funding_complete:
                break
            fill_cursor = None if fills_complete else fills.get("cursor")
            funding_cursor = None if funding_complete else funding.get("cursor")
            if ((not fills_complete and not isinstance(fill_cursor, Mapping))
                    or (not funding_complete and not isinstance(funding_cursor, Mapping))):
                raise RuntimeError("Native incomplete history page lacks its exact continuation cursor")
        else:
            raise RuntimeError("Native fill/funding reconciliation exceeded its page bound")
        account = latest.get("account")
        orders = latest.get("working_orders")
        positions = latest.get("positions")
        fills = all_fills
        if (not isinstance(account, Mapping) or not isinstance(orders, list)
                or not isinstance(positions, list) or not isinstance(fills, list)
                or account.get("scope_key") != native_scope_key
                or not isinstance(account.get("raw"), Mapping)):
            raise RuntimeError("Native account, working-order, position, or fill inventory is incomplete")
        if any(not isinstance(row, Mapping) or row.get("scope_key") != native_scope_key
               for collection in (orders, positions, fills) for row in collection):
            raise RuntimeError("Native reconciliation contains a foreign or unscoped row")
        ledger = self.binding.owner.ledger
        owned = ledger.owned()
        unresolved = {row["intent_id"]: row for row in ledger.unresolved()}
        unsafe_states = {"AUTHORIZED", "HANDOFF", "UNKNOWN", "LEGACY_UNKNOWN"}
        if any(row["state"] in unsafe_states for row in unresolved.values()):
            raise RuntimeError("Native owner has an unresolved UNKNOWN/handoff intent; promotion is blocked")
        owned_by_order = {}
        for row in owned:
            for key in (row.get("target_order_id"), row.get("client_order_id")):
                if key:
                    if key in owned_by_order and owned_by_order[key]["intent_id"] != row["intent_id"]:
                        raise RuntimeError("Local owner has conflicting durable native order identities")
                    owned_by_order[key] = row
        # Reconciliation reads must belong to one unchanged durable owner and
        # strategy-control cut before any returned fill is applied locally.
        if (ledger.account_version() != ledger_before
                or tuple(sorted(self.binding.owner.control.snapshot(self.strategy_id)["epochs"].items()))
                    != epochs_before):
            raise RuntimeError("Owner ledger/control epochs changed during native reconciliation")
        self._apply_reconciled_fills(fills, symbol, owned_by_order)
        seen_orders = set()
        for order in orders:
            order_id = order.get("order_id")
            client_id = order.get("client_order_id")
            row = owned_by_order.get(order_id) or owned_by_order.get(client_id)
            if (row is None or order.get("symbol") != symbol
                    or order_id not in {row.get("target_order_id"), row.get("client_order_id")}
                    or row["state"] not in {"SUBMITTED", "CANCEL_PENDING"}
                    or order_id in seen_orders):
                raise RuntimeError("Native working order identity is foreign or not durably owned")
            seen_orders.add(order_id)
            intent = __import__("core.models", fromlist=["OrderIntent"]).OrderIntent.model_validate_json(row["payload"])
            if intent.instrument.raw_symbol != symbol:
                raise RuntimeError("Owned native working order does not match its configured execution alias")
            latest_row = ledger.get(row["intent_id"])
            if Decimal(str(order.get("cumulative_quantity", "0"))) != Decimal(latest_row["filled"]):
                raise RuntimeError("Native working order cumulative quantity differs from its reconciled local ledger")
        latest_unresolved = {row["intent_id"]: row for row in ledger.unresolved()}
        expected_open = {row.get("target_order_id") for row in latest_unresolved.values()
                         if row["state"] in {"SUBMITTED", "CANCEL_PENDING"} and row.get("target_order_id")}
        if expected_open != seen_orders:
            raise RuntimeError("Complete native open-order inventory differs from durable owned submissions")
        position_rows = {}
        for row in positions:
            if not isinstance(row, Mapping) or row.get("symbol") in position_rows:
                raise RuntimeError("Native position inventory contains a malformed or duplicate symbol")
            position_rows[row.get("symbol")] = row
        if set(position_rows) - {symbol}:
            raise RuntimeError("Native position inventory includes an unselected instrument")
        from core.models import OrderIntent
        execution_instruments = {OrderIntent.model_validate_json(row["payload"]).instrument.key
            for row in ledger.owned()
            if OrderIntent.model_validate_json(row["payload"]).instrument.raw_symbol == symbol}
        native_quantity = Decimal(str(position_rows[symbol].get("quantity", "0"))) if symbol in position_rows else Decimal(0)
        if any(ledger.position(key) != native_quantity for key in execution_instruments):
            raise RuntimeError("Native position differs from the exact selected local-ledger position")
        provider = route_binding.native_facts_provider
        if not callable(provider):
            raise RuntimeError("Native account identity proof provider is unavailable")
        facts = provider(symbol)
        if inspect.isawaitable(facts):
            if inspect.iscoroutine(facts): facts.close()
            raise RuntimeError("Native promotion identity proof must be a bounded synchronous snapshot")
        if not isinstance(facts, Mapping):
            raise RuntimeError("Native account identity proof is unavailable")
        account_facts, products = facts.get("account_facts"), facts.get("product_specs")
        max_age = facts.get("max_age_ns")
        observed = account_facts.get("observed_ns") if isinstance(account_facts, Mapping) else None
        validation_now = self.binding.owner.clock()
        validated = validate_native_account_facts(account_facts, products, adapter.scope,
            account_version=(account_facts.get("account_version") if isinstance(account_facts, Mapping) else None),
            expected_native_account_id=route_binding.route.native_account_id,
            now_ns=validation_now, max_age_ns=max_age)
        if validated.get("quality") != "VALID":
            raise RuntimeError("Native account reconciliation lacks validated identity/version/freshness: "
                + str(validated.get("reason", "unknown"))[:192])
        normalized = account_facts.get("normalized") if isinstance(account_facts, Mapping) else None
        if (not isinstance(normalized, Mapping) or not isinstance(normalized.get("raw"), Mapping)
                or canonical(normalized["raw"]) != canonical(account["raw"])):
            raise RuntimeError("Validated native identity facts do not bind the exact reconciled account snapshot")
        normalized_currency = normalized.get("currency")
        reconciled_currency = account.get("currency")
        if normalized_currency == reconciled_currency:
            for field in ("equity", "available"):
                if normalized.get(field) != account.get(field):
                    raise RuntimeError("Validated native identity facts differ from same-unit account values")
        else:
            raw_coins = account["raw"].get("coin", account["raw"].get("balances"))
            if not isinstance(raw_coins, list):
                raise RuntimeError("Different account valuation units require exact native settlement-coin facts")
            matches = [row for row in raw_coins if isinstance(row, Mapping)
                       and row.get("coin", row.get("asset")) == adapter.scope.settlement_currency]
            if len(matches) != 1:
                raise RuntimeError("Native account snapshot lacks one exact selected settlement-coin row")
            coin = matches[0]
            margin_mode = account_facts.get("margin_mode")
            if margin_mode != "isolated":
                raise RuntimeError("Different-unit Bybit summaries require isolated native coin facts")
            # Bybit Unified isolated availability follows its documented native
            # coin formula; CROSS uses USD/index valuation and requires a
            # separate native conversion proof, which this bridge does not infer.
            native_equity = coin.get("equity")
            required = ("walletBalance", "totalPositionIM", "totalOrderIM", "locked", "bonus")
            if native_equity is None or any(coin.get(name) is None for name in required):
                raise RuntimeError("Native settlement coin lacks the isolated account facts required for availability")
            try:
                coin_available = (Decimal(str(coin["walletBalance"]))
                    - Decimal(str(coin["totalPositionIM"]))
                    - Decimal(str(coin["totalOrderIM"]))
                    - Decimal(str(coin["locked"])) - Decimal(str(coin["bonus"])))
                exact_equity = Decimal(str(native_equity))
            except Exception as error:
                raise RuntimeError("Native settlement coin facts are not exact finite decimals") from error
            if (not coin_available.is_finite() or not exact_equity.is_finite() or coin_available < 0
                    or Decimal(str(normalized.get("equity"))) != exact_equity
                    or Decimal(str(normalized.get("available"))) != coin_available):
                raise RuntimeError("Validated native facts differ from exact raw settlement-coin balances")
        normalized_settlement = normalized.get("settlement_currency", normalized_currency)
        if normalized_settlement != adapter.scope.settlement_currency:
            raise RuntimeError("Validated native identity facts differ from the selected settlement currency")
        if (normalized_currency != adapter.scope.settlement_currency
                and validated.get("facts", {}).get("basis") != "ARCUS_NATIVE_QUOTE_USD"):
            raise RuntimeError("Validated native identity facts use an unsupported account valuation currency")
        if (isinstance(observed, bool) or not isinstance(observed, int)
                or observed > validation_now or isinstance(max_age, bool) or not isinstance(max_age, int)
                or max_age <= 0 or validation_now - observed > max_age):
            raise RuntimeError("Native account reconciliation is stale")
        ledger_version = self.binding.owner.ledger.account_version()
        epochs = tuple(sorted(self.binding.owner.control.snapshot(self.strategy_id)["epochs"].items()))
        if epochs != epochs_before or ledger_version < ledger_before:
            raise RuntimeError("Owner control epochs changed or durable ledger regressed during reconciliation")
        local_cut = account_facts.get("local_ledger_version")
        if local_cut is not None and (type(local_cut) is not int or local_cut != ledger_before):
            raise RuntimeError("Native account proof is stale against the selected owner ledger cut")
        proof_sha256 = hashlib.sha256(canonical({"pages": page_proofs,
            "native_identity": validated["facts"], "route_id": self.binding.route_id}).encode("utf-8")).hexdigest()
        self._promotion_receipt = _PromotionReceipt(self.selection_scope_key,
            route_binding.route.native_account_id, self.strategy_id,
            self.selection.registration["config_version"], ledger_version,
            min(read_started_ns, observed),
            min(observed + min(max_age, 5_000_000_000), read_started_ns + 5_000_000_000),
            epochs, proof_sha256)
        if not self._promotion_guard():
            raise RuntimeError("Native promotion receipt failed its exact scoped owner/epoch recheck")

    async def _prepare_cash_promotion(self, route_binding, adapter, symbol):
        """Use registered read APIs plus an exact complete cash-history contract.

        Kiwoom uses its existing bounded daily execution-history reader.
        Binance Spot may use its existing recent trades page only when exact
        durable-order queries prove cumulative coverage for every known order.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from decimal import Decimal
        from types import SimpleNamespace
        from execution.execution_ledger import canonical
        import hashlib
        owner, registry = self.binding.owner, self.binding.registry
        route, ledger = route_binding.route, owner.ledger
        if self.selection.mode == "LIVE":
            self._check_live_authorization(self.strategy_id)
        history_end_ns = owner.clock()
        ledger_before = ledger.account_version()
        epochs_before = tuple(sorted(owner.control.snapshot(self.strategy_id)["epochs"].items()))
        read_started = history_end_ns
        timeout = owner.budgets.prepare_timeout_seconds
        account = await asyncio.wait_for(registry.invoke(self.binding.route_id, "account_state"), timeout)
        orders_result = await asyncio.wait_for(registry.invoke(
            self.binding.route_id, "list_open_orders", symbol), timeout)
        if (not isinstance(account, Mapping) or not isinstance(orders_result, Mapping)
                or orders_result.get("complete") is not True
                or not isinstance(orders_result.get("rows"), list)):
            raise RuntimeError("Native cash account/open-order read is incomplete")
        owned = ledger.owned()
        unresolved = {row["intent_id"]: row for row in ledger.unresolved()}
        if any(row["state"] in {"AUTHORIZED", "HANDOFF", "UNKNOWN", "LEGACY_UNKNOWN"}
               for row in unresolved.values()):
            raise RuntimeError("Native cash owner has UNKNOWN/handoff state; promotion is blocked")
        # Build the minimum exact set of days represented by the current owner
        # ledger and the current account-local calendar day.
        day_zone = ZoneInfo("Asia/Seoul" if route.venue == "kiwoom" else "UTC")
        days = {datetime.fromtimestamp(history_end_ns / 1_000_000_000, day_zone).strftime("%Y%m%d")}
        for row in owned:
            stamp = row.get("updated_ns")
            if type(stamp) is int and stamp > 0:
                days.add(datetime.fromtimestamp(stamp / 1_000_000_000, day_zone).strftime("%Y%m%d"))
        if len(days) > 64:
            raise RuntimeError("Native cash selected-owner history exceeds the bounded day set")
        history_pages = []
        history_rows = []
        execution_fills = getattr(adapter, "execution_fills", None)
        if callable(execution_fills):
            for day in sorted(days):
                page = await asyncio.wait_for(execution_fills(symbol, day), timeout)
                if (not isinstance(page, Mapping) or page.get("symbol") != symbol
                        or page.get("complete") is not True or not isinstance(page.get("rows"), list)):
                    raise RuntimeError("Native cash execution history page is incomplete or foreign")
                history_pages.append(dict(page))
                history_rows.extend(page["rows"])
                if len(history_rows) > 16_384:
                    raise RuntimeError("Native cash execution history exceeds its row bound")
        else:
            page = await asyncio.wait_for(registry.invoke(
                self.binding.route_id, "get_fills", symbol), timeout)
            bounded_order_coverage = (isinstance(page, Mapping)
                and page.get("page_complete") is True
                and page.get("coverage") == "RECENT_OR_FROM_ID_ONLY"
                and page.get("account_history_complete") is False
                and callable(getattr(adapter, "query_order", None)))
            if (not isinstance(page, Mapping) or page.get("symbol") != symbol
                    or not (page.get("account_history_complete") is True or bounded_order_coverage)
                    or not isinstance(page.get("rows"), list)):
                raise RuntimeError("Selected cash route exposes no complete native fill-history proof")
            history_pages.append(dict(page))
            history_rows.extend(page["rows"])
        facts_provider = route_binding.native_facts_provider
        if not callable(facts_provider):
            raise RuntimeError("Native cash identity proof provider is unavailable")
        facts = facts_provider(symbol)
        if inspect.isawaitable(facts):
            if inspect.iscoroutine(facts): facts.close()
            raise RuntimeError("Native cash identity proof must be a bounded synchronous snapshot")
        proxy = SimpleNamespace(intent=SimpleNamespace(
            instrument=SimpleNamespace(raw_symbol=symbol)))
        native_binding = SimpleNamespace(route=route_binding.route,
            capabilities=route_binding.capabilities,
            native_facts_provider=lambda requested_symbol: facts)
        native = owner._native_facts(native_binding, proxy)
        account_facts = facts.get("account_facts") if isinstance(facts, Mapping) else None
        normalized = account_facts.get("normalized") if isinstance(account_facts, Mapping) else None
        observed, max_age = (account_facts.get("observed_ns") if isinstance(account_facts, Mapping) else None,
                            facts.get("max_age_ns") if isinstance(facts, Mapping) else None)
        validation_now = owner.clock()
        actual_account_raw = account.get("raw") if isinstance(account.get("raw"), Mapping) else dict(account)
        if (not isinstance(normalized, Mapping) or not isinstance(normalized.get("raw"), Mapping)
                or canonical(normalized["raw"]) != canonical(dict(actual_account_raw))
                or isinstance(observed, bool) or not isinstance(observed, int)
                or observed > validation_now or type(max_age) is not int or max_age <= 0
                or validation_now - observed > max_age):
            raise RuntimeError("Native cash account read differs from its fresh validated identity boundary")
        local_cut = account_facts.get("local_ledger_version")
        if local_cut is not None and (type(local_cut) is not int or local_cut != ledger_before):
            raise RuntimeError("Native cash identity proof is stale against the selected owner ledger cut")
        if (ledger.account_version() != ledger_before
                or tuple(sorted(owner.control.snapshot(self.strategy_id)["epochs"].items())) != epochs_before):
            raise RuntimeError("Owner ledger/control epochs changed during native cash reads")
        owned_by_order = {}
        for row in owned:
            for key in (row.get("target_order_id"), row.get("client_order_id")):
                if key:
                    if key in owned_by_order and owned_by_order[key]["intent_id"] != row["intent_id"]:
                        raise RuntimeError("Native cash ledger has conflicting durable order identities")
                    owned_by_order[key] = row
        seen = set()
        native_cumulative = {}
        for raw in orders_result["rows"]:
            if not isinstance(raw, Mapping):
                raise RuntimeError("Native cash open-order row is malformed")
            order_id = str(raw.get("order_id", raw.get("orderId", raw.get("ord_no", ""))))
            client_id = raw.get("client_order_id", raw.get("clientOrderId", ""))
            row = owned_by_order.get(order_id) or owned_by_order.get(client_id)
            raw_symbol = raw.get("symbol")
            if raw_symbol is None and hasattr(adapter, "_row_symbol"):
                raw_symbol = adapter._row_symbol(raw)
            if (not order_id or row is None or raw_symbol != symbol
                    or row["state"] not in {"SUBMITTED", "CANCEL_PENDING"}
                    or order_id not in {row.get("target_order_id"), row.get("client_order_id")}
                    or order_id in seen):
                raise RuntimeError("Native cash open order is foreign or not exactly owned")
            cumulative = raw.get("cumulative_quantity", raw.get("executedQty",
                raw.get("executed_qty")))
            if cumulative is None:
                raise RuntimeError("Native cash open order lacks exact cumulative execution quantity")
            native_cumulative[order_id] = Decimal(str(cumulative))
            seen.add(order_id)
        native_fills = history_rows
        exact_order_proofs = []
        if not callable(execution_fills) and page.get("account_history_complete") is False:
            # Binance Spot's existing trade reader is deliberately recent/page
            # bounded. Prove complete coverage for every durable owner order by
            # exact order queries, then require its cumulative quantity to equal
            # the fills actually present in that complete bounded page.
            product_facts = facts.get("product_specs", {}).get(symbol) if isinstance(
                facts.get("product_specs"), Mapping) else None
            quote_asset = product_facts.get("quote_currency") if isinstance(product_facts, Mapping) else None
            per_order_quantity = {}
            normalized_fills = []
            for raw_fill in history_rows:
                if not isinstance(raw_fill, Mapping):
                    raise RuntimeError("Native cash trade page contains a malformed fill")
                order_id = str(raw_fill.get("orderId", ""))
                row = owned_by_order.get(order_id)
                if row is None or raw_fill.get("symbol") != symbol:
                    raise RuntimeError("Recent native cash fill is not attributable to a durable selected order")
                if type(raw_fill.get("isBuyer")) is not bool:
                    raise RuntimeError("Native Spot fill lacks an exact buyer/seller fact")
                quantity = Decimal(str(raw_fill.get("qty")))
                per_order_quantity[order_id] = per_order_quantity.get(order_id, Decimal(0)) + quantity
                commission_asset = raw_fill.get("commissionAsset")
                if not isinstance(quote_asset, str) or commission_asset != quote_asset:
                    raise RuntimeError("Native Spot commission is not denominated in the exact configured quote asset")
                normalized_fills.append({"event_id": str(raw_fill.get("id", "")),
                    "order_id": order_id, "symbol": symbol,
                    "side": "buy" if raw_fill["isBuyer"] else "sell",
                    "quantity": str(quantity), "price": str(raw_fill.get("price")),
                    "fee": str(raw_fill.get("commission")), "fee_known": True,
                    "exchange_ns": int(raw_fill.get("time", 0)) * 1_000_000})
            exact_orders = {}
            for row in owned:
                order_id = row.get("target_order_id")
                client_id = row.get("client_order_id")
                if not order_id and not client_id:
                    if row.get("state") in {"SUBMITTED", "CANCEL_PENDING", "FILLED", "CANCELED", "REJECTED"}:
                        raise RuntimeError("Durable cash order lacks an exact native order identity")
                    continue
                try:
                    exact_id = int(order_id) if order_id is not None and str(order_id).isdigit() else None
                except (TypeError, ValueError):
                    exact_id = None
                queried = await asyncio.wait_for(registry.invoke(self.binding.route_id,
                    "query_order", symbol, order_id=exact_id,
                    client_order_id=None if exact_id is not None else client_id), timeout)
                if (not isinstance(queried, Mapping) or queried.get("status") == "UNKNOWN"
                        or queried.get("symbol") != symbol):
                    raise RuntimeError("Exact native cash order history query is unresolved")
                native_id = str(queried.get("orderId", ""))
                if order_id is not None and native_id != str(order_id):
                    raise RuntimeError("Exact native cash order query returned a foreign order identity")
                if client_id and queried.get("clientOrderId") != client_id:
                    raise RuntimeError("Exact native cash order query returned a foreign client identity")
                if queried.get("status") not in {"NEW", "PARTIALLY_FILLED", "FILLED",
                        "CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}:
                    raise RuntimeError("Exact native cash order query returned an unsupported state")
                cumulative = Decimal(str(queried.get("executedQty", "0")))
                if per_order_quantity.get(native_id, Decimal(0)) != cumulative:
                    raise RuntimeError("Bounded Spot fills do not cover the exact queried order cumulative quantity")
                exact_orders[native_id] = queried
                exact_order_proofs.append(dict(queried))
            if set(per_order_quantity) - set(exact_orders):
                raise RuntimeError("Recent Spot fills contain an order without exact durable order coverage")
            native_fills = normalized_fills
        if (ledger.account_version() != ledger_before
                or tuple(sorted(owner.control.snapshot(self.strategy_id)["epochs"].items())) != epochs_before):
            raise RuntimeError("Owner ledger/control epochs changed during exact native cash order reads")
        if history_rows and callable(execution_fills):
            from execution.account_fees import AccountFeePolicy
            policy = AccountFeePolicy(owner.ledger.scope, Decimal(str(native["taker_fee_rate"])))
            normalized_fills = []
            for fill in history_rows:
                if (not isinstance(fill, Mapping) or fill.get("scope_key") != ledger.scope.key
                        or fill.get("fee") is not None or fill.get("fee_known") is not False):
                    raise RuntimeError("Native cash fill lacks exact scoped fee-free execution facts")
                row = owned_by_order.get(fill.get("order_id"))
                if row is None:
                    raise RuntimeError("Native cash fill has no exact durable owner order")
                quantity, price = Decimal(str(fill.get("quantity"))), Decimal(str(fill.get("price")))
                fee = policy.calculate_fee(quantity, price, Decimal(row["contract_multiplier"]))
                normalized_fills.append({**dict(fill), "fee": str(fee), "fee_known": True,
                    "fee_basis": "ACCOUNT_CONFIG"})
            native_fills = normalized_fills
        elif history_rows and page.get("account_history_complete") is True:
            raise RuntimeError("Native cash history does not provide exact owner-compatible fee receipts")
        self._apply_reconciled_fills(native_fills, symbol, owned_by_order)
        latest_unresolved = {row["intent_id"]: row for row in ledger.unresolved()}
        for order_id in seen:
            row = owned_by_order[order_id]
            if native_cumulative[order_id] != Decimal(ledger.get(row["intent_id"])["filled"]):
                raise RuntimeError("Native cash open-order cumulative differs from its exact owner ledger")
        expected_open = {row.get("target_order_id") for row in latest_unresolved.values()
                         if row["state"] in {"SUBMITTED", "CANCEL_PENDING"} and row.get("target_order_id")}
        if seen != expected_open:
            raise RuntimeError("Complete native cash open orders differ from durable owner submissions")
        physical = native.get("physical_positions")
        if not isinstance(physical, Mapping):
            raise RuntimeError("Native cash physical-position proof is malformed")
        owned_instruments = {}
        from core.models import OrderIntent
        for row in owned:
            intent = OrderIntent.model_validate_json(row["payload"])
            owned_instruments[intent.instrument.key] = intent.instrument.raw_symbol
        normalized_positions = {}
        for key, quantity in physical.items():
            if key not in {symbol, *owned_instruments, *owned_instruments.values()}:
                if Decimal(str(quantity)) != 0:
                    raise RuntimeError("Native cash account has an unallocated physical position")
                continue
            normalized_positions[key] = Decimal(str(quantity))
        allocated_keys = set(owned_instruments) | set(owned_instruments.values())
        if any(quantity != 0 and key not in allocated_keys
               for key, quantity in normalized_positions.items()):
            raise RuntimeError("Native cash account has an unallocated physical position")
        for key in owned_instruments:
            raw_symbol = owned_instruments[key]
            native_quantity = normalized_positions.get(key, normalized_positions.get(raw_symbol, Decimal(0)))
            if native_quantity != ledger.position(key):
                raise RuntimeError("Native cash position differs from its durable owner ledger")
        if hasattr(adapter, "execution_fills") and isinstance(account.get("positions"), list):
            account_positions = {}
            for position in account["positions"]:
                if not isinstance(position, Mapping):
                    raise RuntimeError("Native cash account has malformed position rows")
                raw_symbol = adapter._row_symbol(position)
                account_positions[raw_symbol] = Decimal(str(position.get("rmnd_qty", "0")))
            if account.get("complete") is not True:
                raise RuntimeError("Native Kiwoom account position page is incomplete")
            if any(raw not in owned_instruments.values() and quantity != 0
                   for raw, quantity in account_positions.items()):
                raise RuntimeError("Native cash account exposes an unallocated physical position")
            for key, raw_symbol in owned_instruments.items():
                if account_positions.get(raw_symbol, Decimal(0)) != ledger.position(key):
                    raise RuntimeError("Native cash account position differs from the durable owner ledger")
        observed_oldest = min([read_started, observed] + [observed for _ in history_pages])
        ledger_version = ledger.account_version()
        epochs = tuple(sorted(owner.control.snapshot(self.strategy_id)["epochs"].items()))
        if epochs != epochs_before or ledger_version < ledger_before:
            raise RuntimeError("Cash reconciliation changed control epochs or regressed the owner ledger")
        proof = hashlib.sha256(canonical({"account": dict(account), "orders": orders_result,
            "history": history_pages, "exact_order_queries": exact_order_proofs,
            "native": native.get("native"),
            "route_id": self.binding.route_id}).encode("utf-8")).hexdigest()
        self._promotion_receipt = _PromotionReceipt(self.selection_scope_key,
            route.native_account_id, self.strategy_id,
            self.selection.registration["config_version"], ledger_version,
            observed_oldest, min(observed + min(max_age, 5_000_000_000),
                read_started + 5_000_000_000), epochs, proof)
        if not self._promotion_guard():
            raise RuntimeError("Native cash promotion receipt failed its exact scoped owner/epoch recheck")

    def _apply_reconciled_fills(self, fills, symbol, owned_by_order):
        """Apply only exact fills attributable to an existing durable owner row."""
        from decimal import Decimal
        from execution.trading_contracts import ExecutionAction, ActionKind, TradingMode
        from core.models import OrderIntent
        owner = self.binding.owner
        seen = set()
        for fill in fills:
            fill_id, order_id = fill.get("event_id"), fill.get("order_id")
            row = owned_by_order.get(order_id)
            if (row is None or not isinstance(fill_id, str) or not fill_id
                    or fill.get("symbol") != symbol or fill_id in seen):
                raise RuntimeError("Native fill is duplicate, foreign, or not durably owned")
            seen.add(fill_id)
            intent = OrderIntent.model_validate_json(row["payload"])
            if intent.instrument.raw_symbol != symbol:
                raise RuntimeError("Owned native fill differs from the configured execution alias")
            if fill.get("side") is not None and fill.get("side") != intent.side.value.casefold():
                raise RuntimeError("Native fill side differs from the durable order intent")
            quantity, price, fee = (Decimal(str(fill.get("quantity"))),
                                    Decimal(str(fill.get("price"))),
                                    Decimal(str(fill.get("fee"))))
            action = ExecutionAction("reconcile:" + fill_id[:220], ActionKind.NEW,
                owner.ledger.scope, row["strategy_id"], row["config_version"],
                TradingMode(owner.ledger.scope.environment), max(owner.clock() + 1,
                    int(fill.get("exchange_ns", 0)) + 1), intent=intent)
            owner.apply_fill(action, fill_id=fill_id, quantity=quantity,
                price=price, fee=fee, order_id=order_id, fact_quality="VALID")

    @property
    def selection_scope_key(self):
        return self.binding.owner.ledger.scope.key

    def enqueue_applied(self, result, snapshot):
        if self.blocked or not result.applied or result.duplicate:
            return
        if not self._activation_context_valid():
            self._fail_closed(); return
        if (snapshot.phase != "LIVE" or snapshot.cursor != (result.engine_incarnation, result.apply_seq)
                or not any(item.matches(result.event) for item in self.subscriptions)):
            return
        if (snapshot.quality.get(result.event.instrument.key) != "VALID"
                or not self._inputs_ready(snapshot)):
            self._fail_closed(); return
        encoded = result.event.model_dump_json().encode("utf-8")
        from runtime.strategy_process import snapshot_document
        from execution.execution_ledger import canonical
        snapshot_bytes = canonical(snapshot_document(snapshot)).encode("utf-8")
        charge = max(self.selection.snapshot_charge_bytes, len(snapshot_bytes)) + len(encoded)
        if charge > self.selection.max_queue_bytes or self.queued_bytes + charge > self.selection.max_queue_bytes:
            self._fail_closed()
            return
        item = _QueuedDispatch(result, snapshot, charge, time.monotonic_ns())
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self._fail_closed()
            return
        self.queued_bytes += charge

    def _inputs_ready(self, snapshot, *, at_ns=None):
        current_ns = snapshot.now_ns if at_ns is None else max(snapshot.now_ns, at_ns)
        for key in self.required_feeds:
            if snapshot.quality.get(key) != "VALID":
                return False
            latest = (snapshot.latest_book(key) or snapshot.latest_trade(key)
                      or snapshot.latest_index_quote(key))
            if latest is None:
                bars = snapshot.closed_bars(key, max(1, self.warmup_bars))
                latest = bars[-1] if bars else None
            if latest is None or latest.receive_ts_ns is None:
                return False
            if not 0 <= current_ns - latest.receive_ts_ns <= self.selection.max_queue_age_ns:
                return False
            source_time = getattr(latest, "source_ts_ns", None)
            if source_time is not None and not 0 <= current_ns - source_time <= self.selection.max_queue_age_ns:
                return False
            if self.warmup_bars and (snapshot.history_quality.get(key) != "VALID"
                    or len(snapshot.closed_bars(key, self.warmup_bars)) < self.warmup_bars):
                return False
        return True

    def _fail_closed(self):
        self.blocked = True
        owners = {item.binding.owner for item in getattr(self, "_scope_dispatchers", {}).values()}
        if not owners:
            owners = {self.binding.owner}
        for dispatcher in getattr(self, "_scope_dispatchers", {}).values():
            dispatcher._promotion_receipt = None
            dispatcher._dispatch_active = False
            dispatcher._dispatch_base_version = None
        for owner in owners:
            control = getattr(owner, "control", None)
            halt = getattr(control, "halt", None)
            if not callable(halt):
                continue
            try:
                halt(self.strategy_id, scope="strategy")
            except Exception:
                pass
        while True:
            try:
                item = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.queued_bytes -= item.encoded_bytes
            self.queue.task_done()

    def invalidate(self, reason):
        # Feed loss, engine shutdown, or queue pressure invalidates the native
        # source lease. A fresh process/account proof is required to resume.
        self._fail_closed()

    async def run(self):
        from market.hot_path import StrategyDataView
        while True:
            item = await self.queue.get()
            self.queued_bytes -= item.encoded_bytes
            try:
                if not self._activation_context_valid():
                    self._fail_closed(); continue
                if time.monotonic_ns() - item.queued_ns > self.selection.max_queue_age_ns:
                    self._fail_closed()
                    continue
                if not self._inputs_ready(item.snapshot, at_ns=self.binding.owner.clock()):
                    self._fail_closed(); continue
                if not self._promotion_guard():
                    await self.prepare_promotion()
                if (not self._activation_context_valid() or not self._promotion_guard()
                        or time.monotonic_ns() - item.queued_ns > self.selection.max_queue_age_ns
                        or not self._inputs_ready(item.snapshot, at_ns=self.binding.owner.clock())):
                    self._fail_closed(); continue
                data = StrategyDataView(item.snapshot, ready=True, phase="LIVE")
                if len(self._scope_dispatchers) > 1:
                    evidence = {"config_version": self.selection.registration["config_version"]}
                    for key in self._scope_dispatchers:
                        provider = self.binding.evidence_provider[key] if isinstance(
                            self.binding.evidence_provider, Mapping) else self.binding.evidence_provider
                        value = provider(item.result, item.snapshot)
                        if inspect.isawaitable(value):
                            if inspect.iscoroutine(value): value.close()
                            raise ValueError("trusted evidence provider must be synchronous")
                        if not isinstance(value, Mapping):
                            raise ValueError("trusted evidence provider must return factual evidence mappings")
                        evidence[key] = dict(value)
                    if any(evidence[key].get("config_version") != self.selection.registration["config_version"]
                           for key in self._scope_dispatchers):
                        raise ValueError("group evidence config version differs from the selected source")
                    readiness = (dict(self.binding.readiness) if isinstance(self.binding.readiness, Mapping)
                                 else {key: self.binding.readiness for key in self._scope_dispatchers})
                else:
                    provider = self.binding.evidence_provider
                    evidence = provider(item.result, item.snapshot)
                    if inspect.isawaitable(evidence):
                        if inspect.iscoroutine(evidence): evidence.close()
                        raise ValueError("trusted evidence provider must be synchronous")
                    if not isinstance(evidence, Mapping):
                        raise ValueError("trusted evidence provider must return the exact factual evidence mapping")
                    evidence = dict(evidence)
                    readiness = self.binding.readiness
                result = await self.binding.runner.dispatch_strategy_market_event(
                    self.source_proxy, item.result.event, data,
                    registry=self.binding.registry, evidence=evidence,
                    readiness=readiness, guard=(self._dispatch_guard()
                        if len(self._scope_dispatchers) > 1 else self._dispatch_guard),
                    source_trust=self.binding.source_trust)
                self.results.append(result)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.errors.append(type(error).__name__ + ": " + str(error)[:256])
                self._fail_closed()
            finally:
                for dispatcher in self._scope_dispatchers.values():
                    dispatcher._dispatch_active = False
                    dispatcher._dispatch_base_version = None
                self.queue.task_done()
