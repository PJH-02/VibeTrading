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
import stat
import time
import uuid
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from execution.execution_ledger import positive_decimal
from market.hot_path import ApplyResult, DecisionSnapshot, HotPathStore, StrategyDataView, _freeze
from core.models import AssetClass, BaseMarketEvent, EventType, OrderBookEvent, Signal, TradeEvent, Venue, event_from_dict, stable_hash
from execution.paper_execution import PaperExecution
from strategies import StrategyModule, compile_subscriptions, compile_warmup, load_strategy
from strategies.contract import load_strategy_manifest
from strategies.contract import action_protocol_version, source_has_order_feedback, validate_action_protocol
from execution.trading_contracts import (ActionKind, ExecutionAction, ProductSpec, TradingMode,
    StrategyActionPlan, StrategyOrderContext, StrategyOrderEvent, bounded_identity)
from operations.dashboard.latency import observe, observe_strategy


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
    feedback_cursors: dict[str, int] | None = None
    worker: Any = None
    isolated_plugin_class: str | None = None
    isolated_checkpoint_state: Any = None
    isolated_checkpoint_sha256: str | None = None
    isolated_restore_prepared: tuple | None = None
    isolated_pending: dict[str, Any] | None = None
    isolated_generation: int = 0
    isolated_initialized: bool = False
    isolated_init_request: dict[str, Any] | None = None
    isolated_restart_requested: bool = False


class PaperStrategyRuntime:
    def __init__(self, *, hot_path: HotPathStore, accounts: Mapping[str, PaperExecution],
                 products: Mapping[str, ProductSpec], known_instruments: Mapping[str, Any],
                 reference_feeds: Mapping[str, str],
                 budgets: RuntimeBudgets, settings: Mapping[str, Any], mode: TradingMode,
                 phase: str, now_ns: Callable[[], int],
                 callback_clock_ns: Callable[[], int] = time.perf_counter_ns,
                 isolation: Any = None):
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
                legacy_identity = (
                    execution.std_symbol == reference.std_symbol
                    and security(execution.raw_symbol) == security(reference.raw_symbol)
                )
                native_kiwoom_krx = (
                    reference.venue == Venue.KIWOOM
                    and reference.exchange == "KRX"
                    and execution.exchange == "KRX"
                    and reference.raw_symbol.isdigit() and len(reference.raw_symbol) == 6
                    and execution.raw_symbol == reference.raw_symbol
                    and execution.std_symbol == execution.raw_symbol
                    and reference.std_symbol == f"KIWOOM:KRX:{reference.raw_symbol}"
                )
                if (execution.venue != Venue.KIS or reference.venue not in {Venue.KIWOOM, Venue.KRX, Venue.TOSS}
                        or execution.asset_class not in {AssetClass.EQUITY, AssetClass.ETF}
                        or execution.asset_class != reference.asset_class
                        or not (legacy_identity or native_kiwoom_krx)
                        or not execution.currency or execution.currency != reference.currency
                        or execution.currency != spec.quote_currency or spec.quote_currency != "KRW"):
                    raise ValueError("Reference mapping requires the same explicit Korean security, asset class, and KRW currency")
        self.hot_path, self.budgets = hot_path, budgets
        self.accounts, self.products = MappingProxyType(dict(accounts)), MappingProxyType(dict(products))
        self.known = MappingProxyType(dict(known_instruments))
        self.reference_feeds = MappingProxyType(dict(reference_feeds))
        self.settings, self.phase = _freeze(settings), phase
        self.now_ns, self.callback_clock_ns = now_ns, callback_clock_ns
        if isolation is not None:
            from .strategy_process import StrategyIsolation
            if not isinstance(isolation, StrategyIsolation):
                raise TypeError("isolation must be an explicit StrategyIsolation configuration")
        self.isolation = isolation
        self._isolation_pool_cache = None
        self._parent_incarnation = uuid.uuid4().hex
        self._isolation_report = {"active": isolation is not None,
            "mode": (("confined_process" if isolation.require_bubblewrap else "trusted_process_only")
                     if isolation is not None else "cooperative"),
            "security_sandbox": False}
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
        self._draining_feedback = False

    @staticmethod
    def _configure_instance(instance, config):
        hook = getattr(instance, "configure", None)
        if hook is None:
            return
        if not callable(hook) or inspect.iscoroutinefunction(hook):
            raise ValueError("strategy plugin configure hook must be synchronous and callable")
        result = hook(config)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise ValueError("strategy plugin configure hook must be synchronous")

    @staticmethod
    def _validate_instance_registration(instance, facts):
        hook = getattr(instance, "validate_registration", None)
        if hook is None:
            return
        if not callable(hook) or inspect.iscoroutinefunction(hook):
            raise ValueError("strategy plugin validate_registration hook must be synchronous and callable")
        result = hook(facts)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise ValueError("strategy plugin validate_registration hook must be synchronous")

    def _registration_facts(self, registration):
        accounts = {}
        for key in registration.accounts:
            matcher = self.accounts[key]
            scope = matcher.ledger.scope
            capabilities = getattr(matcher, "capabilities", None)
            if not isinstance(capabilities, Mapping):
                capabilities = {"allow_taker_gtc": False, "allow_market_orders": False}
            else:
                capabilities = {
                    "allow_taker_gtc": capabilities.get("allow_taker_gtc", False) is True,
                    "allow_market_orders": capabilities.get("allow_market_orders", False) is True,
                }
            accounts[key] = {
                "scope": {"venue": scope.venue, "account": scope.account,
                          "product": scope.product, "environment": scope.environment},
                "mode": matcher.ledger.mode.value,
                "capabilities": capabilities,
            }
        return _freeze({
            "job_id": registration.job_id,
            "strategy_id": registration.module.strategy_id,
            "config_version": registration.config_version,
            "accounts": accounts,
            "products": {key: spec.to_document() for key, spec in self.products.items()},
            "instruments": {key: value.model_dump(mode="json") for key, value in self.known.items()},
            "required_feeds": tuple(registration.required_feeds),
            "reference_feeds": dict(self.reference_feeds),
        })

    @staticmethod
    def _document(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str,
                          allow_nan=False, ensure_ascii=False)

    def _isolation_pool(self):
        if self.isolation is None:
            return None
        if self._isolation_pool_cache is None:
            from .strategy_process import StrategyProcessPool
            self._isolation_pool_cache = StrategyProcessPool(self.isolation,
                parent_incarnation=self._parent_incarnation, now_ns=self.now_ns)
        return self._isolation_pool_cache

    async def _await_worker(self, worker, request, timeout_ns=None):
        """Control-plane worker handshake without blocking the parent loop."""
        return await worker.await_response(request, timeout_ns=timeout_ns)

    def _start_isolated_job(self, registration):
        from .strategy_process import IsolatedStrategyWorker
        sid = registration.module.strategy_id
        module = registration.module.module
        module_name = getattr(module, "__name__", None)
        module_path = getattr(module, "__file__", None)
        if not module_name and not module_path:
            raise ValueError("isolated registration requires an importable module or explicit source file")
        source_sha256 = None
        if module_path:
            source_path = Path(module_path).resolve(strict=True)
            if self.isolation.allowed_source_roots and not any(
                    source_path == Path(root).resolve() or
                    Path(root).resolve() in source_path.parents
                    for root in self.isolation.allowed_source_roots):
                raise ValueError("isolated strategy source is outside explicit source roots")
            source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
            declared_sha = getattr(module, "__cap04_source_sha256__", None)
            if declared_sha != source_sha256:
                raise ValueError("isolated strategy source changed after static manifest read")
        protocol = action_protocol_version(module)
        feedback = protocol == 1 and source_has_order_feedback(module_path)
        if protocol == 1 and not feedback:
            raise TypeError("ACTION_PROTOCOL_VERSION=1 requires a declared on_order_event method")
        worker = IsolatedStrategyWorker(
            strategy_id=sid, module_name=module_name, module_path=module_path,
            config=dict(registration.config),
            registration_facts=self._registration_facts(registration),
            expected={"strategy_id": sid, "job_id": registration.job_id,
                      "config_version": registration.config_version,
                      "source_sha256": source_sha256,
                      "subscriptions": registration.module.subscriptions,
                      "warmup": registration.module.warmup,
                      "action_protocol_version": protocol,
                      "has_feedback": feedback},
            isolation=self.isolation, parent_incarnation=self._parent_incarnation,
            now_ns=self.now_ns)
        pool = self._isolation_pool()
        pool.add(worker)
        try:
            request = worker.start()
            return worker, None, feedback, request
        except BaseException:
            pool.remove(sid)
            raise

    def _isolated_has_capacity(self):
        pool = self._isolation_pool()
        if pool is None:
            raise RuntimeError("hard isolation is not configured")
        if len(pool.workers) >= self.isolation.budgets.max_processes:
            raise ValueError("isolated process capacity exceeded")
        if pool.total_rss_bytes() > self.isolation.budgets.max_total_rss_bytes:
            raise ValueError("isolated process RSS capacity exceeded")

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
        if self.isolation is not None:
            proof["source_sha256"] = getattr(registration.module.module,
                                               "__cap04_source_sha256__", None)
        text = self._document(proof)
        if len(text.encode()) > self.budgets.max_config_bytes:
            raise ValueError("Strategy registration document capacity exceeded")
        fingerprint = stable_hash([text])
        registration = replace(registration, config=_freeze(registration.config),
            research_receipt=_freeze(registration.research_receipt),
            risk=MappingProxyType(dict(registration.risk)),
            accounts=tuple(registration.accounts), required_feeds=tuple(registration.required_feeds))
        # The physical account owns one explicit policy across every strategy
        # registered against it.  Account-scoped limits are compared with all
        # existing jobs (including paused/blocked/faulted ones) before any
        # durable claim or plugin instance exists; strategy-scoped limits and
        # funding buffers stay independent per registration.
        for account in registration.accounts:
            candidate = registration.risk[account]
            for existing in self._jobs.values():
                if account not in existing.registration.accounts:
                    continue
                current = existing.registration.risk[account]
                if (candidate.account_notional_limit != current.account_notional_limit
                        or candidate.account_capital_limit != current.account_capital_limit):
                    raise ValueError("Shared account risk limits differ from existing registrations")
        restart_blocked = False
        worker = plugin_class = init_request = None
        if self.isolation is None:
            # An optional startup-only hook may load/validate native history
            # before any durable registration claim or callback can expose risk.
            # Legacy plugins without the hook are unchanged.
            instance = registration.module.create()
            feedback_enabled = validate_action_protocol(registration.module.module, instance)
            self._configure_instance(instance, registration.config)
            self._validate_instance_registration(instance, self._registration_facts(registration))
        else:
            # Hard mode never calls create/configure/validate_registration in the
            # parent. A child is started and must return its own typed identity.
            if getattr(registration.module.module, "__cap04_static_manifest__", False) is not True:
                raise ValueError("isolated registration requires a static source manifest, not an imported plugin")
            self._isolated_has_capacity()
            worker, plugin_class, feedback_enabled, init_request = self._start_isolated_job(registration)
            instance = None
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
        if feedback_enabled:
            if self.phase != "replay":
                for account in registration.accounts:
                    self.accounts[account].ledger.enable_order_feedback(sid)
        job = _Job(registration, instance, subscriptions, _freeze(registration.config), fingerprint,
                   worker=worker, isolated_plugin_class=plugin_class,
                   isolated_pending=({init_request["request_id"]: {
                       "kind": "init", "generation": 0, "request": init_request}}
                       if worker is not None else None),
                   isolated_init_request=init_request)
        if feedback_enabled:
            job.feedback_cursors = {account: 0 for account in registration.accounts}
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
        for account in registration.accounts:
            observe_strategy(sid,self.accounts[account].ledger.scope.key,"PAPER",
                             "BLOCKED" if job.state=="BLOCKED" else "REGISTERED")

    def _validate_data_manifest(self, receipt):
        """Verify an optional point-in-time data-export manifest binding."""
        if "data_manifest" not in receipt:
            return
        fields = {"path", "sha256", "available_at_ns", "max_manifest_bytes",
                  "max_files", "max_total_bytes"}
        descriptor = receipt["data_manifest"]
        if not isinstance(descriptor, Mapping) or set(descriptor) != fields:
            raise ValueError("Data manifest requires exactly the explicit descriptor fields")

        def bounded_int(value, name, minimum, maximum):
            if (isinstance(value, bool) or not isinstance(value, int)
                    or not minimum <= value <= maximum):
                raise ValueError(name + " must be an explicit bounded integer")
            return value

        def nonnegative_int(value, name):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(name + " must be an explicit nonnegative integer")
            return value

        def checksum(value, name):
            if (not isinstance(value, str) or len(value) != 64
                    or any(character not in "0123456789abcdef" for character in value)):
                raise ValueError(name + " must be a lowercase SHA-256 digest")
            return value

        path_value = descriptor["path"]
        if (not isinstance(path_value, str) or not path_value
                or not Path(path_value).is_absolute()
                or Path(path_value).name != "manifest.jsonl"):
            raise ValueError("Data manifest path must be an absolute manifest.jsonl path")
        manifest_sha = checksum(descriptor["sha256"], "Data manifest sha256")
        available_at_ns = bounded_int(descriptor["available_at_ns"],
                                      "Data manifest available_at_ns", 1, self.now_ns())
        max_manifest_bytes = bounded_int(descriptor["max_manifest_bytes"],
                                         "Data manifest max_manifest_bytes", 1, 1048576)
        max_files = bounded_int(descriptor["max_files"],
                                "Data manifest max_files", 1, 10000)
        max_total_bytes = bounded_int(descriptor["max_total_bytes"],
                                      "Data manifest max_total_bytes", 1, 1073741824)

        def regular_file(path, name):
            for candidate in (path, *path.parents):
                try:
                    if candidate.is_symlink():
                        raise ValueError(name + " must not traverse a symlink")
                except OSError as exc:
                    raise ValueError(name + " is not readable") from exc
            try:
                info = path.lstat()
            except OSError as exc:
                raise ValueError(name + " must be an existing regular file") from exc
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(name + " must be an existing regular file")
            return info

        manifest_path = Path(path_value)
        manifest_info = regular_file(manifest_path, "Data manifest")
        if manifest_info.st_size > max_manifest_bytes:
            raise ValueError("Data manifest exceeds its explicit byte bound")
        try:
            with manifest_path.open("rb") as handle:
                raw = handle.read(max_manifest_bytes + 1)
        except OSError as exc:
            raise ValueError("Data manifest is not readable") from exc
        if len(raw) > max_manifest_bytes:
            raise ValueError("Data manifest exceeds its explicit byte bound")
        if hashlib.sha256(raw).hexdigest() != manifest_sha:
            raise ValueError("Data manifest checksum differs from its explicit descriptor")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Data manifest is not UTF-8 JSONL") from exc

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Data manifest rows require unique object keys")
                result[key] = value
            return result

        def reject_constant(value):
            raise ValueError("Data manifest rows require finite JSON numbers")

        required = {"relative_path", "sha256", "byte_size", "file_id",
                    "min_ts_ns", "max_ts_ns"}
        rows, file_ids, relative_paths = [], set(), set()
        total_declared = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line, object_pairs_hook=unique_object,
                                 parse_constant=reject_constant)
            except ValueError as exc:
                raise ValueError("Data manifest line is not unique-key JSON") from exc
            if type(row) is not dict or not required <= set(row):
                raise ValueError("Data manifest rows require the existing exporter fields")
            relative = row["relative_path"]
            if not isinstance(relative, str) or not relative:
                raise ValueError("Data manifest relative_path must be a nonempty string")
            relative_path = Path(relative)
            if (relative_path.is_absolute() or ".." in relative_path.parts
                    or not relative_path.parts):
                raise ValueError("Data manifest relative_path must stay relative without traversal")
            file_id = row["file_id"]
            if not isinstance(file_id, str) or not file_id:
                raise ValueError("Data manifest file_id must be a nonempty string")
            checksum(row["sha256"], "Data manifest row sha256")
            byte_size = nonnegative_int(row["byte_size"], "Data manifest row byte_size")
            min_ts_ns = nonnegative_int(row["min_ts_ns"], "Data manifest row min_ts_ns")
            max_ts_ns = nonnegative_int(row["max_ts_ns"], "Data manifest row max_ts_ns")
            if min_ts_ns > max_ts_ns:
                raise ValueError("Data manifest row min_ts_ns exceeds max_ts_ns")
            if max_ts_ns > available_at_ns:
                raise ValueError("Data manifest row max_ts_ns is not yet available")
            if file_id in file_ids or relative_path.as_posix() in relative_paths:
                raise ValueError("Data manifest rows require unique file_id and relative_path")
            file_ids.add(file_id)
            relative_paths.add(relative_path.as_posix())
            total_declared += byte_size
            rows.append(row)
        if not rows:
            raise ValueError("Data manifest must contain at least one row")
        if len(rows) > max_files:
            raise ValueError("Data manifest file count exceeds its explicit bound")
        if total_declared > max_total_bytes:
            raise ValueError("Data manifest total bytes exceed their explicit bound")

        root = manifest_path.parent
        total_read = 0
        for row in rows:
            target = root / row["relative_path"]
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError("Data manifest relative_path escapes its export root") from exc
            info = regular_file(target, "Data manifest referenced object")
            expected_size = row["byte_size"]
            if info.st_size != expected_size:
                raise ValueError("Data manifest referenced object byte size differs")
            if total_read + expected_size > max_total_bytes:
                raise ValueError("Data manifest referenced bytes exceed their explicit bound")
            digest = hashlib.sha256()
            remaining = expected_size
            try:
                with target.open("rb") as handle:
                    while remaining:
                        block = handle.read(min(1024 * 1024, remaining))
                        if not block:
                            raise ValueError("Data manifest referenced object is truncated")
                        digest.update(block)
                        remaining -= len(block)
                    extra = handle.read(1)
            except OSError as exc:
                raise ValueError("Data manifest referenced object is not readable") from exc
            if extra:
                raise ValueError("Data manifest referenced object grew beyond its declared size")
            total_read += expected_size
            if digest.hexdigest() != row["sha256"]:
                raise ValueError("Data manifest referenced object checksum differs")

    def register_configured(self, document, *, action_builder):
        """Load one explicit strategy file and register it through the existing path."""
        if not isinstance(document, Mapping):
            raise ValueError("Strategy configuration document must be an explicit mapping")
        fields = {"job_id", "module", "strategy_version", "config_version", "config",
                  "research_receipt", "paper_permission", "required_feeds", "warmup_bars",
                  "accounts", "risk"}
        if set(document) != fields:
            raise ValueError("Strategy configuration requires exactly the explicit registration fields")
        module_name = document["module"]
        if not isinstance(module_name, str) or not module_name.strip():
            raise ValueError("Strategy module must be a nonempty canonical name")
        config, receipt = document["config"], document["research_receipt"]
        if not isinstance(config, Mapping) or not isinstance(receipt, Mapping):
            raise ValueError("Strategy config and research receipt must be explicit mappings")
        feeds, accounts = document["required_feeds"], document["accounts"]
        if not isinstance(feeds, (list, tuple)) or isinstance(feeds, str):
            raise ValueError("Required feeds must be an explicit list or tuple")
        if not isinstance(accounts, (list, tuple)) or isinstance(accounts, str):
            raise ValueError("Accounts must be an explicit list or tuple")
        if not all(isinstance(value, str) and value for value in (*feeds, *accounts)):
            raise ValueError("Required feeds and accounts must be explicit nonempty strings")
        if not isinstance(document["paper_permission"], bool):
            raise ValueError("PAPER permission must be explicit")
        if len(self._document(document).encode()) > self.budgets.max_config_bytes:
            raise ValueError("Strategy configuration document capacity exceeded")
        risk_document = document["risk"]
        if not isinstance(risk_document, Mapping) or set(risk_document) != set(accounts):
            raise ValueError("Strategy risk requires exact account bindings")
        risk_fields = {"funding_buffer", "account_notional_limit", "strategy_notional_limit",
                       "account_capital_limit", "strategy_capital_limit", "margin_rate"}
        risk = {}
        for account, values in risk_document.items():
            if not isinstance(values, Mapping) or set(values) != risk_fields:
                raise ValueError("Strategy risk requires exact explicit StrategyRisk fields")
            try:
                risk[account] = StrategyRisk(**dict(values))
            except (ArithmeticError, TypeError, ValueError) as exc:
                raise ValueError("Strategy risk values must be explicit finite decimals") from exc
        self._validate_data_manifest(receipt)
        module = (load_strategy_manifest(module_name, self.isolation.allowed_source_roots)
                  if self.isolation is not None else load_strategy(module_name))
        return self.register(StrategyRegistration(
            job_id=document["job_id"], module=module,
            strategy_version=document["strategy_version"],
            config_version=document["config_version"], config=config,
            research_receipt=receipt, paper_permission=document["paper_permission"],
            required_feeds=tuple(feeds), warmup_bars=document["warmup_bars"],
            accounts=tuple(accounts), risk=risk, action_builder=action_builder))

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
        if self.isolation is not None and any(
                job.worker is not None and (job.worker.queued or job.isolated_pending)
                for job in self._jobs.values()):
            raise ValueError("Paper checkpoint capture requires quiescent isolated callbacks")
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

    def _plugin_state(self, job, limit):
        if job.worker is not None:
            if job.isolated_checkpoint_state is None:
                raise ValueError("isolated plugin state requires an awaited child checkpoint")
            value = job.isolated_checkpoint_state
        else:
            hook = getattr(job.instance, "checkpoint_state", None)
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
        plugin_state, plugin_sha = self._plugin_state(job, max_bytes)
        plugin_class = (job.isolated_plugin_class if job.worker is not None else
                        type(job.instance).__module__ + "." + type(job.instance).__qualname__)
        return {"strategy_id": sid, "job_id": job.registration.job_id,
            "plugin_class": plugin_class,
            "strategy_version": job.registration.strategy_version,
            "config_version": job.registration.config_version,
            "fingerprint": job.fingerprint, "plugin_sha256": plugin_sha,
            "plugin_state": plugin_state,
            **({"feedback_cursors": dict(job.feedback_cursors)} if job.feedback_cursors is not None else {}),
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
        if self.isolation is not None and any(
                job.worker is not None and (job.worker.queued or job.isolated_pending)
                for job in self._jobs.values()):
            raise ValueError("Paper checkpoint capture requires quiescent isolated callbacks")
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

    async def prepare_checkpoint_capture(self):
        """Await bounded child checkpoints before the synchronous capture seam.

        No untrusted plugin hook is called in the parent. Cooperative runtimes
        return immediately and keep their existing synchronous behavior.
        """
        if self.isolation is None:
            return None
        if self._processing or self._pending:
            raise ValueError("isolated checkpoint capture requires no callback/pending action")
        for sid, job in self._jobs.items():
            if job.worker is None:
                continue
            if not job.isolated_initialized:
                request = job.isolated_init_request
                if request is None:
                    raise ValueError("isolated checkpoint capture requires child init proof")
                payload = await self._await_worker(job.worker, request)
                job.isolated_pending.pop(request["request_id"], None)
                self._accept_isolated_init(job, payload)
            if job.isolated_pending or job.worker.inflight:
                raise ValueError("isolated checkpoint capture requires quiescent strategies")
            if not job.worker.alive:
                raise ValueError("isolated checkpoint capture requires a live child")
            request = job.worker.submit_checkpoint()
            payload = await self._await_worker(job.worker, request)
            state = payload.get("state")
            sha = payload.get("sha256")
            detached = self._json_state(state, self.budgets.max_action_bytes * 16)
            encoded = self._document(detached).encode()
            if len(encoded) > self.isolation.budgets.max_state_bytes:
                raise ValueError("isolated checkpoint state exceeds its byte bound")
            if hashlib.sha256(encoded).hexdigest() != sha:
                raise ValueError("isolated child checkpoint checksum mismatch")
            job.isolated_checkpoint_state = detached
            job.isolated_checkpoint_sha256 = sha
        return None

    async def prepare_checkpoint_restore(self, state):
        """Restart children and verify their restored state before publication."""
        if self.isolation is None:
            return None
        if self._processing or self._pending:
            raise ValueError("isolated checkpoint restore requires no callback/pending action")
        if type(state) is not dict or type(state.get("jobs")) is not list:
            raise ValueError("isolated checkpoint restore state schema mismatch")
        snapshots = {item.get("strategy_id"): item
                     for item in state["jobs"] if type(item) is dict}
        for sid, job in self._jobs.items():
            if job.worker is None:
                continue
            if not job.isolated_initialized:
                init_request = job.isolated_init_request
                if init_request is None:
                    raise ValueError("isolated restore requires child init proof")
                init_payload = await self._await_worker(job.worker, init_request)
                job.isolated_pending.pop(init_request["request_id"], None)
                self._accept_isolated_init(job, init_payload)
            snap = snapshots.get(sid)
            if snap is None:
                raise ValueError("isolated checkpoint job inventory differs from registration")
            if (snap.get("job_id") != job.registration.job_id
                    or snap.get("strategy_version") != job.registration.strategy_version
                    or snap.get("config_version") != job.registration.config_version
                    or snap.get("fingerprint") != job.fingerprint
                    or snap.get("plugin_class") != job.isolated_plugin_class):
                raise ValueError("isolated checkpoint job identity differs from registration")
            plugin_state = self._json_state(snap.get("plugin_state"),
                                            self.isolation.budgets.max_state_bytes)
            plugin_sha = snap.get("plugin_sha256")
            encoded = self._document(plugin_state).encode()
            if hashlib.sha256(encoded).hexdigest() != plugin_sha:
                raise ValueError("isolated checkpoint plugin checksum mismatch")
            if len(encoded) > self.isolation.budgets.max_state_bytes:
                raise ValueError("isolated checkpoint state exceeds its byte bound")
            if job.isolated_pending:
                raise ValueError("isolated checkpoint restore requires quiescent strategies")
            job.isolated_generation += 1
            request = job.worker.restart(epoch=job.isolated_generation)
            job.isolated_pending = {}
            try:
                init_payload = await self._await_worker(job.worker, request)
                self._accept_isolated_init(job, init_payload)
                request = job.worker.submit_restore(state=plugin_state, sha256=plugin_sha)
                restored = await self._await_worker(job.worker, request)
            except BaseException:
                job.worker.kill()
                raise
            if (restored.get("state") != plugin_state
                    or restored.get("sha256") != plugin_sha):
                job.worker.kill()
                raise ValueError("isolated child restore roundtrip differs from checkpoint")
            job.isolated_checkpoint_state = deepcopy(plugin_state)
            job.isolated_checkpoint_sha256 = plugin_sha
            job.isolated_restore_prepared = {
                "plugin_class": job.isolated_plugin_class,
                "state": deepcopy(plugin_state), "sha256": plugin_sha}
        return None

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
            if job.worker is not None:
                # The child restore is prepared by the async checkpoint seam.
                # Never call create/configure/restore_state in the parent.
                prepared = job.isolated_restore_prepared
                if prepared is None:
                    raise ValueError("isolated checkpoint restore requires awaited child preparation")
                if (prepared.get("plugin_class") != snap.get("plugin_class")
                        or prepared.get("state") != plugin_state
                        or prepared.get("sha256") != plugin_sha):
                    raise ValueError("isolated child restore identity differs from checkpoint")
                job.isolated_checkpoint_state = deepcopy(plugin_state)
                job.isolated_checkpoint_sha256 = plugin_sha
                instance = None
            else:
                instance = job.registration.module.create()
                if (type(instance).__module__ + "." + type(instance).__qualname__) != snap.get("plugin_class"):
                    raise ValueError("Paper checkpoint plugin module/class identity differs")
                # Native context/config is re-established before the plugin state
                # and factual execution facts are restored.
                self._configure_instance(instance, job.registration.config)
                self._validate_instance_registration(instance, self._registration_facts(job.registration))
                hook = getattr(instance, "restore_state", None)
                if not callable(hook) or inspect.iscoroutinefunction(hook):
                    raise ValueError("strategy plugin requires a synchronous restore_state hook")
                result = hook(deepcopy(plugin_state))
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise ValueError("strategy restore_state must be synchronous")
            # Validate the staged instance itself. The prior implementation
            # accidentally checkpointed job.instance (the old live object),
            # which made an unrelated old state appear to prove restore.
            if job.worker is not None:
                check, check_sha = self._json_state(plugin_state, max_bytes), plugin_sha
            else:
                hook = getattr(instance, "checkpoint_state", None)
                if not callable(hook) or inspect.iscoroutinefunction(hook):
                    raise ValueError("strategy plugin requires a synchronous checkpoint_state hook")
                check = self._json_state(hook(), max_bytes)
                check_sha = hashlib.sha256(self._document(check).encode()).hexdigest()
            if check != plugin_state or check_sha != plugin_sha:
                raise ValueError("strategy restore_state roundtrip differs from checkpoint")
            lifecycle = snap.get("lifecycle")
            if (type(lifecycle) is not dict or set(lifecycle) != {
                    "state", "reason", "callbacks", "actions", "permission_blocked",
                    "restart_blocked"}):
                raise ValueError("Paper checkpoint lifecycle schema mismatch")
            cursors = snap.get("feedback_cursors", {})
            expected_accounts = set(job.feedback_cursors or {})
            if (type(cursors) is not dict or set(cursors) != expected_accounts
                    or any(type(value) is not int or value < 0 for value in cursors.values())):
                raise ValueError("Paper checkpoint feedback cursor schema mismatch")
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
                "feedback_cursors": dict(cursors),
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
            job.feedback_cursors = values["feedback_cursors"] or None
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
        event, reference_key = result.event, result.event.instrument.key
        if (self._deferred_input_gaps and (not result.applied
                or not self._fresh_input(result.event, snapshot.now_ns))):
            return
        # Matcher callbacks advance every order in the physical account, not
        # only this event's instrument. Before any such clock movement, fence
        # stale committed depth from the actual durable matcher books. The
        # post-apply snapshot contains the arriving book and cannot attest the
        # old depth that an account-wide advance would otherwise consume.
        for account, matcher in self.accounts.items():
            if account in self._account_faults:
                continue
            try:
                state = matcher.ledger.paper_state() or {}
                books = state.get("books") if isinstance(state, dict) else None
                orders = state.get("orders") if isinstance(state, dict) else None
                books = books if isinstance(books, dict) else {}
                orders = orders if isinstance(orders, dict) else {}
                for key, bound_spec in self.products.items():
                    if matcher.ledger.scope.venue != bound_spec.instrument.venue.value:
                        continue
                    feed = self.reference_feeds.get(key, key)
                    cached = books.get(key)
                    if isinstance(cached, dict):
                        received = cached.get("received_ns")
                        source_ts = cached.get("source_ns")
                        stale = (isinstance(received, bool) or not isinstance(received, int)
                                 or not 0 <= snapshot.now_ns-received <= self.budgets.max_feed_age_ns
                                 or (source_ts is not None and (
                                     isinstance(source_ts, bool) or not isinstance(source_ts, int)
                                     or not 0 <= snapshot.now_ns-source_ts <= self.budgets.max_feed_age_ns)))
                        if stale:
                            self._fence_feed(key, feed, matcher, "cached_book_not_fresh")
                    elif any(isinstance(order, dict) and order.get("instrument") == key
                             for order in orders.values()):
                        # A missing book is actionable only where committed
                        # risk still depends on it; otherwise a first book is
                        # allowed to establish its generation normally.
                        self._fence_feed(key, feed, matcher, "cached_book_missing")
            except Exception as exc:
                self._account_faults[account] = type(exc).__name__
        if self._deferred_input_gaps:
            # Recovery did not establish a continuous matching input cursor.
            # Fence from the last committed clock before advancing any account
            # using a new live input; cached depth cannot fill the missing span.
            self._gap_matchers(self._deferred_input_gaps)
            self._deferred_input_gaps.clear()
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
                        self._fence_feed(key, reference_key, matcher,
                                         "receipt_or_native_source_not_fresh")
                        matcher.advance(snapshot.now_ns)
                    elif not result.applied or snapshot.quality.get(reference_key) != "VALID":
                        self._fence_feed(key, reference_key, matcher, result.reason)
                        matcher.advance(snapshot.now_ns)
                    elif isinstance(event, OrderBookEvent):
                        projected = snapshot.latest_book(reference_key)
                        if projected is None:
                            self._fence_feed(key, reference_key, matcher, "projected_book_missing")
                            matcher.advance(snapshot.now_ns)
                        elif matcher.on_book(mapped(projected)):
                            # The native matcher accepted the fresh full book
                            # (including any explicit new generation) after the
                            # preflight fence; only now may that feed reopen.
                            self._invalid_inputs.pop(reference_key, None)
                    elif isinstance(event, TradeEvent):
                        # A fresh trade is only new evidence about the trade.
                        # It cannot make the reference book that the matcher
                        # would consume for arrival/fill any fresher, so reject
                        # stale cached depth before on_trade moves the clock.
                        reference_book = snapshot.latest_book(reference_key)
                        if reference_book is None or not self._fresh_input(reference_book, snapshot.now_ns):
                            self._fence_feed(key, reference_key, matcher, "reference_book_not_fresh")
                            matcher.advance(snapshot.now_ns)
                        else:
                            matcher.on_trade(mapped(event))
                except Exception as exc:
                    self._account_faults[account] = type(exc).__name__
                    try:
                        self._fence_feed(key, reference_key, matcher, "market_input_rejected")
                        matcher.advance(snapshot.now_ns)
                    except Exception:
                        pass

    def _fence_feed(self, product_key, feed_key, matcher, reason):
        """Invalidate one exact feed and fence from its last durable clock."""
        bounded = reason if isinstance(reason, str) and reason else "market_input_invalid"
        bounded = bounded[:1024]
        self._invalid_inputs[feed_key] = bounded[:256]
        for job in self._jobs.values():
            if (feed_key in job.registration.required_feeds
                    and job.state not in {"PAUSED", "HALTED", "FAULTED", "BLOCKED"}):
                job.state, job.reason = "WARMING", "input_invalid"
        matcher.on_gap(product_key, matcher.report()["clock_ns"], bounded)

    def _fresh_input(self, event, now):
        return (event.ingest_mode == "realtime" and event.receive_ts_ns is not None and
                0 <= now-event.receive_ts_ns <= self.budgets.max_feed_age_ns and
                (event.source_ts_ns is None or
                 0 <= now-event.source_ts_ns <= self.budgets.max_feed_age_ns))

    def _ledger_facts(self, accounts=None, *, strategy=None):
        """Bounded factual execution snapshot from a job's already bound ledgers."""
        facts = {}
        selected = tuple(sorted(self.accounts)) if accounts is None else tuple(accounts)
        for account in selected:
            matcher = self.accounts[account]
            report = matcher.ledger.report()
            active = []
            for row in matcher.ledger.active_orders():
                if strategy is not None and row["strategy"] != strategy:
                    continue
                active.append({
                    "intent_id": row["intent_id"], "strategy": row["strategy"],
                    "instrument": row["instrument"], "side": row["side"],
                    "state": row["state"], "quantity": str(row["quantity"]),
                    "filled": str(row["filled"]),
                    **({"remaining": str(Decimal(row["quantity"])-Decimal(row["filled"]))}
                       if strategy is not None else {}),
                    "reservation": str(row["reservation"]),
                    "expiry_ns": row["expiry_ns"],
                })
            orders = []
            for row in report.get("orders", []):
                if strategy is not None and row["strategy"] != strategy:
                    continue
                try:
                    payload = json.loads(row["payload"])
                except (TypeError, ValueError) as exc:
                    raise ValueError("Portfolio order payload is malformed") from exc
                instrument = str(row["instrument"])
                intent_instrument = payload.get("instrument") if isinstance(payload, dict) else None
                raw_symbol = intent_instrument.get("raw_symbol") if isinstance(intent_instrument, dict) else None
                metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
                if (not isinstance(raw_symbol, str) or not raw_symbol
                        or not isinstance(metadata, dict)):
                    raise ValueError("Portfolio order instrument/metadata facts are malformed")
                created = payload.get("created_ts_ns") if isinstance(payload, dict) else None
                if isinstance(created, bool) or not isinstance(created, int) or created < 0:
                    raise ValueError("Portfolio order creation time is malformed")
                orders.append({
                    "intent_id": str(row["intent_id"]), "strategy": row["strategy"],
                    "instrument": instrument, "raw_symbol": raw_symbol, "side": row["side"],
                    "state": row["state"], "quantity": str(row["quantity"]),
                    "filled": str(row["filled"]),
                    **({"remaining": str(Decimal(row["quantity"])-Decimal(row["filled"]))}
                       if strategy is not None else {}),
                    "reservation": str(row["reservation"]),
                    "expiry_ns": row["expiry_ns"], "created_ns": created,
                    "metadata": metadata,
                })
            orders = sorted(orders, key=lambda row: (row["created_ns"], row["intent_id"]))
            facts[account] = {
                "scope": report["scope"], "mode": report["mode"],
                "account_version": report["account_version"],
                "balances": {key: str(value) for key, value in report["balances"].items()},
                "reserved": {key: str(value) for key, value in report["reserved"].items()},
                **({"available": {key: str(Decimal(value)-Decimal(report["reserved"].get(key, 0)))
                                   for key, value in report["balances"].items()}}
                   if strategy is not None else {}),
                "simulated_margin": report["simulated_margin"],
                "simulated_capital": report["simulated_capital"],
                "physical_positions": {key: str(value) for key, value in report["physical_positions"].items()},
                "virtual_positions": {
                    owner: {key: str(value) for key, value in positions.items()}
                    for owner, positions in report["virtual_positions"].items()
                    if strategy is None or owner == strategy},
                "strategy_cash": [
                    {key: (str(value) if isinstance(value, Decimal) else value)
                     for key, value in row.items()} for row in report["strategy_cash"]
                    if strategy is None or row.get("strategy") == strategy],
                "active_orders": active,
                "orders": orders,
                "readiness": dict(report["readiness"]),
            }
        return facts

    @staticmethod
    def _event_proof(event):
        def plain(value):
            if isinstance(value, BaseMarketEvent):
                return plain(value.model_dump(mode="python"))
            if isinstance(value, Mapping):
                return {str(key): plain(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [plain(item) for item in value]
            if isinstance(value, Decimal):
                return str(value)
            return value
        return stable_hash([plain(event)])

    def _claim(self, job, event, snapshot):
        registration = job.registration
        proof = {"registration": job.fingerprint, "event": self._event_proof(event),
                 "strategy_version": registration.strategy_version}
        # A partial cross-account claim also suppresses replay; omitted actions
        # require explicit operator review rather than an automatic risk retry.
        claims = [self.accounts[account].ledger.claim_strategy_event(registration.module.strategy_id,
                  registration.config_version, event.event_id, proof) for account in registration.accounts]
        return all(claims)

    def _isolated_reject(self, job, reason, now_ns):
        """Fence one strategy only; never stop other feeds or strategies."""
        job.state, job.reason = "FAULTED", str(reason)[:256] or "isolated_strategy_fault"
        for account in job.registration.accounts:
            observe_strategy(job.registration.module.strategy_id,
                             self.accounts[account].ledger.scope.key,"PAPER","FAULTED")
        job.isolated_pending = {} if job.worker is not None else None
        if job.worker is not None:
            job.isolated_initialized = False
            job.isolated_init_request = None
            job.isolated_restart_requested = False
            job.worker.kill()
        self._cancel_owned(job, now_ns)

    def _accept_isolated_init(self, job, payload):
        plugin_class = payload.get("plugin_class")
        if type(plugin_class) is not str or not plugin_class or len(plugin_class) > 512:
            raise ValueError("isolated child plugin class identity is invalid")
        has_feedback = payload.get("has_feedback")
        if type(has_feedback) is not bool or has_feedback != (job.feedback_cursors is not None):
            raise ValueError("isolated child feedback capability differs from static manifest")
        job.isolated_plugin_class = plugin_class
        job.isolated_initialized = True
        job.isolated_init_request = None
        return True

    def _handle_isolated_response(self, job, response, observed_ns):
        from .strategy_process import ProtocolError, WorkerUnavailable, result_from_document
        pending_by_id = job.isolated_pending
        if not isinstance(pending_by_id, dict):
            return ()
        pending = pending_by_id.get(response.get("request_id"))
        if pending is None:
            # Late/duplicate/out-of-order responses mutate nothing.
            return ()
        try:
            payload = job.worker.accept_response(response, now_ns=observed_ns)
        except (ProtocolError, WorkerUnavailable, TimeoutError, RuntimeError, ValueError) as exc:
            pending_by_id.pop(response.get("request_id"), None)
            self._isolated_reject(job, type(exc).__name__, self.now_ns())
            return ()
        pending_by_id.pop(response.get("request_id"), None)
        if pending["kind"] == "init":
            try:
                self._accept_isolated_init(job, payload)
            except (ValueError, TypeError) as exc:
                self._isolated_reject(job, "IsolatedInitProofFailed", self.now_ns())
                return ()
            return ()
        # Pause/halt/restart/generation/feed fences invalidate queued work even
        # when a correct child reply becomes readable later.
        if (job.state in {"PAUSED", "HALTED", "FAULTED", "BLOCKED"}
                or self._recovery_gate()):
            return ()
        if pending["kind"] == "event" and (job.state == "WARMING" or (
                not pending["replay"] and not self._ready(job, pending["snapshot"]))):
            return ()
        if pending["generation"] != job.isolated_generation:
            return ()
        if (pending["cursor"] != pending["snapshot"].cursor
                or pending["snapshot"].engine_incarnation != self.hot_path.engine_incarnation):
            return ()
        current = self.hot_path.snapshot(self.now_ns(), self.phase.upper())
        for key in job.registration.required_feeds:
            if (pending["snapshot"].versions.get(key) != current.versions.get(key)
                    or pending["snapshot"].book_versions.get(key) != current.book_versions.get(key)
                    or pending["snapshot"].quality.get(key) != current.quality.get(key)):
                return ()
            if (job.registration.warmup_bars and
                    pending["snapshot"].history_quality.get(key) != current.history_quality.get(key)):
                return ()
        try:
            result = result_from_document(payload)
        except ProtocolError as exc:
            self._isolated_reject(job, "MalformedIsolatedResult", self.now_ns())
            return ()
        job.callbacks += 1
        if pending["kind"] == "event":
            return self._consume_callback_result(job, result, pending["view"],
                pending["snapshot"], replay=pending["replay"],
                started=self.callback_clock_ns(), causal_time=pending["causal_time"])
        view = pending["view"]
        return self._consume_callback_result(job, result, view, pending["snapshot"],
            replay=False, started=self.callback_clock_ns(),
            causal_time=pending["snapshot"].now_ns)

    def poll_isolated(self, *, max_frames=8):
        """Bounded nonblocking parent poll/watchdog for isolated callbacks."""
        if self.isolation is None:
            return ()
        pool = self._isolation_pool()
        generated = []
        for sid, job in list(self._jobs.items()):
            worker = job.worker
            if worker is None:
                continue
            worker.reap()
            if job.isolated_restart_requested:
                if worker.process is not None:
                    continue
                try:
                    request = worker.start(epoch=job.isolated_generation)
                except BaseException:
                    self._isolated_reject(job, "IsolatedRestartFailed", self.now_ns())
                    continue
                job.isolated_restart_requested = False
                job.isolated_init_request = request
                job.isolated_pending = {request["request_id"]: {
                    "kind": "init", "generation": job.isolated_generation,
                    "request": request}}
            safety_now = time.monotonic_ns()
            market_now = self.now_ns()
            if worker.pending_expired(safety_now):
                self._isolated_reject(job, "StrategyTimeout", market_now)
                continue
            if not worker.alive:
                if job.isolated_pending:
                    self._isolated_reject(job, job.worker.fault or "WorkerUnavailable", market_now)
                continue
            try:
                responses = worker.poll(max_frames=max_frames, now_ns=safety_now)
            except BaseException:
                self._isolated_reject(job, "WorkerPollFailed", market_now)
                continue
            for response, observed in responses:
                try:
                    generated.extend(self._handle_isolated_response(job, response, observed))
                except BaseException as exc:
                    self._isolated_reject(job, type(exc).__name__, self.now_ns())
        if pool.total_rss_bytes() > self.isolation.budgets.max_total_rss_bytes:
            victims = sorted((item for item in self._jobs.values() if item.worker is not None),
                             key=lambda item: item.worker.rss_bytes() or 0, reverse=True)
            if victims:
                self._isolated_reject(victims[0], "IsolatedRssCapacityExceeded", self.now_ns())
        return tuple(generated)

    def watchdog(self):
        """Advance deadlines independently of incoming market events."""
        return self.poll_isolated()

    def _dispatch(self, result, snapshot, phase):
        self.view = StrategyDataView(snapshot, ready=False, phase=phase)
        if phase in {"INVALID", "WARMING"}:
            return ()
        replay = phase == "REPLAY"
        self._market(result, snapshot, phase)
        if not replay:
            self._drain_feedback(snapshot, phase)
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
                for account in job.registration.accounts:
                    observe_strategy(job.registration.module.strategy_id,
                                     self.accounts[account].ledger.scope.key,"PAPER","WARMING")
                continue
            job.state = "REPLAY" if replay else "PAPER"
            for account in job.registration.accounts:
                observe_strategy(job.registration.module.strategy_id,
                                 self.accounts[account].ledger.scope.key,"PAPER",job.state)
            job.reason = None
            # Refresh per job so actions from an earlier job are factual for
            # the next callback, while no other job's account is exposed.
            execution_facts = self._ledger_facts(job.registration.accounts)
            view = StrategyDataView(snapshot, ready=True, phase=job.state,
                                    execution=execution_facts)
            self.view = view
            try:
                if not replay and not self._claim(job, event, snapshot):
                    continue
                causal_time = (event.source_ts_ns if event.source_ts_ns is not None
                               else event.receive_ts_ns)
                if job.worker is not None:
                    request = job.worker.submit_event(event=event, snapshot=snapshot,
                        execution=execution_facts, ready=True, phase=job.state,
                        cursor=snapshot.cursor)
                    job.isolated_pending[request["request_id"]] = {
                        "kind": "event", "generation": job.isolated_generation,
                        "cursor": snapshot.cursor, "snapshot": snapshot, "view": view,
                        "replay": replay, "causal_time": causal_time,
                        "request_ns": self.now_ns()}
                    continue
                started = self.callback_clock_ns()
                callback_started=time.monotonic_ns()
                callback_failed=False
                try:
                    signals = job.instance.on_event(event, view)
                except BaseException:
                    callback_failed=True
                    raise
                finally:
                    for account in job.registration.accounts:
                        observe("strategy_callback",started_ns=callback_started,ended_ns=time.monotonic_ns(),
                                strategy_id=job.registration.module.strategy_id,
                                account_scope=self.accounts[account].ledger.scope.key,mode="PAPER",
                                error=callback_failed)
                job.callbacks += 1
                if self.callback_clock_ns()-started > self.budgets.max_callback_ns:
                    raise TimeoutError("Cooperative strategy callback exceeded its return-time budget")
                generated.extend(self._consume_callback_result(job, signals, view, snapshot,
                    replay=replay, started=started, causal_time=causal_time))
            except Exception as exc:
                job.state, job.reason = "FAULTED", type(exc).__name__
                for account in job.registration.accounts:
                    observe_strategy(job.registration.module.strategy_id,
                                     self.accounts[account].ledger.scope.key,"PAPER",job.state)
                if job.worker is not None:
                    job.worker.kill()
                if not replay:
                    self._cancel_owned(job, snapshot.now_ns)
        if self.isolation is not None:
            generated.extend(self.poll_isolated())
        return tuple(generated)

    def _consume_callback_result(self, job, result, view, snapshot, *, replay, started, causal_time=None):
        sid = job.registration.module.strategy_id
        protocol = action_protocol_version(job.registration.module.module)
        pending = []
        if isinstance(result, StrategyActionPlan):
            if protocol != 1:
                raise ValueError("StrategyActionPlan requires ACTION_PROTOCOL_VERSION=1")
            if replay:
                return ()
            if (result.strategy_id != sid or result.config_version != job.registration.config_version
                    or result.event_ts_ns > snapshot.now_ns
                    or (causal_time is not None and result.event_ts_ns < causal_time)):
                raise ValueError("Action plan source/config/time differs from this callback")
            if len(result.actions) > self.budgets.max_actions_per_callback:
                raise ValueError("Strategy action plan exceeds action count capacity")
            for action in result.actions:
                pending.append((action, result, self._validate_plan_action(job, action, result,
                    snapshot.now_ns, view)))
        else:
            if protocol == 1 and not isinstance(result, list):
                raise ValueError("Opted-in on_event must return list[Signal] or StrategyActionPlan")
            if not isinstance(result, list) or len(result) > self.budgets.max_signals_per_callback:
                raise ValueError("Strategy signal count/type exceeds callback contract")
            for signal in result:
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
                for action in actions:
                    pending.append((action, signal, self._validate(job, action, signal,
                                                                    snapshot.now_ns, view)))
        if self.callback_clock_ns()-started > self.budgets.max_callback_ns:
            raise TimeoutError("Cooperative strategy callback/action planning exceeded its return-time budget")
        if replay:
            return ()
        if len(self._document([document for _, _, document in pending]).encode()) > self.budgets.max_action_bytes:
            raise ValueError("Callback action batch byte capacity exceeded")
        if self._pending+len(pending) > self.budgets.max_pending_actions:
            raise ValueError("Pending strategy action capacity exceeded")
        result_ids = []
        self._pending += len(pending)
        try:
            for action, source, document in pending:
                if self._execute(job, action, source, document, snapshot):
                    result_ids.append(action.action_id)
                    job.actions += 1
                    self._drain_feedback(snapshot, "PAPER")
        finally:
            self._pending -= len(pending)
        return tuple(result_ids)

    def _validate_plan_action(self, job, action, plan, now, view):
        registration = job.registration
        if not isinstance(action, ExecutionAction):
            raise ValueError("Action plan entries must be ExecutionAction")
        if (action.mode != TradingMode.PAPER or action.strategy_id != registration.module.strategy_id
                or action.config_version != registration.config_version
                or action.scope.key not in registration.accounts):
            raise ValueError("Action mode/source/config/account permission mismatch")
        if not now < action.expiry_ns:
            raise ValueError("Plan action is expired")
        if action.action == ActionKind.CANCEL:
            target = self.accounts[action.scope.key].ledger.get(action.target_order_id)
            if target is None or target["strategy"] != registration.module.strategy_id:
                raise ValueError("Cancel target is not owned by this strategy")
            # Earlier feedback rows can be delivered after a later durable
            # terminal fact. A same-owner cancel then remains a safe no-op.
        if action.action in {ActionKind.NEW, ActionKind.REPLACE}:
            if (not view.ready or view.phase not in {"PAPER", "LIVE"} or self._recovery_gate()
                    or self._input_phase not in {"LIVE", "PAPER"}
                    or not self._ready(job, view.hot_path)):
                raise ValueError("New plan risk requires a ready, fresh PAPER market/account context")
            if (action.intent.job_id != registration.job_id
                    or action.intent.instrument.key not in self.products
                    or self.reference_feeds.get(action.intent.instrument.key, action.intent.instrument.key)
                    not in registration.required_feeds):
                raise ValueError("Plan intent job/product/feed binding differs from registration")
            intent = action.intent
            if intent.instrument != self.products[intent.instrument.key].instrument:
                raise ValueError("Plan intent instrument differs from its declared execution product")
            for value, name in ((intent.intent_id, "intent_id"),
                    (intent.client_order_id, "client_order_id"), (intent.job_id, "job_id"),
                    (intent.signal_id, "intent.signal_id")):
                bounded_identity(value, name)
            if self._has_unresolved_account_orders(action.scope.key):
                raise ValueError("New risk is blocked by unresolved account order ownership")
            created = intent.created_ts_ns
            if isinstance(created, bool) or not isinstance(created, int) or not plan.event_ts_ns <= created <= now:
                raise ValueError("Plan intent creation timestamp is not causally bounded")
            if action.action == ActionKind.REPLACE:
                self._validate_replacement(job, action)
        document = {"action_id": action.action_id, "action": action.action.value,
            "scope": action.scope.key, "strategy": action.strategy_id,
            "config_version": action.config_version, "expiry_ns": action.expiry_ns,
            "intent": action.intent.model_dump(mode="json") if action.intent else None,
            "target": action.target_order_id, "post_only": action.post_only,
            "reduce_only": action.reduce_only, "time_in_force": action.time_in_force,
            "group_id": action.group_id, "plan_id": plan.plan_id,
            "plan_event_ts_ns": plan.event_ts_ns}
        if len(self._document(document).encode()) > self.budgets.max_action_bytes:
            raise ValueError("Action document capacity exceeded")
        return document

    def _has_unresolved_account_orders(self, account_key):
        unresolved = {"UNKNOWN", "LEGACY_UNKNOWN", "HANDOFF", "SUBMITTED"}
        matcher = self.accounts[account_key]
        return any(row["state"] in unresolved for row in matcher.ledger.active_orders())

    def _validate_replacement(self, job, action):
        if action.scope.key not in job.registration.accounts or action.intent is None:
            raise ValueError("Replacement must remain within this strategy's bound account")
        ledger = self.accounts[action.scope.key].ledger
        target = ledger.get(action.target_order_id)
        if target is None or target["strategy"] != job.registration.module.strategy_id:
            raise ValueError("Replacement target is not owned by this strategy")
        if target["state"] != "CANCELED":
            raise ValueError("Replacement requires confirmed CANCELED target")
        old = json.loads(target["payload"])
        old_intent = old.get("instrument")
        intent = action.intent
        if (target["instrument"] != intent.instrument.key or target["side"] != intent.side.value
                or old_intent != intent.instrument.model_dump(mode="json")):
            raise ValueError("Replacement must retain the target instrument identity and side")
        remaining = Decimal(target["quantity"]) - Decimal(target["filled"])
        if intent.quantity is None or intent.quantity > remaining:
            raise ValueError("Replacement quantity exceeds factual remaining quantity")

    def _validate(self, job, action, signal, now, view=None):
        registration = job.registration
        if not isinstance(action, ExecutionAction):
            raise ValueError("Action builder must return list[ExecutionAction]")
        if (action.mode != TradingMode.PAPER or action.strategy_id != registration.module.strategy_id or
                action.config_version != registration.config_version or action.scope.key not in registration.accounts):
            raise ValueError("Action mode/strategy/config/account permission mismatch")
        if (action.action == ActionKind.NEW
                and (isinstance(now, bool) or not isinstance(now, int) or now < 0)):
            raise ValueError("NEW validation requires a nonnegative integer nanosecond boundary")
        if (action.action == ActionKind.NEW and view is not None
                and (not view.ready or view.phase not in {"PAPER", "LIVE"}
                     or self._recovery_gate() or self._input_phase not in {"LIVE", "PAPER"}
                     or not self._ready(job, view.hot_path))):
            raise ValueError("Signal action NEW requires a ready, fresh PAPER market/account context")
        if (action.action == ActionKind.NEW
                and action_protocol_version(registration.module.module) == 1
                and self._has_unresolved_account_orders(action.scope.key)):
            raise ValueError("Opted-in signal action NEW is blocked by unresolved account order ownership")
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
            intent = action.intent
            reference_key = self.reference_feeds.get(intent.instrument.key, intent.instrument.key)
            if intent.instrument != self.products[intent.instrument.key].instrument:
                raise ValueError("Intent instrument differs from its declared execution product")
            if signal.instrument != self.known[reference_key]:
                raise ValueError("Signal instrument differs from its declared reference feed")
            for value, name in ((intent.intent_id, "intent_id"),
                    (intent.client_order_id, "client_order_id"), (intent.job_id, "job_id"),
                    (intent.signal_id, "signal_id"), (signal.signal_id, "signal.signal_id")):
                bounded_identity(value, name)
            created = intent.created_ts_ns
            if (isinstance(created, bool) or not isinstance(created, int) or created < 0
                    or not signal.event_ts_ns <= created <= now):
                raise ValueError("Intent creation time is not a causally bounded nanosecond timestamp")
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
        if action.action == ActionKind.REPLACE:
            self._validate_replacement(job, action)
        plan = isinstance(signal, StrategyActionPlan)
        local_rejection = None
        if plan:
            if action.time_in_force == "FOK":
                local_rejection = "paper_fok_unsupported"
            elif action.post_only and action.time_in_force == "IOC":
                local_rejection = "paper_post_only_ioc_unsupported"
            elif (action.intent.limit_price is None
                    and not matcher.capabilities.get("allow_market_orders", False)):
                local_rejection = "paper_market_order_unsupported"
            elif (not action.post_only and action.time_in_force == "GTC"
                    and not matcher.capabilities.get("allow_taker_gtc", False)):
                local_rejection = "paper_taker_gtc_unsupported"
        if local_rejection is not None:
            matcher.ledger.record_order_rejection(action.intent.intent_id, action.strategy_id,
                local_rejection, intent=action.intent,
                evidence={"provenance": "LOCAL_NOT_SENT", "action_id": action.action_id},
                now_ns=snapshot.now_ns)
            return True
        risk = job.registration.risk[action.scope.key]
        reference_price = (self._current_reference_price(action.intent.instrument.key, snapshot)
                           if plan else signal.reference_price)
        intent = action.intent
        if action.action == ActionKind.REPLACE:
            intent = intent.model_copy(update={"metadata": {
                **intent.metadata, "replacement_of": action.target_order_id,
                "replacement_provenance": "confirmed_canceled"}})
        created, _ = matcher.submit(intent, action.strategy_id,
            {"registration": job.fingerprint,
             "source_event_id": None if plan else signal.source_event_id,
             "signal_id": None if plan else signal.signal_id,
             "plan_id": signal.plan_id if plan else None,
             "replacement_of": action.target_order_id if action.action == ActionKind.REPLACE else None,
             "action_id": action.action_id,
             "reference_feed": self.reference_feeds.get(action.intent.instrument.key, action.intent.instrument.key),
             "cursor": snapshot.cursor, "input_versions": dict(snapshot.versions)},
            action.expiry_ns, self.products[action.intent.instrument.key], snapshot.now_ns,
            reference_price=reference_price, margin_rate=risk.margin_rate,
            funding_buffer=risk.funding_buffer, account_notional_limit=risk.account_notional_limit,
            strategy_notional_limit=risk.strategy_notional_limit, account_capital_limit=risk.account_capital_limit,
            strategy_capital_limit=risk.strategy_capital_limit, post_only=action.post_only,
            reduce_only=action.reduce_only, time_in_force=action.time_in_force, group_id=action.group_id)
        return created

    def _current_reference_price(self, product_key, snapshot):
        feed = self.reference_feeds.get(product_key, product_key)
        book = snapshot.latest_book(feed)
        if book is None or not book.bids or not book.asks:
            raise ValueError("Plan requires a current two-sided reference book")
        bid = max(level.price for level in book.bids)
        ask = min(level.price for level in book.asks)
        if bid > ask:
            raise ValueError("Plan reference book is crossed")
        return (bid + ask) / Decimal(2)

    def _drain_feedback(self, snapshot, phase):
        if self._draining_feedback or phase == "REPLAY":
            return
        self._draining_feedback = True
        remaining_budget = 64 * self.budgets.max_jobs
        try:
            for sid, job in self._jobs.items():
                job_budget = 64
                cursors = job.feedback_cursors
                if (cursors is None or job.permission_blocked or job.state in
                        {"PAUSED", "HALTED", "FAULTED", "BLOCKED"}):
                    continue
                hook = None if job.worker is not None else getattr(job.instance, "on_order_event", None)
                if job.worker is None and not callable(hook):
                    job.state, job.reason = "FAULTED", "MissingOrderEventHook"
                    continue
                for account in job.registration.accounts:
                    if remaining_budget <= 0:
                        return
                    matcher = self.accounts[account]
                    deferred = False
                    while remaining_budget > 0 and job_budget > 0 and job.state != "FAULTED":
                        rows = matcher.ledger.order_feedback(sid, after=cursors[account],
                                                             limit=min(remaining_budget, job_budget, 64))
                        if not rows:
                            break
                        for rowid, event in rows:
                            remaining_budget -= 1
                            job_budget -= 1
                            if (not isinstance(event, StrategyOrderEvent)
                                    or event.strategy_id != sid
                                    or event.account_scope != matcher.ledger.scope):
                                job.state, job.reason = "FAULTED", "InvalidOrderFeedbackFact"
                                break
                            try:
                                facts = self._ledger_facts((account,), strategy=sid)
                                execution = facts[account]
                                execution["feedback_cursor"] = (account, rowid)
                                ready = self._ready(job, snapshot)
                                data = (StrategyDataView(snapshot, ready=True, phase=phase,
                                    execution=facts) if ready else None)
                                quality = ("VALID" if all(snapshot.quality.get(key) == "VALID"
                                    for key in job.registration.required_feeds) else "PARTIAL") if data else "UNAVAILABLE"
                                context = StrategyOrderContext(data=data, execution=execution,
                                    now_ns=snapshot.now_ns, cursor=(snapshot.cursor, account, rowid), quality=quality)
                                event_document = {"event_id": event.event_id,
                                    "strategy_id": event.strategy_id,
                                    "account_scope": {"venue": event.account_scope.venue,
                                        "account": event.account_scope.account,
                                        "product": event.account_scope.product,
                                        "environment": event.account_scope.environment},
                                    "order_id": event.order_id, "status": event.status,
                                    "event_ts_ns": event.event_ts_ns,
                                    "quantity": event.quantity, "filled_quantity": event.filled_quantity,
                                    "remaining_quantity": event.remaining_quantity,
                                    "fill_id": event.fill_id, "fill_quantity": event.fill_quantity,
                                    "fill_price": event.fill_price, "fee": event.fee,
                                    "fee_currency": event.fee_currency, "metadata": event.metadata}
                                context_size = len(self._document({"event": event_document,
                                    "execution": dict(context.execution),
                                    "now_ns": context.now_ns, "cursor": context.cursor,
                                    "quality": context.quality}).encode())
                                if context_size > self.budgets.max_action_bytes:
                                    job.reason = "order_feedback_deferred_capacity"
                                    deferred = True
                                    break
                                if (job.worker is not None and job.worker.queued
                                        >= self.isolation.budgets.max_queue_frames):
                                    job.reason = "order_feedback_deferred_capacity"
                                    deferred = True
                                    break
                                proof = {"event_id": event.event_id, "rowid": rowid,
                                    "account_scope": event.account_scope.key,
                                    "registration": job.fingerprint}
                                claimed = matcher.ledger.claim_strategy_event(sid,
                                    job.registration.config_version, "@order-feedback:" + event.event_id, proof)
                                # The durable claim comes immediately before
                                # callback invocation/enqueue. A stale cursor can only skip.
                                cursors[account] = rowid
                                if not claimed:
                                    continue
                                callback_view = data or StrategyDataView(snapshot, ready=False, phase=phase)
                                if job.worker is not None:
                                    request = job.worker.submit_feedback(event=event,
                                        context={"snapshot": snapshot, "execution": execution,
                                                 "now_ns": context.now_ns, "cursor": context.cursor,
                                                 "quality": quality, "ready": data is not None,
                                                 "phase": phase},
                                        cursor=snapshot.cursor)
                                    job.isolated_pending[request["request_id"]] = {
                                        "kind": "feedback", "generation": job.isolated_generation,
                                        "cursor": snapshot.cursor, "snapshot": snapshot,
                                        "view": callback_view, "request_ns": self.now_ns()}
                                    continue
                                started = self.callback_clock_ns()
                                result = hook(event, context)
                                job.callbacks += 1
                                if inspect.isawaitable(result):
                                    if inspect.iscoroutine(result):
                                        result.close()
                                    raise ValueError("on_order_event must be synchronous")
                                if self.callback_clock_ns()-started > self.budgets.max_callback_ns:
                                    raise TimeoutError("Cooperative order callback exceeded its return-time budget")
                                self._consume_callback_result(job, result, callback_view, snapshot,
                                    replay=False, started=started, causal_time=snapshot.now_ns)
                            except Exception as exc:
                                job.state, job.reason = "FAULTED", type(exc).__name__
                                self._cancel_owned(job, snapshot.now_ns)
                                break
                        if job.state == "FAULTED" or deferred or job_budget <= 0:
                            break
                    if job.state == "FAULTED":
                        break
        finally:
            self._draining_feedback = False

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
                for account in job.registration.accounts:
                    observe_strategy(job.registration.module.strategy_id,
                                     self.accounts[account].ledger.scope.key,"PAPER","WARMING")
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

    def _fence_isolated_job(self, job):
        if job.worker is None:
            return
        job.isolated_generation += 1
        job.isolated_pending = {}
        job.worker.kill()

    def pause(self, strategy_id: str, now_ns: int):
        job = self._jobs[strategy_id]
        self._fence_isolated_job(job)
        if self._hold_blocked(job):
            self._cancel_owned(job, now_ns)
            return
        job.state, job.reason = "PAUSED", "operator_pause"
        for account in job.registration.accounts:
            observe_strategy(strategy_id,self.accounts[account].ledger.scope.key,"PAPER","PAUSED")
        self._cancel_owned(job, now_ns)

    def resume(self, strategy_id: str):
        job = self._jobs[strategy_id]
        if self._hold_blocked(job):
            return
        if job.state != "PAUSED":
            raise ValueError("Only an explicitly paused strategy may resume")
        job.state, job.reason = "WARMING", None
        for account in job.registration.accounts:
            observe_strategy(strategy_id,self.accounts[account].ledger.scope.key,"PAPER","WARMING")
        if job.worker is not None:
            job.isolated_generation += 1
            job.isolated_pending = {}
            job.isolated_initialized = False
            job.isolated_init_request = None
            job.isolated_restart_requested = True

    def halt(self, reason: str, now_ns: int, strategy_id: str | None = None):
        bounded_identity(reason, "halt reason")
        for sid, job in self._jobs.items():
            if strategy_id is None or sid == strategy_id:
                self._fence_isolated_job(job)
                if self._hold_blocked(job):
                    # Operator stop remains risk-reducing even while the
                    # no-new-risk fence stays in place.
                    self._cancel_owned(job, now_ns)
                    continue
                job.state, job.reason = "HALTED", reason
                for account in job.registration.accounts:
                    observe_strategy(sid,self.accounts[account].ledger.scope.key,"PAPER","HALTED")
                self._cancel_owned(job, now_ns)

    def close(self):
        """Boundedly reap isolated children without blocking market callbacks."""
        for sid, job in self._jobs.items():
            for account in job.registration.accounts:
                observe_strategy(sid,self.accounts[account].ledger.scope.key,"PAPER","STOPPED")
        if self.isolation is None:
            return
        pool = self._isolation_pool()
        for job in self._jobs.values():
            if job.worker is not None:
                job.isolated_pending = {}
                job.worker.kill()
        pool.close()

    async def close_async(self):
        """Asynchronous bounded child cleanup for the local runtime driver."""
        for sid, job in self._jobs.items():
            for account in job.registration.accounts:
                observe_strategy(sid,self.accounts[account].ledger.scope.key,"PAPER","STOPPED")
        if self.isolation is None:
            return
        pool = self._isolation_pool()
        for job in self._jobs.values():
            if job.worker is not None:
                job.isolated_pending = {}
        await pool.close_async()

    def advance(self, now_ns: int):
        if self.isolation is not None:
            self.poll_isolated()
        if self._recovery_gate():
            return
        snapshot = self.hot_path.snapshot(now_ns, self.phase.upper())
        if (self.phase == "paper" and self._input_phase in {"LIVE", "PAPER"}
                and not self._deferred_input_gaps):
            for account, matcher in self.accounts.items():
                if account in self._account_faults:
                    continue
                try:
                    for key, spec in self.products.items():
                        if spec.instrument.venue.value != matcher.ledger.scope.venue:
                            continue
                        feed = self.reference_feeds.get(key, key)
                        projected = snapshot.latest_book(feed)
                        if snapshot.quality.get(feed) != "VALID":
                            reason = "dependency_invalid_or_stale"
                        elif projected is None:
                            reason = "projected_book_missing"
                        elif not self._fresh_input(projected, now_ns):
                            reason = "receipt_or_native_source_not_fresh"
                        else:
                            continue
                        # Never let advance consume cached depth across the
                        # unobserved span. Fence from the matcher's last
                        # durable clock, then only cancellation/expiry may move.
                        self._fence_feed(key, feed, matcher, reason)
                    matcher.advance(now_ns)
                except Exception as exc:
                    self._account_faults[account] = type(exc).__name__
        self._drain_feedback(snapshot, "REPLAY" if self._input_phase == "REPLAY"
                             else self.phase.upper())

    def report(self):
        isolation = (None if self.isolation is None else
                     {"callback_isolation": ("process_hard_isolation" if self.isolation.require_bubblewrap
                                              else "process_isolation_trusted_only"),
                      "security_sandbox": ("bubblewrap_fail_closed" if self.isolation.require_bubblewrap
                                           else "not_claimed"),
                      "pool": self._isolation_pool().report()})
        return {"mode": "paper", "phase": self.phase, "external_mutations": 0,
                "input_phase": self._input_phase,
                "production_gate": "BLOCKED",
                "callback_isolation": (("process_hard_isolation" if self.isolation.require_bubblewrap
                                         else "process_isolation_trusted_only")
                                       if self.isolation is not None else "cooperative_return_time_only"),
                "isolation": isolation,
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
