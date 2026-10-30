from __future__ import annotations

import importlib
import inspect
import ast
import hashlib
import re
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from pathlib import Path
from typing import Any, Protocol

from market.hot_path import StrategyDataView
from core.models import BaseMarketEvent, EventType, InstrumentId, Signal, Venue

_MODERN_STRATEGY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_STRATEGY_SOURCE_BYTES = 1_048_576
_MAX_STRATEGY_AST_NODES = 25_000
_MAX_STRATEGY_AST_DEPTH = 128
_MAX_STRATEGY_ID_BYTES = 512


class StrategyInstance(Protocol):
    def on_event(self, event: BaseMarketEvent, data: StrategyDataView) -> list[Signal]: ...


def action_protocol_version(module: ModuleType) -> int | None:
    """Return the explicitly advertised, supported action protocol version."""
    value = getattr(module, "ACTION_PROTOCOL_VERSION", None)
    if value is None:
        return None
    if type(value) is not int or value != 1:
        raise ValueError("Unsupported strategy ACTION_PROTOCOL_VERSION")
    return value


def validate_action_protocol(module: ModuleType, instance: Any) -> bool:
    """Validate opt-in against the actual synchronous strategy instance."""
    version = action_protocol_version(module)
    if version is None:
        return False
    hook = getattr(instance, "on_order_event", None)
    if not callable(hook) or inspect.iscoroutinefunction(hook):
        raise TypeError("ACTION_PROTOCOL_VERSION=1 requires synchronous on_order_event(event, context)")
    return True


@dataclass(frozen=True)
class CompiledSubscription:
    instrument_key: str | None = None
    venue: Venue | None = None
    symbol: str | None = None
    event_type: EventType | None = None

    def matches_instrument(self, instrument: InstrumentId) -> bool:
        return ((self.instrument_key is None or self.instrument_key == instrument.key)
                and (self.venue is None or self.venue == instrument.venue)
                and (self.symbol is None or self.symbol == instrument.std_symbol))

    def matches(self, event: BaseMarketEvent) -> bool:
        return self.matches_instrument(event.instrument) and (
            self.event_type is None or self.event_type == event.event_type)


def compile_subscriptions(values: list[Any]) -> tuple[CompiledSubscription, ...]:
    out = []
    allowed = {"instrument_key", "venue", "symbol", "event_type"}
    for value in values:
        if isinstance(value, str) and value:
            out.append(CompiledSubscription(instrument_key=value))
        elif isinstance(value, dict) and value and set(value) <= allowed:
            key, symbol = value.get("instrument_key"), value.get("symbol")
            if key is not None and (not isinstance(key, str) or not key):
                raise ValueError("instrument_key must be a nonempty exact key")
            if symbol is not None and (not isinstance(symbol, str) or not symbol.strip()):
                raise ValueError("symbol must be nonempty")
            out.append(CompiledSubscription(
                key, Venue(value["venue"]) if "venue" in value else None,
                symbol.strip().upper() if symbol is not None else None,
                EventType(value["event_type"]) if "event_type" in value else None))
        else:
            raise ValueError(f"Unsupported strategy subscription: {value!r}")
    return tuple(out)


def compile_warmup(values: dict[str, Any]) -> int:
    if set(values) - {"bars"}:
        raise ValueError("Only explicit closed-bar count warmup is supported")
    bars = values.get("bars", 0)
    if isinstance(bars, bool) or not isinstance(bars, int) or bars < 0:
        raise ValueError("Warmup bars must be a nonnegative integer")
    return bars


@dataclass(frozen=True)
class StrategyModule:
    module: ModuleType
    strategy_id: str
    subscriptions: list[Any]
    warmup: dict[str, Any]

    def create(self) -> StrategyInstance:
        strategy = self.module.create_strategy()
        if not hasattr(strategy, "on_event"):
            raise TypeError(f"Strategy {self.strategy_id} must implement on_event(event, data)")
        validate_action_protocol(self.module, strategy)
        return strategy


def _canonical_module_name(module_name: str) -> str:
    """Map supported spellings to the sole modern event-strategy namespace."""
    if not isinstance(module_name, str):
        raise TypeError("Strategy module name must be a string")
    name = module_name.strip()
    if not name or "\x00" in name:
        raise ValueError("Strategy module name must be nonempty")
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", name):
        raise ValueError(f"Absolute strategy module paths are not allowed: {module_name!r}")
    normalized = name.replace("\\", "/")
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"Strategy module path traversal is not allowed: {module_name!r}")
    dotted = ".".join(parts)
    if dotted.startswith("vibetrading.strategies."):
        relative = dotted[len("vibetrading.strategies."):]
    elif dotted.startswith("strategies."):
        relative = dotted[len("strategies."):]
    elif "." not in dotted:
        relative = dotted
    else:
        raise ValueError(f"Strategy module namespace is not allowed: {module_name!r}")
    components = relative.split(".")
    if not components or any(not _MODERN_STRATEGY_NAME.fullmatch(part) for part in components):
        raise ValueError(f"Invalid modern strategy module name: {module_name!r}")
    if components[0] in {"legacy", "research"}:
        raise ValueError("Legacy candle and research modules are not modern event strategies")
    return f"strategies.{relative}"


def _read_bounded_strategy_source(path: Path) -> bytes:
    """Read one bounded source snapshot, rejecting oversized files before parsing."""
    import os
    import stat

    with path.open("rb") as source_file:
        metadata = os.fstat(source_file.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Strategy source must be a regular file")
        if metadata.st_size > _MAX_STRATEGY_SOURCE_BYTES:
            raise ValueError("Strategy source exceeds the bounded byte limit")
        source_bytes = source_file.read(_MAX_STRATEGY_SOURCE_BYTES + 1)
    if len(source_bytes) > _MAX_STRATEGY_SOURCE_BYTES:
        raise ValueError("Strategy source exceeds the bounded byte limit")
    return source_bytes


def _parse_bounded_strategy_source(source_bytes: bytes, filename: str) -> ast.Module:
    if len(source_bytes) > _MAX_STRATEGY_SOURCE_BYTES:
        raise ValueError("Strategy source exceeds the bounded byte limit")
    try:
        source = source_bytes.decode("utf-8")
        tree = ast.parse(source, filename=filename)
    except (UnicodeDecodeError, SyntaxError, RecursionError, MemoryError) as exc:
        raise ValueError("Strategy source is not bounded valid UTF-8 Python") from exc

    # Iterative DFS bounds both parser output complexity and recursion depth
    # before any manifest expression is passed to literal_eval.
    nodes = 0
    pending = [(iter((tree,)), -1)]
    while pending:
        siblings, parent_depth = pending[-1]
        try:
            node = next(siblings)
        except StopIteration:
            pending.pop()
            continue
        depth = parent_depth + 1
        if depth > _MAX_STRATEGY_AST_DEPTH:
            raise ValueError("Strategy AST exceeds the bounded nesting depth")
        nodes += 1
        if nodes > _MAX_STRATEGY_AST_NODES:
            raise ValueError("Strategy AST exceeds the bounded node count")
        pending.append((iter(ast.iter_child_nodes(node)), depth))
    return tree


def load_strategy(module_name: str) -> StrategyModule:
    module_name = _canonical_module_name(module_name)
    module = importlib.import_module(module_name)
    missing = [name for name in ("STRATEGY_ID", "SUBSCRIPTIONS", "WARMUP", "create_strategy") if not hasattr(module, name)]
    if missing:
        raise ValueError(f"{module_name} does not satisfy the new strategy contract. Missing: {', '.join(missing)}")
    subscriptions = getattr(module, "SUBSCRIPTIONS")
    if not isinstance(subscriptions, list):
        raise TypeError(f"{module_name}.SUBSCRIPTIONS must be a list")
    warmup = getattr(module, "WARMUP")
    if not isinstance(warmup, dict):
        raise TypeError(f"{module_name}.WARMUP must be a dict")
    return StrategyModule(
        module=module,
        strategy_id=str(getattr(module, "STRATEGY_ID")),
        subscriptions=subscriptions,
        warmup=warmup,
    )


def load_strategy_manifest(module_name: str, source_roots) -> StrategyModule:
    """Read only literal plugin identity metadata without importing plugin code.

    Used by the optional isolated runtime. The plugin is imported, constructed,
    configured and validated only in its confined child. Roots are explicit and
    the selected source bytes are pinned for the child handshake.
    """
    canonical = _canonical_module_name(module_name)
    relative = canonical.removeprefix("strategies.").replace(".", "/") + ".py"
    roots = tuple(Path(root).resolve(strict=True) for root in source_roots)
    candidates = []
    for root in roots:
        candidates.extend((root / "strategies" / relative,
                          root / "src" / "strategies" / relative,
                          root / relative))
    path = next((item.resolve() for item in candidates if item.is_file()
                 and any(item.resolve() == root or root in item.resolve().parents
                         for root in roots)), None)
    if path is None:
        raise ValueError("Strategy source is not found beneath an explicit source root")
    source_bytes = _read_bounded_strategy_source(path)
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    tree = _parse_bounded_strategy_source(source_bytes, str(path))
    values = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value_node = node.value
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {
                        "STRATEGY_ID", "SUBSCRIPTIONS", "WARMUP", "ACTION_PROTOCOL_VERSION"}:
                    try:
                        values[target.id] = ast.literal_eval(value_node)
                    except (ValueError, TypeError) as exc:
                        raise ValueError("Isolated strategy manifest fields must be literal constants") from exc
    if not all(name in values for name in ("STRATEGY_ID", "SUBSCRIPTIONS", "WARMUP")):
        raise ValueError("Isolated strategy source lacks static identity/subscription/warmup manifest")
    strategy_id = values["STRATEGY_ID"]
    if (type(strategy_id) is not str or not strategy_id.strip()
            or len(strategy_id.encode("utf-8")) > _MAX_STRATEGY_ID_BYTES):
        raise ValueError("Isolated strategy STRATEGY_ID must be a bounded literal string")
    if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == "create_strategy" for node in tree.body):
        raise ValueError("Isolated strategy source lacks create_strategy")
    feedback = any(isinstance(node, ast.ClassDef) and any(
        isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
        and member.name == "on_order_event" for member in node.body) for node in tree.body)
    values["HAS_ORDER_FEEDBACK"] = feedback
    metadata = SimpleNamespace(__name__=canonical, __file__=str(path),
                              __cap04_static_manifest__=True,
                              __cap04_source_sha256__=source_sha256, **values)
    return StrategyModule(metadata, strategy_id,
                          values["SUBSCRIPTIONS"], values["WARMUP"])


def source_has_order_feedback(path: str) -> bool:
    """Check for a literal class method declaration without executing source."""
    source_path = Path(path)
    tree = _parse_bounded_strategy_source(
        _read_bounded_strategy_source(source_path), str(source_path))
    return any(isinstance(node, ast.ClassDef) and any(
        isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
        and member.name == "on_order_event" for member in node.body) for node in tree.body)
