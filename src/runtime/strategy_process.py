"""Bounded process isolation primitive for one explicit strategy plugin.

This module deliberately contains no strategy engine, matcher, ledger, owner or
broker client.  It is a transport/scheduling primitive used by
``PaperStrategyRuntime`` when the caller explicitly enables hard isolation.

The wire format is canonical, finite JSON over a single AF_UNIX socketpair.
Floats are rejected because every economic value must round-trip exactly as a
decimal string.  The child is started with a cleared environment, a private
HOME/TMPDIR, a private working directory and ``close_fds`` so only the declared
IPC descriptor is inherited.  Process separation is *not* a security sandbox;
optional bubblewrap confinement must be requested explicitly and fails closed
when it is unavailable.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping

from core.models import BaseMarketEvent, Signal, event_from_dict
from execution.execution_ledger import AccountScope
from execution.trading_contracts import (
    ActionKind, ExecutionAction, StrategyActionPlan, StrategyOrderContext,
    StrategyOrderEvent, TradingMode,
)
from market.hot_path import DecisionSnapshot, StrategyDataView


PROTOCOL_VERSION = 1
_CHILD_FLAG = "--cap04-child-path"
_ALLOWED_REQUEST = frozenset({
    "version", "request_id", "strategy_id", "kind", "parent_incarnation",
    "epoch", "sequence", "cursor", "payload",
})
_ALLOWED_RESPONSE = frozenset({
    "version", "request_id", "strategy_id", "kind", "parent_incarnation",
    "epoch", "sequence", "cursor", "ok", "error", "payload",
})
_KINDS = frozenset({"init", "event", "feedback", "checkpoint", "restore", "probe", "shutdown"})
_CHILD_ALLOWED_ENV = frozenset({
    "PATH", "PYTHONPATH", "PYTHONHASHSEED", "PYTHONIOENCODING", "LANG", "LC_ALL",
    "TMPDIR", "HOME", "LD_LIBRARY_PATH", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
})
_MIN_WORKER_ADDRESS_SPACE_BYTES = 96 * 1024 * 1024
_CONFINED_MAX_CHILD_PROCESSES = 1


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(name + " must be an explicit positive integer")


@dataclass(frozen=True)
class IsolationBudgets:
    """Explicit capacities for the isolated process primitive."""

    max_processes: int = 8
    max_queue_frames: int = 16
    max_queue_bytes: int = 1_048_576
    max_message_bytes: int = 1_048_576
    max_state_bytes: int = 524_288
    max_depth: int = 32
    max_items: int = 65_536
    callback_timeout_ns: int = 2_000_000_000
    result_ttl_ns: int = 2_000_000_000
    max_total_rss_bytes: int = 1_073_741_824
    shutdown_grace_ns: int = 500_000_000

    def __post_init__(self):
        for name in ("max_processes", "max_queue_frames", "max_queue_bytes",
                     "max_message_bytes", "max_state_bytes", "max_depth",
                     "max_items", "callback_timeout_ns", "result_ttl_ns",
                     "max_total_rss_bytes", "shutdown_grace_ns"):
            _positive(getattr(self, name), name)
        if self.max_worker_address_space_bytes < _MIN_WORKER_ADDRESS_SPACE_BYTES:
            raise ValueError("global RSS budget is too small for the bounded per-worker address-space limit")

    @property
    def max_worker_address_space_bytes(self):
        """Conservative per-worker address-space ceiling from the global budget."""
        return self.max_total_rss_bytes // self.max_processes


@dataclass(frozen=True)
class StrategyIsolation:
    """Opt-in hard-isolation configuration.

    ``allowed_source_roots`` and ``readonly_paths`` document the explicit
    source/dependency scope supplied to child workers.  ``public_settings`` is
    the only caller-provided mapping forwarded to a child; the parent's full
    settings mapping is never copied.
    """

    budgets: IsolationBudgets = field(default_factory=IsolationBudgets)
    allowed_source_roots: tuple[str, ...] = ()
    readonly_paths: tuple[str, ...] = ()
    public_settings: Mapping[str, Any] = field(default_factory=dict)
    require_bubblewrap: bool = False

    def __post_init__(self):
        if not isinstance(self.budgets, IsolationBudgets):
            raise TypeError("isolation budgets must be IsolationBudgets")
        for name in ("allowed_source_roots", "readonly_paths"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(v, str) or not v for v in values):
                raise ValueError(name + " must be an explicit tuple of paths")
        if not isinstance(self.public_settings, Mapping):
            raise ValueError("public_settings must be an explicit mapping")
        if not isinstance(self.require_bubblewrap, bool):
            raise ValueError("require_bubblewrap must be explicit")

    def document(self):
        return {
            "mode": ("confined_process" if self.require_bubblewrap
                     else "trusted_process_only"),
            "security_sandbox": bool(self.require_bubblewrap),
            "bubblewrap_required": self.require_bubblewrap,
            "public_settings_keys": tuple(sorted(self.public_settings)),
            "allowed_source_roots": self.allowed_source_roots,
            "readonly_paths": self.readonly_paths,
            "confined_resource_policy": ({
                "worker_address_space_bytes": self.budgets.max_worker_address_space_bytes,
                "worker_process_limit": _CONFINED_MAX_CHILD_PROCESSES,
                "thread_environment_limit": 1,
                "no_new_privileges": self.require_bubblewrap,
            } if self.require_bubblewrap else "trusted_process_only_unlimited_resources"),
            "budgets": vars(self.budgets),
        }


class ProtocolError(RuntimeError):
    pass


class WorkerUnavailable(RuntimeError):
    pass


class StrategyTimeout(TimeoutError):
    pass


def _walk(value, *, budgets, depth=0):
    if depth > budgets.max_depth:
        raise ProtocolError("IPC nesting exceeds its bound")
    if type(value) is float:
        raise ProtocolError("IPC floats are rejected; use exact decimal strings")
    if value is None or type(value) in {bool, int, str}:
        if type(value) is str and len(value.encode("utf-8")) > budgets.max_message_bytes:
            raise ProtocolError("IPC string exceeds its byte bound")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > budgets.max_items:
            raise ProtocolError("IPC sequence exceeds its item bound")
        return [_walk(item, budgets=budgets, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        if len(value) > budgets.max_items:
            raise ProtocolError("IPC mapping exceeds its item bound")
        out = {}
        for key, item in value.items():
            if type(key) is not str or not key:
                raise ProtocolError("IPC mapping keys must be explicit strings")
            out[key] = _walk(item, budgets=budgets, depth=depth + 1)
        return out
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ProtocolError("IPC decimal is nonfinite")
        return str(value)
    raise ProtocolError("IPC contains an unsupported value type: " + type(value).__name__)


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ProtocolError("duplicate IPC JSON key")
        out[key] = value
    return out


def _reject_constant(_value):
    raise ProtocolError("nonfinite IPC JSON constant")


def encode_frame(document, *, budgets: IsolationBudgets) -> bytes:
    clean = _walk(document, budgets=budgets)
    try:
        payload = json.dumps(clean, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("IPC document is not canonical finite JSON") from exc
    if not 0 < len(payload) <= budgets.max_message_bytes:
        raise ProtocolError("IPC frame exceeds its byte bound")
    return payload


def decode_frame(payload, *, budgets: IsolationBudgets):
    if type(payload) is not bytes or not 0 < len(payload) <= budgets.max_message_bytes:
        raise ProtocolError("IPC frame exceeds its byte bound")
    try:
        value = json.loads(payload, object_pairs_hook=_unique,
                           parse_constant=_reject_constant)
    except ProtocolError:
        raise
    except (ValueError, TypeError, RecursionError) as exc:
        raise ProtocolError("IPC frame is not valid JSON") from exc
    _walk(value, budgets=budgets)
    return value


def _framed(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


def _validate_envelope(document, allowed, *, budgets):
    if type(document) is not dict or set(document) != allowed:
        raise ProtocolError("IPC envelope schema mismatch")
    if document["version"] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported IPC protocol version")
    for name in ("request_id", "strategy_id", "kind", "parent_incarnation"):
        if type(document[name]) is not str or not document[name]:
            raise ProtocolError("IPC " + name + " must be a nonempty string")
    if document["kind"] not in _KINDS:
        raise ProtocolError("unsupported IPC request kind")
    for name in ("epoch", "sequence"):
        if type(document[name]) is not int or document[name] < 0:
            raise ProtocolError("IPC " + name + " must be a nonnegative integer")
    cursor = document["cursor"]
    if cursor is not None and (
            type(cursor) is not list or len(cursor) != 2
            or type(cursor[0]) is not str or not cursor[0]
            or type(cursor[1]) is not int or cursor[1] < 0):
        raise ProtocolError("IPC cursor must be null or [incarnation, sequence]")
    if type(document["payload"]) is not dict:
        raise ProtocolError("IPC payload must be an object")
    return document


# ---------------------------------------------------------------------------
# Typed JSON reconstruction. The parent encodes model documents; the child
# reconstructs immutable snapshots/facts with the existing domain models.
# ---------------------------------------------------------------------------

def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def event_document(event: BaseMarketEvent) -> dict:
    if not isinstance(event, BaseMarketEvent):
        raise TypeError("IPC event must be a market event model")
    return event.model_dump(mode="json")


def event_from_document(document) -> BaseMarketEvent:
    if type(document) is not dict:
        raise ProtocolError("event document must be an object")
    try:
        return event_from_dict(document)
    except Exception as exc:
        raise ProtocolError("invalid typed market event") from exc


def snapshot_document(snapshot: DecisionSnapshot) -> dict:
    if not isinstance(snapshot, DecisionSnapshot):
        raise TypeError("snapshot must be a DecisionSnapshot")
    def events(values):
        return {key: event_document(value) for key, value in values.items()}
    return {
        "engine_incarnation": snapshot.engine_incarnation,
        "apply_seq": snapshot.apply_seq,
        "now_ns": snapshot.now_ns,
        "phase": snapshot.phase,
        "clock_version": snapshot.clock_version,
        "versions": dict(snapshot.versions),
        "book_versions": dict(snapshot.book_versions),
        "quality": dict(snapshot.quality),
        "trades": events(snapshot.trades),
        "books": events(snapshot.books),
        "index_quotes": events(snapshot.index_quotes),
        "bars": {key: [event_document(bar) for bar in bars]
                 for key, bars in snapshot.bars.items()},
        "history_quality": dict(snapshot.history_quality),
    }


def snapshot_from_document(document) -> DecisionSnapshot:
    if type(document) is not dict or set(document) != {
            "engine_incarnation", "apply_seq", "now_ns", "phase", "clock_version",
            "versions", "book_versions", "quality", "trades", "books",
            "index_quotes", "bars", "history_quality"}:
        raise ProtocolError("snapshot document schema mismatch")
    def events(values):
        if type(values) is not dict:
            raise ProtocolError("snapshot event map must be an object")
        return MappingProxyType({key: event_from_document(value)
                                 for key, value in values.items()})
    bars = {}
    if type(document["bars"]) is not dict:
        raise ProtocolError("snapshot bars must be an object")
    for key, values in document["bars"].items():
        if type(values) is not list:
            raise ProtocolError("snapshot bars must be lists")
        bars[key] = tuple(event_from_document(value) for value in values)
    for name in ("versions", "book_versions", "quality", "history_quality"):
        if type(document[name]) is not dict:
            raise ProtocolError("snapshot " + name + " must be an object")
    return DecisionSnapshot(
        document["engine_incarnation"], document["apply_seq"], document["now_ns"],
        document["phase"], document["clock_version"],
        MappingProxyType(dict(document["versions"])),
        MappingProxyType(dict(document["book_versions"])),
        MappingProxyType(dict(document["quality"])),
        events(document["trades"]), events(document["books"]),
        events(document["index_quotes"]), MappingProxyType(bars),
        MappingProxyType(dict(document["history_quality"])))


def signal_document(signal: Signal) -> dict:
    return signal.model_dump(mode="json")


def signal_from_document(document) -> Signal:
    try:
        return Signal.model_validate(document)
    except Exception as exc:
        raise ProtocolError("invalid typed strategy signal") from exc


def _decimal(value, name, *, optional=False):
    if value is None and optional:
        return None
    if type(value) is not str or not value:
        raise ProtocolError(name + " requires an exact decimal string")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise ProtocolError(name + " is not an exact decimal") from exc
    if not result.is_finite():
        raise ProtocolError(name + " is not finite")
    return result


def action_document(action: ExecutionAction) -> dict:
    scope = action.scope
    return {
        "action_id": action.action_id,
        "action": action.action.value,
        "scope": {"venue": scope.venue, "account": scope.account,
                  "product": scope.product, "environment": scope.environment},
        "strategy_id": action.strategy_id,
        "config_version": action.config_version,
        "mode": action.mode.value,
        "expiry_ns": action.expiry_ns,
        "intent": action.intent.model_dump(mode="json") if action.intent else None,
        "target_order_id": action.target_order_id,
        "post_only": action.post_only,
        "reduce_only": action.reduce_only,
        "time_in_force": action.time_in_force,
        "group_id": action.group_id,
    }


def action_from_document(document) -> ExecutionAction:
    if type(document) is not dict:
        raise ProtocolError("action document must be an object")
    scope_doc = document.get("scope")
    if type(scope_doc) is not dict or set(scope_doc) != {"venue", "account", "product", "environment"}:
        raise ProtocolError("action scope schema mismatch")
    intent_doc = document.get("intent")
    from core.models import OrderIntent
    try:
        intent = OrderIntent.model_validate(intent_doc) if intent_doc is not None else None
        scope = AccountScope(scope_doc["venue"], scope_doc["account"],
                             scope_doc["product"], scope_doc["environment"])
        return ExecutionAction(
            action_id=document["action_id"],
            action=ActionKind(document["action"]),
            scope=scope,
            strategy_id=document["strategy_id"],
            config_version=document["config_version"],
            mode=TradingMode(document["mode"]),
            expiry_ns=document["expiry_ns"],
            intent=intent,
            target_order_id=document.get("target_order_id"),
            post_only=document.get("post_only", False),
            reduce_only=document.get("reduce_only", False),
            time_in_force=document.get("time_in_force", "GTC"),
            group_id=document.get("group_id"),
        )
    except ProtocolError:
        raise
    except Exception as exc:
        raise ProtocolError("invalid typed execution action") from exc


def plan_document(plan: StrategyActionPlan) -> dict:
    return {"plan_id": plan.plan_id, "strategy_id": plan.strategy_id,
            "config_version": plan.config_version, "event_ts_ns": plan.event_ts_ns,
            "actions": [action_document(action) for action in plan.actions]}


def plan_from_document(document) -> StrategyActionPlan:
    if type(document) is not dict:
        raise ProtocolError("action plan document must be an object")
    actions = document.get("actions")
    if type(actions) is not list:
        raise ProtocolError("action plan actions must be a list")
    try:
        return StrategyActionPlan(document["plan_id"], document["strategy_id"],
            document["config_version"], document["event_ts_ns"],
            tuple(action_from_document(action) for action in actions))
    except ProtocolError:
        raise
    except Exception as exc:
        raise ProtocolError("invalid typed action plan") from exc


def order_event_document(event: StrategyOrderEvent) -> dict:
    scope = event.account_scope
    return {
        "event_id": event.event_id, "strategy_id": event.strategy_id,
        "account_scope": {"venue": scope.venue, "account": scope.account,
                          "product": scope.product, "environment": scope.environment},
        "order_id": event.order_id, "status": event.status,
        "event_ts_ns": event.event_ts_ns, "quantity": str(event.quantity),
        "filled_quantity": str(event.filled_quantity),
        "remaining_quantity": str(event.remaining_quantity),
        "fill_id": event.fill_id,
        "fill_quantity": str(event.fill_quantity) if event.fill_quantity is not None else None,
        "fill_price": str(event.fill_price) if event.fill_price is not None else None,
        "fee": str(event.fee) if event.fee is not None else None,
        "fee_currency": event.fee_currency,
        "metadata": _walk(event.metadata, budgets=IsolationBudgets(
            max_message_bytes=16_384, max_items=2048, max_depth=8)),
    }


def order_event_from_document(document) -> StrategyOrderEvent:
    if type(document) is not dict:
        raise ProtocolError("order event document must be an object")
    scope_doc = document.get("account_scope")
    if type(scope_doc) is not dict or set(scope_doc) != {"venue", "account", "product", "environment"}:
        raise ProtocolError("order event scope schema mismatch")
    try:
        return StrategyOrderEvent(
            document["event_id"], document["strategy_id"],
            AccountScope(scope_doc["venue"], scope_doc["account"],
                         scope_doc["product"], scope_doc["environment"]),
            document["order_id"], document["status"], document.get("event_ts_ns"),
            _decimal(document["quantity"], "quantity"),
            _decimal(document["filled_quantity"], "filled_quantity"),
            _decimal(document["remaining_quantity"], "remaining_quantity"),
            document.get("fill_id"),
            _decimal(document.get("fill_quantity"), "fill_quantity", optional=True),
            _decimal(document.get("fill_price"), "fill_price", optional=True),
            _decimal(document.get("fee"), "fee", optional=True),
            document.get("fee_currency"), document.get("metadata", {}))
    except ProtocolError:
        raise
    except Exception as exc:
        raise ProtocolError("invalid typed order event") from exc


def context_document(*, snapshot, execution, now_ns, cursor, quality, ready, phase):
    return {
        "snapshot": snapshot_document(snapshot),
        "execution": _walk(execution, budgets=IsolationBudgets(
            max_message_bytes=262_144, max_items=8192, max_depth=12)),
        "now_ns": now_ns,
        "cursor": list(cursor) if cursor is not None else None,
        "quality": quality,
        "ready": bool(ready),
        "phase": phase,
    }


def context_from_document(document):
    if type(document) is not dict or set(document) != {
            "snapshot", "execution", "now_ns", "cursor", "quality", "ready", "phase"}:
        raise ProtocolError("order context document schema mismatch")
    snapshot = snapshot_from_document(document["snapshot"])
    data = StrategyDataView(snapshot, ready=document["ready"], phase=document["phase"],
                            execution=document["execution"])
    context = StrategyOrderContext(
        data=data, execution=document["execution"], now_ns=document["now_ns"],
        cursor=tuple(document["cursor"]) if document["cursor"] is not None else None,
        quality=document["quality"])
    return data, context


def _result_document(result):
    if isinstance(result, StrategyActionPlan):
        return {"type": "plan", "plan": plan_document(result)}
    if not isinstance(result, list):
        raise ProtocolError("strategy callback must return list[Signal] or StrategyActionPlan")
    return {"type": "signals", "signals": [signal_document(item) for item in result]}


def result_from_document(document):
    if type(document) is not dict or document.get("type") not in {"signals", "plan"}:
        raise ProtocolError("callback result schema mismatch")
    if document["type"] == "plan":
        return plan_from_document(document["plan"])
    signals = document.get("signals")
    if type(signals) is not list:
        raise ProtocolError("callback signals must be a list")
    return [signal_from_document(item) for item in signals]


# ---------------------------------------------------------------------------
# Child process
# ---------------------------------------------------------------------------

def _child_module(module_name, module_path, source_roots):
    if module_path:
        path = Path(module_path).resolve()
        if not path.is_file():
            raise ProtocolError("strategy source file is missing")
        allowed = tuple(Path(root).resolve() for root in source_roots)
        if allowed and not any(path == root or root in path.parents for root in allowed):
            raise ProtocolError("strategy source is outside the explicit read-only roots")
        name = module_name or ("cap04_plugin_" + hashlib.sha256(str(path).encode()).hexdigest()[:12])
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ProtocolError("strategy source cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    if not module_name:
        raise ProtocolError("strategy module name is missing")
    return importlib.import_module(module_name)


class _ChildRuntime:
    def __init__(self, document):
        self.parent_incarnation = document["parent_incarnation"]
        self.strategy_id = document["strategy_id"]
        self.module_name = document.get("module_name")
        self.module_path = document.get("module_path")
        self.source_roots = tuple(document.get("source_roots", ()))
        self.config = document.get("config") or {}
        self.facts = document.get("registration_facts") or {}
        self.expected = document.get("expected") or {}
        self._instance = None
        expected_sha = self.expected.get("source_sha256")
        if expected_sha is not None:
            if not self.module_path or type(expected_sha) is not str:
                raise ProtocolError("pinned strategy source manifest is incomplete")
            try:
                actual_sha = hashlib.sha256(Path(self.module_path).read_bytes()).hexdigest()
            except OSError as exc:
                raise ProtocolError("pinned strategy source is unreadable") from exc
            if actual_sha != expected_sha:
                raise ProtocolError("strategy source hash changed after registration")
        module = _child_module(self.module_name, self.module_path, self.source_roots)
        for name in ("strategy_id", "subscriptions", "warmup", "action_protocol_version"):
            if name not in self.expected:
                continue
            expected = self.expected.get(name)
            actual_name = {"strategy_id": "STRATEGY_ID", "subscriptions": "SUBSCRIPTIONS",
                           "warmup": "WARMUP", "action_protocol_version": "ACTION_PROTOCOL_VERSION"}[name]
            actual = str(getattr(module, actual_name)) if name == "strategy_id" else getattr(module, actual_name, None)
            if actual != expected:
                raise ProtocolError("child source metadata differs from pinned manifest: " + name)
        self.module = module
        self._build()

    def _build(self):
        create = getattr(self.module, "create_strategy", None)
        if not callable(create):
            raise ProtocolError("strategy module lacks create_strategy")
        instance = create()
        hook = getattr(instance, "configure", None)
        if callable(hook):
            result = hook(self.config)
            if result is not None:
                raise ProtocolError("configure must be synchronous")
        hook = getattr(instance, "validate_registration", None)
        if callable(hook):
            result = hook(_freeze(self.facts))
            if result is not None:
                raise ProtocolError("validate_registration must be synchronous")
        if not callable(getattr(instance, "on_event", None)):
            raise ProtocolError("strategy instance lacks on_event")
        expected_id = self.expected.get("strategy_id")
        declared = getattr(self.module, "STRATEGY_ID", None)
        if expected_id is not None and declared is not None and str(declared) != expected_id:
            raise ProtocolError("child module strategy identity differs from registration")
        self._instance = instance

    def event(self, payload):
        event = event_from_document(payload["event"])
        snapshot = snapshot_from_document(payload["snapshot"])
        view = StrategyDataView(snapshot, ready=bool(payload["ready"]),
                                phase=payload["phase"], execution=payload.get("execution", {}))
        return _result_document(self._instance.on_event(event, view))

    def feedback(self, payload):
        event = order_event_from_document(payload["event"])
        _, context = context_from_document(payload["context"])
        hook = getattr(self._instance, "on_order_event", None)
        if not callable(hook):
            raise ProtocolError("strategy lacks on_order_event")
        return _result_document(hook(event, context))

    def checkpoint(self):
        hook = getattr(self._instance, "checkpoint_state", None)
        if not callable(hook):
            raise ProtocolError("strategy lacks checkpoint_state")
        state = _walk(hook(), budgets=IsolationBudgets(
            max_message_bytes=524_288, max_items=16_384, max_depth=32))
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()
        return {"state": state, "sha256": hashlib.sha256(encoded).hexdigest()}

    def restore(self, payload):
        # A restore is only accepted on a freshly started child. Rebuild once
        # more so configure/validate cannot observe a stale prior instance.
        self._build()
        hook = getattr(self._instance, "restore_state", None)
        if not callable(hook):
            raise ProtocolError("strategy lacks restore_state")
        hook(_freeze(payload["state"]))
        check = self.checkpoint()
        if check["sha256"] != payload["sha256"] or check["state"] != payload["state"]:
            raise ProtocolError("restore_state roundtrip differs from checkpoint")
        return check

    def probe(self):
        env = {key: value for key, value in os.environ.items()
               if key in _CHILD_ALLOWED_ENV}
        fd_dir = "/proc/self/fd"
        try:
            fd_count = len(os.listdir(fd_dir))
        except OSError:
            fd_count = -1
        limits = {}
        try:
            import resource
            limits = {
                "address_space": list(resource.getrlimit(resource.RLIMIT_AS)),
                "process_count": list(resource.getrlimit(resource.RLIMIT_NPROC)),
            }
        except (ImportError, OSError, ValueError):
            limits = {"error": "resource_limits_unavailable"}
        no_new_privileges = _get_no_new_privileges()
        return {"pid": os.getpid(), "env": env, "cwd": os.getcwd(),
                "fd_count": fd_count, "has_instance": self._instance is not None,
                "resource_limits": limits,
                "no_new_privileges": no_new_privileges}


def _read_exact(sock, count):
    chunks = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError("child socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _get_no_new_privileges():
    if not sys.platform.startswith("linux"):
        return None
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.prctl(39, 0, 0, 0, 0)  # PR_GET_NO_NEW_PRIVS
        return bool(result) if result >= 0 else None
    except (AttributeError, OSError):
        return None


def _apply_confined_resource_limits(address_space_bytes: int, process_count: int) -> None:
    """Install non-raiseable child limits after bwrap setup, before plugin import."""
    if (not sys.platform.startswith("linux") or type(address_space_bytes) is not int
            or address_space_bytes < _MIN_WORKER_ADDRESS_SPACE_BYTES
            or type(process_count) is not int or process_count != _CONFINED_MAX_CHILD_PROCESSES):
        raise WorkerUnavailable("invalid confined worker resource policy")
    try:
        import ctypes
        import resource
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
        if _get_no_new_privileges() is not True:
            raise OSError("PR_SET_NO_NEW_PRIVS could not be verified")
        resource.setrlimit(resource.RLIMIT_AS, (address_space_bytes, address_space_bytes))
        resource.setrlimit(resource.RLIMIT_NPROC, (process_count, process_count))
        if resource.getrlimit(resource.RLIMIT_AS) != (address_space_bytes, address_space_bytes):
            raise OSError("RLIMIT_AS could not be verified")
        if resource.getrlimit(resource.RLIMIT_NPROC) != (process_count, process_count):
            raise OSError("RLIMIT_NPROC could not be verified")
    except (ImportError, AttributeError, OSError, ValueError) as exc:
        raise WorkerUnavailable("confined worker resource limits failed closed") from exc


def _child_main(path: str, address_space_bytes=None, process_count=None) -> int:
    if address_space_bytes is not None or process_count is not None:
        try:
            _apply_confined_resource_limits(address_space_bytes, process_count)
        except WorkerUnavailable:
            return 3
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)
    sock.setblocking(True)
    budgets = IsolationBudgets()
    runtime = None
    try:
        header = _read_exact(sock, 4)
        size = int.from_bytes(header, "big")
        if not 0 < size <= budgets.max_message_bytes:
            return 2
        request = decode_frame(_read_exact(sock, size), budgets=budgets)
        _validate_envelope(request, _ALLOWED_REQUEST, budgets=budgets)
        if request["kind"] != "init":
            return 2
        runtime = _ChildRuntime({**request["payload"],
            "parent_incarnation": request["parent_incarnation"],
            "strategy_id": request["strategy_id"]})
        response = {"version": PROTOCOL_VERSION, "request_id": request["request_id"],
            "strategy_id": request["strategy_id"], "kind": request["kind"],
            "parent_incarnation": request["parent_incarnation"],
            "epoch": request["epoch"], "sequence": request["sequence"],
            "cursor": request["cursor"], "ok": True, "error": None,
            "payload": {"plugin_class": type(runtime._instance).__module__ + "." +
                           type(runtime._instance).__qualname__,
                        "has_feedback": callable(getattr(runtime._instance, "on_order_event", None)),
                        "checkpoint": callable(getattr(runtime._instance, "checkpoint_state", None))
                                      and callable(getattr(runtime._instance, "restore_state", None))}}
        payload = encode_frame(response, budgets=budgets)
        sock.sendall(_framed(payload))
        while True:
            header = _read_exact(sock, 4)
            size = int.from_bytes(header, "big")
            if not 0 < size <= budgets.max_message_bytes:
                return 2
            request = decode_frame(_read_exact(sock, size), budgets=budgets)
            _validate_envelope(request, _ALLOWED_REQUEST, budgets=budgets)
            kind = request["kind"]
            if kind == "shutdown":
                response = {**request, "ok": True, "error": None, "payload": {}}
                payload = encode_frame(response, budgets=budgets)
                sock.sendall(_framed(payload))
                return 0
            try:
                if kind == "event":
                    result = runtime.event(request["payload"])
                elif kind == "feedback":
                    result = runtime.feedback(request["payload"])
                elif kind == "checkpoint":
                    result = runtime.checkpoint()
                elif kind == "restore":
                    result = runtime.restore(request["payload"])
                elif kind == "probe":
                    result = runtime.probe()
                else:
                    raise ProtocolError("unsupported child request kind")
                response = {**request, "ok": True, "error": None, "payload": result}
            except BaseException as exc:
                response = {**request, "ok": False,
                            "error": type(exc).__name__ + ":" + str(exc)[:512],
                            "payload": {}}
            payload = encode_frame(response, budgets=budgets)
            sock.sendall(_framed(payload))
    except (EOFError, BrokenPipeError, ConnectionResetError):
        return 0
    except BaseException:
        return 1
    finally:
        try:
            sock.close()
        finally:
            pass


def _child_env(source_roots, private_root, readonly_paths=(), *, inherit_pythonpath=True):
    keep = {}
    for key in (("PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH") if inherit_pythonpath
                else ("LANG", "LC_ALL")):
        if key in os.environ:
            keep[key] = os.environ[key]
    if not inherit_pythonpath:
        keep["PATH"] = "/usr/bin:/bin"
    roots = [str(Path(root).resolve()) for root in (*source_roots, *readonly_paths)]
    # Preserve explicit dependency roots supplied by the caller's Python
    # environment, but only existing directories. The rest of the parent
    # environment (credentials, broker tokens, cloud metadata) is dropped.
    for entry in (os.environ.get("PYTHONPATH", "").split(os.pathsep)
                  if inherit_pythonpath else ()):
        if entry and Path(entry).resolve().is_dir():
            candidate = str(Path(entry).resolve())
            if candidate not in roots:
                roots.append(candidate)
    package_root = str(Path(__file__).resolve().parents[1])
    if package_root not in roots:
        roots.append(package_root)
    keep.update({
        "PYTHONPATH": os.pathsep.join(roots),
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "HOME": str(private_root),
        "TMPDIR": str(private_root),
    })
    return keep


def _bwrap_available():
    return shutil.which("bwrap") is not None


def _rss_bytes(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def _rss_tree_bytes(pid):
    """Observe the worker plus descendants (bubblewrap keeps a wrapper PID)."""
    profile = _rss_tree_profile(pid)
    if profile is None:
        return None
    return profile["root_bytes"] + profile["descendant_bytes"]


def _rss_tree_profile(pid):
    """Return root/wrapper and descendant RSS separately for honest accounting."""
    pending, seen = [int(pid)], set()
    root_bytes = 0
    descendant_bytes = 0
    root_observed = False
    any_observed = False
    while pending and len(seen) < 256:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        rss = _rss_bytes(current)
        if rss is not None:
            any_observed = True
            if current == int(pid):
                root_bytes += rss
                root_observed = True
            else:
                descendant_bytes += rss
        try:
            children = Path(f"/proc/{current}/task/{current}/children").read_text().split()
            pending.extend(int(item) for item in children if item.isdigit())
        except (OSError, ValueError):
            pass
    if not any_observed:
        return None
    return {"root_bytes": root_bytes if root_observed else 0,
            "descendant_bytes": descendant_bytes}


@dataclass
class _Pending:
    request_id: str
    kind: str
    sequence: int
    epoch: int
    cursor: tuple | None
    deadline_ns: int
    enqueued_ns: int
    frame_bytes: int = 0
    metadata: dict = field(default_factory=dict)


class IsolatedStrategyWorker:
    """One child process and at most one in-flight callback/job."""

    def __init__(self, *, strategy_id, module_name, module_path, config,
                 registration_facts, expected, isolation: StrategyIsolation,
                 parent_incarnation, now_ns: Callable[[], int]):
        self.strategy_id = strategy_id
        self.module_name = module_name
        self.module_path = module_path
        self.config = dict(config)
        self.registration_facts = registration_facts
        self.expected = expected
        self.isolation = isolation
        self.budgets = isolation.budgets
        self.parent_incarnation = parent_incarnation
        self.now_ns = now_ns
        self.process = None
        self.sock = None
        self.listener = None
        self._private_root = None
        self._buffer = bytearray()
        self._outbound = deque()
        self._outbound_bytes = 0
        self._queue_bytes = 0
        self._requests: deque[_Pending] = deque()
        self._pending: _Pending | None = None
        self._sequence = 0
        self._epoch = 0
        self._closed = False
        self._fault = None
        self._started_ns = None

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None and not self._closed

    @property
    def pid(self):
        return None if self.process is None else self.process.pid

    @property
    def inflight(self):
        return self._pending is not None

    @property
    def queued(self):
        return len(self._requests)

    @property
    def queue_bytes(self):
        return self._queue_bytes

    @property
    def fault(self):
        return self._fault

    @property
    def epoch(self):
        return self._epoch

    def rss_bytes(self):
        return None if self.pid is None else _rss_tree_bytes(self.pid)

    def rss_profile(self):
        return None if self.pid is None else _rss_tree_profile(self.pid)

    def _fail(self, reason):
        self._fault = str(reason)[:512]
        self.kill()

    def _await_exit(self):
        """Deprecated nonblocking reap; never synchronously waits for a child."""
        process = self.process
        if process is None:
            return
        if process.poll() is not None:
            self.process = None

    def reap(self):
        """Nonblocking zombie reaping; safe from the market callback path."""
        if self.process is not None and self.process.poll() is not None:
            self.process = None

    def start(self, *, epoch=None):
        if self.alive:
            return
        self.reap()
        if self.process is not None:
            raise WorkerUnavailable("previous isolated child is still being reaped")
        self._closed = False
        self._fault = None
        self._buffer.clear()
        self._outbound.clear()
        self._outbound_bytes = 0
        self._queue_bytes = 0
        self._requests.clear()
        self._pending = None
        if epoch is not None:
            self._epoch = int(epoch)
        if self.isolation.require_bubblewrap and not _bwrap_available():
            raise WorkerUnavailable("bubblewrap is required but unavailable")
        self._private_root = tempfile.mkdtemp(prefix="cap04-worker-")
        ipc_path = str(Path(self._private_root) / "worker.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(ipc_path)
        listener.listen(1)
        listener.setblocking(False)
        self.listener = listener
        command = [sys.executable, "-m", "runtime.strategy_process", _CHILD_FLAG, ipc_path]
        if self.isolation.require_bubblewrap:
            command += ["--resource-limits",
                        str(self.budgets.max_worker_address_space_bytes),
                        str(_CONFINED_MAX_CHILD_PROCESSES)]
            # The worker primitive and its domain models are part of the
            # trusted runtime dependency closure. Bind that exact source tree
            # read-only alongside the explicit plugin roots.
            source = [str(Path(root).resolve()) for root in (
                *self.isolation.allowed_source_roots,
                str(Path(__file__).resolve().parents[1]))]
            argv = ["bwrap", "--die-with-parent", "--unshare-all", "--unshare-user", "--new-session",
                    "--disable-userns", "--assert-userns-disabled", "--cap-drop", "ALL",
                    "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
            # Mount real directories and recreate standard merged-/usr links
            # explicitly. Binding host symlink aliases directly is rejected
            # by bubblewrap on several supported distributions.
            for path in ("/usr", "/etc"):
                if Path(path).is_dir() and not Path(path).is_symlink():
                    argv += ["--ro-bind", path, path]
            for alias, target in (("/bin", "usr/bin"), ("/lib", "usr/lib"),
                                  ("/lib64", "usr/lib64"), ("/sbin", "usr/sbin")):
                if Path(alias).is_symlink() and Path(alias).resolve().is_dir():
                    argv += ["--symlink", target, alias]
                elif Path(alias).is_dir():
                    argv += ["--ro-bind", alias, alias]
            for path in source:
                if Path(path).exists():
                    argv += ["--ro-bind", path, path]
            for path in self.isolation.readonly_paths:
                if Path(path).exists():
                    argv += ["--ro-bind", str(path), str(path)]
            argv += ["--bind", self._private_root, self._private_root,
                     "--chdir", self._private_root, *command]
            command = argv
        env = _child_env(self.isolation.allowed_source_roots or
                         (tuple(filter(None, (str(Path(__file__).resolve().parents[1]),))),),
                         self._private_root, self.isolation.readonly_paths,
                         inherit_pythonpath=not self.isolation.require_bubblewrap)
        if self.isolation.require_bubblewrap:
            for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                         "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
                env[name] = "1"
        try:
            self.process = subprocess.Popen(command, close_fds=True,
                env=env, cwd=self._private_root, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except BaseException:
            listener.close()
            self._cleanup_private()
            raise
        self._started_ns = self.now_ns()
        self._sequence = 0
        request = self._enqueue("init", {
            "module_name": self.module_name,
            "module_path": self.module_path,
            "source_roots": tuple(self.isolation.allowed_source_roots),
            "config": self.config,
            "registration_facts": self.registration_facts,
            "expected": self.expected,
        }, cursor=None)
        return request

    def restart(self, *, epoch=None):
        self.kill()
        self._epoch = int(epoch) if epoch is not None else self._epoch + 1
        return self.start(epoch=self._epoch)

    def bump_epoch(self):
        self._epoch += 1
        self._pending = None
        self._requests.clear()
        self._buffer.clear()
        self._outbound.clear()
        self._outbound_bytes = 0
        self._queue_bytes = 0
        return self._epoch

    def _enqueue(self, kind, payload, *, cursor):
        if not self.alive:
            raise WorkerUnavailable("isolated strategy process is not alive")
        if len(self._requests) >= self.budgets.max_queue_frames:
            self._fail("isolated_queue_count_overflow")
            raise WorkerUnavailable("isolated queue count capacity exceeded")
        self._sequence += 1
        request_id = hashlib.sha256(
            f"{self.parent_incarnation}:{self.strategy_id}:{self._epoch}:{self._sequence}:{kind}".encode()
        ).hexdigest()
        request = {
            "version": PROTOCOL_VERSION, "request_id": request_id,
            "strategy_id": self.strategy_id, "kind": kind,
            "parent_incarnation": self.parent_incarnation, "epoch": self._epoch,
            "sequence": self._sequence,
            "cursor": list(cursor) if cursor is not None else None,
            "payload": payload,
        }
        encoded = encode_frame(request, budgets=self.budgets)
        framed = _framed(encoded)
        if self._queue_bytes + len(framed) > self.budgets.max_queue_bytes:
            self._fail("isolated_queue_byte_overflow")
            raise WorkerUnavailable("isolated queue byte capacity exceeded")
        # Callback deadlines are wall-clock safety bounds, independent of the
        # market clock (which may be paused, replayed, or jump forwards).
        now = time.monotonic_ns()
        timeout_ns = self.budgets.callback_timeout_ns
        if kind in {"init", "restore", "checkpoint", "probe", "shutdown"}:
            timeout_ns = max(timeout_ns, 2_000_000_000)
        pending = _Pending(request_id, kind, self._sequence, self._epoch,
            (tuple(cursor) if cursor is not None else None), now + timeout_ns, now,
            frame_bytes=len(framed))
        self._requests.append(pending)
        self._queue_bytes += len(framed)
        if self._pending is None:
            self._pending = pending
        self._outbound.append(framed)
        self._outbound_bytes += len(framed)
        return request

    def submit_event(self, *, event, snapshot, execution, ready, phase, cursor):
        payload = {"event": event_document(event),
                   "snapshot": snapshot_document(snapshot),
                   "execution": _walk(execution, budgets=self.budgets),
                   "ready": bool(ready), "phase": phase}
        return self._enqueue("event", payload, cursor=cursor)

    def submit_feedback(self, *, event, context, cursor):
        return self._enqueue("feedback", {"event": order_event_document(event),
                                          "context": context_document(**context)},
                             cursor=cursor)

    def submit_checkpoint(self):
        return self._enqueue("checkpoint", {}, cursor=None)

    def submit_restore(self, *, state, sha256):
        return self._enqueue("restore", {"state": state, "sha256": sha256}, cursor=None)

    def submit_probe(self):
        return self._enqueue("probe", {}, cursor=None)

    def flush_outbound(self, *, max_frames=4):
        sent = 0
        while self._outbound and sent < max_frames and self.sock is not None:
            frame = self._outbound[0]
            try:
                count = self.sock.send(frame)
            except BlockingIOError:
                break
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                self._fail("isolated_send_failed:" + type(exc).__name__)
                raise WorkerUnavailable(self._fault) from exc
            if count < len(frame):
                self._outbound[0] = frame[count:]
                break
            self._outbound.popleft()
            self._outbound_bytes -= len(frame)
            sent += 1
        return sent

    def poll(self, *, max_frames=4, now_ns=None):
        """Read bounded responses without blocking the caller's event loop."""
        if self.sock is None and self.listener is not None:
            try:
                self.sock, _ = self.listener.accept()
                self.sock.setblocking(False)
                self.listener.close()
                self.listener = None
            except BlockingIOError:
                return []
            except OSError:
                self._fail("isolated_accept_failed")
                return []
        if self.sock is None:
            return []
        now = time.monotonic_ns() if now_ns is None else now_ns
        self.flush_outbound()
        responses = []
        frames = 0
        while frames < max_frames:
            try:
                ready, _, _ = select.select([self.sock], [], [], 0)
            except (OSError, ValueError):
                self._fail("isolated_select_failed")
                break
            if not ready:
                break
            try:
                chunk = self.sock.recv(65536)
            except BlockingIOError:
                break
            except (ConnectionResetError, OSError):
                chunk = b""
            if not chunk:
                if self.alive:
                    self._fail("isolated_child_closed")
                break
            self._buffer.extend(chunk)
            if len(self._buffer) > self.budgets.max_message_bytes + 4:
                self._fail("isolated_response_overflow")
                break
            while len(self._buffer) >= 4:
                size = int.from_bytes(self._buffer[:4], "big")
                if size <= 0 or size > self.budgets.max_message_bytes:
                    self._fail("isolated_response_size_invalid")
                    break
                if len(self._buffer) < 4 + size:
                    break
                payload = bytes(self._buffer[4:4 + size])
                del self._buffer[:4 + size]
                try:
                    response = decode_frame(payload, budgets=self.budgets)
                    _validate_envelope(response, _ALLOWED_RESPONSE, budgets=self.budgets)
                except ProtocolError as exc:
                    self._fail("isolated_response_malformed:" + str(exc))
                    break
                responses.append((response, now))
                frames += 1
                if frames >= max_frames:
                    break
        return responses

    def pending_expired(self, now_ns=None):
        if self._pending is None:
            return False
        now = time.monotonic_ns() if now_ns is None else now_ns
        return now > self._pending.deadline_ns

    async def await_response(self, request, *, timeout_ns=None):
        """Asynchronously await a control-plane response with wall deadline.

        Event-loop users should use this for init/checkpoint/restore. Market
        callbacks must continue using bounded `poll()` and never await a child.
        """
        import asyncio
        if not isinstance(request, Mapping) or type(request.get("request_id")) is not str:
            raise ValueError("worker request envelope is required")
        deadline = time.monotonic_ns() + (timeout_ns or max(
            self.budgets.callback_timeout_ns, 2_000_000_000))
        while time.monotonic_ns() <= deadline:
            for response, observed in self.poll(max_frames=4):
                if response.get("request_id") != request["request_id"]:
                    raise ProtocolError("isolated response request identity differs")
                return self.accept_response(response, now_ns=observed)
            if not self.alive:
                break
            await asyncio.sleep(0.001)
        self.kill()
        raise StrategyTimeout("isolated worker did not reply before its bounded deadline")

    def accept_response(self, response, *, now_ns=None):
        """Validate exact request/response identity before returning a result."""
        pending = self._pending
        if pending is None:
            raise ProtocolError("late or duplicate isolated response")
        now = time.monotonic_ns() if now_ns is None else now_ns
        for name, expected in (
                ("version", PROTOCOL_VERSION),
                ("request_id", pending.request_id),
                ("strategy_id", self.strategy_id),
                ("kind", pending.kind),
                ("parent_incarnation", self.parent_incarnation),
                ("epoch", pending.epoch),
                ("sequence", pending.sequence)):
            if response.get(name) != expected:
                raise ProtocolError("isolated response identity mismatch: " + name)
        cursor = tuple(response["cursor"]) if response["cursor"] is not None else None
        if cursor != pending.cursor:
            raise ProtocolError("isolated response cursor mismatch")
        if now > pending.deadline_ns:
            raise StrategyTimeout("isolated response arrived after its deadline")
        if now - pending.enqueued_ns > self.budgets.result_ttl_ns:
            raise ProtocolError("isolated response exceeded its result TTL")
        if self._fault is not None:
            raise WorkerUnavailable(self._fault)
        previous_kind = pending.kind
        self._requests.popleft()
        self._queue_bytes = max(0, self._queue_bytes - pending.frame_bytes)
        self._pending = self._requests[0] if self._requests else None
        if previous_kind == "init" and self._pending is not None:
            # Worker initialization is a separate bounded control-plane phase;
            # do not charge its cold import time against the first callback.
            self._pending.enqueued_ns = now
            self._pending.deadline_ns = now + self.budgets.callback_timeout_ns
        if response.get("ok") is not True:
            raise RuntimeError(response.get("error") or "isolated callback failed")
        return response["payload"]

    def kill(self):
        process = self.process
        self._closed = True
        self._pending = None
        self._requests.clear()
        self._outbound.clear()
        self._outbound_bytes = 0
        self._queue_bytes = 0
        self._buffer.clear()
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None
        if self.listener is not None:
            try:
                self.listener.close()
            finally:
                self.listener = None
        if process is not None and process.poll() is None:
            try:
                # Never wait on an untrusted child from a market callback.
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                try:
                    process.kill()
                except (ProcessLookupError, OSError):
                    pass
        self._cleanup_private()

    def close(self):
        """Request stop and return immediately; `reap` is always nonblocking."""
        self.kill()

    async def close_async(self):
        """Kill then asynchronously reap within the explicit shutdown bound."""
        import asyncio
        self.kill()
        deadline = time.monotonic_ns() + self.budgets.shutdown_grace_ns
        while self.process is not None and time.monotonic_ns() <= deadline:
            self.reap()
            if self.process is None:
                return
            await asyncio.sleep(0.005)
        self.reap()

    def _cleanup_private(self):
        if self._private_root:
            try:
                shutil.rmtree(self._private_root, ignore_errors=True)
            finally:
                self._private_root = None


class StrategyProcessPool:
    """Explicit global process/RSS capacity guard for isolated strategies."""

    def __init__(self, isolation: StrategyIsolation, *, parent_incarnation, now_ns):
        if not isinstance(isolation, StrategyIsolation):
            raise TypeError("pool requires StrategyIsolation")
        self.isolation = isolation
        self.budgets = isolation.budgets
        self.parent_incarnation = parent_incarnation
        self.now_ns = now_ns
        self.workers: dict[str, IsolatedStrategyWorker] = {}

    def add(self, worker):
        if worker.strategy_id in self.workers:
            raise ValueError("duplicate isolated strategy worker")
        if len(self.workers) >= self.budgets.max_processes:
            raise ValueError("isolated process capacity exceeded")
        total = sum(item.rss_bytes() or 0 for item in self.workers.values())
        if total + (worker.rss_bytes() or 0) > self.budgets.max_total_rss_bytes:
            raise ValueError("isolated process RSS capacity exceeded")
        self.workers[worker.strategy_id] = worker

    def remove(self, strategy_id):
        worker = self.workers.pop(strategy_id, None)
        if worker is not None:
            worker.kill()

    def close(self):
        for worker in list(self.workers.values()):
            worker.close()
        self.workers.clear()

    async def close_async(self):
        for worker in list(self.workers.values()):
            await worker.close_async()
        self.workers.clear()

    def total_rss_bytes(self):
        return sum(item.rss_bytes() or 0 for item in self.workers.values())

    def report(self):
        return {"processes": len(self.workers), "total_rss_bytes": self.total_rss_bytes(),
                "workers": {sid: {"pid": worker.pid, "alive": worker.alive,
                                  "epoch": worker.epoch, "inflight": worker.inflight,
                                  "queue_bytes": worker.queue_bytes,
                                  "fault": worker.fault,
                                  "rss_bytes": worker.rss_bytes(),
                                  "rss_root_bytes": ((worker.rss_profile() or {}).get("root_bytes")),
                                  "rss_descendant_bytes": ((worker.rss_profile() or {}).get("descendant_bytes"))}
                            for sid, worker in sorted(self.workers.items())}}


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if _CHILD_FLAG not in argv:
        print("runtime.strategy_process is an internal child primitive", file=sys.stderr)
        return 2
    index = argv.index(_CHILD_FLAG)
    try:
        path = argv[index + 1]
    except IndexError:
        return 2
    remainder = argv[index + 2:]
    if not remainder:
        return _child_main(path)
    if len(remainder) != 3 or remainder[0] != "--resource-limits":
        return 2
    try:
        address_space_bytes, process_count = (int(remainder[1]), int(remainder[2]))
    except ValueError:
        return 2
    return _child_main(path, address_space_bytes, process_count)


if __name__ == "__main__":
    raise SystemExit(_main())
