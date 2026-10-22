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

from dataclasses import dataclass, replace
from decimal import Decimal
import json
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .execution_ledger import positive_decimal
from .hot_path import ApplyResult, DecisionSnapshot, HotPathStore, StrategyDataView, _freeze
from .models import (AssetClass, BaseMarketEvent, EventType, OrderBookEvent, Signal, TradeEvent, Venue,
                     event_from_dict, stable_hash)
from .paper_execution import PaperExecution
from .strategy_contract import StrategyModule, compile_subscriptions, compile_warmup
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
        if self.phase != "replay":
            for account in registration.accounts:
                self.accounts[account].ledger.claim_strategy_event(sid, registration.config_version,
                    "@registration", {"registration": fingerprint})
        instance = registration.module.create()
        job = _Job(registration, instance, subscriptions, _freeze(registration.config), fingerprint)
        if not registration.paper_permission:
            job.state, job.reason = "BLOCKED", "paper_permission_missing"
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

    def process_applied(self, result: ApplyResult, snapshot: DecisionSnapshot):
        """Consume the shared engine's exact post-apply result and snapshot.

        Snapshot phase carries transport/recovery authority independently of
        this host's paper permission. Replay may warm callbacks but cannot
        claim decisions, build actions, or advance durable paper matching.
        """
        if self._processing:
            raise RuntimeError("Strategy runtime dispatch is not reentrant")
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
            if job.state in {"PAUSED", "HALTED", "FAULTED", "BLOCKED"}:
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

    def pause(self, strategy_id: str, now_ns: int):
        job = self._jobs[strategy_id]
        job.state, job.reason = "PAUSED", "operator_pause"
        self._cancel_owned(job, now_ns)

    def resume(self, strategy_id: str):
        job = self._jobs[strategy_id]
        if job.state != "PAUSED":
            raise ValueError("Only an explicitly paused strategy may resume")
        job.state, job.reason = "WARMING", None

    def halt(self, reason: str, now_ns: int, strategy_id: str | None = None):
        bounded_identity(reason, "halt reason")
        for sid, job in self._jobs.items():
            if strategy_id is None or sid == strategy_id:
                job.state, job.reason = "HALTED", reason
                self._cancel_owned(job, now_ns)

    def advance(self, now_ns: int):
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
                    "continuous_input_cursor": "MISSING_EVIDENCE", "full_restart_gate": "BLOCKED"},
                "pending_actions": self._pending, "seen_actions": len(self._seen_actions),
                "account_faults": dict(self._account_faults),
                "invalid_inputs": dict(self._invalid_inputs),
                "deferred_matching_gaps": tuple(sorted(self._deferred_input_gaps)),
                "strategies": {sid: {"state": job.state, "reason": job.reason,
                    "config_version": job.registration.config_version,
                    "callbacks": job.callbacks, "actions": job.actions}
                    for sid, job in self._jobs.items()}}
