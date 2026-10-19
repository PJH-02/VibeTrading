"""Authenticated GET-only account observers. No order methods are called here."""
from __future__ import annotations

import os
import time
from decimal import Decimal

from vibetrading.bybit_execution import BybitCredentials, BybitOrderClient


async def bybit_account(session, *, testnet: bool, symbols: tuple[str, ...]) -> tuple[list[dict], list[dict]]:
    prefix = "BYBIT_TESTNET" if testnet else "BYBIT"
    key, secret = os.environ.get(prefix + "_API_KEY"), os.environ.get(prefix + "_API_SECRET")
    if not key or not secret:
        return [], []
    # Reading mainnet does not require or enable the live-order arm.
    client = BybitOrderClient(BybitCredentials(key, secret, testnet), session)
    await client.sync_clock(samples=1)
    mode, account = ("testnet", "bybit-testnet") if testnet else ("live", "bybit-live")
    end = int(time.time() * 1000)
    start = end - 7 * 86400_000 + 1000
    rows, closed, complete = [], [], True
    for endpoint, target in (("/v5/order/history", rows), ("/v5/position/closed-pnl", closed)):
        cursor = None
        for _ in range(10):
            params = {"category": "linear", "startTime": start, "endTime": end, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            result = await client.request("GET", endpoint, params)
            target.extend(result["result"].get("list", []))
            cursor = result["result"].get("nextPageCursor")
            if not cursor:
                break
        if cursor and endpoint.endswith("closed-pnl"):
            complete = False
    # Include live orders as well as terminal history, deduplicated by exchange order ID.
    active = await client.request("GET", "/v5/order/realtime", {"category": "linear", "settleCoin": "USDT", "limit": 50})
    rows.extend(active["result"].get("list", []))
    positions = await client.request("GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT", "limit": 200})
    position_rows = [p for p in positions["result"].get("list", []) if p.get("symbol") in symbols]
    orders = {}
    for row in rows:
        if row.get("symbol") not in symbols:
            continue
        identifier = account + ":" + row["orderId"]
        orders[identifier] = {"id": identifier, "account": account, "section": "crypto", "mode": mode,
            "strategy": "계정 주문 (전략 귀속 미확인)", "symbol": row["symbol"], "venue": "bybit",
            "side": row.get("side", "").lower(), "status": row.get("orderStatus", "unknown"),
            "price": row.get("price"), "quantity": row.get("qty"), "filled": row.get("cumExecQty"),
            "created_ms": int(row.get("createdTime") or end), "updated_ms": int(row.get("updatedTime") or end)}
    realized = sum((Decimal(row["closedPnl"]) for row in closed if row.get("symbol") in symbols), Decimal(0))
    unrealized = sum((Decimal(row.get("unrealisedPnl") or "0") for row in position_rows), Decimal(0))
    strategy = {"id": account + ":7d", "name": "Bybit 계정 집계", "account": account,
        "section": "crypto", "mode": mode, "currency": "USDT", "running": False, "status": "ACCOUNT",
        "updated_ms": end, "realized_pnl": str(realized) if complete else None, "unrealized_pnl": str(unrealized),
        "pnl_basis": "거래소 closedPnl · 최근 7일 · 수집 종목" if complete else "페이지 한도 초과 · 손익 합계 미확인"}
    return [strategy], list(orders.values())
