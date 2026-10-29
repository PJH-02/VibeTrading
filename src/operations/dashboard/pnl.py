"""Read-only, bounded strategy PnL projection for the dashboard."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from execution.execution_ledger import exact_difference, exact_product, exact_sum


_MAX_CURVE_POINTS = 1440
_MAX_STRATEGIES = 100


def public_scope(raw_scope: str) -> str:
    """Return the stable public scope token shared with runtime telemetry."""
    return hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()[:24]


def projection_id(strategy_id: str, scope: str, mode: str, currency: str) -> str:
    """Encode the full stable projection tuple without exposing raw scope."""
    key = json.dumps([strategy_id, scope, mode, currency], ensure_ascii=False,
                     separators=(",", ":"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _wire(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _exact_add(first: Decimal, second: Decimal) -> Decimal:
    return exact_sum(first, second)


def _exact_product(first: Decimal, second: Decimal) -> Decimal:
    return exact_product(first, second)


def paper_pnl_projection(document: dict, *, generated_ms: int | None = None,
                         marks=(), max_mark_age_ms: int = 10000,
                         curve_limit: int = _MAX_CURVE_POINTS) -> list[dict]:
    """Map a bounded ``paper_projection`` result into public PnL rows.

    Cash rows are ledger facts. Open positions require a bounded, versioned
    market mark; no order price is substituted for a mark.
    """
    if isinstance(curve_limit, bool) or not isinstance(curve_limit, int) or not 1 <= curve_limit <= _MAX_CURVE_POINTS:
        raise ValueError("curve_limit must be in 1..1440")
    if isinstance(max_mark_age_ms, bool) or not isinstance(max_mark_age_ms, int) or not 1 <= max_mark_age_ms <= 300000:
        raise ValueError("max_mark_age_ms must be in 1..300000")
    mark_map = {}
    if not isinstance(marks, (list, tuple)) or len(marks) > 8192:
        raise ValueError("marks must be an explicit bounded sequence")
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        instrument, currency, source, asof_ms, raw_scope = (mark.get("key"), mark.get("currency"),
                                                  mark.get("provider"), mark.get("last_event_ms"),
                                                  mark.get("account_scope"))
        price = _decimal(mark.get("price"))
        if (not isinstance(instrument, str) or not instrument or not isinstance(currency, str) or not currency or
                not isinstance(source, str) or not source or not isinstance(raw_scope, str) or not raw_scope or isinstance(asof_ms, bool) or
                not isinstance(asof_ms, int) or price is None or price <= 0):
            continue
        mark_map[(raw_scope, instrument)] = {"price": price, "currency": currency, "source": source, "asof_ms": asof_ms}
    pnl_rows = document.get("pnl", [])
    if not isinstance(pnl_rows, list) or len(pnl_rows) > _MAX_STRATEGIES * 16:
        return []
    positions = document.get("positions", [])
    orders = document.get("orders", [])
    fills = document.get("fills", [])
    accounts = document.get("accounts", [])
    gaps_by_scope = {row.get("scope"): row for row in accounts if row.get("gap_count")}
    unallocated_cash: dict[tuple[str, str], dict] = {}
    for row in pnl_rows:
        strategy, raw_scope, currency = row.get("strategy"), row.get("account"), row.get("currency")
        if (isinstance(strategy, str) and strategy.startswith("@unallocated") and
                isinstance(raw_scope, str) and isinstance(currency, str)):
            identity = (raw_scope, currency)
            target = unallocated_cash.setdefault(identity, {"realized": Decimal(0), "fees": Decimal(0),
                                                             "funding": Decimal(0), "amount": Decimal(0)})
            for field, target_name in (("realized_pnl", "realized"), ("fees", "fees"),
                                       ("funding", "funding"), ("amount", "amount")):
                value = _decimal(row.get(field))
                if value is None:
                    continue
                target[target_name] = exact_sum(target[target_name], value)
    open_positions: dict[tuple[str, str, str], dict] = {}
    for row in positions:
        strategy, scope = row.get("strategy"), row.get("account")
        currency = row.get("currency")
        if not isinstance(strategy, str) or strategy.startswith("@") or not isinstance(scope, str) or not isinstance(currency, str):
            continue
        quantity = _decimal(row.get("quantity"))
        if quantity is not None and quantity != 0:
            bucket = open_positions.setdefault((scope, strategy, currency),
                {"count": 0, "unrealized": Decimal(0), "marks": [], "complete": True})
            bucket["count"] += 1
            instrument = row.get("instrument")
            mark = mark_map.get((scope, instrument))
            average, multiplier = _decimal(row.get("average")), _decimal(row.get("multiplier"))
            settlement, quote = row.get("settlement_currency"), row.get("quote_currency")
            if (mark is None or average is None or multiplier is None or multiplier <= 0 or
                    settlement != quote or settlement != currency or mark["currency"] != settlement or
                    generated_ms is None or mark["asof_ms"] > generated_ms or
                    generated_ms - mark["asof_ms"] > max_mark_age_ms):
                bucket["complete"] = False
            else:
                delta = exact_difference(mark["price"], average)
                bucket["unrealized"] = exact_sum(bucket["unrealized"], exact_product(exact_product(quantity, delta), multiplier))
                bucket["marks"].append(mark["asof_ms"])
    orders_by_strategy: dict[tuple[str, str], list[dict]] = {}
    for row in orders:
        strategy, scope = row.get("strategy"), row.get("account")
        if isinstance(strategy, str) and isinstance(scope, str):
            orders_by_strategy.setdefault((scope, strategy), []).append(row)
    fills_by_strategy: dict[tuple[str, str, str], int] = {}
    for row in fills:
        strategy, scope, currency = row.get("strategy"), row.get("account"), row.get("currency")
        if isinstance(strategy, str) and isinstance(scope, str) and isinstance(currency, str):
            identity = (scope, strategy, currency)
            fills_by_strategy[identity] = fills_by_strategy.get(identity, 0) + 1

    result = []
    seen = set()
    for row in pnl_rows:
        strategy, raw_scope = row.get("strategy"), row.get("account")
        currency = row.get("currency")
        mode = row.get("mode") or "paper"
        if (not isinstance(strategy, str) or not strategy or strategy.startswith("@") or
                not isinstance(raw_scope, str) or not raw_scope or
                not isinstance(currency, str) or not currency or mode != "paper"):
            continue
        scope = public_scope(raw_scope)
        key = projection_id(strategy, scope, mode, currency)
        if key in seen:
            continue
        seen.add(key)
        realized = _decimal(row.get("realized_pnl"))
        fees = _decimal(row.get("fees"))
        funding = _decimal(row.get("funding"))
        position = open_positions.get((raw_scope, strategy, currency), {"count": 0, "unrealized": Decimal(0), "marks": [], "complete": True})
        count = position["count"]
        gap = raw_scope in gaps_by_scope
        unallocated = (raw_scope, currency) in unallocated_cash
        unresolved = row.get("recovery") == "UNRECONCILED"
        attribution = "INCOMPLETE" if gap or unresolved or unallocated else "COMPLETE"
        unrealized = position["unrealized"] if count == 0 or position["complete"] else None
        net = None
        if not gap and not unresolved and not unallocated and realized is not None and fees is not None and funding is not None and unrealized is not None:
            net = _exact_add(_exact_add(_exact_add(realized, unrealized), fees.copy_negate()), funding)
        strategy_orders = orders_by_strategy.get((raw_scope, strategy), [])
        working = sum(1 for item in strategy_orders if str(item.get("status", "")).upper()
                      in {"OPEN", "UNKNOWN", "CANCEL_PENDING"})
        trades = fills_by_strategy.get((raw_scope, strategy, currency), 0)
        result.append({
            "key": key,
            "id": key,
            "strategy_id": strategy,
            "account_scope": scope,
            "mode": mode,
            "currency": currency,
            "realized_pnl": _wire(realized),
            "unrealized_pnl": _wire(unrealized),
            "fees": _wire(fees),
            "funding": _wire(funding),
            "net_pnl": _wire(net),
            "fee_convention": "gross realized; net = realized + unrealized - fees + funding",
            "pnl_method_version": "paper-ledger-v1",
            "equity_curve": [],
            "position_count": count,
            "working_order_count": working,
            "trades": trades,
            "asof_ms": generated_ms,
            "mark_asof_ms": min(position["marks"]) if position["marks"] else None,
            "mark_provenance": "LOCAL_VALIDATED_MARKET_SNAPSHOT" if position["complete"] and count else "MISSING_TRUSTED_MARK" if count else "NO_OPEN_POSITION",
            "stale": bool((count and not position["complete"]) or gap or unresolved or unallocated),
            "attribution_status": attribution,
            "status": "AVAILABLE" if net is not None else "INCOMPLETE",
            "evidence": "PAPER_SIMULATION",
        })
        if len(result) >= _MAX_STRATEGIES:
            break

    # Explicit unmatched account facts are visible at account level; no
    # strategy gets a fabricated pro-rata allocation.
    for account in accounts:
        raw_scope = account.get("scope")
        if not isinstance(raw_scope, str) or not account.get("gap_count"):
            continue
        scope = public_scope(raw_scope)
        for balance in account.get("balances", [])[:16]:
            currency = balance.get("currency")
            if not isinstance(currency, str) or not currency:
                continue
            key = projection_id("UNALLOCATED", scope, "paper", currency)
            existing = next((item for item in result if item["key"] == key), None)
            if existing is not None:
                existing["attribution_status"] = "UNALLOCATED"
                existing["status"] = "INCOMPLETE"
                existing["stale"] = True
                existing["net_pnl"] = None
                continue
            seen.add(key)
            result.append({
                "key": key, "id": key, "strategy_id": "UNALLOCATED",
                "account_scope": scope, "mode": "paper", "currency": currency,
                "realized_pnl": None, "unrealized_pnl": None, "fees": None,
                "funding": None, "net_pnl": None,
                "fee_convention": "unallocated account facts; no strategy net",
                "pnl_method_version": "paper-ledger-v1", "equity_curve": [],
                "position_count": None, "working_order_count": None, "trades": None,
                "asof_ms": generated_ms, "mark_asof_ms": None,
                "mark_provenance": "UNALLOCATED", "stale": True,
                "attribution_status": "UNALLOCATED", "status": "INCOMPLETE",
                "evidence": "PAPER_SIMULATION",
            })
    for (raw_scope, currency), facts in unallocated_cash.items():
        scope = public_scope(raw_scope)
        key = projection_id("UNALLOCATED", scope, "paper", currency)
        if key in seen:
            existing = next((item for item in result if item["key"] == key), None)
            if existing is not None:
                existing["realized_pnl"] = _wire(facts["realized"])
                existing["fees"] = _wire(facts["fees"])
                existing["funding"] = _wire(facts["funding"])
                existing["unallocated_amount"] = _wire(facts["amount"])
                existing["attribution_status"] = "UNALLOCATED"
                existing["status"] = "INCOMPLETE"
                existing["stale"] = True
                existing["net_pnl"] = None
            continue
        seen.add(key)
        result.append({
            "key": key, "id": key, "strategy_id": "UNALLOCATED", "account_scope": scope,
            "mode": "paper", "currency": currency, "realized_pnl": _wire(facts["realized"]),
            "unrealized_pnl": None, "fees": _wire(facts["fees"]), "funding": _wire(facts["funding"]),
            "unallocated_amount": _wire(facts["amount"]), "net_pnl": None,
            "fee_convention": "unallocated account cash/fee/funding facts; no strategy net",
            "pnl_method_version": "paper-ledger-v1", "equity_curve": [],
            "position_count": None, "working_order_count": None, "trades": None,
            "asof_ms": generated_ms, "mark_asof_ms": None, "mark_provenance": "UNALLOCATED",
            "stale": True, "attribution_status": "UNALLOCATED", "status": "INCOMPLETE",
            "evidence": "PAPER_SIMULATION",
        })
    return result[:_MAX_STRATEGIES]


def merge_runtime_registry(rows: list[dict], snapshots: list[dict], *, generated_ms: int | None = None) -> list[dict]:
    """Attach actual observed lifecycle status and retain observed stopped rows."""
    if len(rows) > _MAX_STRATEGIES or len(snapshots) > 32:
        return rows[:_MAX_STRATEGIES]
    result = [dict(row) for row in rows]
    positions = {(row.get("strategy_id"), row.get("account_scope"), row.get("mode"), row.get("currency")): i
                 for i, row in enumerate(result)}
    for snapshot in snapshots:
        strategies = snapshot.get("strategies", [])
        if not isinstance(strategies, list) or len(strategies) > _MAX_STRATEGIES:
            continue
        for fact in strategies:
            strategy = fact.get("strategy_id")
            scope = fact.get("account_scope")
            mode = fact.get("mode")
            status = fact.get("status")
            currency = fact.get("currency")
            if (not isinstance(strategy, str) or not strategy or len(strategy) > 256 or
                    not isinstance(scope, str) or len(scope) != 24 or
                    any(ch not in "0123456789abcdef" for ch in scope) or
                    not isinstance(mode, str) or not mode or len(mode) > 32 or
                    not isinstance(status, str) or not status or len(status) > 48):
                continue
            currency = currency if isinstance(currency, str) and currency else None
            matches = ([positions[(strategy, scope, mode, currency)]] if currency is not None and
                       (strategy, scope, mode, currency) in positions else
                       [index for identity, index in positions.items()
                        if currency is None and identity[:3] == (strategy, scope, mode)])
            if matches:
                for index in matches:
                    result[index]["runtime_status"] = status
                    result[index]["runtime_updated_ms"] = fact.get("updated_ms")
                continue
            currency = currency or "UNKNOWN"
            identity = (strategy, scope, mode, currency)
            key = projection_id(strategy, scope, mode, currency)
            positions[identity] = len(result)
            result.append({
                "key": key, "id": key, "strategy_id": strategy, "account_scope": scope,
                "mode": mode, "currency": currency, "realized_pnl": None,
                "unrealized_pnl": None, "fees": None, "funding": None, "net_pnl": None,
                "fee_convention": "authoritative ledger facts not available",
                "pnl_method_version": "unavailable-v1", "equity_curve": [],
                "position_count": None, "working_order_count": None, "trades": None,
                "asof_ms": generated_ms, "mark_asof_ms": None,
                "mark_provenance": "NO_LEDGER_PROJECTION", "stale": True,
                "attribution_status": "INCOMPLETE", "status": "INCOMPLETE",
                "runtime_status": status, "runtime_updated_ms": fact.get("updated_ms"),
                "evidence": "RUNTIME_REGISTRY",
            })
            if len(result) >= _MAX_STRATEGIES:
                return result[:_MAX_STRATEGIES]
    return result


def merge_dashboard_strategies(rows: list[dict], strategies: list[dict], *, generated_ms: int | None = None) -> list[dict]:
    """Retain existing dashboard strategies when detailed PnL is unavailable."""
    result = [dict(row) for row in rows[:_MAX_STRATEGIES]]
    positions = {(row.get("strategy_id"), row.get("account_scope"), row.get("mode"), row.get("currency")): i
                 for i, row in enumerate(result)}
    if not isinstance(strategies, list):
        return result
    for fact in strategies[:100]:
        if not isinstance(fact, dict):
            continue
        strategy = fact.get("strategy") or fact.get("id")
        raw_scope = fact.get("account")
        currency = fact.get("currency") if isinstance(fact.get("currency"), str) else "UNKNOWN"
        mode = fact.get("mode")
        if (not isinstance(strategy, str) or not strategy or len(strategy) > 256 or
                not isinstance(raw_scope, str) or not raw_scope or len(raw_scope) > 512 or
                not isinstance(mode, str) or not mode or len(mode) > 32 or len(currency) > 16):
            continue
        mode = mode.lower()
        if mode in {"real", "live"}:
            mode = "live"
        elif mode in {"sandbox", "testnet"}:
            mode = "sandbox"
        elif mode in {"paper", "mock", "mock2", "shadow", "timefolio", "unverified"}:
            pass
        else:
            mode = "unverified"
        scope = public_scope(raw_scope)
        identity = (strategy, scope, mode, currency)
        index = positions.get(identity)
        running = fact.get("running")
        status_value = fact.get("status")
        runtime_status = status_value if isinstance(status_value, str) and status_value.upper() in {"STOPPED", "BLOCKED", "RUNNING", "ACTIVE"} else (
            "RUNNING" if running is True else "UNVERIFIED")
        realized_after_fee = fact.get("realized_pnl_after_execution_fees_usdt")
        if index is not None:
            existing = result[index].get("runtime_status")
            if existing in (None, "UNVERIFIED", "UNKNOWN"):
                result[index]["runtime_status"] = runtime_status
            if runtime_status in {"STOPPED", "BLOCKED", "RUNNING", "ACTIVE"} and existing in (None, "UNVERIFIED", "UNKNOWN"):
                result[index]["runtime_updated_ms"] = fact.get("updated_ms")
            continue
        realized = _decimal(realized_after_fee)
        if realized is None:
            realized = None
        fees = _decimal(fact.get("execution_fees_usdt"))
        key = projection_id(strategy, scope, mode, currency)
        positions[identity] = len(result)
        result.append({
            "key": key, "id": key, "strategy_id": strategy, "account_scope": scope,
            "mode": mode, "currency": currency, "realized_pnl": _wire(realized),
            "unrealized_pnl": None, "fees": _wire(fees), "funding": None, "net_pnl": None,
            "fee_convention": ("realized summary already includes execution fees; fee is informational" if realized_after_fee is not None
                               else "authoritative realized/fee/funding facts unavailable"),
            "pnl_method_version": "legacy-summary-incomplete-v1", "equity_curve": [],
            "position_count": None, "working_order_count": None, "trades": None,
            "asof_ms": generated_ms, "mark_asof_ms": None,
            "mark_provenance": "NO_TRUSTED_STRATEGY_MARK", "stale": True,
            "attribution_status": "INCOMPLETE", "status": "INCOMPLETE",
            "runtime_status": runtime_status, "runtime_updated_ms": fact.get("updated_ms"),
            "evidence": "LEGACY_SUMMARY",
        })
        if len(result) >= _MAX_STRATEGIES:
            break
    return result[:_MAX_STRATEGIES]


def native_ledger_pnl_projection(paths, *, product_metadata=(), marks=(),
                                 max_mark_age_ms: int = 10000,
                                 generated_ms: int | None = None,
                                 max_rows: int = 5000) -> list[dict]:
    """Project exact native fill receipts; missing product facts remain N/A.

    ``execution_fills.fingerprint`` is the canonical four-tuple
    ``[intent_id, quantity, price, fee]``. Reporting currency, fee currency,
    settlement currency and a product multiplier match are provided through a
    bounded read-side metadata list; no owner instance or broker API is used.
    """
    if isinstance(paths, (str, bytes)) or not isinstance(paths, (list, tuple)) or len(paths) > 16:
        raise ValueError("native ledger paths must be an explicit sequence of at most 16 paths")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= 10000:
        raise ValueError("max_rows must be in 1..10000")
    if not isinstance(product_metadata, (list, tuple)) or len(product_metadata) > 256:
        raise ValueError("native product metadata must contain at most 256 explicit entries")
    if isinstance(max_mark_age_ms, bool) or not isinstance(max_mark_age_ms, int) or not 1 <= max_mark_age_ms <= 300000:
        raise ValueError("max_mark_age_ms must be in 1..300000")
    if not isinstance(marks, (list, tuple)) or len(marks) > 8192:
        raise ValueError("marks must be an explicit bounded sequence")
    mark_map = {}
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        raw_scope, instrument = mark.get("account_scope"), mark.get("key")
        price, currency = _decimal(mark.get("price")), mark.get("currency")
        asof, source = mark.get("last_event_ms"), mark.get("provider")
        if (not isinstance(raw_scope, str) or not raw_scope or not isinstance(instrument, str) or not instrument or
                price is None or price <= 0 or not isinstance(currency, str) or not currency or
                isinstance(asof, bool) or not isinstance(asof, int) or not isinstance(source, str) or not source):
            continue
        mark_map[(raw_scope, instrument)] = {"price": price, "currency": currency,
                                            "asof_ms": asof, "source": source}
    products = {}
    for item in product_metadata:
        if not isinstance(item, dict):
            raise ValueError("native product metadata entry must be an object")
        required = {"scope", "instrument", "currency", "fee_currency", "settlement_currency",
                    "multiplier", "funding_applicable", "funding_complete", "source"}
        if set(item) != required:
            raise ValueError("native product metadata fields mismatch")
        scope_parts, instrument = item["scope"], item["instrument"]
        if (not isinstance(scope_parts, list) or len(scope_parts) != 4 or
                not all(isinstance(part, str) and 0 < len(part) <= 200 for part in scope_parts) or
                not isinstance(instrument, str) or not 1 <= len(instrument) <= 512 or
                any(not isinstance(item[field], str) or not 1 <= len(item[field]) <= 16
                    for field in ("currency", "fee_currency", "settlement_currency")) or
                type(item["funding_applicable"]) is not bool or
                type(item["funding_complete"]) is not bool or
                not isinstance(item["source"], str) or not 1 <= len(item["source"]) <= 128):
            raise ValueError("native product metadata identity is invalid")
        multiplier = _decimal(item["multiplier"])
        if multiplier is None or multiplier <= 0:
            raise ValueError("native product multiplier must be positive finite decimal")
        scope_key = json.dumps(scope_parts, separators=(",", ":"), ensure_ascii=False)
        key = (scope_key, instrument)
        if key in products:
            raise ValueError("duplicate native product metadata key")
        products[key] = {**item, "scope_key": scope_key, "multiplier_decimal": multiplier}

    group_plans = {}
    funding_documents = {}
    funding_conflicts = set()
    funding_malformed_scopes = set()
    seen_scopes = set()
    seen_paths = set()
    seen_fill_ids = set()
    valid_quantity_by_intent = {}
    expected_fill_by_intent = {}
    row_states = {}
    scope_modes = {}
    result = []

    def exact_divide(left: Decimal, right: Decimal) -> Decimal:
        with localcontext() as context:
            context.prec = 128
            return left / right

    def project_position(position: dict, delta: Decimal, price: Decimal, multiplier: Decimal) -> Decimal:
        old = position["quantity"]
        average = position["average"]
        new = _exact_add(old, delta)
        realized = Decimal(0)
        if old and (old > 0) != (delta > 0):
            closed = min(old.copy_abs(), delta.copy_abs())
            realized = _exact_product(_exact_product(closed, multiplier), _exact_add(price, average.copy_negate()))
            if old < 0:
                realized = realized.copy_negate()
        if not new:
            average = Decimal(0)
        elif not old or (old > 0) != (new > 0):
            average = price
        elif (old > 0) == (delta > 0):
            average = exact_divide(_exact_add(_exact_product(old.copy_abs(), average),
                                              _exact_product(delta.copy_abs(), price)), new.copy_abs())
        position["quantity"], position["average"] = new, average
        return realized

    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute() or not path.is_file():
            continue
        resolved = str(path.resolve())
        if resolved in seen_paths:
            raise ValueError("duplicate native ledger path")
        seen_paths.add(resolved)
        db = None
        try:
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"owner_metadata", "execution_intents", "execution_fills"} <= tables:
                db.rollback()
                db.close()
                continue
            metadata = {row[0]: row[1] for row in db.execute(
                "SELECT key,value FROM owner_metadata WHERE key='scope' LIMIT 1")}
            try:
                scope_parts = json.loads(metadata.get("scope", ""))
            except (TypeError, ValueError):
                scope_parts = None
            if (not isinstance(scope_parts, list) or len(scope_parts) != 4 or
                    not all(isinstance(part, str) and part for part in scope_parts)):
                db.rollback()
                db.close()
                continue
            raw_scope = json.dumps(scope_parts, separators=(",", ":"), ensure_ascii=False)
            mode = scope_parts[3].lower()
            if mode == "testnet":
                mode = "sandbox"
            if mode == "paper" or len(mode) > 32:
                db.rollback()
                db.close()
                continue
            seen_scopes.add(raw_scope)
            scope_modes[raw_scope] = mode
            intents = list(db.execute("""SELECT intent_id,strategy_id,state,payload,filled,
                contract_multiplier,updated_ns FROM execution_intents ORDER BY updated_ns,intent_id LIMIT ?""",
                                      (max_rows + 1,)))
            receipts = list(db.execute("""SELECT f.rowid AS receipt_seq,f.fill_id,f.intent_id,f.fingerprint,
                i.strategy_id,i.payload,i.contract_multiplier,i.state,i.updated_ns
                FROM execution_fills f JOIN execution_intents i ON i.intent_id=f.intent_id
                ORDER BY f.rowid LIMIT ?""", (max_rows + 1,)))
            groups = []
            if "execution_group_facts" in tables:
                groups = list(db.execute("SELECT group_id,fact_id,kind,document,sha256 FROM execution_group_facts ORDER BY seq LIMIT ?",
                                         (max_rows + 1,)))
            db.commit()
            db.close()
            db = None
            if len(intents) > max_rows or len(receipts) > max_rows or len(groups) > max_rows:
                continue
        except (OSError, sqlite3.Error):
            if db is not None:
                db.close()
            continue

        for fact in groups:
            try:
                document = json.loads(fact["document"])
            except (TypeError, ValueError):
                if fact["kind"] == "funding-receipt":
                    funding_malformed_scopes.add(raw_scope)
                continue
            if fact["kind"] == "plan":
                spec = document.get("spec") if isinstance(document, dict) else None
                strategy = spec.get("strategy_id") if isinstance(spec, dict) else None
                if isinstance(strategy, str) and strategy:
                    group_plans[(raw_scope, fact["group_id"])] = strategy
            elif fact["kind"] == "funding-receipt" and isinstance(document, dict):
                physical_identity = (document.get("scope_key"), document.get("native_account_id"),
                                     document.get("receipt_id"))
                if not all(isinstance(part, str) and part for part in physical_identity):
                    funding_malformed_scopes.add(raw_scope)
                    continue
                key = (physical_identity[0], physical_identity[1], physical_identity[2])
                previous = funding_documents.get(key)
                if previous is not None and previous != document:
                    funding_conflicts.add(key)
                else:
                    funding_documents.setdefault(key, document)

        intent_context = {}
        for intent in intents:
            try:
                payload = json.loads(intent["payload"])
            except (TypeError, ValueError):
                payload = {}
            instrument = payload.get("instrument") if isinstance(payload, dict) else None
            strategy = intent["strategy_id"] or "UNALLOCATED"
            instrument_key = _native_instrument_key(instrument)
            product = products.get((raw_scope, instrument_key)) if instrument_key else None
            currency = product["currency"] if product else "UNKNOWN"
            scope = public_scope(raw_scope)
            identity = (strategy, scope, mode, currency)
            state = row_states.setdefault(identity, {
                "realized": Decimal(0), "fees": Decimal(0), "funding": Decimal(0),
                "trade_cashflow": Decimal(0), "positions": {}, "working": 0,
                "working_ids": set(), "intent_ids": set(), "fills": 0, "complete": True,
                "fees_complete": True, "funding_complete": True,
                "funding_applicable": bool(product and product["funding_applicable"]),
                "unallocated": strategy == "UNALLOCATED", "missing_marks": False,
                "raw_scope": raw_scope,
            })
            state["complete"] = state["complete"] and product is not None
            state["fees_complete"] = state["fees_complete"] and bool(product and product["fee_currency"] == currency)
            state["funding_complete"] = state["funding_complete"] and bool(
                product and (not product["funding_applicable"] or product["funding_complete"]))
            state["intent_ids"].add(intent["intent_id"])
            if str(intent["state"]).upper() in {"AUTHORIZED", "HANDOFF", "UNKNOWN", "LEGACY_UNKNOWN", "SUBMITTED", "CANCEL_PENDING"}:
                state["working_ids"].add(intent["intent_id"])
            intent_context[intent["intent_id"]] = (state, payload, instrument_key, product, intent)

        for receipt in receipts:
            context = intent_context.get(receipt["intent_id"])
            if context is None:
                continue
            state, payload, instrument_key, product, intent = context
            fill_identity = (raw_scope, receipt["fill_id"])
            if fill_identity in seen_fill_ids:
                continue
            seen_fill_ids.add(fill_identity)
            state["fills"] += 1
            direction = None
            if isinstance(payload, dict):
                side = payload.get("side")
                direction = 1 if side in ("buy", "BUY") else -1 if side in ("sell", "SELL") else None
            multiplier = _decimal(intent["contract_multiplier"])
            if product is None or multiplier is None or multiplier != product["multiplier_decimal"]:
                state["complete"] = state["fees_complete"] = False
                state["funding_complete"] = False
            if direction is None or instrument_key is None or multiplier is None:
                state["complete"] = state["fees_complete"] = False
                continue
            if product is not None and product["currency"] != product["settlement_currency"]:
                state["complete"] = state["fees_complete"] = state["funding_complete"] = False
            if product is not None and product["funding_applicable"]:
                state["funding_applicable"] = True
                state["funding_complete"] = state["funding_complete"] and product["funding_complete"]
            instrument_position = state["positions"].setdefault(instrument_key,
                {"quantity": Decimal(0), "average": Decimal(0), "product": product,
                 "multiplier": multiplier})
            try:
                fingerprint = json.loads(receipt["fingerprint"])
                if (not isinstance(fingerprint, list) or len(fingerprint) != 4 or
                        fingerprint[0] != intent["intent_id"]):
                    raise ValueError("receipt fingerprint identity")
                quantity, price, fee = (_decimal(fingerprint[1]), _decimal(fingerprint[2]),
                                        _decimal(fingerprint[3]))
                if quantity is None or quantity <= 0 or price is None or price <= 0 or fee is None or fee < 0:
                    raise ValueError("receipt fingerprint amounts")
            except (TypeError, ValueError):
                state["complete"] = state["fees_complete"] = False
                continue
            valid_key = (raw_scope, intent["intent_id"])
            valid_quantity_by_intent[valid_key] = exact_sum(
                valid_quantity_by_intent.get(valid_key, Decimal(0)), quantity)
            cashflow = exact_product(exact_product(quantity, price), multiplier)
            if direction > 0:
                cashflow = cashflow.copy_negate()
            state["trade_cashflow"] = exact_sum(state["trade_cashflow"], cashflow)
            realized = project_position(instrument_position,
                _exact_product(quantity, Decimal(direction)), price, multiplier)
            if product is not None and multiplier == product["multiplier_decimal"]:
                state["realized"] = _exact_add(state["realized"], realized)
            else:
                state["complete"] = False
            if product is not None and product["fee_currency"] == product["currency"]:
                state["fees"] = _exact_add(state["fees"], fee)
            else:
                state["fees_complete"] = False
        for intent in intents:
            expected = _decimal(intent["filled"])
            if expected is None:
                expected_fill_by_intent[(raw_scope, intent["intent_id"])] = None
            else:
                identity = (raw_scope, intent["intent_id"])
                previous = expected_fill_by_intent.get(identity, expected)
                expected_fill_by_intent[identity] = expected if previous == expected else None

    for (raw_scope, intent_id), expected in expected_fill_by_intent.items():
        if expected is None or valid_quantity_by_intent.get((raw_scope, intent_id), Decimal(0)) != expected:
            for state in row_states.values():
                if state.get("raw_scope") == raw_scope and intent_id in state.get("intent_ids", set()):
                    state["complete"] = state["fees_complete"] = False

    for malformed_scope in funding_malformed_scopes:
        for identity, state in row_states.items():
            if state.get("raw_scope") == malformed_scope:
                state["funding_complete"] = False

    for (fact_scope, native_account_id, receipt_id), document in funding_documents.items():
        try:
            scope_parts = json.loads(fact_scope)
            raw_scope = json.dumps(scope_parts, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        if not isinstance(scope_parts, list) or len(scope_parts) != 4:
            continue
        mode = scope_modes.get(raw_scope, str(scope_parts[3]).lower())
        if mode == "testnet":
            mode = "sandbox"
        currency = document.get("currency") if isinstance(document, dict) else None
        invalid = (document is None or (fact_scope, native_account_id, receipt_id) in funding_conflicts or
                   document.get("scope_key") != raw_scope or
                   document.get("native_account_id") != native_account_id or
                   document.get("receipt_id") != receipt_id or
                   document.get("source") != "native_funding_receipt" or
                   not isinstance(currency, str) or not currency or len(currency) > 16 or
                   not isinstance(document.get("allocations"), dict))
        allocations = document.get("allocations") if isinstance(document, dict) else None
        if not invalid:
            known_total = _decimal(document.get("unallocated"))
            if known_total is None:
                invalid = True
                known_total = Decimal(0)
            for amount_text in allocations.values():
                amount = _decimal(amount_text)
                if amount is None:
                    invalid = True
                    break
                known_total = exact_sum(known_total, amount)
            declared_total = _decimal(document.get("amount"))
            if declared_total is None or declared_total != known_total:
                invalid = True
        if invalid:
            for identity, state in row_states.items():
                if state.get("raw_scope") == raw_scope and (currency is None or identity[3] == currency):
                    state["funding_complete"] = False
            continue
        for group_id, amount_text in allocations.items():
            strategy = group_plans.get((raw_scope, group_id))
            amount = _decimal(amount_text)
            if not strategy or amount is None:
                for identity, state in row_states.items():
                    if state.get("raw_scope") == raw_scope and identity[3] == currency:
                        state["funding_complete"] = False
                continue
            identity = (strategy, public_scope(raw_scope), mode, currency)
            state = row_states.setdefault(identity, {
                "realized": Decimal(0), "fees": Decimal(0), "funding": Decimal(0),
                "positions": {}, "working": 0, "working_ids": set(), "fills": 0,
                "complete": False, "fees_complete": False, "funding_complete": True,
                "funding_applicable": True, "unallocated": False, "missing_marks": False,
                "raw_scope": raw_scope,
            })
            state["funding"] = exact_sum(state["funding"], amount)
            state["funding_applicable"] = True
        unallocated_amount = _decimal(document.get("unallocated"))
        if unallocated_amount is not None and unallocated_amount != 0:
            identity = ("UNALLOCATED", public_scope(raw_scope), mode, currency)
            state = row_states.setdefault(identity, {
                "realized": Decimal(0), "fees": Decimal(0), "funding": Decimal(0),
                "positions": {}, "working": 0, "working_ids": set(), "fills": 0,
                "complete": False, "fees_complete": False, "funding_complete": True,
                "funding_applicable": True, "unallocated": True, "missing_marks": False,
                "raw_scope": raw_scope,
            })
            state["funding"] = exact_sum(state["funding"], unallocated_amount)
            state["unallocated"] = True
            for identity, affected in row_states.items():
                if (affected.get("raw_scope") == raw_scope and identity[2] == mode and
                        identity[3] == currency and identity[0] != "UNALLOCATED"):
                    affected["funding_complete"] = False

    for (strategy, scope, row_mode, currency), facts in sorted(row_states.items()):
        raw_scope = facts.get("raw_scope", "")
        facts["working"] = len(facts.get("working_ids", set()))
        position_count = sum(1 for position in facts["positions"].values()
                             if position["quantity"] != 0)
        unrealized = Decimal(0)
        mark_value = Decimal(0)
        mark_times = []
        unrealized_complete = True
        if position_count:
            for instrument_key, position in facts["positions"].items():
                if not position["quantity"]:
                    continue
                product = position["product"]
                mark = mark_map.get((raw_scope, instrument_key))
                if (product is None or mark is None or mark["currency"] != currency or
                        product["currency"] != product["settlement_currency"] or
                        generated_ms is None or mark["asof_ms"] > generated_ms or
                        generated_ms - mark["asof_ms"] > max_mark_age_ms):
                    unrealized_complete = False
                    continue
                delta = exact_difference(mark["price"], position["average"])
                unrealized = exact_sum(unrealized,
                    exact_product(exact_product(position["quantity"], delta), position["multiplier"]))
                mark_value = exact_sum(mark_value, exact_product(
                    exact_product(position["quantity"], mark["price"]), position["multiplier"]))
                mark_times.append(mark["asof_ms"])
        if not unrealized_complete:
            unrealized = None
        realized = facts["realized"] if facts["complete"] else None
        fees = facts["fees"] if facts["fees_complete"] else None
        funding = facts["funding"] if facts["funding_complete"] else None
        net = None
        if (not facts["unallocated"] and unrealized is not None and realized is not None and
                fees is not None and funding is not None):
            net = exact_sum(facts["trade_cashflow"], mark_value, fees.copy_negate(), funding)
        attribution = ("UNALLOCATED" if facts["unallocated"] else
                       "COMPLETE" if (facts["complete"] and facts["fees_complete"] and
                                      facts["funding_complete"] and unrealized is not None) else "INCOMPLETE")
        key = projection_id(strategy, scope, row_mode, currency)
        result.append({
            "key": key, "id": key, "strategy_id": strategy, "account_scope": scope,
            "mode": row_mode, "currency": currency,
            "realized_pnl": None if facts["unallocated"] else _wire(realized),
            "unrealized_pnl": None if facts["unallocated"] else _wire(unrealized),
            "fees": None if facts["unallocated"] else _wire(fees),
            "funding": _wire(funding), "net_pnl": _wire(net),
            "fee_convention": "gross realized; net = realized + unrealized - fees + funding",
            "pnl_method_version": "native-fill-average-cost-v1", "equity_curve": [],
            "position_count": position_count, "working_order_count": facts["working"],
            "trades": facts["fills"], "asof_ms": generated_ms,
            "mark_asof_ms": min(mark_times) if mark_times else None,
            "mark_provenance": "LOCAL_VALIDATED_MARKET_SNAPSHOT" if position_count and unrealized is not None else "MISSING_TRUSTED_MARK" if position_count else "NO_OPEN_POSITION",
            "stale": bool((position_count and unrealized is None) or facts["unallocated"] or net is None),
            "attribution_status": attribution,
            "status": "AVAILABLE" if net is not None else "INCOMPLETE",
            "evidence": "NATIVE_EXECUTION_LEDGER",
        })
    return result[:_MAX_STRATEGIES]


def _native_instrument_key(instrument: Any) -> str | None:
    if not isinstance(instrument, dict):
        return None
    venue = instrument.get("venue")
    asset = instrument.get("asset_class")
    symbol = instrument.get("std_symbol")
    if not all(isinstance(part, str) and part for part in (venue, asset, symbol)):
        return None
    key = f"{venue}:{asset}:{symbol}"
    for name in ("contract_month", "expiry", "option_right"):
        value = instrument.get(name)
        if value:
            key += ":" + str(value)
    if instrument.get("strike") is not None:
        key += ":" + str(instrument["strike"])
    return key


def etf_journal_pnl_projection(configs, *, marks=(), generated_ms: int | None = None,
                               max_mark_age_ms: int = 10000,
                               max_rows: int = 5000) -> list[dict]:
    """Project local KIS MOCK/MOCK2 immutable ETF feedback receipts read-only.

    Fees are an explicitly configured mock fee model because this journal
    intentionally does not collect broker commissions. The UI labels that
    convention. No KIS API/client or journal writer is imported here.
    """
    if isinstance(configs, (str, bytes)) or not isinstance(configs, (list, tuple)) or len(configs) > 16:
        raise ValueError("ETF journal config must be an explicit sequence of at most 16 entries")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= 10000:
        raise ValueError("max_rows must be in 1..10000")
    if isinstance(max_mark_age_ms, bool) or not isinstance(max_mark_age_ms, int) or not 1 <= max_mark_age_ms <= 300000:
        raise ValueError("max_mark_age_ms must be in 1..300000")
    if not isinstance(marks, (list, tuple)) or len(marks) > 8192:
        raise ValueError("marks must be an explicit bounded sequence")
    mark_map = {}
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        scope, instrument = mark.get("account_scope"), mark.get("key")
        price, currency = _decimal(mark.get("price")), mark.get("currency")
        asof, source = mark.get("last_event_ms"), mark.get("provider")
        if (not isinstance(scope, str) or not scope or not isinstance(instrument, str) or not instrument or
                price is None or price <= 0 or not isinstance(currency, str) or not currency or
                isinstance(asof, bool) or not isinstance(asof, int) or not isinstance(source, str) or not source):
            continue
        mark_map[(scope, instrument)] = {"price": price, "currency": currency, "asof_ms": asof, "source": source}

    result = []
    seen_paths = set()
    seen_scopes = set()
    for config in configs:
        required = {"path", "scope", "currency", "fee_rate", "source", "instruments"}
        if not isinstance(config, dict) or set(config) != required:
            raise ValueError("ETF mock product config fields mismatch")
        raw_path, scope_parts, currency = config["path"], config["scope"], config["currency"]
        mode = scope_parts[3] if isinstance(scope_parts, list) and len(scope_parts) == 4 else None
        fee_rate, source = _decimal(config["fee_rate"]), config["source"]
        if (not isinstance(raw_path, str) or not Path(raw_path).is_absolute() or
                not isinstance(scope_parts, list) or len(scope_parts) != 4 or
                not all(isinstance(part, str) and 0 < len(part) <= 200 for part in scope_parts) or
                scope_parts[0] != "kis" or scope_parts[2] != "cash" or mode not in {"mock", "mock2"} or
                not isinstance(currency, str) or not 1 <= len(currency) <= 16 or
                fee_rate is None or fee_rate < 0 or fee_rate > Decimal("0.1") or
                not isinstance(source, str) or not 1 <= len(source) <= 128 or
                not isinstance(config["instruments"], dict) or len(config["instruments"]) > 1000 or
                any(not isinstance(symbol, str) or not symbol or len(symbol) > 128 or
                    not isinstance(instrument, str) or not instrument or len(instrument) > 512
                    for symbol, instrument in config["instruments"].items())):
            raise ValueError("ETF mock product config identity invalid")
        path = Path(raw_path)
        resolved = str(path.resolve())
        config_identity = (resolved, mode)
        if config_identity in seen_paths:
            raise ValueError("duplicate ETF journal mode path")
        seen_paths.add(config_identity)
        db = None
        try:
            if not path.is_file():
                continue
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"metadata", "books", "orders", "order_feedback"} <= tables:
                db.rollback()
                db.close()
                continue
            metadata = {row[0]: row[1] for row in db.execute("SELECT key,value FROM metadata")}
            account_id = metadata.get("order_feedback_scope:" + mode)
            if not account_id:
                db.rollback()
                db.close()
                continue
            if account_id != scope_parts[1]:
                db.rollback()
                db.close()
                continue
            raw_scope = json.dumps(scope_parts, separators=(",", ":"), ensure_ascii=False)
            if raw_scope in seen_scopes:
                raise ValueError("duplicate KIS mock account scope configured")
            seen_scopes.add(raw_scope)
            books = list(db.execute("SELECT strategy FROM books ORDER BY strategy LIMIT ?", (max_rows + 1,)))
            orders = list(db.execute("SELECT intent_id,strategy,symbol,side,status,filled,account_scope FROM orders ORDER BY intent_id LIMIT ?", (max_rows + 1,)))
            feedback = list(db.execute("SELECT row_id,event_id,strategy,order_id,payload FROM order_feedback ORDER BY row_id LIMIT ?", (max_rows + 1,)))
            db.commit()
            db.close()
            db = None
            if max(len(books), len(orders), len(feedback)) > max_rows:
                continue
        except (OSError, sqlite3.Error):
            if db is not None:
                db.close()
            continue

        orders_by_id = {row["intent_id"]: dict(row) for row in orders
                        if row["account_scope"] == mode}
        account_scope_hash = public_scope(raw_scope)
        rows_by_strategy = {}
        dedupe = set()
        filled_by_order = {}
        for raw in feedback:
            order = orders_by_id.get(raw["order_id"])
            if order is None:
                continue
            if not isinstance(raw["strategy"], str) or not raw["strategy"]:
                continue
            try:
                event = json.loads(raw["payload"])
            except (TypeError, ValueError):
                rows_by_strategy.setdefault(raw["strategy"], {"realized": Decimal(0), "fees": Decimal(0),
                    "positions": {}, "trades": 0, "orders": set(), "complete": False})
                continue
            if not isinstance(event, dict) or event.get("fill_id") is None or event.get("fill_quantity") is None:
                continue
            identity = (raw["order_id"], event.get("fill_id"))
            if identity in dedupe:
                continue
            dedupe.add(identity)
            event_scope = event.get("account_scope")
            if (order is None or not isinstance(event_scope, dict) or event_scope.get("venue") != "kis" or
                    event_scope.get("environment") != mode or event_scope.get("account") != account_id or
                    order.get("account_scope") != mode):
                continue
            strategy = raw["strategy"]
            symbol = order.get("symbol")
            side = str(order.get("side") or "").lower()
            quantity, price = _decimal(event.get("fill_quantity")), _decimal(event.get("fill_price"))
            if (not isinstance(strategy, str) or not strategy or not isinstance(symbol, str) or not symbol or
                    side not in {"buy", "sell"} or quantity is None or quantity <= 0 or price is None or price <= 0):
                if isinstance(raw["strategy"], str):
                    rows_by_strategy.setdefault(raw["strategy"], {"realized": Decimal(0), "fees": Decimal(0),
                        "positions": {}, "trades": 0, "orders": set(), "complete": False})
                continue
            state = rows_by_strategy.setdefault(strategy, {"realized": Decimal(0), "fees": Decimal(0),
                "positions": {}, "trades": 0, "orders": set(), "complete": True})
            instrument = config["instruments"].get(symbol)
            position = state["positions"].setdefault(symbol, {"quantity": Decimal(0), "average": Decimal(0),
                                                               "mark_instrument": instrument})
            filled_by_order[raw["order_id"]] = exact_sum(filled_by_order.get(raw["order_id"], Decimal(0)), quantity)
            delta = quantity if side == "buy" else quantity.copy_negate()
            old, average = position["quantity"], position["average"]
            new = exact_sum(old, delta)
            realized = Decimal(0)
            if old and (old > 0) != (delta > 0):
                realized = exact_product(min(old.copy_abs(), delta.copy_abs()), exact_difference(price, average))
                if old < 0:
                    realized = realized.copy_negate()
            if not new:
                average = Decimal(0)
            elif not old or (old > 0) != (new > 0):
                average = price
            elif (old > 0) == (delta > 0):
                with localcontext() as context:
                    context.prec = 128
                    average = exact_sum(exact_product(old.copy_abs(), average),
                                        exact_product(delta.copy_abs(), price)) / new.copy_abs()
            position.update(quantity=new, average=average)
            state["realized"] = exact_sum(state["realized"], realized)
            fee = exact_product(exact_product(quantity, price), fee_rate)
            state["fees"] = exact_sum(state["fees"], fee)
            state["trades"] += 1
            state["orders"].add(raw["order_id"])

        for order_id, order in orders_by_id.items():
            expected = _decimal(order["filled"])
            actual = filled_by_order.get(order_id, Decimal(0))
            if expected is None or actual != expected:
                strategy = order["strategy"]
                if isinstance(strategy, str) and strategy:
                    state = rows_by_strategy.setdefault(strategy, {"realized": Decimal(0), "fees": Decimal(0),
                        "positions": {}, "trades": 0, "orders": set(), "complete": False})
                    state["complete"] = False
        for book in books:
            strategy = book["strategy"]
            if isinstance(strategy, str) and strategy and strategy not in rows_by_strategy:
                rows_by_strategy[strategy] = {"realized": None, "fees": None, "positions": {},
                    "trades": 0, "orders": set(), "complete": False}
        working_by_strategy = {}
        for order in orders:
            if order["account_scope"] == mode and str(order["status"]).lower() in {"submitting", "unknown", "pending", "cancel_pending"}:
                working_by_strategy[order["strategy"]] = working_by_strategy.get(order["strategy"], 0) + 1

        for strategy, facts in sorted(rows_by_strategy.items()):
            unrealized = Decimal(0)
            mark_times = []
            position_count = 0
            unrealized_complete = True
            for instrument, position in facts["positions"].items():
                if not position["quantity"]:
                    continue
                position_count += 1
                mark = mark_map.get((raw_scope, position["mark_instrument"]))
                if (mark is None or mark["currency"] != currency or generated_ms is None or
                        mark["asof_ms"] > generated_ms or generated_ms - mark["asof_ms"] > max_mark_age_ms):
                    unrealized_complete = False
                    continue
                unrealized = exact_sum(unrealized,
                    exact_product(position["quantity"], exact_difference(mark["price"], position["average"])))
                mark_times.append(mark["asof_ms"])
            if not unrealized_complete:
                unrealized = None
            realized = facts["realized"]
            fees = facts["fees"]
            net = None
            if facts["complete"] and unrealized is not None and realized is not None and fees is not None:
                net = exact_sum(exact_sum(exact_sum(realized, unrealized), fees.copy_negate()), Decimal(0))
            key = projection_id(strategy, account_scope_hash, mode, currency)
            result.append({
                "key": key, "id": key, "strategy_id": strategy, "account_scope": account_scope_hash,
                "mode": mode, "currency": currency, "realized_pnl": _wire(realized),
                "unrealized_pnl": _wire(unrealized), "fees": _wire(fees), "funding": "0",
                "net_pnl": _wire(net), "fee_convention": "configured KIS mock fee-rate model; not broker commission",
                "pnl_method_version": "etf-journal-mock-fee-v1", "equity_curve": [],
                "position_count": position_count, "working_order_count": working_by_strategy.get(strategy, 0),
                "trades": facts["trades"], "asof_ms": generated_ms,
                "mark_asof_ms": min(mark_times) if mark_times else None,
                "mark_provenance": "LOCAL_VALIDATED_MARKET_SNAPSHOT" if position_count and unrealized is not None else "MISSING_TRUSTED_MARK" if position_count else "NO_OPEN_POSITION",
                "stale": bool(position_count and unrealized is None) or net is None,
                "attribution_status": "COMPLETE" if facts["complete"] else "INCOMPLETE",
                "status": "AVAILABLE" if net is not None else "INCOMPLETE",
                "runtime_status": "UNVERIFIED", "evidence": "ETF_JOURNAL",
            })
    return result[:_MAX_STRATEGIES]
