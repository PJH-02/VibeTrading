"""Cooperative, local PAPER strategy host with one shared market snapshot.

Callbacks remain the existing on_event(event, StrategyDataView)->list[Signal].
The caller supplies sizing and action construction; this module contains no
alpha, broker factory, service connection, or credential lookup. Callback time
limits are checked after return and cannot preempt Python code. Consequently the
production gate remains BLOCKED until process isolation is separately supplied.

Durable decision/action claims precede callbacks/submission. A crash can omit an
unsent action; it cannot automatically replay a claimed callback into new risk.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from decimal import Decimal
import hashlib
import inspect
import json
import math
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .execution_ledger import positive_decimal
from .hot_path import ApplyResult, DecisionSnapshot, HotPathStore, StrategyDataView, _freeze
from .models import (AssetClass, BaseMarketEvent, EventType, OrderBookEvent, Signal, TradeEvent, Venue,
                     event_from_dict, stable_hash)
from .paper_execution import PaperExecution
from .strategies import StrategyModule, compile_subscriptions, compile_warmup
from .trading_contracts import (ActionKind, ExecutionAction, ProductSpec, TradingMode,
                                bounded_identity)


@dataclass(frozen=True)
class RuntimeBudgets:
    max_jobs: int
    max_accounts: int
    max_required_feeds: int
    max_signals_per_callback: int
    max_actions_per_callback: int
    max_pending_actions: int
    max_seen_actions: int
    max_config_bytes: int
    max_signal_bytes: int
    max_action_bytes: int
    max_callback_ns: int
    max_feed_age_ns: int

    def __post_init__(self):
        for name, value in vars(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(name + " must be an explicit positive integer")


@dataclass(frozen=True)
class StrategyRisk:
    funding_buffer: Decimal
    account_notional_limit: Decimal
    strategy_notional_limit: Decimal
    account_capital_limit: Decimal
    strategy_capital_limit: Decimal
    margin_rate: Decimal | None

    def __post_init__(self):
        for name in ("funding_buffer", "account_notional_limit", "strategy_notional_limit",
                     "account_capital_limit", "strategy_capital_limit"):
            object.__setattr__(self, name, positive_decimal(getattr(self, name), name,
                                                          zero=name == "funding_buffer"))
        if self.margin_rate is not None:
            value = positive_decimal(self.margin_rate, "explicit paper margin rate")
            if value > 1:
                raise ValueError("Paper margin rate exceeds one")
            object.__setattr__(self, "margin_rate", value)


@dataclass(frozen=True)
class StrategyRegistration:
    job_id: str
    module: StrategyModule
    strategy_version: str
    config_version: str
    config: Mapping[str, Any]
    research_receipt: Mapping[str, Any]
    paper_permission: bool
    required_feeds: tuple[str, ...]
    warmup_bars: int
    accounts: tuple[str, ...]
    risk: Mapping[str, StrategyRisk]
    action_builder: Callable[[Signal, StrategyDataView, Mapping[str, Any]], list[ExecutionAction]]


@dataclass
class _Job:
    registration: StrategyRegistration
    instance: Any
    subscriptions: tuple
    config: Mapping[str, Any]
    fingerprint: str
    state: str = "WARMING"
    reason: str | None = None
    callbacks: int = 0
    actions: int = 0
    restart_blocked: bool = False
    permission_blocked: bool = False


class PaperStrategyRuntime:
    def __init__(self, *, hot_path: HotPathStore, accounts: Mapping[str, PaperExecution],
                 products: Mapping[str, ProductSpec], known_instruments: Mapping[str, Any],
                 reference_feeds: Mapping[str, str],
                 budgets: RuntimeBudgets, settings: Mapping[str, Any], mode: TradingMode,
                 phase: str, now_ns: Callable[[], int],
                 callback_clock_ns: Callable[[], int] = time.perf_counter_ns):
        if TradingMode(mode) != TradingMode.PAPER:
            raise ValueError("Sandbox/live strategy execution is capability BLOCKED")
        if phase not in {"paper", "replay"}:
            raise ValueError("Runtime phase must explicitly be paper or replay")
        if not isinstance(settings, Mapping):
            raise ValueError("Explicit local runtime settings are required")
        if not 0 < len(accounts) <= budgets.max_accounts:
            raise ValueError("Runtime account count exceeds declared capacity")
        if not known_instruments or len(known_instruments) > budgets.max_required_feeds:
            raise ValueError("Runtime feed inventory exceeds declared capacity")
        if len({id(matcher) for matcher in accounts.values()}) != len(accounts):
            raise ValueError("Each shared account requires exactly one matcher")
        for key, matcher in accounts.items():
            if key != matcher.ledger.scope.key or matcher.ledger.mode != TradingMode.PAPER:
                raise ValueError("Paper account binding differs from its durable ledger")
        for key, instrument in known_instruments.items():
            if instrument.key != key:
                raise ValueError("Known instrument key mismatch")
        if not set(reference_feeds) <= set(products):
            raise ValueError("Reference mapping requires an explicit execution product")
        for key, spec in products.items():
            spec.supported()
            feed = reference_feeds.get(key, key)
            if key != spec.instrument.key or feed not in known_instruments:
                raise ValueError("Product inventory must match the known feed identity")
            if key != feed:
                reference, execution = known_instruments[feed], spec.instrument
                def security(raw):
                    return raw[1:] if len(raw) == 7 and raw.startswith("A") and raw[1:].isdigit() else raw
                if (execution.venue != Venue.KIS or reference.venue not in {Venue.KIWOOM, Venue.KRX, Venue.TOSS}
                        or execution.asset_class not in {AssetClass.EQUITY, AssetClass.ETF}
                        or execution.asset_class != reference.asset_class
                        or execution.std_symbol != reference.std_symbol
                        or security(execution.raw_symbol) != security(reference.raw_symbol)
                        or not execution.currency or execution.currency != reference.currency
                        or execution.currency != spec.quote_currency or spec.quote_currency != "KRW"):
                    raise ValueError("Reference mapping requires the same explicit Korean security, asset class, and KRW currency")
        self.hot_path, self.budgets = hot_path, budgets
        self.accounts, self.products = MappingProxyType(dict(accounts)), MappingProxyType(dict(products))
        self.known = MappingProxyType(dict(known_instruments))
        self.reference_feeds = MappingProxyType(dict(reference_feeds))
        self.settings, self.phase = _freeze(settings), phase
        self.now_ns, self.callback_clock_ns = now_ns, callback_clock_ns
        self._jobs: dict[str, _Job] = {}
        self._seen_actions: dict[str, str] = {}
        self._account_faults: dict[str, str] = {}
        self._invalid_inputs: dict[str, str] = {}
        self._deferred_input_gaps: dict[str, str] = {}
        self._pending = 0
        self._processing = False
        # A restored matcher alone is not proof that the shared market engine
        # completed replay/handoff. Heartbeats cannot advance it before a
        # causally applied LIVE/PAPER input reaches this seam.
        self._input_phase = "UNVERIFIED"
        self.view: StrategyDataView | None = None
        # A coupled checkpoint gate starts IDLE for the existing runtime.  Only
        # an explicit verified RuntimeCheckpoint restore opens it to READY.
        self._recovery_state, self._recovery_reason = "IDLE", None
        self._recovery_evidence = None
        self._last_process_cursor = None
        self._last_process_clock = None
        self._checkpoint_captured_cursor = None
        self._checkpoint_expected_state = self._checkpoint_restore_proof_value = None
        self._staged_instances = None

    @staticmethod
    def _document(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str,
                          allow_nan=False, ensure_ascii=False)

    def register(self, registration: StrategyRegistration):
        sid = bounded_identity(registration.module.strategy_id, "strategy id")
        for name in ("job_id", "strategy_version", "config_version"):
            bounded_identity(getattr(registration, name), name)
        if sid in self._jobs or len(self._jobs) >= self.budgets.max_jobs:
            raise ValueError("Strategy registration is duplicate or exceeds capacity")
        if not isinstance(registration.paper_permission, bool):
            raise ValueError("PAPER permission must be explicit")
        if not registration.research_receipt or not callable(registration.action_builder):
            raise ValueError("Research receipt and explicit action builder are required")
        if (not registration.required_feeds or len(set(registration.required_feeds)) != len(registration.required_feeds)
                or len(registration.required_feeds) > self.budgets.max_required_feeds
                or not set(registration.required_feeds) <= set(self.known)):
            raise ValueError("Required feeds must be explicit, unique, known, and bounded")
        if (not registration.accounts or len(set(registration.accounts)) != len(registration.accounts)
                or not set(registration.accounts) <= set(self.accounts)
                or set(registration.risk) != set(registration.accounts)):
            raise ValueError("Registration requires exact account/risk bindings")
        minimum = compile_warmup(registration.module.warmup)
        if (isinstance(registration.warmup_bars, bool) or not isinstance(registration.warmup_bars, int)
                or not minimum <= registration.warmup_bars <= self.hot_path.history.max_rows):
            raise ValueError("Explicit warmup must cover the module requirement within history capacity")
        subscriptions = compile_subscriptions(registration.module.subscriptions)
        if not subscriptions:
            raise ValueError("Strategy must declare subscriptions")
        for sub in subscriptions:
            if not any(sub.matches_instrument(self.known[key]) for key in registration.required_feeds):
                raise ValueError("Subscriptions require an explicit matching feed dependency")
        proof = {"strategy_version": registration.strategy_version, "config": dict(registration.config),
                 "research_receipt": dict(registration.research_receipt),
                 "required_feeds": registration.required_feeds, "warmup_bars": registration.warmup_bars,
                 "accounts": registration.accounts,
                 "reference_feeds": dict(self.reference_feeds),
                 "risk": {key: vars(value) for key, value in registration.risk.items()},
                 "paper_permission": registration.paper_permission}
        text = self._document(proof)
        if len(text.encode()) > self.budgets.max_config_bytes:
            raise ValueError("Strategy registration document capacity exceeded")
        fingerprint = stable_hash([text])
        registration = replace(registration, config=_freeze(registration.config),
            research_receipt=_freeze(registration.research_receipt),
            risk=MappingProxyType(dict(registration.risk)),
            accounts=tuple(registration.accounts), required_feeds=tuple(registration.required_feeds))
        restart_blocked = False
        if self.phase != "replay":
            # Any prior dispatch evidence in any bound account means a fresh
            # plugin instance cannot prove it restored that strategy's state.
            # Read every account before writing registration claims.
            for account in registration.accounts:
                if self.accounts[account].ledger.has_strategy_dispatch(sid):
                    restart_blocked = True
            for account in registration.accounts:
                self.accounts[account].ledger.claim_strategy_event(sid, registration.config_version,
                    "@registration", {"registration": fingerprint})
        instance = registration.module.create()
        job = _Job(registration, instance, subscriptions, _freeze(registration.config), fingerprint)
        # Durable dispatch evidence is a property of the ledger, not of the
        # permission label displayed for this instance. Keep both fences
        # independently sticky so neither a control operation nor replay can
        # turn a permission-denied or state-unverified job back into dispatch.
        job.restart_blocked = restart_blocked
        job.permission_blocked = not registration.paper_permission
        if job.permission_blocked:
            job.state, job.reason = "BLOCKED", "paper_permission_missing"
        elif job.restart_blocked:
            job.state, job.reason = "BLOCKED", "strategy_state_restore_unverified"
        self._jobs[sid] = job

    def prepare_history(self, instrument_key: str, rows):
        if self._processing or instrument_key not in self.known:
            raise ValueError("History preparation requires a known feed outside callback dispatch")
        self.hot_path.load_history(instrument_key, rows)

    def _ready(self, job, snapshot):
        registration = job.registration
        for key in registration.required_feeds:
            if key in self._invalid_inputs:
                return False
            if snapshot.quality.get(key) != "VALID":
                return False
            latest = snapshot.latest_book(key) or snapshot.latest_trade(key) or snapshot.latest_index_quote(key)
            if latest is None:
                bars = snapshot.closed_bars(key, 1)
                latest = bars[-1] if bars else None
            if latest is None or latest.receive_ts_ns is None or not 0 <= snapshot.now_ns-latest.receive_ts_ns <= self.budgets.max_feed_age_ns:
                return False
            if latest.source_ts_ns is not None and not 0 <= snapshot.now_ns-latest.source_ts_ns <= self.budgets.max_feed_age_ns:
                return False
            if key in {self.reference_feeds.get(product, product) for product in self.products} and snapshot.latest_book(key) is None:
                return False
            if registration.warmup_bars and (snapshot.history_quality.get(key) != "VALID" or
                    len(snapshot.closed_bars(key, registration.warmup_bars)) < registration.warmup_bars):
                return False
        return all(account not in self._account_faults and self.accounts[account].ledger.readiness()["ready"]
                   for account in registration.accounts)

    def process_event(self, event: BaseMarketEvent | dict):
        if self._processing:
            raise RuntimeError("Strategy runtime dispatch is not reentrant")
        if isinstance(event, dict):
            event = event_from_dict(event)
        if event.instrument.key not in self.known or self.known[event.instrument.key] != event.instrument:
            raise ValueError("Runtime input instrument is not in the explicit inventory")
        result = self.hot_path.apply_versioned(event)
        snapshot = self.hot_path.snapshot(self.now_ns(), self.phase.upper())
        return self.process_applied(result, snapshot)

    @property
    def recovery_state(self):
        return self._recovery_state

    def _recovery_gate(self):
        return self._recovery_state in {"RECOVERING", "QUARANTINED"}

    def begin_checkpoint_recovery(self):
        if self._processing or self._pending:
            raise RuntimeError("Paper checkpoint recovery requires a quiescent runtime")
        if self._recovery_state == "QUARANTINED":
            raise RuntimeError("Paper runtime is permanently quarantined")
        if self._recovery_state == "RECOVERING":
            raise RuntimeError("Paper checkpoint recovery is already in progress")
        self._recovery_state, self._recovery_reason = "RECOVERING", "checkpoint_restore_started"

    def quarantine_checkpoint_recovery(self, reason):
        self._recovery_state = "QUARANTINED"
        self._recovery_reason = str(reason)[:256] or "checkpoint_restore_failed"

    def _checkpoint_capture_guard(self, engine):
        if self._processing or self._pending:
            raise ValueError("Paper checkpoint capture requires no callback/pending action")
        if self._recovery_state in {"RECOVERING", "QUARANTINED"}:
            raise ValueError("Paper checkpoint capture requires an idle/ready recovery gate")
        hot = engine.hot_path
        cursor = (hot.engine_incarnation, hot.apply_seq, hot.clock_version)
        if hot.apply_seq:
            if (self._last_process_cursor != (hot.engine_incarnation, hot.apply_seq)
                    or self._last_process_clock != hot.clock_version):
                raise ValueError("Paper runtime process_applied cursor is stale or not attached")
        elif self._last_process_cursor is not None and self._last_process_cursor[1] != 0:
            raise ValueError("Paper runtime holds a nonvirgin cursor")
        self._checkpoint_captured_cursor = cursor

    @staticmethod
    def _json_state(value, limit, depth=0):
        if depth > 64:
            raise ValueError("plugin checkpoint state nesting exceeds bound")
        if value is None or type(value) in {bool, int, str}:
            if type(value) is str and len(value.encode("utf-8")) > limit:
                raise ValueError("plugin checkpoint state string exceeds bound")
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("plugin checkpoint state contains a nonfinite number")
            return value
        if type(value) is list:
            if len(value) > limit:
                raise ValueError("plugin checkpoint state list exceeds capacity")
            return [PaperStrategyRuntime._json_state(item, limit, depth + 1) for item in value]
        if type(value) is dict:
            if len(value) > limit:
                raise ValueError("plugin checkpoint state mapping exceeds capacity")
            result = {}
            for key, item in value.items():
                if type(key) is not str or not key:
                    raise ValueError("plugin checkpoint state keys must be explicit strings")
                result[key] = PaperStrategyRuntime._json_state(item, limit, depth + 1)
            return result
        raise ValueError("plugin checkpoint state must contain only dict/list/scalars")

    def _plugin_state(self, instance, limit):
        hook = getattr(instance, "checkpoint_state", None)
        if not callable(hook) or inspect.iscoroutinefunction(hook):
            raise ValueError("strategy plugin requires a synchronous checkpoint_state hook")
        value = hook()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise ValueError("strategy checkpoint_state must be synchronous")
        detached = self._json_state(value, limit)
        encoded = self._document(detached).encode()
        if len(encoded) > limit:
            raise ValueError("plugin checkpoint state byte bound exceeded")
        return detached, hashlib.sha256(encoded).hexdigest()

    def _runtime_identity(self):
        accounts = {key: {"scope": matcher.ledger.scope.key, "mode": matcher.ledger.mode.value,
                          "path": str(matcher.ledger.path)}
                    for key, matcher in sorted(self.accounts.items())}
        # Bind complete economics and instrument identities, not only their
        # keys. A fee/lot/multiplier/currency change at the same key must reject.
        products = {key: spec.to_document() for key, spec in sorted(self.products.items())}
        known = {key: (instrument.model_dump(mode="json")
                       if callable(getattr(instrument, "model_dump", None)) else str(instrument))
                 for key, instrument in sorted(self.known.items())}
        jobs = {}
        for sid, job in sorted(self._jobs.items()):
            registration = job.registration
            module = registration.module.module
            jobs[sid] = {
                "job_id": registration.job_id,
                "strategy_id": registration.module.strategy_id,
                "module_name": getattr(module, "__name__", None),
                "declared_version": getattr(module, "STRATEGY_VERSION",
                                            getattr(module, "VERSION", None)),
                "strategy_version": registration.strategy_version,
                "config_version": registration.config_version,
                "config": dict(registration.config),
                "fingerprint": job.fingerprint,
            }
        proof = {"phase": self.phase, "budgets": vars(self.budgets),
                 "settings": self._document(dict(self.settings)),
                 "products": products, "known": known,
                 "jobs": jobs,
                 "reference_feeds": dict(self.reference_feeds),
                 "accounts": accounts,
                 "matchers": {key: dict(matcher.config)
                              for key, matcher in sorted(self.accounts.items())}}
        return proof, hashlib.sha256(self._document(proof).encode()).hexdigest()

    def _job_state_document(self, sid, job, max_bytes):
        plugin_state, plugin_sha = self._plugin_state(job.instance, max_bytes)
        return {"strategy_id": sid, "job_id": job.registration.job_id,
            "plugin_class": type(job.instance).__module__ + "." + type(job.instance).__qualname__,
            "strategy_version": job.registration.strategy_version,
            "config_version": job.registration.config_version,
            "fingerprint": job.fingerprint, "plugin_sha256": plugin_sha,
            "plugin_state": plugin_state,
            "lifecycle": {"state": job.state, "reason": job.reason,
                "callbacks": job.callbacks, "actions": job.actions,
                "permission_blocked": job.permission_blocked,
                "restart_blocked": job.restart_blocked}}

    def _current_runtime_state(self, max_bytes):
        identity, identity_sha = self._runtime_identity()
        jobs = [self._job_state_document(sid, job, max_bytes)
                for sid, job in sorted(self._jobs.items())]
        return {"version": 1, "identity": identity, "identity_sha256": identity_sha,
                "jobs": jobs, "seen_actions": dict(self._seen_actions),
                "account_faults": dict(self._account_faults),
                "invalid_inputs": dict(self._invalid_inputs),
                "deferred_input_gaps": dict(self._deferred_input_gaps)}

    def capture_runtime_state(self, *, max_bytes):
        if self._processing or self._pending:
            raise ValueError("Paper checkpoint capture requires no callback/pending action")
        if self._recovery_state in {"RECOVERING", "QUARANTINED"}:
            raise ValueError("Paper checkpoint capture requires an idle/ready recovery gate")
        hot_before = self._checkpoint_captured_cursor
        state = self._current_runtime_state(max_bytes)
        self._json_state(state, max_bytes)
        if hot_before is not None and self._checkpoint_captured_cursor != hot_before:
            raise ValueError("market cursor changed during strategy checkpoint capture")
        if self._checkpoint_captured_cursor is None:
            if self.hot_path.apply_seq:
                raise ValueError("Paper checkpoint capture lacks a market cursor guard")
            self._checkpoint_captured_cursor = (self.hot_path.engine_incarnation,
                self.hot_path.apply_seq, self.hot_path.clock_version)
        return deepcopy(state)

    def restore_runtime_state(self, state, *, verify=None, max_bytes):
        if self._processing or self._pending:
            raise ValueError("Paper checkpoint restore requires no callback/pending action")
        if self._recovery_state != "RECOVERING":
            raise ValueError("Paper checkpoint restore requires the recovery gate")
        if type(state) is not dict or set(state) != {
                "version", "identity", "identity_sha256", "jobs", "seen_actions",
                "account_faults", "invalid_inputs", "deferred_input_gaps"}:
            raise ValueError("Paper checkpoint state schema mismatch")
        if state["version"] != 1:
            raise ValueError("unsupported Paper checkpoint version")
        identity, identity_sha = self._runtime_identity()
        if state["identity"] != identity or state["identity_sha256"] != identity_sha:
            raise ValueError("Paper runtime settings/inventory/account identity mismatch")
        snapshots = {}
        for item in state["jobs"]:
            if type(item) is not dict or item.get("strategy_id") in snapshots:
                raise ValueError("Paper checkpoint job inventory is invalid")
            snapshots[item["strategy_id"]] = item
        if set(snapshots) != set(self._jobs):
            raise ValueError("Paper checkpoint job inventory differs from registration")
        staged = {}
        restored_jobs = {}
        for sid, job in self._jobs.items():
            snap = snapshots[sid]
            if (snap.get("job_id") != job.registration.job_id
                    or snap.get("strategy_version") != job.registration.strategy_version
                    or snap.get("config_version") != job.registration.config_version
                    or snap.get("fingerprint") != job.fingerprint):
                raise ValueError("Paper checkpoint job/config identity differs from registration")
            plugin_state, plugin_sha = self._json_state(snap.get("plugin_state"), max_bytes), snap.get("plugin_sha256")
            if hashlib.sha256(self._document(plugin_state).encode()).hexdigest() != plugin_sha:
                raise ValueError("Paper checkpoint plugin checksum mismatch")
            instance = job.registration.module.create()
            if (type(instance).__module__ + "." + type(instance).__qualname__) != snap.get("plugin_class"):
                raise ValueError("Paper checkpoint plugin module/class identity differs")
            hook = getattr(instance, "restore_state", None)
            if not callable(hook) or inspect.iscoroutinefunction(hook):
                raise ValueError("strategy plugin requires a synchronous restore_state hook")
            result = hook(deepcopy(plugin_state))
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise ValueError("strategy restore_state must be synchronous")
            check, check_sha = self._plugin_state(instance, max_bytes)
            if check != plugin_state or check_sha != plugin_sha:
                raise ValueError("strategy restore_state roundtrip differs from checkpoint")
            lifecycle = snap.get("lifecycle")
            if (type(lifecycle) is not dict or set(lifecycle) != {
                    "state", "reason", "callbacks", "actions", "permission_blocked",
                    "restart_blocked"}):
                raise ValueError("Paper checkpoint lifecycle schema mismatch")
            saved = lifecycle.get("state")
            if saved not in {"PAPER", "REPLAY", "WARMING", "PAUSED", "HALTED", "FAULTED", "BLOCKED"}:
                raise ValueError("Paper checkpoint lifecycle state is invalid")
            reason_saved = lifecycle.get("reason")
            if reason_saved is not None and type(reason_saved) is not str:
                raise ValueError("Paper checkpoint lifecycle reason is invalid")
            callbacks, actions = lifecycle.get("callbacks"), lifecycle.get("actions")
            for value, name in ((callbacks, "callbacks"), (actions, "actions")):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("Paper checkpoint lifecycle " + name + " is invalid")
            if (type(lifecycle.get("permission_blocked")) is not bool
                    or type(lifecycle.get("restart_blocked")) is not bool):
                raise ValueError("Paper checkpoint lifecycle fence schema mismatch")
            if job.permission_blocked or lifecycle.get("permission_blocked"):
                final_state, reason = "BLOCKED", "paper_permission_missing"
            elif saved in {"PAUSED", "HALTED", "FAULTED"}:
                final_state, reason = saved, reason_saved
            elif saved == "BLOCKED":
                if reason_saved == "strategy_state_restore_unverified":
                    final_state, reason = "WARMING", None
                else:
                    final_state, reason = "BLOCKED", reason_saved
            else:
                final_state, reason = "WARMING", None
            restored_jobs[sid] = {"instance": instance, "state": final_state,
                "reason": reason, "callbacks": callbacks, "actions": actions,
                "permission_blocked": bool(job.permission_blocked
                    or lifecycle.get("permission_blocked")),
                "restart_blocked": final_state == "BLOCKED" and reason not in {
                    "strategy_state_restore_unverified", "paper_permission_missing"}}
            staged[sid] = instance
        seen = state.get("seen_actions")
        if (type(seen) is not dict or len(seen) > self.budgets.max_seen_actions
                or any(type(k) is not str or type(v) is not str for k, v in seen.items())):
            raise ValueError("Paper checkpoint seen-action dedup schema mismatch")
        for name in ("account_faults", "invalid_inputs", "deferred_input_gaps"):
            value = state.get(name)
            if type(value) is not dict or any(type(k) is not str or type(v) is not str
                                              for k, v in value.items()):
                raise ValueError("Paper checkpoint " + name + " schema mismatch")
        if verify is not None and verify() is not True:
            raise ValueError("Paper journal evidence changed during staged restore")
        # Publish only after every hook and external journal proof succeeded.
        for sid, values in restored_jobs.items():
            job = self._jobs[sid]
            job.instance = values["instance"]
            job.state, job.reason = values["state"], values["reason"]
            job.callbacks, job.actions = values["callbacks"], values["actions"]
            job.permission_blocked = values["permission_blocked"]
        # The VN07A restart barrier is cleared only when the complete
        # market/plugin/journal proof is published, not merely when hooks return.
        self._staged_restart_blocked = {
            sid: values["restart_blocked"] for sid, values in restored_jobs.items()}
        self._seen_actions = dict(seen)
        self._account_faults = dict(state["account_faults"])
        self._invalid_inputs = dict(state["invalid_inputs"])
        self._deferred_input_gaps = dict(state["deferred_input_gaps"])
        # A digest of the saved document alone cannot detect a readiness hook
        # mutating a staged instance or any restored map. Capture the actual
        # normalized post-hook state and revalidate that before publication.
        current = self._current_runtime_state(max_bytes)
        self._checkpoint_source_state = deepcopy(state)
        self._checkpoint_expected_state = deepcopy(current)
        self._checkpoint_max_bytes = max_bytes
        self._checkpoint_restore_proof_value = hashlib.sha256(
            self._document(current).encode()).hexdigest()
        self._staged_instances = staged
        self._input_phase = "UNVERIFIED"
        self.view = None
        return self._checkpoint_restore_proof_value

    def checkpoint_restore_proof(self, state=None):
        if self._checkpoint_expected_state is None:
            return None
        source = getattr(self, "_checkpoint_source_state", None)
        if (state is not None and source is not None
                and self._document(state) != self._document(source)):
            return None
        current = self._current_runtime_state(self._checkpoint_max_bytes)
        if self._document(current) != self._document(self._checkpoint_expected_state):
            return None
        return self._checkpoint_restore_proof_value

    def publish_checkpoint_restore(self, hot_path, proof):
        if self._recovery_state != "RECOVERING":
            return False
        if not hot_path or proof != self.checkpoint_restore_proof():
            return False
        for sid, blocked in getattr(self, "_staged_restart_blocked", {}).items():
            if sid in self._jobs:
                self._jobs[sid].restart_blocked = blocked
        self.hot_path = hot_path
        self._recovery_state, self._recovery_reason = "READY", None
        self._recovery_evidence = {"proof": proof,
            "cursor": (hot_path.engine_incarnation, hot_path.apply_seq, hot_path.clock_version)}
        # The staged restore is the chain of custody for this cursor.  Recording
        # it makes a later quiescent save causally aligned without pretending an
        # event callback ran.
        self._last_process_cursor = (hot_path.engine_incarnation, hot_path.apply_seq)
        self._last_process_clock = hot_path.clock_version
        self._input_phase = "UNVERIFIED"
        self.view = None
        return True

    def process_applied(self, result: ApplyResult, snapshot: DecisionSnapshot):
        """Consume the shared engine's exact post-apply result and snapshot.

        Snapshot phase carries transport/recovery authority independently of
        this host's paper permission. Replay may warm callbacks but cannot
        claim decisions, build actions, or advance durable paper matching.
        """
        if self._processing:
            raise RuntimeError("Strategy runtime dispatch is not reentrant")
        if self._recovery_gate():
            return ()
        self._last_process_cursor = snapshot.cursor
        self._last_process_clock = snapshot.clock_version
        self._input_phase = "INVALID"
        if (snapshot.cursor != (result.engine_incarnation, result.apply_seq) or
                snapshot.cursor != (self.hot_path.engine_incarnation, self.hot_path.apply_seq) or
                snapshot.clock_version != self.hot_path.clock_version):
            raise ValueError("Applied event and shared snapshot cursor differ")
        if (result.event.instrument.key not in self.known or
                self.known[result.event.instrument.key] != result.event.instrument or
                result.event.venue != result.event.instrument.venue):
            raise ValueError("Applied event differs from the explicit feed inventory")
        phase = snapshot.phase
        if phase not in {"LIVE", "PAPER", "REPLAY", "INVALID", "WARMING"}:
            raise ValueError("Applied snapshot requires an explicit LIVE/PAPER/REPLAY/INVALID/WARMING phase")
        if phase not in {"INVALID", "WARMING"} and (self.phase == "replay" or result.event.ingest_mode != "realtime"):
            phase = "REPLAY"
        self._input_phase = phase
        if (phase in {"LIVE", "PAPER"} and result.applied and not result.duplicate
                and self._fresh_input(result.event, snapshot.now_ns)
                and snapshot.quality.get(result.event.instrument.key) == "VALID"):
            key = result.event.instrument.key
            execution_feed = key in {self.reference_feeds.get(product, product) for product in self.products}
            if not execution_feed or isinstance(result.event, OrderBookEvent):
                self._invalid_inputs.pop(key, None)
        self._processing = True
        try:
            return self._dispatch(result, snapshot, phase)
        finally:
            self._processing = False

    def _market(self, result, snapshot, phase):
        if phase not in {"LIVE", "PAPER"} or result.duplicate or result.reason == "raw_only":
            return
        if self._deferred_input_gaps:
            if not result.applied or not self._fresh_input(result.event, snapshot.now_ns):
                return
            # Recovery did not establish a continuous matching input cursor.
            # Fence from the last committed clock before advancing any account
            # using a new live input; cached depth cannot fill the missing span.
            self._gap_matchers(self._deferred_input_gaps)
            self._deferred_input_gaps.clear()
        event, reference_key = result.event, result.event.instrument.key
        for account, matcher in self.accounts.items():
            if account in self._account_faults:
                continue
            for key, spec in self.products.items():
                if (self.reference_feeds.get(key, key) != reference_key or
                        matcher.ledger.scope.venue != spec.instrument.venue.value):
                    continue
                def mapped(value):
                    if key == reference_key:
                        return value
                    return value.model_copy(update={"instrument": spec.instrument, "venue": spec.instrument.venue,
                        "event_id": stable_hash(["paper_reference", value.event_id, reference_key, key])})
                try:
                    if not self._fresh_input(event, snapshot.now_ns):
                        matcher.on_gap(key, max(snapshot.now_ns, matcher.report()["clock_ns"]),
                                       "receipt_or_native_source_not_fresh")
                    elif not result.applied or snapshot.quality.get(reference_key) != "VALID":
                        matcher.on_gap(key, int(event.receive_ts_ns), result.reason)
                    elif isinstance(event, OrderBookEvent):
                        projected = snapshot.latest_book(reference_key)
                        if projected is None:
                            matcher.on_gap(key, int(event.receive_ts_ns), "projected_book_missing")
                        else:
                            matcher.on_book(mapped(projected))
                    elif isinstance(event, TradeEvent):
                        matcher.on_trade(mapped(event))
                except Exception as exc:
                    self._account_faults[account] = type(exc).__name__
                    try:
                        matcher.on_gap(key, max(int(event.receive_ts_ns), matcher.report()["clock_ns"]),
                                       "market_input_rejected")
                    except Exception:
                        pass

    def _fresh_input(self, event, now):
        return (event.ingest_mode == "realtime" and event.receive_ts_ns is not None and
                0 <= now-event.receive_ts_ns <= self.budgets.max_feed_age_ns and
                (event.source_ts_ns is None or
                 0 <= now-event.source_ts_ns <= self.budgets.max_feed_age_ns))

    def _claim(self, job, event, snapshot):
        registration = job.registration
        proof = {"registration": job.fingerprint, "event": stable_hash([event.model_dump(mode="json")]),
                 "strategy_version": registration.strategy_version}
        # A partial cross-account claim also suppresses replay; omitted actions
        # require explicit operator review rather than an automatic risk retry.
        claims = [self.accounts[account].ledger.claim_strategy_event(registration.module.strategy_id,
                  registration.config_version, event.event_id, proof) for account in registration.accounts]
        return all(claims)

    def _dispatch(self, result, snapshot, phase):
        self.view = StrategyDataView(snapshot, ready=False, phase=phase)
        if phase in {"INVALID", "WARMING"}:
            return ()
        replay = phase == "REPLAY"
        self._market(result, snapshot, phase)
        if result.duplicate or not result.applied:
            return ()
        if not replay and not self._fresh_input(result.event, snapshot.now_ns):
            return ()
        event, generated = result.event, []
        for sid, job in self._jobs.items():
            # Replay may warm callbacks for inspection, but a restart-blocked
            # job can never dispatch PAPER work or establish recovery proof.
            if (job.permission_blocked or job.state in {"PAUSED", "HALTED", "FAULTED"}
                    or (job.state == "BLOCKED" and not (job.restart_blocked and replay))
                    or (job.restart_blocked and not replay)):
                continue
            if (event.instrument.key not in job.registration.required_feeds or
                    not any(sub.matches(event) for sub in job.subscriptions)):
                continue
            if not self._ready(job, snapshot):
                job.state = "WARMING"
                continue
            job.state = "REPLAY" if replay else "PAPER"
            job.reason = None
            view = StrategyDataView(snapshot, ready=True, phase=job.state)
            try:
                if not replay and not self._claim(job, event, snapshot):
                    continue
                started = self.callback_clock_ns()
                signals = job.instance.on_event(event, view)
                job.callbacks += 1
                if self.callback_clock_ns()-started > self.budgets.max_callback_ns:
                    raise TimeoutError("Cooperative strategy callback exceeded its return-time budget")
                if not isinstance(signals, list) or len(signals) > self.budgets.max_signals_per_callback:
                    raise ValueError("Strategy signal count/type exceeds callback contract")
                pending = []
                for signal in signals:
                    if not isinstance(signal, Signal) or signal.strategy_id != sid:
                        raise ValueError("on_event must return this strategy's list[Signal]")
                    if len(signal.model_dump_json().encode()) > self.budgets.max_signal_bytes:
                        raise ValueError("Signal document capacity exceeded")
                    if signal.event_ts_ns > snapshot.now_ns or signal.valid_until_ts_ns <= snapshot.now_ns:
                        raise ValueError("Strategy signal is future-dated or expired")
                    if replay:
                        continue
                    actions = job.registration.action_builder(signal, view, job.config)
                    if not isinstance(actions, list) or len(actions)+len(pending) > self.budgets.max_actions_per_callback:
                        raise ValueError("Strategy action count/type exceeds callback contract")
                    pending.extend((action, signal) for action in actions)
                if self.callback_clock_ns()-started > self.budgets.max_callback_ns:
                    raise TimeoutError("Cooperative action builder exceeded its return-time budget")
                if self._pending+len(pending) > self.budgets.max_pending_actions:
                    raise ValueError("Pending strategy action capacity exceeded")
                prepared = [(action, signal, self._validate(job, action, signal, snapshot.now_ns))
                            for action, signal in pending]
                self._pending += len(prepared)
                try:
                    for action, signal, document in prepared:
                        if self._execute(job, action, signal, document, snapshot):
                            generated.append(action.action_id)
                            job.actions += 1
                finally:
                    self._pending -= len(prepared)
            except Exception as exc:
                job.state, job.reason = "FAULTED", type(exc).__name__
                if not replay:
                    self._cancel_owned(job, snapshot.now_ns)
        return tuple(generated)

    def _validate(self, job, action, signal, now):
        registration = job.registration
        if not isinstance(action, ExecutionAction):
            raise ValueError("Action builder must return list[ExecutionAction]")
        if (action.mode != TradingMode.PAPER or action.strategy_id != registration.module.strategy_id or
                action.config_version != registration.config_version or action.scope.key not in registration.accounts):
            raise ValueError("Action mode/strategy/config/account permission mismatch")
        if not now < action.expiry_ns <= signal.valid_until_ts_ns:
            raise ValueError("Action expiry exceeds its causal signal lifetime")
        if action.action == ActionKind.REPLACE:
            raise ValueError("REPLACE is unsupported until confirmed remaining-quantity handoff exists")
        if action.action == ActionKind.NEW:
            if (action.intent.job_id != registration.job_id or action.intent.signal_id != signal.signal_id or
                    action.intent.instrument.key not in self.products or
                    self.reference_feeds.get(action.intent.instrument.key, action.intent.instrument.key)
                    not in registration.required_feeds):
                raise ValueError("Intent job/signal/product/feed binding differs from registration")
        document = {"action_id": action.action_id, "action": action.action.value,
                    "scope": action.scope.key, "strategy": action.strategy_id,
                    "config_version": action.config_version, "expiry_ns": action.expiry_ns,
                    "intent": action.intent.model_dump(mode="json") if action.intent else None,
                    "target": action.target_order_id, "post_only": action.post_only,
                    "reduce_only": action.reduce_only, "time_in_force": action.time_in_force,
                    "group_id": action.group_id}
        if len(self._document(document).encode()) > self.budgets.max_action_bytes:
            raise ValueError("Action document capacity exceeded")
        return document

    def _execute(self, job, action, signal, document, snapshot):
        fingerprint = stable_hash([document])
        identity = action.scope.key + ":" + action.action_id
        old = self._seen_actions.get(identity)
        if old is not None:
            if old != fingerprint:
                raise ValueError("Immutable action identity collision")
            return False
        if len(self._seen_actions) >= self.budgets.max_seen_actions:
            raise ValueError("Seen-action capacity exceeded; dedup cannot be discarded")
        matcher = self.accounts[action.scope.key]
        if not matcher.ledger.claim_strategy_event(action.strategy_id, action.config_version,
                "@action:"+action.action_id, document):
            self._seen_actions[identity] = fingerprint
            return False
        self._seen_actions[identity] = fingerprint
        if action.action == ActionKind.CANCEL:
            return matcher.cancel(action.target_order_id, action.strategy_id, snapshot.now_ns)
        risk = job.registration.risk[action.scope.key]
        created, _ = matcher.submit(action.intent, action.strategy_id,
            {"registration": job.fingerprint, "source_event_id": signal.source_event_id,
             "signal_id": signal.signal_id, "action_id": action.action_id,
             "reference_feed": self.reference_feeds.get(action.intent.instrument.key, action.intent.instrument.key),
             "cursor": snapshot.cursor, "input_versions": dict(snapshot.versions)},
            action.expiry_ns, self.products[action.intent.instrument.key], snapshot.now_ns,
            reference_price=signal.reference_price, margin_rate=risk.margin_rate,
            funding_buffer=risk.funding_buffer, account_notional_limit=risk.account_notional_limit,
            strategy_notional_limit=risk.strategy_notional_limit, account_capital_limit=risk.account_capital_limit,
            strategy_capital_limit=risk.strategy_capital_limit, post_only=action.post_only,
            reduce_only=action.reduce_only, time_in_force=action.time_in_force, group_id=action.group_id)
        return created

    def _cancel_owned(self, job, now):
        sid = job.registration.module.strategy_id
        for account in job.registration.accounts:
            matcher = self.accounts[account]
            for identity, order in matcher.report()["open_orders"].items():
                if order["strategy"] == sid:
                    try:
                        matcher.cancel(identity, sid, max(now, matcher.report()["clock_ns"]))
                    except Exception as exc:
                        self._account_faults[account] = type(exc).__name__

    def invalidate_inputs(self, feed_keys: tuple[str, ...], reason: str, now_ns: int):
        """Fence exact known dependencies after a producer/transport fault.

        Callers without proven producer-to-feed ownership must pass every known
        feed. Provider names alone are not evidence for narrowing this fence.
        Fresh books are required to reopen execution-feed dependencies.
        """
        if self._processing:
            raise RuntimeError("Input invalidation cannot reenter strategy dispatch")
        if (not isinstance(feed_keys, tuple) or not feed_keys or len(set(feed_keys)) != len(feed_keys)
                or not set(feed_keys) <= set(self.known)):
            raise ValueError("Input invalidation requires exact unique known feed keys")
        bounded_identity(reason, "input invalidation reason")
        if isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns < 0:
            raise ValueError("Input invalidation requires nonnegative epoch nanoseconds")
        matching_was_live = self.phase == "paper" and self._input_phase in {"LIVE", "PAPER"}
        self._input_phase = "INVALID"
        for key in feed_keys:
            self._invalid_inputs[key] = reason
        for job in self._jobs.values():
            if (set(job.registration.required_feeds).intersection(feed_keys)
                    and job.state not in {"PAUSED", "HALTED", "FAULTED", "BLOCKED"}):
                job.state, job.reason = "WARMING", "input_invalid"
        if not matching_was_live:
            self._deferred_input_gaps.update({key: reason for key in feed_keys})
            return
        self._gap_matchers({key: reason for key in feed_keys}, now_ns)

    def _gap_matchers(self, feed_reasons, now_ns=None):
        for account, matcher in self.accounts.items():
            for key, spec in self.products.items():
                feed = self.reference_feeds.get(key, key)
                if (feed not in feed_reasons
                        or matcher.ledger.scope.venue != spec.instrument.venue.value):
                    continue
                try:
                    clock = matcher.report()["clock_ns"]
                    matcher.on_gap(key, clock if now_ns is None else max(now_ns, clock), feed_reasons[feed])
                except Exception as exc:
                    self._account_faults[account] = type(exc).__name__

    @staticmethod
    def _hold_blocked(job):
        """Restore a sticky fence; return whether the job remains blocked."""
        if job.permission_blocked:
            job.state, job.reason = "BLOCKED", "paper_permission_missing"
        elif job.restart_blocked:
            job.state, job.reason = "BLOCKED", "strategy_state_restore_unverified"
        else:
            return False
        return True

    def pause(self, strategy_id: str, now_ns: int):
        job = self._jobs[strategy_id]
        if self._hold_blocked(job):
            self._cancel_owned(job, now_ns)
            return
        job.state, job.reason = "PAUSED", "operator_pause"
        self._cancel_owned(job, now_ns)

    def resume(self, strategy_id: str):
        job = self._jobs[strategy_id]
        if self._hold_blocked(job):
            return
        if job.state != "PAUSED":
            raise ValueError("Only an explicitly paused strategy may resume")
        job.state, job.reason = "WARMING", None

    def halt(self, reason: str, now_ns: int, strategy_id: str | None = None):
        bounded_identity(reason, "halt reason")
        for sid, job in self._jobs.items():
            if strategy_id is None or sid == strategy_id:
                if self._hold_blocked(job):
                    # Operator stop remains risk-reducing even while the
                    # no-new-risk fence stays in place.
                    self._cancel_owned(job, now_ns)
                    continue
                job.state, job.reason = "HALTED", reason
                self._cancel_owned(job, now_ns)

    def advance(self, now_ns: int):
        if self._recovery_gate():
            return
        snapshot = self.hot_path.snapshot(now_ns, self.phase.upper())
        if (self.phase == "paper" and self._input_phase in {"LIVE", "PAPER"}
                and not self._deferred_input_gaps):
            for account, matcher in self.accounts.items():
                if account in self._account_faults:
                    continue
                try:
                    for key in self.products:
                        if (self.products[key].instrument.venue.value == matcher.ledger.scope.venue and
                                snapshot.quality.get(self.reference_feeds.get(key, key)) != "VALID"):
                            matcher.on_gap(key, now_ns, "dependency_invalid_or_stale")
                    matcher.advance(now_ns)
                except Exception as exc:
                    self._account_faults[account] = type(exc).__name__

    def report(self):
        return {"mode": "paper", "phase": self.phase, "external_mutations": 0,
                "input_phase": self._input_phase,
                "production_gate": "BLOCKED", "callback_isolation": "cooperative_return_time_only",
                "durable_dispatch": "at_most_once_claim_before_callback_or_submission",
                "replace": "unsupported_until_confirmed_remaining_handoff",
                "reference_feeds": dict(self.reference_feeds),
                "restart_evidence": {"matcher": "durable_ledger_state_checked",
                    "strategy_state_recovery": ("VERIFIED_LOCAL_EXACT_CUT" if self._recovery_state == "READY"
                                                else "UNVERIFIED"),
                    "blocked_strategies": tuple(sorted(
                        sid for sid, job in self._jobs.items() if job.restart_blocked)),
                    "continuous_input_cursor": ("VERIFIED_LOCAL_EXACT_CUT" if self._recovery_state == "READY"
                                                else "MISSING_EVIDENCE"),
                    "full_restart_gate": "BLOCKED",
                    "runtime_recovery": self._recovery_state,
                    "runtime_recovery_reason": self._recovery_reason},
                "pending_actions": self._pending, "seen_actions": len(self._seen_actions),
                "account_faults": dict(self._account_faults),
                "invalid_inputs": dict(self._invalid_inputs),
                "deferred_matching_gaps": tuple(sorted(self._deferred_input_gaps)),
                "strategies": {sid: {"state": job.state, "reason": job.reason,
                    "config_version": job.registration.config_version,
                    "callbacks": job.callbacks, "actions": job.actions}
                    for sid, job in self._jobs.items()}}
