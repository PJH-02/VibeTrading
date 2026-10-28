"""Shared pure ETF-family plugin helpers, plans and source-owned policy.

This module is deliberately not a strategy engine or registry.  The three real
strategy files own their decisions through the existing frozen helpers; this
module only provides the common contract plumbing, native-history validation,
paper plan construction and the small amount of economics already owned by the
Kiwoom ETF family.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from functools import lru_cache
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from core.models import (
    AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Signal,
    SignalAction, Venue,
)
from execution.execution_ledger import AccountScope
from execution.trading_contracts import (ActionKind, ExecutionAction, StrategyActionPlan,
    TradingMode)
from ._intraday import intraday_decision
from .etf_reference import MARKETS, weights

D = Decimal
WARMUP = {"bars": 0}
FOLLOWUP_TTL_NS = 90_000_000_000
CANCEL_AFTER_NS = 5_000_000_000
SESSION_DEADLINES = {"C3": "15:20:05", "KP": "15:19:55", "KD": "15:19:55"}
ACCOUNT_SCOPES = {"069500": "mock", "229200": "mock",
                  "122630": "mock2", "251340": "mock2"}
STRATEGY_MARKETS = {"C3": "KOSPI200", "KP": "KOSPI200", "KD": "KOSDAQ150"}
STRATEGY_SIGNALS = {"C3": "069500", "KP": "069500", "KD": "229200"}
_MODULE_NAME = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")
CONFIG_FIELDS = frozenset({
    "day", "history_root", "mode", "job_id", "strategy", "config_version",
    "account", "account_scope", "symbols", "execution_venue", "reference_venue",
    "history_manifest_sha256",
})
ACCOUNT_FIELDS = frozenset({"venue", "account", "product", "environment"})
STATE_FIELDS = frozenset({"day", "entries", "direction", "safety_stop",
                          "exit_pending", "first_open", "followups", "decision_complete"})
PREVIOUS_STATE_FIELDS = frozenset({"day", "entries", "direction", "safety_stop",
                                   "exit_pending", "first_open", "followups"})
LEGACY_STATE_FIELDS = frozenset({"day", "entries", "direction", "safety_stop",
                                 "exit_pending"})
FOLLOWUP_FIELDS = frozenset({"symbol", "side", "quantity", "filled", "state",
                             "created_ns", "cancel_emitted", "replacement_sent"})


def kis_order_event_plan(strategy: str, event, context):
    """Return the original ETF family's explicit KIS follow-up choice.

    `context.execution` is a frozen, credential-free fact envelope supplied by
    the existing KIS owner. It contains `order`, `book`, `quote`, `buying_power`,
    `account_identity`, `route`, `mode`, and `config_version`. This function
    never talks to a broker and never changes a factual quantity.
    """
    from core.models import AssetClass, InstrumentId, OrderDestination, OrderIntent, Side, Venue
    facts = context.execution
    order, book = facts.get('order'), facts.get('book')
    quote, power = facts.get('quote'), facts.get('buying_power')
    if not all(isinstance(value, Mapping) for value in (order, book, quote, power)):
        raise StrategyUnsupported('KIS order callback facts are incomplete')
    if (event.strategy_id != strategy or order.get('strategy') != strategy
            or order.get('status') != event.status or order.get('intent_id') != event.order_id):
        raise StrategyUnsupported('KIS order callback facts differ from the original strategy event')
    if facts.get('mode') not in {'mock', 'mock2'} or facts.get('route') != order.get('account_scope'):
        raise StrategyUnsupported('KIS callback route is not the original owned mock account')
    if order.get('account_scope') != account_scope_for(str(order.get('symbol', ''))):
        raise StrategyUnsupported('KIS callback symbol differs from its original mock route')
    scope = AccountScope('kis', str(facts.get('account_identity')), 'cash', str(facts.get('mode')))
    if event.account_scope != scope:
        raise StrategyUnsupported('KIS callback event account scope differs from the owned source scope')
    try:
        quantity_decimal = D(str(order['quantity']))
        order_filled = D(str(order.get('current_filled', order.get('filled', event.filled_quantity))))
        event_quantity = D(str(event.quantity))
        filled_decimal = D(str(event.filled_quantity))
        remaining_decimal = D(str(event.remaining_quantity))
    except (KeyError, TypeError, ValueError) as exc:
        raise StrategyUnsupported('KIS callback quantity facts are malformed') from exc
    if (any(not value.is_finite() for value in
            (quantity_decimal, order_filled, event_quantity, filled_decimal, remaining_decimal))
            or quantity_decimal <= 0 or quantity_decimal != quantity_decimal.to_integral_value()
            or event_quantity != quantity_decimal or order_filled < filled_decimal
            or any(value != value.to_integral_value() for value in
                   (order_filled, filled_decimal, remaining_decimal))
            or order_filled < 0 or order_filled > quantity_decimal
            or remaining_decimal != quantity_decimal - filled_decimal):
        raise StrategyUnsupported('KIS callback quantity facts are inconsistent')
    quantity, filled, remaining_factual = int(quantity_decimal), int(filled_decimal), int(remaining_decimal)
    now_ns = context.now_ns
    if isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns <= 0:
        raise StrategyUnsupported("KIS callback requires the owner's positive observation clock")
    original_meta = order.get('metadata')
    if not isinstance(original_meta, Mapping):
        raise StrategyUnsupported('KIS callback is missing the immutable original intent metadata')
    expiry = original_meta.get('valid_until_ts_ns')
    if expiry is None and type(order.get('created_ns')) is int:
        # Existing unguarded owner fixtures have no source TTL; retain the
        # original KIS five-second/90-second compatibility window.
        expiry = int(order['created_ns']) + FOLLOWUP_TTL_NS
    if type(expiry) is not int or expiry <= now_ns:
        return StrategyActionPlan('plan:' + hashlib.sha256((event.event_id + ':expired').encode()).hexdigest(),
                                  strategy, str(facts.get('config_version') or 'kis-source-v1'),
                                  now_ns, ())
    status = str(event.status).upper()
    current_status = str(order.get('current_status', 'pending')).upper()
    if (current_status in {'UNKNOWN', 'FILLED', 'REJECTED', 'EXPIRED'}
            or (current_status == 'COMPLETE' and order_filled >= quantity_decimal)):
        return StrategyActionPlan('plan:' + hashlib.sha256((event.event_id + ':current-terminal').encode()).hexdigest(),
                                  strategy, str(facts.get('config_version') or 'kis-source-v1'),
                                  now_ns, ())
    created = order.get('accepted_ns') or order.get('created_ns')
    if status in {'UNKNOWN', 'REJECTED', 'FILLED', 'EXPIRED'}:
        return StrategyActionPlan('plan:' + hashlib.sha256((event.event_id + ':none').encode()).hexdigest(), strategy,
                                  str(facts.get('config_version') or 'kis-source-v1'),
                                  now_ns, ())
    if status == 'CANCEL_PENDING':
        return StrategyActionPlan('plan:' + hashlib.sha256((event.event_id + ':pending').encode()).hexdigest(), strategy,
                                  str(facts.get('config_version') or 'kis-source-v1'), now_ns, ())
    actions = ()
    due_order = {'status': 'pending', 'order_type': order.get('order_type'),
                 'accepted_ns': order.get('accepted_ns'), 'created_ns': order.get('created_ns'),
                 'cancel_requested': order.get('cancel_requested')}
    if status in {'ACCEPTED', 'PARTIAL_FILL'} and order.get('order_type') == 'limit':
        if followup_due(due_order, now_ns) and not order.get('cancel_requested'):
            digest = hashlib.sha256((event.event_id + ':cancel').encode()).hexdigest()
            actions = (ExecutionAction(action_id='action:' + digest, action=ActionKind.CANCEL,
                scope=scope, strategy_id=strategy,
                config_version=str(facts.get('config_version') or 'kis-source-v1'),
                mode=TradingMode(scope.environment), expiry_ns=expiry,
                target_order_id=str(order['intent_id'])),)
    elif status == 'CANCELLED' and order.get('cancel_confirmed') is True:
        intent = kis_market_remainder_intent(strategy, event, context, expiry=expiry)
        if intent is not None:
            digest = hashlib.sha256((event.event_id + ':market-remainder').encode()).hexdigest()
            actions = (ExecutionAction(action_id='action:' + digest, action=ActionKind.NEW,
                scope=scope, strategy_id=strategy,
                config_version=str(facts.get('config_version') or 'kis-source-v1'),
                mode=TradingMode(scope.environment), expiry_ns=expiry,
                intent=intent, reduce_only=intent.side == Side.SELL, time_in_force='IOC'),)
    return StrategyActionPlan('plan:' + hashlib.sha256((event.event_id + ':plan').encode()).hexdigest(), strategy,
        str(facts.get('config_version') or 'kis-source-v1'), now_ns, actions)


def kis_market_remainder_intent(strategy: str, event, context, *, expiry=None):
    """Shared original-source sizing used by callback and legacy owner adapter."""
    facts = context.execution
    order, book = facts.get('order'), facts.get('book')
    power = facts.get('buying_power')
    if not all(isinstance(value, Mapping) for value in (order, book, power)):
        raise StrategyUnsupported('KIS remainder facts are incomplete')
    quantity = int(order.get('quantity', 0))
    event_filled = int(event.filled_quantity)
    current_filled = int(order.get('current_filled', order.get('filled', event_filled)))
    remaining = int(order.get('current_remaining', quantity-current_filled))
    if (quantity <= 0 or current_filled < event_filled or current_filled > quantity
            or remaining != quantity-current_filled or remaining <= 0):
        raise StrategyUnsupported('KIS confirmed-cancel callback current facts are inconsistent')
    side = Side(str(order['side']))
    symbol = str(order['symbol'])
    if side == Side.SELL:
        remaining = min(remaining, int(book.get('positions', {}).get(symbol, 0)))
    else:
        reserve = D(str(power.get('market_reserve_price', 0)))
        available = D(str(book.get('cash', 0))) - D(str(book.get('reserved', 0)))
        cap_decimal = D(str(power.get('max_buy_qty', 0)))
        if (not reserve.is_finite() or reserve <= 0 or not available.is_finite()
                or not cap_decimal.is_finite() or cap_decimal < 0
                or cap_decimal != cap_decimal.to_integral_value()):
            raise StrategyUnsupported('KIS market-buy reservation price/capacity is invalid')
        with localcontext() as decimal_context:
            decimal_context.prec = 128
            affordable = int(available / (reserve * D('1.005')))
        remaining = min(remaining, int(cap_decimal), affordable)
    if remaining <= 0 or order.get('replacement_id'):
        return None
    now_ns = context.now_ns
    original_meta = order.get('metadata') or {}
    if expiry is None:
        expiry = original_meta.get('valid_until_ts_ns')
        if expiry is None and type(order.get('created_ns')) is int:
            expiry = int(order['created_ns']) + FOLLOWUP_TTL_NS
    key = hashlib.sha256((str(order['intent_id']) + ':market-remainder').encode()).hexdigest()
    meta = _plain(original_meta)
    meta.update({'account_scope': str(order['account_scope']), 'account_mode': str(facts['mode']),
        'strategy': strategy, 'replacement_for': str(order['intent_id']),
        'reserve_unit_price': str(power.get('market_reserve_price') or order['price']),
        'execution': 'market_after_5_seconds', 'source_followup': True,
        'direction': int(order.get('direction', 0)), 'safety_stop': order.get('safety_stop')})
    quote = facts.get('quote') or {}
    if isinstance(quote, Mapping) and type(quote.get('observed_ns')) is int:
        meta['intent_quote'] = {'bid': str(quote.get('bid')), 'ask': str(quote.get('ask')),
                                'received_ns': quote['observed_ns'], 'intent_ns': now_ns}
    meta['valid_until_ts_ns'] = expiry
    return OrderIntent.model_validate_json(str(order['payload'])).model_copy(update={
        'intent_id': key, 'job_id': strategy, 'signal_id': event.event_id,
        'client_order_id': key[:32], 'quantity': Decimal(remaining),
        'limit_price': None, 'created_ts_ns': now_ns, 'metadata': meta})


class StrategyUnsupported(ValueError):
    """The configured native strategy input cannot support a real decision."""


def module_for(strategy: str):
    """Resolve one optional sibling lazily; removing another file is harmless."""
    if not isinstance(strategy, str) or not _MODULE_NAME.fullmatch(strategy):
        raise ValueError(f"unsupported ETF strategy {strategy!r}")
    name = f"strategies.kr.{strategy.lower()}"
    module = importlib.import_module(name)
    if getattr(module, "STRATEGY_ID", None) != strategy:
        raise ValueError(f"unsupported ETF strategy {strategy!r}")
    return module


def intraday_decision_for(strategy: str, feature: dict, book: dict) -> tuple[int, float, str]:
    return module_for(strategy).decide(feature, book)


def account_scope_for(symbol: str) -> str:
    return ACCOUNT_SCOPES.get(str(symbol), "mock")


def market_for(strategy: str) -> str:
    return STRATEGY_MARKETS[strategy]


def signal_symbol_for(strategy: str) -> str:
    return STRATEGY_SIGNALS[strategy]


def session_exit_deadline(strategy: str) -> str:
    return SESSION_DEADLINES[strategy]


def followup_due(order: Mapping[str, Any], now_ns: int) -> bool:
    """Shared 5s cancel trigger consumed by the legacy journal owner."""
    return (order.get("status") == "pending" and order.get("order_type") == "limit"
            and now_ns - (order.get("accepted_ns") or order.get("created_ns") or 0)
            >= CANCEL_AFTER_NS and not order.get("cancel_requested"))


def session_action(strategy: str, timestamp: pd.Timestamp) -> str | None:
    """Source-owned KRX session policy shared by the live timer and Paper."""
    clock = timestamp.strftime("%H:%M:%S")
    if "09:00:00" <= clock < "09:05:00":
        return "overnight_recovery"
    if session_exit_deadline(strategy) <= clock <= "15:30:00":
        return "session_exit"
    return None


def limit_price(side: Side | str, bid, ask) -> Decimal:
    """The original ETF family's explicit five-won protective limit."""
    side = Side(side)
    bid, ask = D(str(bid)), D(str(ask))
    if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask <= 0 or bid > ask:
        raise ValueError("ETF quotes must be finite and ordered")
    if side == Side.BUY:
        return ((ask + D(5)) / D(5)).to_integral_value(rounding=ROUND_CEILING) * D(5)
    return (max(D(5), bid - D(5)) / D(5)).to_integral_value(rounding=ROUND_FLOOR) * D(5)


def entry_targets(strategy: str, direction: int, leverage: float) -> dict[str, float]:
    if direction not in (-1, 1):
        raise ValueError("entry direction must be long or short")
    return weights(direction, float(leverage), market_for(strategy))


def plan_entry_quantities(capital, targets: Mapping[str, float],
                          prices: Mapping[str, Decimal]) -> list[tuple[str, int]]:
    """Exact frozen per-vehicle sizing; zero/sub-lot vehicles are skipped.

    The frozen live entry iterates every positive-weight vehicle and silently
    omits a zero quantity.  Capital exhaustion is therefore an empty plan, not a
    strategy failure, so another affordable vehicle can still be submitted.
    """
    capital = D(str(capital))
    if not capital.is_finite():
        raise StrategyUnsupported("paper KRW capital is not finite")
    planned = []
    if capital <= 0:
        return planned
    for symbol, weight in targets.items():
        weight = D(str(weight))
        if weight <= 0:
            continue
        price = D(str(prices[symbol]))
        if not price.is_finite() or price <= 0:
            raise StrategyUnsupported(f"missing explicit protective price for {symbol}")
        quantity = int(capital * weight / (price * D("1.005")))
        if quantity <= 0:
            continue
        planned.append((symbol, quantity))
    return planned


def intraday_stop(strategy: str, bid, ask, feature: Mapping[str, Any], direction: int) -> float | None:
    if strategy != "KD":
        return None
    stop = float((D(str(bid)) + D(str(ask))) / D(2)) * (
        1 - int(direction) * .25 * float(feature["prior_median_range20"]))
    if not math.isfinite(stop):
        raise StrategyUnsupported("KD safety stop is not finite")
    return stop


def can_signal(strategy: str, timestamp: pd.Timestamp) -> bool:
    clock = timestamp.strftime("%H:%M")
    if strategy == "C3":
        return clock == "09:01"
    return clock <= "15:00" and timestamp.minute in {0, 30}


def _event_ts_ns(event) -> int:
    value = getattr(event, "close_ts_ns", None)
    if value is None:
        value = getattr(event, "source_ts_ns", None) or getattr(event, "receive_ts_ns", None)
    if not isinstance(value, int) or value <= 0:
        raise StrategyUnsupported("ETF event has no factual timestamp")
    return value


def _timestamp(event) -> pd.Timestamp:
    return pd.Timestamp(_event_ts_ns(event), unit="ns", tz="UTC").tz_convert(
        "Asia/Seoul").tz_localize(None)


def feature_identity(strategy: str, timestamp: pd.Timestamp, row: Mapping[str, Any]) -> str:
    payload = {str(key): (value.item() if hasattr(value, "item") else value)
               for key, value in row.items()}
    return hashlib.sha256(json.dumps(
        [strategy, str(timestamp), payload], sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def intraday_source_decision(strategy: str, history, timestamp: pd.Timestamp,
                             frame: pd.DataFrame, book: Mapping[str, Any],
                             *, bid=None, ask=None) -> dict:
    """One pure KP/KD family decision used by both live and Paper callbacks."""
    positions = dict(book.get("positions") or {})
    direction = int(book.get("direction") or 0)
    stop = book.get("safety_stop")
    # KD owns a minute-by-minute safety stop; it precedes every 30m gate.
    if strategy == "KD" and positions and direction in (-1, 1) and stop is not None:
        selected = frame[frame.timestamp == timestamp] if not frame.empty else frame
        if not selected.empty:
            price = float(selected.iloc[-1]["close"])
            stop = float(stop)
            if ((direction == 1 and price <= stop)
                    or (direction == -1 and price >= stop)):
                return {"action": "paper_stop", "direction": 0, "leverage": 0.,
                        "stop": None, "feature": None, "identity": feature_identity(
                            strategy, timestamp, {"stop": stop, "price": price}),
                        "detail": {"stop": stop, "price": price}}
    decision_time = (timestamp.strftime("%H:%M") <= "15:00"
                     and timestamp.minute in {0, 30})
    if not decision_time:
        return {"action": "no_entry", "direction": 0, "leverage": 0.,
                "stop": None, "feature": None, "identity": None, "detail": {}}
    symbol = signal_symbol_for(strategy)
    features = history.intraday_features(symbol, frame)
    if features.empty:
        return {"action": "no_entry", "direction": 0, "leverage": 0.,
                "stop": None, "feature": None, "identity": None, "detail": {}}
    selected = features[features.timestamp == timestamp]
    if selected.empty:
        return {"action": "no_entry", "direction": 0, "leverage": 0.,
                "stop": None, "feature": None, "identity": None, "detail": {}}
    row = {key: (value.item() if hasattr(value, "item") else value)
           for key, value in selected.iloc[0].to_dict().items()}
    if not bool(row.get("usable")):
        return {"action": "no_entry", "direction": 0, "leverage": 0.,
                "stop": None, "feature": None, "identity": None, "detail": {}}
    entry_direction, leverage, action = intraday_decision_for(strategy, row, dict(book))
    stop_value = None
    if action == "entry" and strategy == "KD":
        if bid is None or ask is None:
            raise StrategyUnsupported("KD entry requires a factual executable quote")
        stop_value = intraday_stop(strategy, bid, ask, row, entry_direction)
    return {"action": action, "direction": int(entry_direction),
            "leverage": float(leverage), "stop": stop_value, "feature": row,
            "identity": feature_identity(strategy, timestamp, row),
            "detail": {key: row[key] for key in
                       ("noise", "vol14", "previous_close", "vwap_used")
                       if key in row}}


def _plain(value):
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("ETF plugin config must contain only JSON data")


def validate_config(config, strategy: str, *, require_history: bool = True) -> dict:
    if not isinstance(config, Mapping):
        raise ValueError("ETF plugin config must be an explicit mapping")
    cfg = _plain(config)
    fields = set(cfg)
    allowed = set(CONFIG_FIELDS)
    if strategy == "C3":
        allowed.add("index_key")
    if not CONFIG_FIELDS <= fields or fields - allowed:
        raise ValueError("ETF plugin config has missing or unknown fields")
    if cfg["strategy"] != strategy:
        raise ValueError("ETF plugin config strategy differs from the module")
    if cfg["mode"] != "paper":
        raise StrategyUnsupported("ETF file plugins currently support explicit PAPER mode only")
    try:
        day = pd.Timestamp(cfg["day"]).strftime("%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError("ETF plugin day must be a calendar date") from exc
    if day != cfg["day"]:
        raise ValueError("ETF plugin day must use YYYY-MM-DD")
    for name in ("job_id", "config_version"):
        value = cfg[name]
        if not isinstance(value, str) or not value.strip() or len(value.encode()) > 512:
            raise ValueError(f"ETF plugin {name} must be a bounded explicit identity")
    history_root = cfg["history_root"]
    if not isinstance(history_root, str) or not Path(history_root).is_absolute():
        raise ValueError("ETF plugin history_root must be an absolute path")
    if require_history and not Path(history_root).is_dir():
        raise StrategyUnsupported("ETF native history_root does not exist")
    digest = cfg.get("history_manifest_sha256")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise ValueError("ETF history_manifest_sha256 must be lowercase SHA-256")
    account = cfg["account"]
    if not isinstance(account, dict) or set(account) != ACCOUNT_FIELDS:
        raise ValueError("ETF plugin account binding requires exact scope fields")
    scope = AccountScope(**account)
    if cfg["account_scope"] != scope.key:
        raise ValueError("ETF plugin account_scope differs from its explicit account binding")
    if scope.venue != "kis":
        raise StrategyUnsupported("ETF execution owns only the KIS product account")
    if scope.environment != "paper":
        raise StrategyUnsupported("ETF file plugins require an explicit paper account environment")
    if not scope.account.strip() or not scope.product.strip():
        raise ValueError("ETF plugin account/product binding must be explicit")
    if cfg["execution_venue"] != "kis":
        raise StrategyUnsupported("ETF execution owns only the KIS product account")
    if cfg["reference_venue"] != "kiwoom":
        raise StrategyUnsupported(
            "ETF source-owned signal subscriptions use the kiwoom ETF feed")
    symbols = cfg["symbols"]
    if (not isinstance(symbols, list) or not symbols
            or any(not isinstance(symbol, str) or not symbol for symbol in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("ETF plugin symbols must be a unique nonempty list")
    market = MARKETS[market_for(strategy)]
    required_symbols = {market["signal"], *market["long"], *market["short"]}
    if not required_symbols <= set(symbols):
        raise StrategyUnsupported("ETF plugin symbols do not cover its source-owned vehicles")
    if strategy == "C3":
        index_key = cfg.get("index_key")
        if index_key != "kis:index:KOSPI200":
            raise StrategyUnsupported(
                "C3 index_key must bind the native KRX KOSPI200 index quote")
    return cfg


def execution_symbols(strategy: str) -> tuple[str, ...]:
    market = MARKETS[market_for(strategy)]
    return tuple(dict.fromkeys((market["signal"], *market["long"], *market["short"])))


def load_history(config: Mapping[str, Any], strategy: str):
    from .etf_history import EtfHistory
    try:
        import hashlib
        manifest_path = Path(config["history_root"]) / "manifest.json"
        if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != config["history_manifest_sha256"]:
            raise ValueError("native ETF history manifest digest differs from config")
        return EtfHistory.from_csv(
            Path(config["history_root"]), config["day"],
            symbols=execution_symbols(strategy), include_c3=strategy == "C3")
    except Exception as exc:
        raise StrategyUnsupported(f"native ETF history is unavailable: {exc}") from exc


def blank_state(day: str) -> dict:
    return {"day": day, "entries": 0, "direction": 0,
            "safety_stop": None, "exit_pending": False,
            "first_open": None, "followups": {}, "decision_complete": False}


class FileEtfStrategy:
    """Common lifecycle only; subclasses own their decision callback."""

    STRATEGY_ID = ""

    def __init__(self):
        self.config: dict | None = None
        self.history = None
        self.state = blank_state("")

    def configure(self, config):
        cfg = validate_config(config, self.STRATEGY_ID)
        # Resolve the actual Kiwoom master identity while the plugin is being
        # configured, never from a market-event callback.
        for symbol in execution_symbols(self.STRATEGY_ID):
            _native_feed_instrument(symbol)
        self.config, self.history = cfg, load_history(cfg, self.STRATEGY_ID)
        self.state = blank_state(cfg["day"])
        return self

    def on_order_event(self, event, context):
        """Keep feedback policy in the same original source module."""
        return module_for(self.STRATEGY_ID).on_order_event(event, context, self)


    def validate_registration(self, frozen_facts):
        """Validate source bindings against JSON-only frozen registration facts."""
        cfg = self._ready()
        if not isinstance(frozen_facts, Mapping):
            raise StrategyUnsupported("ETF frozen registration facts are malformed")
        if (frozen_facts.get("job_id") != cfg["job_id"]
                or frozen_facts.get("config_version") != cfg["config_version"]):
            raise StrategyUnsupported("ETF registration job/config binding differs from source config")
        accounts = frozen_facts.get("accounts")
        account = accounts.get(cfg["account_scope"]) if isinstance(accounts, Mapping) else None
        if not isinstance(account, Mapping) or account.get("scope") != cfg["account"]:
            raise StrategyUnsupported("ETF registration account scope differs from source config")
        if account.get("mode") != "paper":
            raise StrategyUnsupported("ETF file plugins require PAPER account registration")
        capabilities = account.get("capabilities")
        if (not isinstance(capabilities, Mapping)
                or capabilities.get("allow_taker_gtc") is not True
                or capabilities.get("allow_market_orders") is not True):
            raise StrategyUnsupported("ETF Paper account lacks required taker GTC/market capabilities")
        required = frozen_facts.get("required_feeds")
        instruments = frozen_facts.get("instruments")
        products = frozen_facts.get("products")
        references = frozen_facts.get("reference_feeds")
        if not all(isinstance(x, Mapping) for x in (instruments, products, references)):
            raise StrategyUnsupported("ETF registration instrument/product bindings are malformed")
        expected_feeds = {_feed_instrument(cfg, symbol).key for symbol in execution_symbols(self.STRATEGY_ID)}
        if self.STRATEGY_ID == "C3":
            expected_feeds.add(cfg["index_key"])
        if not isinstance(required, (list, tuple)) or set(required) != expected_feeds:
            raise StrategyUnsupported("ETF registration required feeds differ from source subscriptions")
        for key in expected_feeds:
            if key not in instruments:
                raise StrategyUnsupported(f"ETF registration instrument missing: {key}")
        for symbol in execution_symbols(self.STRATEGY_ID):
            product_key = _execution_instrument(cfg, symbol).key
            feed_key = _feed_instrument(cfg, symbol).key
            if product_key not in products or references.get(product_key) != feed_key:
                raise StrategyUnsupported("ETF execution product/reference binding differs from source config")
            product = products[product_key]
            if not isinstance(product, Mapping) or product.get("instrument", {}).get("raw_symbol") != symbol:
                raise StrategyUnsupported("ETF execution product has wrong native security identity")
        if self.STRATEGY_ID == "C3":
            index = instruments[cfg["index_key"]]
            if (index.get("venue") != "kis" or index.get("asset_class") != "index"
                    or index.get("raw_symbol") != "2001" or index.get("std_symbol") != "KOSPI200"
                    or index.get("exchange") != "KRX" or index.get("currency") != "KRW"):
                raise StrategyUnsupported("C3 registration index is not the native KIS KOSPI200 feed")
        return True

    def _ready(self):
        if self.config is None or self.history is None:
            raise StrategyUnsupported("ETF plugin was not configured with native history")
        return self.config

    def checkpoint_state(self):
        return dict(self.state)

    def restore_state(self, state):
        fields = set(state) if isinstance(state, Mapping) else set()
        if not isinstance(state, Mapping) or fields not in (
                STATE_FIELDS, PREVIOUS_STATE_FIELDS, LEGACY_STATE_FIELDS):
            raise ValueError("ETF plugin checkpoint state schema mismatch")
        if state.get("day") != (self.config or {}).get("day"):
            raise ValueError("ETF plugin checkpoint day differs from configured day")
        entries = state.get("entries")
        direction = state.get("direction")
        stop = state.get("safety_stop")
        pending = state.get("exit_pending")
        first_open = state.get("first_open")
        decision_complete = state.get("decision_complete", state.get("first_open") is not None)
        followups = state.get("followups", {})
        if isinstance(entries, bool) or not isinstance(entries, int) or entries not in (0, 1):
            raise ValueError("ETF plugin checkpoint entries is invalid")
        if isinstance(direction, bool) or not isinstance(direction, int) or direction not in (-1, 0, 1):
            raise ValueError("ETF plugin checkpoint direction is invalid")
        if stop is not None and (isinstance(stop, bool) or not isinstance(stop, (int, float))
                                 or not math.isfinite(float(stop))):
            raise ValueError("ETF plugin checkpoint stop is invalid")
        if type(pending) is not bool:
            raise ValueError("ETF plugin checkpoint exit_pending is invalid")
        if type(decision_complete) is not bool:
            raise ValueError("ETF plugin checkpoint decision_complete is invalid")
        if first_open is not None and (isinstance(first_open, bool)
                                       or not isinstance(first_open, (int, float))
                                       or not math.isfinite(float(first_open))
                                       or float(first_open) <= 0):
            raise ValueError("ETF plugin checkpoint first_open is invalid")
        if not isinstance(followups, Mapping) or len(followups) > 8:
            raise ValueError("ETF plugin checkpoint followups are invalid")
        clean_followups = {}
        for intent_id, row in followups.items():
            if not isinstance(intent_id, str) or not intent_id:
                raise ValueError("ETF plugin follow-up order identity is invalid")
            if not isinstance(row, Mapping) or set(row) != FOLLOWUP_FIELDS:
                raise ValueError("ETF plugin follow-up order schema mismatch")
            if (not isinstance(row["symbol"], str) or not row["symbol"]
                    or row["side"] not in {"buy", "sell"}
                    or isinstance(row["quantity"], bool) or not isinstance(row["quantity"], int)
                    or row["quantity"] <= 0
                    or isinstance(row["filled"], bool) or not isinstance(row["filled"], int)
                    or row["filled"] < 0
                    or not isinstance(row["state"], str)
                    or isinstance(row["created_ns"], bool)
                    or not isinstance(row["created_ns"], int) or row["created_ns"] <= 0
                    or type(row["cancel_emitted"]) is not bool
                    or type(row["replacement_sent"]) is not bool):
                raise ValueError("ETF plugin follow-up order values are invalid")
            clean_followups[intent_id] = {key: row[key] for key in FOLLOWUP_FIELDS}
        self.state = {"day": state["day"], "entries": entries, "direction": direction,
                      "safety_stop": None if stop is None else float(stop),
                      "exit_pending": pending,
                      "first_open": None if first_open is None else float(first_open),
                      "followups": clean_followups, "decision_complete": decision_complete}
        return self

def file_strategy_order_event(instance: FileEtfStrategy, event, context):
    if context.data is None:
        raise StrategyUnsupported('Paper source callback requires native StrategyDataView')
    cfg = instance._ready()
    facts = context.execution
    if (facts.get('scope') != cfg['account_scope'] or facts.get('mode') != 'paper'
            or facts.get('account_version') is None):
        raise StrategyUnsupported('Paper order callback facts do not match the source account')
    from types import SimpleNamespace
    view = SimpleNamespace(execution={cfg['account_scope']: facts})
    policy_event = SimpleNamespace(event_id=event.event_id, receive_ts_ns=context.now_ns)
    positions = _positions(facts, instance.STRATEGY_ID)
    return _followup_signals(instance, instance.STRATEGY_ID, policy_event,
                             context.data, _facts(view, cfg), positions,
                             allow_replacement=False)


@lru_cache(maxsize=8)
def _native_feed_instrument(symbol: str) -> InstrumentId:
    from feeds.providers.kiwoom import KiwoomCollector, KiwoomSubscription
    instrument = KiwoomCollector.__new__(KiwoomCollector).instrument(
        KiwoomSubscription(symbol=symbol, market="KRX"))
    if (instrument.raw_symbol != symbol or instrument.exchange != "KRX"
            or instrument.std_symbol != f"KIWOOM:KRX:{symbol}"):
        raise StrategyUnsupported("ETF feed instrument differs from native Kiwoom KRX identity")
    return instrument


def _feed_instrument(config: Mapping[str, Any], symbol: str) -> InstrumentId:
    if config.get("reference_venue") != "kiwoom":
        raise StrategyUnsupported("ETF feed instrument is not bound to Kiwoom")
    return _native_feed_instrument(symbol)


def _execution_instrument(config: Mapping[str, Any], symbol: str) -> InstrumentId:
    return InstrumentId(venue=Venue(config["execution_venue"]), asset_class=AssetClass.ETF,
                        raw_symbol=symbol, std_symbol=symbol, currency="KRW", exchange="KRX")


def _facts(view, config: Mapping[str, Any]) -> Mapping[str, Any]:
    execution = getattr(view, "execution", {}) or {}
    facts = execution.get(config["account_scope"])
    if not isinstance(facts, Mapping):
        raise StrategyUnsupported("paper factual execution snapshot is missing")
    if facts.get("mode") != "paper" or facts.get("scope") != config["account_scope"]:
        raise StrategyUnsupported("paper factual execution snapshot has wrong ownership")
    return facts


def _positions(facts: Mapping[str, Any], strategy: str) -> dict[str, Decimal]:
    raw = (facts.get("virtual_positions") or {}).get(strategy, {})
    if not isinstance(raw, Mapping):
        raise StrategyUnsupported("paper virtual positions are malformed")
    positions = {}
    for key, value in raw.items():
        symbol = str(key).rsplit(":", 1)[-1]
        quantity = D(str(value))
        if not quantity.is_finite():
            raise StrategyUnsupported("paper virtual position is not finite")
        if quantity:
            if quantity != quantity.to_integral_value() or quantity < 0:
                raise StrategyUnsupported("paper cash ETF positions must be nonnegative whole shares")
            positions[symbol] = quantity
    return positions


def _active_orders(facts: Mapping[str, Any], strategy: str) -> list[Mapping[str, Any]]:
    rows = facts.get("active_orders") or []
    if not isinstance(rows, (list, tuple)):
        raise StrategyUnsupported("paper active orders are malformed")
    return [row for row in rows if isinstance(row, Mapping) and row.get("strategy") == strategy]


def _capital(facts: Mapping[str, Any]) -> Decimal:
    balances = facts.get("balances") or {}
    reserved = facts.get("reserved") or {}
    if not isinstance(balances, Mapping) or not isinstance(reserved, Mapping):
        raise StrategyUnsupported("paper balances are malformed")
    capital = D(str(balances.get("KRW", "0"))) - D(str(reserved.get("KRW", "0")))
    if not capital.is_finite():
        raise StrategyUnsupported("paper KRW capital is not finite")
    return capital


def _quote(view, feed_key: str) -> tuple[Any, Decimal, Decimal]:
    book = view.latest_book(feed_key)
    if book is None or not book.bids or not book.asks:
        raise StrategyUnsupported(f"fresh full book required for {feed_key}")
    bid = max(level.price for level in book.bids)
    ask = min(level.price for level in book.asks)
    if bid <= 0 or ask <= 0 or bid > ask:
        raise StrategyUnsupported(f"invalid executable book for {feed_key}")
    return book, bid, ask


def _bar_frame(view, feed_key: str, day: str) -> pd.DataFrame:
    rows = []
    for bar in view.closed_bars(feed_key, 1000):
        if bar.trading_date != day:
            continue
        timestamp = pd.Timestamp(bar.close_ts_ns, unit="ns", tz="UTC").tz_convert(
            "Asia/Seoul").tz_localize(None)
        rows.append({"timestamp": timestamp, "date": day, "symbol": bar.instrument.raw_symbol,
                     "source": bar.provider.upper(), "open": float(bar.open),
                     "high": float(bar.high), "low": float(bar.low),
                     "close": float(bar.close), "volume": float(bar.volume),
                     "trading_value": float("nan")})
    return pd.DataFrame(rows).sort_values("timestamp").drop_duplicates("timestamp") if rows else pd.DataFrame()


def _entry_orders(config: Mapping[str, Any], view, facts, targets, direction: int) -> list[dict]:
    capital = _capital(facts)
    prices, quotes = {}, {}
    for symbol, weight in targets.items():
        if float(weight) <= 0:
            continue
        if symbol not in config["symbols"]:
            raise StrategyUnsupported(f"source-owned vehicle {symbol} is absent from config")
        feed_key = _feed_instrument(config, symbol).key
        book, bid, ask = _quote(view, feed_key)
        quotes[symbol] = (book, bid, ask)
        prices[symbol] = limit_price(Side.BUY, bid, ask)
    planned = plan_entry_quantities(capital, targets, prices)
    orders = []
    for symbol, quantity in planned:
        book, bid, ask = quotes[symbol]
        orders.append({
            "kind": "entry", "symbol": symbol, "side": "buy", "quantity": quantity,
            "limit_price": str(prices[symbol]), "reference_price": str((bid + ask) / 2),
            "account_scope": config["account_scope"], "route": account_scope_for(symbol),
            "direction": int(direction), "weight": str(float(targets[symbol])),
            "feed_key": book.instrument.key, "execution": "limit", "post_only": False,
        })
    return orders


def _exit_orders(config: Mapping[str, Any], view, positions: Mapping[str, Decimal],
                 direction: int) -> list[dict]:
    orders = []
    for symbol, quantity in positions.items():
        if symbol not in config["symbols"]:
            raise StrategyUnsupported(f"owned position {symbol} is absent from config")
        feed_key = _feed_instrument(config, symbol).key
        book, bid, ask = _quote(view, feed_key)
        orders.append({
            "kind": "exit", "symbol": symbol, "side": "sell", "quantity": int(quantity),
            "limit_price": str(limit_price(Side.SELL, bid, ask)),
            "reference_price": str((bid + ask) / 2),
            "account_scope": config["account_scope"], "route": account_scope_for(symbol),
            "direction": int(direction), "weight": "0",
            "feed_key": book.instrument.key, "execution": "limit", "post_only": False,
        })
    if not orders and positions:
        raise StrategyUnsupported("owned position has no executable exit order")
    return orders


def _signal(config: Mapping[str, Any], strategy: str, event, data, order: Mapping[str, Any],
            source_id: str, detail: Mapping[str, Any] | None = None) -> Signal:
    kind = order["kind"]
    direction = int(order["direction"])
    if kind == "entry":
        action = SignalAction.ENTER_LONG if direction == 1 else SignalAction.ENTER_SHORT
    elif kind == "cancel":
        action = SignalAction.EXIT_LONG if direction == 1 else SignalAction.EXIT_SHORT
    else:
        action = SignalAction.EXIT_LONG if direction == 1 else SignalAction.EXIT_SHORT
    feed_key = order["feed_key"]
    book = data.latest_book(feed_key)
    if book is None:
        raise StrategyUnsupported(f"reference instrument disappeared for {order['symbol']}")
    plan = {
        "strategy": strategy, "action": kind, "direction": direction,
        "orders": [dict(order)],
    }
    metadata = {
        "strategy": strategy, "job_id": config["job_id"],
        "config_version": config["config_version"],
        "account_scope": config["account_scope"], "plan": plan,
    }
    if detail:
        metadata["detail"] = {key: float(value) if isinstance(value, (int, float)) else value
                              for key, value in detail.items()}
    event_ts_ns = int(getattr(data, "now_ns", _event_ts_ns(event)))
    return Signal(
        strategy_id=strategy, instrument=book.instrument, action=action,
        event_ts_ns=event_ts_ns, reference_price=D(str(order["reference_price"])),
        valid_until_ts_ns=event_ts_ns + FOLLOWUP_TTL_NS,
        source_event_id=source_id, metadata=metadata)


def _sync_actual(instance: FileEtfStrategy, facts, positions, strategy: str,
                 *, recovery: bool = False) -> None:
    state = instance.state
    if positions and not state["entries"]:
        if not recovery:
            raise StrategyUnsupported("execution positions exist without checkpointed entry state")
        long_vehicles = set(MARKETS[market_for(strategy)]["long"])
        short_vehicles = set(MARKETS[market_for(strategy)]["short"])
        if set(positions) <= long_vehicles:
            state["direction"] = 1
        elif set(positions) <= short_vehicles:
            state["direction"] = -1
        else:
            raise StrategyUnsupported("recovered positions contradict the source-owned market")
    if positions:
        if state["direction"] not in (-1, 1):
            raise StrategyUnsupported("execution positions exist without a direction")
        vehicles = MARKETS[market_for(strategy)]["long" if state["direction"] == 1 else "short"]
        if not set(positions) <= set(vehicles):
            raise StrategyUnsupported("execution positions contradict the source-owned direction")
        if strategy == "KD" and state["safety_stop"] is None:
            raise StrategyUnsupported("KD position exists without a checkpointed safety stop")
    if not positions and state["exit_pending"]:
        state.update(direction=0, safety_stop=None, exit_pending=False)
    if positions and _active_orders(facts, strategy) and state["exit_pending"]:
        # A confirmed fill is the only transition that clears the exit.
        pass


def _entry_signals(instance: FileEtfStrategy, strategy: str, event, data,
                   facts, orders) -> list[Signal]:
    return [_signal(instance.config, strategy, event, data, order, event.event_id)
            for order in orders]


def _exit_signals(instance: FileEtfStrategy, strategy: str, event, data,
                  facts, positions) -> list[Signal]:
    if _active_orders(facts, strategy):
        return []
    orders = _exit_orders(instance.config, data, positions, instance.state["direction"])
    return [_signal(instance.config, strategy, event, data, order, event.event_id)
            for order in orders]


def _order_rows(facts: Mapping[str, Any], strategy: str) -> list[Mapping[str, Any]]:
    rows = facts.get("orders") or []
    if not isinstance(rows, (list, tuple)):
        raise StrategyUnsupported("paper order facts are malformed")
    return [row for row in rows if isinstance(row, Mapping)
            and row.get("strategy") == strategy]


def _track_orders(instance: FileEtfStrategy, strategy: str, facts) -> None:
    """Bounded factual order evidence for the 5s cancel/follow-up policy."""
    followups = instance.state["followups"]
    for row in _order_rows(facts, strategy):
        metadata = row.get("metadata")
        plan = metadata.get("plan") if isinstance(metadata, Mapping) else None
        if not isinstance(plan, Mapping):
            continue
        intent_id = row.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            continue
        if plan.get("followup") or plan.get("kind") not in {"entry", "exit"}:
            continue
        state = str(row.get("state") or "")
        if intent_id in followups:
            followups[intent_id]["filled"] = int(D(str(row.get("filled", "0"))))
            followups[intent_id]["state"] = state
            continue
        if state not in {"OPEN", "CANCEL_PENDING", "UNKNOWN"}:
            continue
        quantity = int(D(str(row.get("quantity", "0"))))
        created_ns = row.get("created_ns")
        if quantity <= 0 or not isinstance(created_ns, int) or created_ns <= 0:
            continue
        if len(followups) >= 8:
            raise StrategyUnsupported("bounded ETF follow-up capacity exceeded")
        followups[intent_id] = {
            "symbol": str(row.get("raw_symbol") or plan.get("symbol") or ""),
            "side": str(row.get("side") or ""),
            "quantity": quantity,
            "filled": int(D(str(row.get("filled", "0")))),
            "state": state,
            "created_ns": created_ns,
            "cancel_emitted": False,
            "replacement_sent": False,
        }


def _followup_order(config, data, strategy, row, direction) -> dict:
    symbol = row["symbol"]
    if symbol not in config["symbols"]:
        raise StrategyUnsupported(f"follow-up vehicle {symbol} is absent from config")
    feed_key = _feed_instrument(config, symbol).key
    book, bid, ask = _quote(data, feed_key)
    side = Side(row["side"])
    remaining = int(row["quantity"]) - int(row["filled"])
    if remaining <= 0:
        raise StrategyUnsupported("follow-up has no factual remainder")
    reserve = limit_price(side, bid, ask)
    return {
        "kind": "entry" if side == Side.BUY else "exit", "symbol": symbol,
        "side": side.value, "quantity": remaining, "limit_price": None,
        "reserve_unit_price": str(reserve),
        "reference_price": str((bid + ask) / 2),
        "account_scope": config["account_scope"], "route": account_scope_for(symbol),
        "direction": int(direction), "weight": "0", "feed_key": book.instrument.key,
        "execution": "market_remainder", "followup": True,
        "replaces": row["intent_id"] if "intent_id" in row else None,
    }


def _followup_signals(instance: FileEtfStrategy, strategy: str, event, data,
                      facts, positions, *, allow_replacement: bool = True) -> list[Signal]:
    """At most one bounded CANCEL/NEW per callback after factual confirmation."""
    _track_orders(instance, strategy, facts)
    followups = instance.state["followups"]
    now_ns = int(getattr(data, "now_ns", _event_ts_ns(event)))
    signals = []
    for intent_id, row in list(followups.items()):
        state = row["state"]
        if state in {"FILLED", "REJECTED", "EXPIRED"} or (
                state == "CANCELED" and row["replacement_sent"]):
            followups.pop(intent_id, None)
            continue
        if state in {"UNKNOWN", "CANCEL_PENDING", "CANCELED"}:
            continue
        if state != "OPEN":
            continue
        if not row["cancel_emitted"]:
            if now_ns - row["created_ns"] < CANCEL_AFTER_NS:
                continue
            symbol = row["symbol"]
            feed_key = _feed_instrument(instance.config, symbol).key
            book = data.latest_book(feed_key)
            if book is None:
                raise StrategyUnsupported(f"reference instrument disappeared for {symbol}")
            order = {"kind": "cancel", "target_order_id": intent_id,
                     "symbol": symbol, "side": row["side"],
                     "quantity": row["quantity"], "direction": instance.state["direction"],
                     "reference_price": str((max(level.price for level in book.bids)
                                             + min(level.price for level in book.asks)) / 2),
                     "account_scope": instance.config["account_scope"],
                     "route": account_scope_for(symbol),
                     "feed_key": book.instrument.key, "execution": "cancel"}
            row["cancel_emitted"] = True
            signals.append(_signal(instance.config, strategy, event, data, order,
                                   event.event_id))
            break
    # Paper feedback may precede the next fresh reference-book event. Keep the
    # confirmed fact, then let ordinary source on_event size the replacement.
    if not allow_replacement:
        return signals
    # A terminal CANCELED fact is the only proof for one market remainder.
    for intent_id, row in list(followups.items()):
        if row["state"] != "CANCELED" or row["replacement_sent"]:
            continue
        row["replacement_sent"] = True
        order = _followup_order(instance.config, data, strategy, row,
                                instance.state["direction"])
        if order["side"] == "sell":
            owned = int(positions.get(order["symbol"], 0))
            if owned <= 0:
                followups.pop(intent_id, None)
                continue
            order["quantity"] = min(order["quantity"], owned)
        else:
            reserve = D(order["reserve_unit_price"])
            affordable = int(_capital(facts) / (reserve * D("1.005")))
            if affordable <= 0:
                followups.pop(intent_id, None)
                continue
            order["quantity"] = min(order["quantity"], affordable)
        order["replaces"] = intent_id
        signals.append(_signal(instance.config, strategy, event, data, order,
                               event.event_id))
        break
    return signals


def _first_open(data, feed_key: str, day: str) -> float | None:
    """The actual first 09:01 completed bar open; never 09:02/last trade."""
    for bar in data.closed_bars(feed_key, 1000):
        if bar.trading_date != day:
            continue
        timestamp = pd.Timestamp(bar.close_ts_ns, unit="ns", tz="UTC").tz_convert(
            "Asia/Seoul").tz_localize(None)
        if timestamp.strftime("%H:%M") == "09:01":
            if bar.volume <= 0:
                return None
            value = float(bar.open)
            return value if math.isfinite(value) and value > 0 else None
    return None


def c3_on_event(instance: FileEtfStrategy, event, data) -> list[Signal]:
    config = instance._ready()
    if event.trading_date != config["day"]:
        return []
    if event.event_type.value not in {"bar_1m", "order_book", "index_quote"}:
        return []
    if event.event_type.value == "index_quote":
        if event.instrument.key != config["index_key"]:
            return []
        if (event.provider != "kis" or event.instrument.venue != Venue.KIS
                or event.instrument.asset_class != AssetClass.INDEX
                or event.instrument.raw_symbol != "2001"
                or event.instrument.std_symbol != "KOSPI200"
                or event.instrument.exchange != "KRX" or event.instrument.currency != "KRW"):
            raise StrategyUnsupported("C3 event is not from the native KIS KOSPI200 index feed")
    if (event.event_type.value != "index_quote"
            and event.instrument.key != _feed_instrument(config, "069500").key):
        return []
    timestamp = _timestamp(event)
    facts = _facts(data, config)
    positions = _positions(facts, "C3")
    session = session_action("C3", timestamp)
    if session == "overnight_recovery" and positions and not instance.state["entries"]:
        _sync_actual(instance, facts, positions, "C3", recovery=True)
        signals = _exit_signals(instance, "C3", event, data, facts, positions)
        if signals:
            instance.state.update(safety_stop=None, exit_pending=True)
        return signals
    _sync_actual(instance, facts, positions, "C3")
    followups = _followup_signals(instance, "C3", event, data, facts, positions)
    if followups:
        return followups
    if session == "session_exit" and positions:
        signals = _exit_signals(instance, "C3", event, data, facts, positions)
        if signals:
            instance.state.update(exit_pending=True)
        return signals
    if timestamp.strftime("%H:%M") > "09:05" or instance.state["decision_complete"]:
        return []
    if instance.state["entries"] or positions:
        return []
    feed_key = _feed_instrument(config, "069500").key
    etf_open = _first_open(data, feed_key, config["day"])
    if etf_open is None:
        return []
    quote = data.latest_index_quote(config["index_key"])
    if quote is None or quote.open is None:
        return []
    if quote.trading_date != config["day"] or quote.instrument.key != config["index_key"]:
        raise StrategyUnsupported("C3 index open provenance does not match the configured day")
    if (quote.instrument.asset_class != AssetClass.INDEX
            or quote.instrument.std_symbol != "KOSPI200" or quote.provider != "kis"
            or quote.instrument.venue != Venue.KIS or quote.instrument.raw_symbol != "2001"
            or quote.instrument.exchange != "KRX" or quote.instrument.currency != "KRW"):
        raise StrategyUnsupported("C3 index_key is not a native KOSPI200 index quote")
    index_open = float(quote.open)
    if not math.isfinite(index_open) or index_open <= 0:
        raise StrategyUnsupported("C3 native ETF/index opens must be positive and finite")
    try:
        decision = module_for("C3").native_open_decision(
            instance.history, etf_open, index_open)
    except Exception as exc:
        raise StrategyUnsupported(f"C3 native regression is unavailable: {exc}") from exc
    instance.state["first_open"] = etf_open
    instance.state["decision_complete"] = True
    if decision["action"] != "entry":
        return []
    targets = entry_targets("C3", 1, decision["leverage"])
    orders = _entry_orders(config, data, facts, targets, 1)
    if not orders:
        return []
    instance.state.update(entries=1, direction=1, safety_stop=None, exit_pending=False)
    return [_signal(config, "C3", event, data, order, event.event_id, decision["detail"])
            for order in orders]


def intraday_on_event(instance: FileEtfStrategy, strategy: str, event, data, decide=None) -> list[Signal]:
    config = instance._ready()
    if event.event_type.value not in {"bar_1m", "order_book"} or event.trading_date != config["day"]:
        return []
    symbol = signal_symbol_for(strategy)
    feed_key = _feed_instrument(config, symbol).key
    if event.instrument.key != feed_key:
        return []
    timestamp = _timestamp(event)
    facts = _facts(data, config)
    positions = _positions(facts, strategy)
    session = session_action(strategy, timestamp)
    if session == "overnight_recovery" and positions and not instance.state["entries"]:
        _sync_actual(instance, facts, positions, strategy, recovery=True)
        signals = _exit_signals(instance, strategy, event, data, facts, positions)
        if signals:
            instance.state.update(safety_stop=None, exit_pending=True)
        return signals
    _sync_actual(instance, facts, positions, strategy)
    followups = _followup_signals(instance, strategy, event, data, facts, positions)
    if followups:
        return followups
    if session == "session_exit" and positions:
        signals = _exit_signals(instance, strategy, event, data, facts, positions)
        if signals:
            instance.state.update(exit_pending=True)
        return signals
    if event.event_type.value != "bar_1m":
        return []
    frame = _bar_frame(data, feed_key, config["day"])
    if frame.empty:
        return []
    _, bid, ask = _quote(data, feed_key)
    book = {"direction": instance.state["direction"],
            "positions": dict(positions), "entries": instance.state["entries"],
            "safety_stop": instance.state["safety_stop"]}
    decision = intraday_source_decision(
        strategy, instance.history, timestamp, frame, book, bid=bid, ask=ask)
    action = decision["action"]
    if action == "entry":
        if instance.state["entries"] or positions:
            return []
        targets = entry_targets(strategy, decision["direction"], decision["leverage"])
        orders = _entry_orders(config, data, facts, targets, decision["direction"])
        if not orders:
            return []
        instance.state.update(entries=1, direction=decision["direction"],
                              safety_stop=decision["stop"], exit_pending=False)
        return _entry_signals(instance, strategy, event, data, facts, orders)
    if action == "paper_stop":
        if not positions:
            return []
        signals = _exit_signals(instance, strategy, event, data, facts, positions)
        if signals:
            instance.state.update(safety_stop=None, exit_pending=True)
        return signals
    return []


def build_actions(strategy: str, signal: Signal, view, config) -> list[ExecutionAction]:
    """Translate one source-owned single-order plan into the existing Paper seam."""
    cfg = validate_config(config, strategy, require_history=False)
    if signal.strategy_id != strategy:
        raise ValueError("ETF signal strategy differs from the action builder module")
    metadata = signal.metadata if isinstance(signal.metadata, Mapping) else {}
    if metadata.get("strategy") != strategy or metadata.get("job_id") != cfg["job_id"]:
        raise ValueError("ETF signal metadata differs from its action builder config")
    plan = metadata.get("plan")
    if not isinstance(plan, Mapping) or plan.get("strategy") != strategy:
        raise ValueError("ETF signal does not carry a source-owned plan")
    orders = plan.get("orders")
    if not isinstance(orders, list) or len(orders) != 1:
        raise ValueError("each ETF signal must carry exactly one source-owned order")
    order = orders[0]
    if not isinstance(order, Mapping):
        raise ValueError("ETF source-owned order must be a mapping")
    if order.get("kind") not in {"entry", "exit", "cancel"}:
        raise ValueError("ETF source-owned order kind is invalid")
    symbol = order.get("symbol")
    if symbol not in cfg["symbols"]:
        raise ValueError("ETF source-owned order symbol is not configured")
    scope = AccountScope(**cfg["account"])
    if order.get("kind") == "cancel":
        target = order.get("target_order_id")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("ETF cancel requires a factual target order identity")
        if order.get("account_scope") != cfg["account_scope"]:
            raise ValueError("ETF cancel account differs from config")
        identity = f"{cfg['job_id']}:{signal.signal_id}:{symbol}:cancel"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return [ExecutionAction(
            action_id="action:" + digest, action=ActionKind.CANCEL, scope=scope,
            strategy_id=strategy, config_version=cfg["config_version"],
            mode=TradingMode.PAPER, expiry_ns=signal.valid_until_ts_ns,
            target_order_id=target)]
    side = Side(str(order.get("side")))
    if (order.get("kind") == "entry") != (side == Side.BUY):
        raise ValueError("ETF entry/exit side contradicts its source-owned plan")
    route = order.get("route")
    if route != account_scope_for(symbol):
        raise ValueError("ETF source-owned account route is invalid")
    if order.get("account_scope") != cfg["account_scope"]:
        raise ValueError("ETF source-owned plan account differs from config")
    facts = _facts(view, cfg)
    quantity = D(str(order.get("quantity")))
    followup = order.get("execution") == "market_remainder"
    raw_price, raw_reserve = order.get("limit_price"), order.get("reserve_unit_price")
    price = None if raw_price is None else D(str(raw_price))
    reserve = None if raw_reserve is None else D(str(raw_reserve))
    direction = order.get("direction")
    if (not quantity.is_finite() or quantity <= 0 or quantity != quantity.to_integral_value()
            or (price is not None and (not price.is_finite() or price <= 0))
            or (reserve is not None and (not reserve.is_finite() or reserve <= 0))
            or (followup and (price is not None or reserve is None))
            or (not followup and (price is None or reserve is not None))
            or isinstance(direction, bool) or direction not in (-1, 1)):
        raise ValueError("ETF source-owned quantity/price/direction is invalid")
    if order.get("kind") == "entry" and direction != plan.get("direction"):
        raise ValueError("ETF entry direction contradicts its plan")
    if order.get("kind") == "exit":
        positions = _positions(facts, strategy)
        if int(quantity) > int(positions.get(symbol, 0)):
            raise ValueError("ETF exit exceeds factual paper holdings")
    if followup and order.get("followup") is not True:
        raise ValueError("ETF market remainder requires explicit follow-up provenance")
    execution_instrument = _execution_instrument(cfg, symbol)
    identity = f"{cfg['job_id']}:{signal.signal_id}:{symbol}:{side.value}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    intent = OrderIntent(
        intent_id="intent:" + digest, job_id=cfg["job_id"], signal_id=signal.signal_id,
        destination=OrderDestination.REAL, instrument=execution_instrument, side=side,
        created_ts_ns=signal.event_ts_ns, client_order_id="client:" + digest[:40],
        quantity=quantity, limit_price=price,
        weight_pct=None if order.get("weight") is None else D(str(order["weight"])),
        metadata={"strategy": strategy, "plan": dict(order),
                  "account_scope": cfg["account_scope"], "route": route,
                  "direction": int(direction),
                  **({"reserve_unit_price": str(reserve)} if reserve is not None else {})})
    return [ExecutionAction(
        action_id="action:" + digest, action=ActionKind.NEW, scope=scope,
        strategy_id=strategy, config_version=cfg["config_version"],
        mode=TradingMode.PAPER, expiry_ns=signal.valid_until_ts_ns,
        intent=intent, post_only=bool(order.get("post_only", False)), reduce_only=side == Side.SELL,
        time_in_force="IOC" if followup else "GTC")]
