from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import deque
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from vibetrading.hot_path import HotPathStore
from vibetrading.models import AssetClass, EventType, event_from_dict


def ms(ns: Any) -> int | None:
    return int(ns) // 1_000_000 if ns is not None else None


def number(value: Any) -> str | None:
    try:
        result = Decimal(str(value).replace(",", "").replace("%", "").strip())
        return str(result) if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


class MarketProjection:
    """Bounded latest state; deltas are reconstructed by the existing book logic."""

    def __init__(self) -> None:
        self.hot = HotPathStore(max_trades=40, max_books=1, max_index_quotes=1)
        self.instruments: dict[str, dict] = {}
        self.status: dict[str, dict] = {}
        self.quotes: dict[str, dict] = {}
        self.recent: dict[str, deque] = {}

    def apply(self, data: dict) -> None:
        if data.get("ingest_mode") == "backfill" or not data.get("instrument"):
            return
        event = event_from_dict(data)
        key = event.instrument.key
        if key not in self.instruments and len(self.instruments) >= 512:
            return
        self.instruments[key] = event.instrument.model_dump(mode="json")
        state = self.status.setdefault(key, {})
        state["last_event_ms"] = ms(event.receive_ts_ns)
        state["session"] = event.session
        state["observed_delay_ms"] = event.observed_delay_ms
        if event.event_type == EventType.DATA_GAP:
            self.hot.apply(event)
            state.update(book_valid=False, gap_reason=event.reason)
            return
        applied = self.hot.apply(event)
        if event.event_type == EventType.ORDER_BOOK:
            state["book_valid"] = applied
            if applied:
                state["gap_reason"] = None
        if event.event_type == EventType.BAR_1M:
            self.quotes[key] = {"price": str(event.close), "source_ms": ms(event.source_ts_ns),
                                "receive_ms": ms(event.receive_ts_ns), "kind": "bar_1m"}
        if event.event_type == EventType.INDEX_QUOTE:
            self.quotes[key] = {"price": str(event.value), "source_ms": ms(event.source_ts_ns),
                                "receive_ms": ms(event.receive_ts_ns), "kind": "index_quote"}
        if event.event_type == EventType.TRADE and applied:
            self.recent.setdefault(key, deque(maxlen=40)).append({"id": event.event_id,
                "price": str(event.price), "quantity": str(event.quantity),
                "side": event.aggressor_side.value, "source_ms": ms(event.source_ts_ns),
                "receive_ms": ms(event.receive_ts_ns)})

    def snapshot(self) -> list[dict]:
        rows = []
        for key, inst in sorted(self.instruments.items()):
            state = self.status[key]
            book = self.hot.latest_book(key) if state.get("book_valid") else None
            trade = self.hot.latest_trade(key)
            price = str(trade.price) if trade else self.quotes.get(key, {}).get("price")
            if price is None and book and book.bids and book.asks:
                price = str((book.bids[0].price + book.asks[0].price) / 2)
            rows.append({"key": key, "venue": inst["venue"], "symbol": inst["std_symbol"],
                "asset_class": inst["asset_class"], "currency": inst.get("currency"),
                "section": "crypto" if inst["asset_class"] in {"crypto_perp", "crypto_spot"} else "stocks",
                "price": price, **state,
                "book": None if not book else {"receive_ms": ms(book.receive_ts_ns),
                    "source_ms": ms(book.source_ts_ns),
                    "bids": [[str(x.price), str(x.quantity)] for x in book.bids[:10]],
                    "asks": [[str(x.price), str(x.quantity)] for x in book.asks[:10]]},
                "trades": list(reversed(self.recent.get(key, []))),
                "quote_kind": self.quotes.get(key, {}).get("kind")})
        return rows


def readonly_db(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    connection = sqlite3.connect(path.resolve().as_uri() + suffix, uri=True, timeout=0.2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def runtime_orders(paths: list[Path], limit: int = 500) -> list[dict]:
    orders = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            with readonly_db(path) as db:
                for row in db.execute("SELECT * FROM order_intents ORDER BY updated_ts_ns DESC LIMIT ?", (limit,)):
                    payload = json.loads(row["payload"])
                    inst = payload.get("instrument", {})
                    meta = payload.get("metadata", {})
                    section = "timefolio" if row["destination"] == "timefolio" else (
                        "crypto" if inst.get("asset_class", "").startswith("crypto") else "stocks")
                    orders[row["intent_id"]] = {"id": row["intent_id"], "strategy": meta.get("strategy_id") or row["job_id"],
                        "account": meta.get("account_id") or row["job_id"], "section": section,
                        "mode": meta.get("environment") or ("timefolio" if section == "timefolio" else "unverified"),
                        "symbol": inst.get("std_symbol", ""), "venue": inst.get("venue", ""),
                        "side": payload.get("side"), "status": row["status"],
                        "quantity": payload.get("quantity"), "weight_pct": payload.get("weight_pct"),
                        "price": payload.get("limit_price"), "created_ms": ms(row["created_ts_ns"]),
                        "updated_ms": ms(row["updated_ts_ns"])}
        except (sqlite3.Error, ValueError):
            continue
    return sorted(orders.values(), key=lambda x: x["updated_ms"] or 0, reverse=True)[:limit]


def mm_projection(root: Path) -> tuple[list[dict], list[dict]]:
    strategies, orders = [], []
    if not root.exists():
        return strategies, orders
    status = {}
    try:
        status = json.loads((root / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    # Only the latest 30 runs are queried; PnL comes from each authoritative run summary.
    for path in sorted(root.glob("*.sqlite3"), reverse=True)[:30]:
        try:
            with readonly_db(path) as db:
                for run in db.execute("SELECT * FROM runs"):
                    config = json.loads(run["config"])
                    summary = status if status.get("run_id") == run["run_id"] else json.loads(run["summary"] or "{}")
                    updated = summary.get("updated_at")
                    stamp = int(datetime.fromisoformat(updated).timestamp() * 1000) if updated else run["started_wall_ms"]
                    current = bool(summary.get("running")) and time.time() * 1000 - stamp < 15000
                    strategies.append({"id": run["run_id"], "name": "잔여 주문흐름 MM", "account": "bybit-testnet",
                        "section": "crypto", "mode": run["mode"], "currency": "USDT",
                        "symbol": summary.get("symbol") or config.get("symbol"),
                        "status": summary.get("state", "UNKNOWN"), "running": current, "updated_ms": stamp,
                        "realized_pnl": summary.get("realized_pnl_after_execution_fees_usdt"),
                        "fees": summary.get("execution_fees_usdt"), "unrealized_pnl": None,
                        "equity_change": summary.get("equity_change_usdt"), "pnl_basis": "실행 수수료 차감 · 런 기준"})
                for row in db.execute("SELECT * FROM orders ORDER BY rowid DESC LIMIT 500"):
                    body = json.loads(row["body"])
                    anchor = db.execute("SELECT received_mono,received_wall_ms FROM events WHERE run_id=? ORDER BY id LIMIT 1", (row["run_id"],)).fetchone()
                    created = int(anchor["received_wall_ms"] + (body["created"] - anchor["received_mono"]) * 1000) if anchor and body.get("created") is not None else None
                    # MM order prices and quantities are ticks/lots, not currency/base quantities.
                    orders.append({"id": row["client_id"], "strategy": "잔여 주문흐름 MM", "account": "bybit-testnet",
                        "run_id": row["run_id"], "section": "crypto", "mode": next((s["mode"] for s in strategies if s["id"] == row["run_id"]), "unverified"),
                        "symbol": next((s["symbol"] for s in strategies if s["id"] == row["run_id"]), ""),
                        "venue": "bybit", "side": body.get("side", "").lower(), "status": body.get("status"),
                        "quantity": body.get("qty"), "quantity_unit": "lot", "price": body.get("price"), "price_unit": "tick",
                        "filled": body.get("filled"), "created_ms": created,
                        "updated_ms": path.stat().st_mtime_ns // 1_000_000})
        except (sqlite3.Error, OSError, ValueError):
            continue
    return strategies, orders


def probe_orders(root: Path) -> list[dict]:
    """Include preserved pilot audit records older than exchange API lookback windows."""
    orders = []
    if not root.exists():
        return orders
    for path in sorted(root.glob("bybit_*.sqlite3"), reverse=True)[:30]:
        try:
            # These are preserved/checkpointed audit copies, not active WAL databases.
            with readonly_db(path, immutable=True) as db:
                for row in db.execute("SELECT * FROM orders ORDER BY rowid DESC LIMIT 500"):
                    first = db.execute("SELECT wall_ns,mono_ns FROM transitions WHERE client_id=? ORDER BY id LIMIT 1", (row["client_id"],)).fetchone()
                    last = db.execute("SELECT wall_ns FROM transitions WHERE client_id=? ORDER BY id DESC LIMIT 1", (row["client_id"],)).fetchone()
                    created = ms(first["wall_ns"] + row["decision_ns"] - first["mono_ns"]) if first else None
                    orders.append({"id": "bybit-testnet:" + (row["order_id"] or row["client_id"]),
                        "strategy": "체결 파일럿 · " + row["mode"], "account": "bybit-testnet", "section": "crypto",
                        "mode": "testnet", "venue": "bybit", "symbol": row["symbol"], "side": row["side"].lower(),
                        "status": row["status"].lower(), "price": row["price"], "quantity": row["qty"],
                        "created_ms": created, "updated_ms": ms(last["wall_ns"]) if last else path.stat().st_mtime_ns // 1_000_000})
        except (sqlite3.Error, ValueError, OSError):
            continue
    return orders


def timefolio_metrics(summary: dict) -> dict:
    """Read only explicitly labelled account-wide metrics; never derive from NAV movement."""
    labels = {"turnover_pct": {"회전율", "매매회전율", "누적회전율"},
              "realized_pnl": {"실현손익", "실현손익(원)", "실현손익금액", "누적실현손익"}}
    # The existing general scraper's NAV regex can match chart axis text such as
    # 'Daily NAV 12:00'. Only explicit account-value labels are safe for this view.
    labels["nav"] = {"순자산", "순자산가액", "순자산총액", "총자산", "NAV(원)"}
    metrics = {"turnover_pct": None, "realized_pnl": None, "nav": None}
    for table in summary.get("tables", []):
        table_rows = table.get("rows", [])
        if table_rows and "현재" in table_rows[0] and "기준" in table_rows[0]:
            current_index = table_rows[0].index("현재")
            for row in table_rows[1:]:
                if row and "주간회전율" in "".join(str(row[0]).split()) and len(row) > current_index:
                    match = re.search(r"(-?[\d,]+(?:\.\d+)?)\s*%", str(row[current_index]))
                    if match:
                        metrics["turnover_pct"] = number(match.group(1))
        for row in table.get("rows", []):
            for index, label in enumerate(row[:-1]):
                normalized = "".join(str(label).split())
                for name, choices in labels.items():
                    if normalized in choices:
                        metrics[name] = number(str(row[index + 1]).replace("원", ""))
    return metrics
