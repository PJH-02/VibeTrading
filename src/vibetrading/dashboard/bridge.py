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
from vibetrading.dashboard.projection import (MarketProjection, mm_projection, paper_projection,
                                              paper_projection_paths, runtime_orders)
from vibetrading.dashboard.accounts import bybit_account
from vibetrading.local_snapshot import SnapshotError, local_snapshot_config, read_market_snapshot

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
    from vibetrading.models import Venue
    from vibetrading.providers.crypto_perp import CryptoPerpCollector
    from vibetrading.providers.extended_perp import ExtendedPerpCollector
    while True:
        try:
            collector = (ExtendedPerpCollector if venue in {"grvt", "arcus"} else CryptoPerpCollector)(Venue(venue), symbols)
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
                if time.monotonic() - last_scan >= 10:
                    roots = [data_root / "state" / "vibetrading.sqlite3", data_root / "crypto_state" / "vibetrading.sqlite3"]
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
                    if local_mode and paper_paths:
                        try:
                            paper = await asyncio.to_thread(paper_projection, paper_paths, limit=500)
                        except Exception:
                            errors["paper-projection"] = {"source": "paper-projection",
                                                          "type": "PaperProjectionUnavailable",
                                                          "at_ms": int(time.time() * 1000)}
                        else:
                            orders.extend(paper["orders"])
                            strategies.extend(paper["strategies"])
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
