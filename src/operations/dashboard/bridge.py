from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import signal
import time
from pathlib import Path

import aiohttp
from cryptography.hazmat.primitives import serialization
from operations.dashboard.projection import MarketProjection, mm_projection, paper_projection, paper_projection_paths, runtime_orders
from operations.dashboard.pnl import (etf_journal_pnl_projection, merge_dashboard_strategies,
                                       merge_runtime_registry, native_ledger_pnl_projection,
                                       paper_pnl_projection)
from operations.dashboard.accounts import bybit_account
from market.local_snapshot import SnapshotError, local_snapshot_config, read_market_snapshot

log = logging.getLogger("dashboard.bridge")


def write_json(path: Path, data) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def public_key(path: Path) -> dict:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None).public_key().public_numbers()
    encode = lambda n: base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).decode().rstrip("=")
    return {"kty": "RSA", "n": encode(key.n), "e": encode(key.e), "alg": "RSA-OAEP-256", "ext": True}


class ProjectionBus:
    def __init__(self, markets: MarketProjection):
        self.markets = markets

    async def publish_market_event(self, event):
        self.markets.apply(event.model_dump(mode="json"))


async def observe_public(venue: str, symbols: tuple, bus: ProjectionBus, errors: dict) -> None:
    # A separate public connection obtains its own initial snapshot. Joining a delta-only
    # live stream midway cannot safely reconstruct an order book.
    from core.models import Venue
    from feeds.providers import create_perp_collector
    while True:
        try:
            collector = create_perp_collector(Venue(venue), symbols)
            await collector.run(bus)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors[venue] = {"source": venue, "type": type(exc).__name__, "at_ms": int(time.time() * 1000)}
            log.warning("Public observer reconnect: %s (%s)", venue, type(exc).__name__)
            await asyncio.sleep(5)


async def run(*, stop: asyncio.Event | None = None) -> None:
    profile = os.environ.get("VIBETRADING_RUNTIME", "legacy")
    if profile not in {"local", "legacy", "nats"}:
        raise ValueError("Unknown dashboard runtime profile")
    local_mode = profile == "local"
    snapshot_file, snapshot_budgets = local_snapshot_config(os.environ) if local_mode else (None, None)
    paper_paths = paper_projection_paths(os.environ) if local_mode else ()
    url = os.environ["DASHBOARD_SITE_URL"].rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("DASHBOARD_SITE_URL must use HTTPS")
    token = os.environ["DASHBOARD_INGEST_TOKEN"]
    bypass = os.environ["DASHBOARD_SITE_BYPASS_TOKEN"]
    data_root = Path(os.environ.get("DASHBOARD_DATA_ROOT", "/data"))
    monitor_root = Path(os.environ.get("DASHBOARD_MONITOR_ROOT", "/monitor"))
    monitor_root.mkdir(parents=True, exist_ok=True)
    projection = None if local_mode else MarketProjection()
    bus = None if local_mode else ProjectionBus(projection)
    errors: dict = {}
    telemetry: dict = {}
    connection = None
    if local_mode:
        if not paper_paths:
            # Market data already belongs to the engine. Without explicit paper
            # DBs do not invent strategy telemetry.
            errors["local-telemetry"] = {"source": "local-facts", "type": "StrategyTelemetryNotConfigured",
                                         "at_ms": int(time.time() * 1000)}
    else:
        import nats
        connection = await nats.connect(os.environ.get("NATS_URL", "nats://nats:4222"), max_reconnect_attempts=-1)

    async def on_event(message):
        try:
            payload = json.loads(message.data)
            if message.subject.startswith("md."):
                projection.apply(payload)
            elif message.subject == "system.dashboard_strategy":
                if payload.get("id") and (payload["id"] in telemetry or len(telemetry) < 100):
                    fields = ("id", "name", "account", "section", "mode", "currency", "symbol", "status",
                              "running", "updated_ms", "realized_pnl", "unrealized_pnl", "pnl_basis")
                    telemetry[payload["id"]] = {field: payload.get(field) for field in fields}
        except Exception as exc:
            errors["nats"] = {"source": "nats", "type": type(exc).__name__, "at_ms": int(time.time() * 1000)}

    if connection is not None:
        await connection.subscribe("md.>", cb=on_event, pending_msgs_limit=1024, pending_bytes_limit=2_000_000)
        await connection.subscribe("system.dashboard_strategy", cb=on_event)
    tasks = []
    symbols = tuple(os.environ.get("DASHBOARD_CRYPTO_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(","))
    venues = [] if local_mode else os.environ.get("DASHBOARD_CRYPTO_VENUES", "binance,grvt,arcus").split(",")
    for venue in venues:
        tasks.append(asyncio.create_task(observe_public(venue, symbols, bus, errors)))
    stop = stop if stop is not None else asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    key = public_key(monitor_root / "private.pem")
    sent_orders = {}
    strategies, orders, account_state = [], [], []
    last_scan = 0.0
    mark_snapshot = None
    exchange_strategies, exchange_orders = [], []
    headers = {"Authorization": "Bearer " + token, "OAI-Sites-Authorization": "Bearer " + bypass}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async def observe_accounts():
            nonlocal exchange_strategies, exchange_orders
            while True:
                next_strategies, next_orders = [], []
                for testnet in (True, False):
                    try:
                        observed, records = await bybit_account(session, testnet=testnet, symbols=symbols)
                        next_strategies.extend(observed)
                        next_orders.extend(records)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors["bybit-account-" + str(testnet)] = {"source": "bybit-account", "type": type(exc).__name__, "at_ms": int(time.time() * 1000)}
                exchange_strategies, exchange_orders = next_strategies, next_orders
                await asyncio.sleep(60)
        if "bybit" in venues:
            tasks.append(asyncio.create_task(observe_accounts()))
        try:
            while not stop.is_set():
                if time.monotonic() - last_scan >= 2:
                    roots = [data_root / "state" / "vibetrading.sqlite3", data_root / "crypto_state" / "vibetrading.sqlite3"]
                    mark_snapshot = None
                    if local_mode and (paper_paths or os.environ.get("NATIVE_EXECUTION_LEDGER_PATHS")
                                       or os.environ.get("ETF_PNL_CONFIGS")):
                        try:
                            mark_snapshot = await asyncio.to_thread(
                                read_market_snapshot, snapshot_file, snapshot_budgets)
                        except SnapshotError:
                            mark_snapshot = None
                    orders = await asyncio.to_thread(runtime_orders, roots)
                    mm, mm_orders = await asyncio.to_thread(mm_projection, Path(os.environ.get("DASHBOARD_MM_ROOT", str(data_root / "liquidity_mm"))))
                    strategies = mm + [{**s, "running": bool(s.get("running")) and time.time() * 1000 - s.get("updated_ms", 0) < 45000}
                                       for s in telemetry.values()]
                    orders.extend(mm_orders)
                    try:
                        orders.extend(json.loads((monitor_root / "pilot_orders.json").read_text(encoding="utf-8")))
                    except (OSError, ValueError):
                        pass
                    orders.extend(exchange_orders)
                    strategies.extend(exchange_strategies)
                    pnl_rows = []
                    if local_mode and paper_paths:
                        try:
                            paper = await asyncio.to_thread(paper_projection, paper_paths, limit=500,
                                                           include_observability=True)
                        except Exception:
                            errors["paper-projection"] = {"source": "paper-projection",
                                                          "type": "PaperProjectionUnavailable",
                                                          "at_ms": int(time.time() * 1000)}
                        else:
                            orders.extend(paper["orders"])
                            strategies.extend(paper["strategies"])
                            now_ms = int(time.time() * 1000)
                            paper_marks = [
                                {**market, "account_scope": account["scope"]}
                                for account in paper["accounts"]
                                for market in (mark_snapshot or {}).get("markets", [])
                            ]
                            paper_pnl_rows = paper_pnl_projection(
                                paper, generated_ms=now_ms, marks=paper_marks,
                                max_mark_age_ms=max(1, min(300000, int(snapshot_budgets.max_age_seconds * 1000))))
                            pnl_rows = sorted(paper_pnl_rows,
                                key=lambda row: (row["strategy_id"] != "UNALLOCATED", row["key"]))[:30]
                            errors.pop("paper-projection", None)
                            paper_errors = {
                                "paper-projection:" + str(item["source"]): {
                                    "source": "paper-projection", "type": item["type"],
                                    "reason": item["reason"], "at_ms": int(time.time() * 1000)}
                                for item in paper["errors"]}
                            for error_key in [key for key in errors if key.startswith("paper-projection:")]:
                                if error_key not in paper_errors:
                                    errors.pop(error_key, None)
                            errors.update(paper_errors)
                    latency_rows = []
                    latency_status = "NOT_CONFIGURED"
                    latency_error = None
                    latency_paths_raw = os.environ.get("DASHBOARD_LATENCY_PATHS")
                    if latency_paths_raw:
                        try:
                            latency_paths = json.loads(latency_paths_raw)
                            if (not isinstance(latency_paths, list) or len(latency_paths) > 32 or
                                    any(not isinstance(path, str) or not Path(path).is_absolute()
                                        for path in latency_paths)):
                                raise ValueError("invalid explicit latency paths")
                            from operations.dashboard.latency import read_latency_snapshots
                            latency_rows = read_latency_snapshots([Path(path) for path in latency_paths])
                            latency_status = "AVAILABLE" if latency_rows else "UNAVAILABLE"
                        except (ImportError, TypeError, ValueError, OSError) as exc:
                            latency_rows = []
                            latency_status = "UNAVAILABLE"
                            latency_error = type(exc).__name__
                    else:
                        latency_error = None
                    native_rows = []
                    native_status = "NOT_CONFIGURED"
                    native_error = None
                    native_paths_raw = os.environ.get("NATIVE_EXECUTION_LEDGER_PATHS")
                    if native_paths_raw:
                        try:
                            native_paths = json.loads(native_paths_raw)
                            product_metadata_raw = os.environ.get("NATIVE_PNL_PRODUCTS", "[]")
                            if len(product_metadata_raw.encode("utf-8")) > 65536:
                                raise ValueError("native product metadata exceeds byte budget")
                            product_metadata = json.loads(product_metadata_raw)
                            if (not isinstance(native_paths, list) or len(native_paths) > 16 or
                                    any(not isinstance(path, str) or not Path(path).is_absolute()
                                        for path in native_paths) or not isinstance(product_metadata, list)):
                                raise ValueError("invalid explicit native PnL configuration")
                            native_scopes = {json.dumps(item.get("scope"), separators=(",", ":"),
                                                         ensure_ascii=False)
                                             for item in product_metadata
                                             if isinstance(item, dict) and isinstance(item.get("scope"), list)}
                            native_marks = [
                                {**market, "account_scope": scope}
                                for scope in native_scopes
                                for market in (mark_snapshot or {}).get("markets", [])
                            ]
                            native_rows = native_ledger_pnl_projection(
                                [Path(path) for path in native_paths], product_metadata=product_metadata,
                                marks=native_marks,
                                max_mark_age_ms=(max(1, min(300000, int(snapshot_budgets.max_age_seconds * 1000)))
                                    if snapshot_budgets is not None else 10000),
                                generated_ms=int(time.time() * 1000))
                            native_rows = sorted(native_rows,
                                key=lambda row: (row["strategy_id"] != "UNALLOCATED", row["key"]))[:30]
                            native_status = "AVAILABLE" if native_rows else "UNAVAILABLE"
                        except (TypeError, ValueError, OSError) as exc:
                            native_status = "UNAVAILABLE"
                            native_error = type(exc).__name__
                    pnl_rows.extend(native_rows)
                    etf_status = "NOT_CONFIGURED"
                    etf_error = None
                    etf_configs_raw = os.environ.get("ETF_PNL_CONFIGS")
                    if etf_configs_raw:
                        try:
                            if len(etf_configs_raw.encode("utf-8")) > 65536:
                                raise ValueError("ETF PnL config exceeds byte budget")
                            etf_configs = json.loads(etf_configs_raw)
                            if (not isinstance(etf_configs, list) or len(etf_configs) > 16 or
                                    any(not isinstance(item, dict) or not isinstance(item.get("path"), str) or
                                        not Path(item["path"]).is_absolute() for item in etf_configs)):
                                raise ValueError("invalid explicit ETF PnL config")
                            etf_scopes = {json.dumps(item.get("scope"), separators=(",", ":"),
                                                     ensure_ascii=False)
                                          for item in etf_configs if isinstance(item.get("scope"), list)}
                            etf_marks = [
                                {**market, "account_scope": scope}
                                for scope in etf_scopes
                                for market in (mark_snapshot or {}).get("markets", [])
                            ]
                            etf_rows = etf_journal_pnl_projection(
                                etf_configs, marks=etf_marks,
                                max_mark_age_ms=(max(1, min(300000, int(snapshot_budgets.max_age_seconds * 1000)))
                                    if snapshot_budgets is not None else 10000),
                                generated_ms=int(time.time() * 1000))
                            pnl_rows.extend(etf_rows[:15])
                            etf_status = "AVAILABLE" if etf_rows else "UNAVAILABLE"
                        except (TypeError, ValueError, OSError) as exc:
                            etf_status = "UNAVAILABLE"
                            etf_error = type(exc).__name__
                    pnl_rows = merge_runtime_registry(
                        pnl_rows, latency_rows, generated_ms=int(time.time() * 1000))
                    pnl_rows = merge_dashboard_strategies(
                        pnl_rows, strategies, generated_ms=int(time.time() * 1000))
                    orders = sorted(orders, key=lambda x: x.get("updated_ms") or 0, reverse=True)[:1000]
                    try:
                        account_state = json.loads((monitor_root / "timefolio.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        account_state = []
                    last_scan = time.monotonic()
                changed = [o for o in orders if sent_orders.get(o["id"]) != json.dumps(o, sort_keys=True)]
                if local_mode:
                    try:
                        local_snapshot = await asyncio.to_thread(read_market_snapshot, snapshot_file, snapshot_budgets)
                        markets = local_snapshot["markets"]
                        errors.pop("local-snapshot", None)
                    except SnapshotError as exc:
                        markets = []
                        errors["local-snapshot"] = {"source": "local-snapshot", "type": type(exc).__name__,
                                                    "at_ms": int(time.time() * 1000)}
                else:
                    markets = projection.snapshot()
                snapshot = {"schema_version": 1, "generated_ms": int(time.time() * 1000),
                    "markets": markets, "strategies": strategies, "orders": changed,
                    "observability": {"pnl": pnl_rows[:100], "latency": latency_rows[:32],
                                      "latency_status": latency_status,
                                      "latency_error": latency_error,
                                      "native_status": native_status,
                                      "native_error": native_error,
                                      "etf_status": etf_status,
                                      "etf_error": etf_error,
                                      "end_to_end_ms": None, "end_to_end_status": "SEGMENTED"},
                    "timefolio": account_state, "public_key": key,
                    "errors": list(errors.values())[-20:], "nats_connected": bool(connection is not None and connection.is_connected),
                    "sampling_ms": 2000}
                try:
                    async with session.post(url + "/api/ingest", json=snapshot, headers=headers, allow_redirects=False) as response:
                        if response.status != 200:
                            # Do not print response bodies, credentials, request headers, or redirects.
                            raise RuntimeError("HTTP " + str(response.status))
                        result = await response.json()
                    for order in changed:
                        sent_orders[order["id"]] = json.dumps(order, sort_keys=True)
                    write_json(monitor_root / "accounts.json", result.get("accounts", []))
                    write_json(monitor_root / "health.json", {"updated_ms": int(time.time() * 1000), "markets": len(snapshot["markets"]), "ok": True})
                except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as exc:
                    log.warning("Dashboard upload retry: %s", type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if connection is not None:
                await connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run())
