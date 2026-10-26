from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from decimal import Decimal
from typing import Any

from .bus import BufferedMarketEventBus, InMemoryEventBus, NatsJetStreamBus
from .brokers import broker_for_name
from storage.compactor import ParquetCompactor
from storage.archive import archive_check
from .logging_utils import configure_logging
from storage.parquet_store import ParquetBatchWriter
from feeds.providers.kiwoom import KiwoomAuthClient, KiwoomCollector, parse_kiwoom_symbols
from feeds.providers.kis import KisCollector, KisParticipantFlowCollector, parse_kis_subscriptions
from feeds.providers.kis_client import KisAuthClient
from feeds.providers.toss import TossAuthClient, TossCollector, parse_toss_symbols
from feeds.providers.hyperliquid import HyperliquidCatalog, HyperliquidCollector
from feeds.providers.binance import BinanceCollector
from feeds.providers.crypto_perp import CryptoPerpCollector, SYMBOLS as CRYPTO_SYMBOLS
from feeds.providers.extended_perp import ExtendedPerpCollector
from feeds.providers.ls_market import LSKospi200ForeignCollector, LSOverseasDelayedCollector
from core.models import Venue
from feeds.providers.universe import InstrumentUniverseResolver
from .real_runner import RealExecutionRunner, RealExecutionRunnerConfig
from .settings import get_settings
from .status import runtime_status
from storage.storage_guard import StorageGuard
from storage.storage_worker import StorageWorker
from .strategy_runner import LiveStrategyRunner
from .timefolio import AsyncTimefolioBrowserManager, TimefolioCredentials, TimefolioStrategyRunner, TimefolioStrategyRunnerConfig
from .runtime import run_runtime
from .bybit_execution import BybitAudit, BybitExecutionProbe
from market.market_source_status import source_status
from market.market_policy import MarketDataPolicyBus
from .telegram import TelegramController
from .health import check as health_check
from feeds.universe_config import configured_binance_securities, configured_hyperliquid_securities


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _secrets_from_settings() -> list[str]:
    settings = get_settings()
    secrets: list[str] = []
    for provider in settings.providers.values():
        for key, value in provider.values.items():
            if any(word in key.upper() for word in ("KEY", "SECRET", "TOKEN", "PASSWORD", "PRIVATE", "ACCOUNT", "PW", "ID")):
                secrets.append(value)
    for key in ("TIMEFOLIO_ID", "TIMEFOLIO_PW", "TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID", "TELEGRAM_CHAT_ID"):
        value = settings.env.get(key)
        if value:
            secrets.append(value)
    return secrets


async def _cmd_collector(args: argparse.Namespace) -> int:
    settings = get_settings()
    guard = StorageGuard(settings)
    decision = guard.evaluate()
    if decision.fail_closed:
        _print_json({"status": "BLOCKED", "reason": "storage_fail_closed", "storage": decision.as_dict()})
        return 2
    bus = NatsJetStreamBus(settings)
    collector = _collector_for_args(args)
    await bus.connect()
    try:
        buffered = BufferedMarketEventBus(bus)
        policy = MarketDataPolicyBus(buffered)
        try:
            await collector.run(policy, duration_sec=args.duration_sec)
        finally:
            await buffered.close()
    finally:
        await bus.close()
    return 0


def _collector_for_args(args: argparse.Namespace):
    if args.provider == "ls-kospi200-foreign":
        return LSKospi200ForeignCollector()
    if args.provider == "ls-overseas-delayed":
        return LSOverseasDelayedCollector()
    if args.provider in {"binance-perp", "bitget-perp", "bybit-perp", "arcus-perp", "grvt-perp"}:
        requested = tuple(x.strip().upper() for x in args.symbols.split(",") if x.strip()) if args.symbols and args.symbols != "@crypto" else CRYPTO_SYMBOLS
        cls = ExtendedPerpCollector if args.provider in {"arcus-perp", "grvt-perp"} else CryptoPerpCollector
        return cls(Venue(args.provider.split("-", 1)[0]), requested)
    if args.provider == "kis-flow":
        raise RuntimeError("KIS domestic-stock participant flow is not verified KOSPI200 futures foreign flow")
    if args.provider == "kis":
        auto_symbols = args.symbols.strip().lower()
        if auto_symbols in {"auto:commodity", "auto:commodities"}:
            return KisCollector(auto_commodity=True)
        if auto_symbols in {"auto:index-derivatives", "auto:derivatives", "auto:options", "auto:all"}:
            return KisCollector(auto_commodity=auto_symbols == "auto:all", auto_index_derivatives=True)
        if auto_symbols in {"auto:overseas-nq", "auto:nq", "auto:emini-nasdaq"}:
            raise RuntimeError("KIS CME realtime ES/NQ requires a paid market-data subscription and is disabled")
        return KisCollector(subscriptions=parse_kis_subscriptions(args.symbols))
    if args.provider == "kiwoom":
        if args.symbols.strip().lower() == "@universe":
            symbols = InstrumentUniverseResolver().configured_symbols("kiwoom")
            if not symbols:
                raise ValueError("kiwoom @universe is empty; add instruments to config/universe.yaml")
            return KiwoomCollector(subscriptions=parse_kiwoom_symbols(",".join(symbols)))
        return KiwoomCollector(subscriptions=parse_kiwoom_symbols(args.symbols))
    if args.provider == "toss":
        if args.symbols.strip().lower() == "@universe":
            symbols = InstrumentUniverseResolver().configured_symbols("toss")
            if not symbols:
                raise ValueError("toss @universe is empty; add instruments to config/universe.yaml")
            return TossCollector(subscriptions=parse_toss_symbols(",".join(symbols)))
        return TossCollector(subscriptions=parse_toss_symbols(args.symbols))
    if args.provider == "hyperliquid":
        configured = configured_hyperliquid_securities()
        if args.symbols.strip().lower() == "@universe":
            selected = configured
        else:
            requested = {value.strip().upper() for value in args.symbols.split(",") if value.strip()}
            selected = {
                provider_symbol: security
                for provider_symbol, security in configured.items()
                if provider_symbol in requested or security.symbol.upper() in requested
            }
            matched = set(selected) | {security.symbol.upper() for security in selected.values()}
            missing = sorted(requested - matched)
            if missing:
                raise ValueError(f"Hyperliquid symbols are not in the explicit universe allowlist: {missing}")
        if not selected:
            raise ValueError("hyperliquid universe is empty; add typed instruments to config/universe.yaml")
        return HyperliquidCollector(selected)
    if args.provider == "binance":
        configured = configured_binance_securities()
        if args.symbols.strip().lower() == "@universe":
            selected = configured
        else:
            requested = {value.strip().upper() for value in args.symbols.split(",") if value.strip()}
            selected = {symbol: security for symbol, security in configured.items() if symbol in requested or security.symbol.upper() in requested}
            matched = set(selected) | {security.symbol.upper() for security in selected.values()}
            missing = sorted(requested - matched)
            if missing:
                raise ValueError(f"Binance symbols are not in the explicit universe allowlist: {missing}")
        if not selected:
            raise ValueError("binance universe is empty; add typed instruments to config/universe.yaml")
        return BinanceCollector(selected)
    raise ValueError(f"Unsupported provider: {args.provider}")


async def _cmd_record_once(args: argparse.Namespace) -> int:
    settings = get_settings()
    decision = StorageGuard(settings).evaluate()
    if decision.fail_closed:
        _print_json({"status": "BLOCKED", "reason": "storage_fail_closed", "storage": decision.as_dict()})
        return 2
    bus = InMemoryEventBus()
    collector = _collector_for_args(args)
    await bus.connect()
    await collector.run(bus, duration_sec=args.duration_sec)
    writer = ParquetBatchWriter(settings)
    for message in bus.messages:
        writer.add(message.data)
    paths = writer.flush_all()
    _print_json({"provider": args.provider, "messages": len(bus.messages), "files": [str(path) for path in paths]})
    return 0


async def _cmd_storage_worker(args: argparse.Namespace) -> int:
    stats = await StorageWorker().run(duration_sec=args.duration_sec)
    _print_json(stats)
    return 0


async def _cmd_nats_init(args: argparse.Namespace) -> int:
    del args
    bus = NatsJetStreamBus()
    await bus.connect(initialize=True)
    await bus.close()
    _print_json({"status": "ok", "market_stream": get_settings().nats.stream_name})
    return 0


async def _cmd_nats_reset_storage_consumer(args: argparse.Namespace) -> int:
    del args
    bus = NatsJetStreamBus()
    await bus.connect()
    try:
        result = await bus.reset_storage_consumer_if_empty()
    finally:
        await bus.close()
    _print_json(result)
    return 0


async def _cmd_health_check(args: argparse.Namespace) -> int:
    result = health_check(get_settings(), args.component, args.max_age_seconds)
    _print_json(result)
    return 0 if result.get("ok") else 2


async def _cmd_archive_check(args: argparse.Namespace) -> int:
    del args
    _print_json(await asyncio.to_thread(archive_check))
    return 0


async def _cmd_strategy(args: argparse.Namespace) -> int:
    runner = LiveStrategyRunner(job_id=args.job, strategy_module=args.strategy_module)
    produced = await runner.run(duration_sec=args.duration_sec)
    _print_json({"signals": produced, "count": len(produced)})
    return 0


async def _cmd_timefolio_login(args: argparse.Namespace) -> int:
    settings = get_settings()
    account = settings.timefolio_account(args.account)
    if not account.has_credentials:
        _print_json({"account": args.account, "status": "SKIPPED_MISSING_CREDENTIALS"})
        return 0
    async with AsyncTimefolioBrowserManager(headless=not args.show_browser) as manager:
        status = await manager.login(args.account, TimefolioCredentials(account.username or "", account.password or ""))
        _print_json(status.__dict__)
    return 0


async def _cmd_timefolio_strategy(args: argparse.Namespace) -> int:
    settings = get_settings()
    submit = bool(args.submit and not args.no_submit)
    if submit and not settings.timefolio_live_submit_enabled:
        _print_json({"account": args.account, "status": "BLOCKED", "reason": "TIMEFOLIO_LIVE_SUBMIT_ENABLED is false"})
        return 2
    config = TimefolioStrategyRunnerConfig(
        job_id=args.job,
        account_id=args.account,
        strategy_module=args.strategy_module,
        entry_weight_pct=Decimal(str(args.entry_weight_pct)),
        exit_weight_pct=Decimal(str(args.exit_weight_pct)) if args.exit_weight_pct is not None else None,
        max_order_notional=Decimal(str(args.max_order_notional)),
        max_position_notional=Decimal(str(args.max_position_notional)),
        opp_tick=args.opp_tick,
        submit=submit,
        show_browser=args.show_browser,
    )
    results = await TimefolioStrategyRunner(config).run(duration_sec=args.duration_sec)
    _print_json({"job": args.job, "account": args.account, "results": results, "count": len(results)})
    return 0


async def _cmd_real_exec(args: argparse.Namespace) -> int:
    settings = get_settings()
    submit = bool(args.submit and not args.no_submit)
    if submit and not settings.live_order_enabled:
        _print_json({"account": args.account, "status": "BLOCKED", "reason": "LIVE_ORDER_ENABLED is false"})
        return 2
    config = RealExecutionRunnerConfig(
        job_id=args.job,
        account_id=args.account,
        destination_name=args.broker,
        max_order_notional=Decimal(str(args.max_order_notional)),
        max_position_notional=Decimal(str(args.max_position_notional)),
        default_quantity=Decimal(str(args.quantity)) if args.quantity is not None else None,
        default_weight_pct=Decimal(str(args.weight_pct)) if args.weight_pct is not None else None,
        submit=submit,
    )
    results = await RealExecutionRunner(config).run(duration_sec=args.duration_sec)
    _print_json({"job": args.job, "account": args.account, "broker": args.broker, "results": results, "count": len(results)})
    return 0


async def _cmd_universe(args: argparse.Namespace) -> int:
    resolver = InstrumentUniverseResolver()
    if args.provider == "hyperliquid":
        assets = await resolver.resolve_hyperliquid_assets()
        _print_json(
            {
                "assets": assets,
                "configured_tokenized_equities": resolver.resolve_hyperliquid_tokenized_equities(),
            }
        )
    elif args.provider == "index-proxy-etf":
        _print_json({key: value.model_dump(mode="json") for key, value in resolver.resolve_index_proxy_etfs().items()})
    elif args.provider == "kis":
        result = await resolver.resolve_kis_index_derivatives(
            underlying=args.underlying,
            underlying_price=Decimal(str(args.underlying_price)) if args.underlying_price is not None else None,
        )
        _print_json({key: [item.model_dump(mode="json") for item in value] for key, value in result.items()})
    elif args.provider == "kis-commodity":
        result = await resolver.resolve_kis_commodity_futures(
            products=tuple(item for item in args.products.split(",") if item.strip())
        )
        _print_json({key: [item.model_dump(mode="json") for item in value] for key, value in result.items()})
    elif args.provider == "kis-overseas":
        result = await resolver.resolve_kis_overseas_futures(
            products=tuple(item for item in args.overseas_products.split(",") if item.strip())
        )
        _print_json({key: [item.model_dump(mode="json") for item in value] for key, value in result.items()})
    else:
        raise ValueError(f"Unsupported universe provider: {args.provider}")
    return 0


async def _cmd_hyperliquid_assets(args: argparse.Namespace) -> int:
    del args
    assets = await HyperliquidCatalog().assets()
    _print_json([asset.__dict__ for asset in assets])
    return 0


async def _cmd_provider_check(args: argparse.Namespace) -> int:
    if args.provider == "kis":
        result = await KisAuthClient().auth_check(include_access_token=not args.skip_access_token)
        _print_json(result)
        return 0 if result.get("status") in {"ok", "SKIPPED_MISSING_CREDENTIALS"} else 1
    if args.provider == "binance":
        pairs = await BinanceCollector(symbols=args.symbols.split(",") if args.symbols else None).resolve_symbols()
        _print_json({"provider": "binance", "status": "ok", "symbols": pairs[: args.limit], "count": len(pairs)})
        return 0
    if args.provider == "hyperliquid":
        assets = await HyperliquidCatalog().assets()
        resolver = InstrumentUniverseResolver()
        _print_json(
            {
                "provider": "hyperliquid",
                "status": "ok",
                "assets": [asset.__dict__ for asset in assets[: args.limit]],
                "count": len(assets),
                "configured_tokenized_equities": resolver.resolve_hyperliquid_tokenized_equities(),
            }
        )
        return 0
    if args.provider == "kiwoom":
        result = await KiwoomAuthClient().auth_check()
        if args.symbols and result.get("status") == "ok":
            parsed = parse_kiwoom_symbols(args.symbols)
            result["symbols"] = [item.__dict__ for item in parsed[: args.limit]]
            result["count"] = len(parsed)
        _print_json(result)
        return 0 if result.get("status") in {"ok", "SKIPPED_MISSING_CREDENTIALS"} else 1
    if args.provider == "toss":
        result = await TossAuthClient().auth_check(include_accounts=not args.skip_accounts)
        if args.symbols and result.get("status") == "ok":
            parsed = parse_toss_symbols(args.symbols)
            result["symbols"] = [item.__dict__ for item in parsed[: args.limit]]
            result["count"] = len(parsed)
        _print_json(result)
        return 0 if result.get("status") in {"ok", "SKIPPED_MISSING_CREDENTIALS"} else 1
    raise ValueError(f"Unsupported provider: {args.provider}")


async def _cmd_account_check(args: argparse.Namespace) -> int:
    if args.provider == "kis":
        client = KisAuthClient()
        if not client.has_credentials:
            _print_json({"provider": "kis", "status": "SKIPPED_MISSING_CREDENTIALS"})
            return 0
        try:
            result = await client.account_state_summary()
        except Exception as exc:
            _print_json({"provider": "kis", "status": "failed", "reason": str(exc)})
            return 1
        if result.get("status") == "SKIPPED_UNSUPPORTED_ACCOUNT_PRODUCT_CODE":
            _print_json(result)
            return 0
        result["status"] = "ok"
        _print_json(result)
        return 0
    if args.provider in {"kiwoom", "toss"}:
        broker = broker_for_name(args.provider)
        try:
            await broker.connect()
            state = await broker.account_state()
        except Exception as exc:
            _print_json({"provider": args.provider, "status": "failed", "reason": str(exc)})
            return 1
        _print_json({"provider": args.provider, "status": "ok", "account_state": state})
        return 0
    raise ValueError(f"Unsupported provider: {args.provider}")


async def _cmd_broker_check(args: argparse.Namespace) -> int:
    try:
        broker = broker_for_name(args.broker)
        await broker.connect()
        state = await broker.account_state()
    except Exception as exc:
        _print_json({"broker": args.broker, "status": "failed", "reason": str(exc)})
        return 1
    _print_json({"broker": args.broker, "status": "ok", "account_state": state})
    return 0


async def _main_async(args: argparse.Namespace) -> int:
    if args.command == "bybit-testnet-probe":
        probe = BybitExecutionProbe()
        try:
            result = await probe.run(symbol=args.symbol,side=args.side,qty=Decimal(args.qty),mode=args.mode,
                                     timeout_sec=args.timeout_sec,max_notional=Decimal(args.max_notional),
                                     reduce_only=args.reduce_only,market_trigger=args.market_trigger)
            _print_json(result)
            return 0 if result["order"]["status"] == "FILLED" else 2
        finally:
            probe.audit.close()
    if args.command == "bybit-kill-switch":
        audit=BybitAudit(get_settings().state_dir/"bybit_testnet"/"orders.sqlite3")
        try:
            audit.set_kill(args.state == "on")
        finally:
            audit.close()
        if args.state == "on":
            try:
                probe=BybitExecutionProbe()
                try:
                    result=await probe.activate_kill_switch()
                finally:
                    probe.audit.close()
            except Exception as exc:
                result={"kill_switch":"on","cancel_submitted":[],"failures":[{"error":str(exc)}]}
            _print_json(result)
            return 0 if not result["failures"] else 2
        _print_json({"testnet_kill_switch":"off"})
        return 0
    if args.command == "market-source-status":
        _print_json(source_status())
        return 0
    if args.command == "collector":
        return await _cmd_collector(args)
    if args.command == "record-once":
        return await _cmd_record_once(args)
    if args.command == "storage-worker":
        return await _cmd_storage_worker(args)
    if args.command == "nats-init":
        return await _cmd_nats_init(args)
    if args.command == "nats-reset-storage-consumer":
        return await _cmd_nats_reset_storage_consumer(args)
    if args.command == "health-check":
        return await _cmd_health_check(args)
    if args.command == "archive-check":
        return await _cmd_archive_check(args)
    if args.command == "strategy":
        return await _cmd_strategy(args)
    if args.command == "timefolio-login":
        return await _cmd_timefolio_login(args)
    if args.command == "timefolio-strategy":
        return await _cmd_timefolio_strategy(args)
    if args.command == "real-exec":
        return await _cmd_real_exec(args)
    if args.command == "universe":
        return await _cmd_universe(args)
    if args.command == "hyperliquid-assets":
        return await _cmd_hyperliquid_assets(args)
    if args.command == "provider-check":
        return await _cmd_provider_check(args)
    if args.command == "account-check":
        return await _cmd_account_check(args)
    if args.command == "broker-check":
        return await _cmd_broker_check(args)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VibeTrading operational runtime")
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    collector = sub.add_parser("collector")
    collector.add_argument("--provider", required=True, choices=["kis", "kis-flow", "kiwoom", "toss", "hyperliquid", "binance", "binance-perp", "bitget-perp", "bybit-perp", "arcus-perp", "grvt-perp", "ls-kospi200-foreign", "ls-overseas-delayed"])
    collector.add_argument("--symbols", default="")
    collector.add_argument("--duration-sec", type=float)

    record_once = sub.add_parser("record-once")
    record_once.add_argument("--provider", required=True, choices=["kis", "kis-flow", "kiwoom", "toss", "hyperliquid", "binance", "binance-perp", "bitget-perp", "bybit-perp", "arcus-perp", "grvt-perp", "ls-kospi200-foreign", "ls-overseas-delayed"])
    record_once.add_argument("--symbols", default="")
    record_once.add_argument("--duration-sec", type=float, default=5.0)

    storage = sub.add_parser("storage-worker")
    storage.add_argument("--duration-sec", type=float)

    compactor = sub.add_parser("compactor")
    compactor.add_argument("--once", action="store_true")

    strategy = sub.add_parser("strategy")
    strategy.add_argument("--job", required=True)
    strategy.add_argument("--strategy-module", required=True)
    strategy.add_argument("--duration-sec", type=float)

    login = sub.add_parser("timefolio-login")
    login.add_argument("--account", required=True)
    login.add_argument("--show-browser", action="store_true")

    tf_strategy = sub.add_parser("timefolio-strategy")
    tf_strategy.add_argument("--job", required=True)
    tf_strategy.add_argument("--account", required=True)
    tf_strategy.add_argument("--strategy-module", required=True)
    tf_strategy.add_argument("--entry-weight-pct", type=Decimal, required=True)
    tf_strategy.add_argument("--exit-weight-pct", type=Decimal)
    tf_strategy.add_argument("--max-order-notional", type=Decimal, required=True)
    tf_strategy.add_argument("--max-position-notional", type=Decimal, required=True)
    tf_strategy.add_argument("--opp-tick", type=int, default=1)
    tf_strategy.add_argument("--duration-sec", type=float)
    tf_strategy.add_argument("--no-submit", action="store_true")
    tf_strategy.add_argument("--submit", action="store_true")
    tf_strategy.add_argument("--show-browser", action="store_true")

    real_exec = sub.add_parser("real-exec")
    real_exec.add_argument("--job", required=True)
    real_exec.add_argument("--account", required=True)
    real_exec.add_argument("--broker", required=True, choices=["kis", "toss", "kiwoom", "binance", "hyperliquid", "arcus", "grvt"])
    real_exec.add_argument("--max-order-notional", type=Decimal, required=True)
    real_exec.add_argument("--max-position-notional", type=Decimal, required=True)
    real_exec.add_argument("--quantity", type=Decimal)
    real_exec.add_argument("--weight-pct", type=Decimal)
    real_exec.add_argument("--duration-sec", type=float)
    real_exec.add_argument("--no-submit", action="store_true")
    real_exec.add_argument("--submit", action="store_true")

    universe = sub.add_parser("universe")
    universe.add_argument("--provider", required=True, choices=["kis", "kis-commodity", "kis-overseas", "index-proxy-etf", "hyperliquid"])
    universe.add_argument("--underlying", default="KOSPI200")
    universe.add_argument("--underlying-price", type=Decimal)
    universe.add_argument("--products", default="B10,USD")
    universe.add_argument("--overseas-products", default="NQ")

    provider_check = sub.add_parser("provider-check")
    provider_check.add_argument("--provider", required=True, choices=["kis", "kiwoom", "toss"])
    provider_check.add_argument("--symbols", default="")
    provider_check.add_argument("--limit", type=int, default=5)
    provider_check.add_argument("--skip-access-token", action="store_true")
    provider_check.add_argument("--skip-accounts", action="store_true")

    account_check = sub.add_parser("account-check")
    account_check.add_argument("--provider", required=True, choices=["kis", "kiwoom", "toss"])

    broker_check = sub.add_parser("broker-check")
    broker_check.add_argument("--broker", required=True, choices=["kis", "toss", "kiwoom", "binance", "hyperliquid", "arcus", "grvt"])

    sub.add_parser("nats-init")
    probe = sub.add_parser("bybit-testnet-probe", help="submit one bounded testnet order and require a private-stream fill")
    probe.add_argument("--symbol", required=True, choices=list(CRYPTO_SYMBOLS))
    probe.add_argument("--side", required=True, choices=["Buy", "Sell"])
    probe.add_argument("--mode", required=True, choices=["maker", "taker"])
    probe.add_argument("--qty", required=True)
    probe.add_argument("--max-notional", default="25")
    probe.add_argument("--timeout-sec", type=float, default=60)
    probe.add_argument("--reduce-only", action="store_true", help="close an existing testnet position without increasing exposure")
    probe.add_argument("--market-trigger", action="store_true", help="decide from a testnet public orderbook WS frame and report stage latency")
    kill = sub.add_parser("bybit-kill-switch")
    kill.add_argument("state", choices=["on", "off"])
    sub.add_parser("market-source-status")
    sub.add_parser("nats-reset-storage-consumer")
    health = sub.add_parser("health-check")
    health.add_argument("--component", required=True, choices=["collectors", "crypto-collectors", "collectors-binance-perp", "collectors-bitget-perp", "collectors-bybit-perp", "collectors-arcus-perp", "collectors-grvt-perp", "storage", "crypto-storage", "maintenance", "etf-mock", "etf-csv-sync"])
    health.add_argument("--max-age-seconds", type=int, default=120)
    sub.add_parser("archive-check")
    sub.add_parser("status")
    sub.add_parser("config-check")
    sub.add_parser("telegram")
    run = sub.add_parser("run", help="run the long-lived Docker supervisor")
    run.add_argument("--config", default=None)
    run.add_argument("--role", choices=["collectors", "ls-collectors", "crypto-collectors", "crypto-strategies", "storage", "crypto-storage", "maintenance", "crypto-maintenance", "all"], default="all")
    run.add_argument("--provider",choices=["binance-perp","bitget-perp","bybit-perp","arcus-perp","grvt-perp"],default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(args.log_level, secrets=_secrets_from_settings())

    if args.command in {"status", "config-check"}:
        _print_json(runtime_status(settings))
        return 0
    if args.command == "telegram":
        if not settings.telegram_user_id:
            _print_json({"status": "DISABLED_CONFIGURATION", "reason": "TELEGRAM_BOT_TOKEN/TELEGRAM_USER_ID are not configured"})
            return 0
        try:
            asyncio.run(TelegramController(settings).run())
        except KeyboardInterrupt:
            return 130
        return 0
    if args.command == "run":
        try:
            asyncio.run(run_runtime(args.config, args.role, args.provider))
        except KeyboardInterrupt:
            return 130
        return 0
    if args.command == "compactor":
        result = ParquetCompactor(settings).compact_once()
        _print_json(result)
        return 0 if result.get("ran") or result.get("reason") in {"no_candidates", "system_gate"} else 1
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
