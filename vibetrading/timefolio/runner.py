from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from vibetrading.bus import EventBus, NatsJetStreamBus
from vibetrading.execution import AccountExecutionConfig, ExecutionSafetyGate, OrderIntentFactory
from vibetrading.hot_path import HotPathStore, StrategyDataView
from vibetrading.models import (
    BaseMarketEvent,
    EventType,
    OrderDestination,
    OrderIntentStatus,
    Side,
    Signal,
    SignalAction,
    event_from_dict,
)
from vibetrading.parquet_store import ParquetHistoryReader
from vibetrading.settings import get_settings
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.strategy_contract import load_strategy
from vibetrading.timefolio.client import (
    AsyncTimefolioBrowserManager,
    TimefolioCredentials,
    TimefolioOrderIntent,
    TimefolioOrderResult,
)
from vibetrading.dedup import BoundedDedup
from vibetrading.dashboard.telemetry import heartbeat


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimefolioStrategyRunnerConfig:
    job_id: str
    account_id: str
    strategy_module: str
    entry_weight_pct: Decimal
    max_order_notional: Decimal
    max_position_notional: Decimal
    exit_weight_pct: Decimal | None = None
    opp_tick: int = 1
    submit: bool = False
    show_browser: bool = False


def side_from_signal(signal: Signal) -> Side:
    if signal.action in {SignalAction.ENTER_LONG, SignalAction.EXIT_SHORT}:
        return Side.BUY
    if signal.action in {SignalAction.EXIT_LONG, SignalAction.ENTER_SHORT}:
        return Side.SELL
    return Side.UNKNOWN


def is_exit_signal(signal: Signal) -> bool:
    return signal.action in {SignalAction.EXIT_LONG, SignalAction.EXIT_SHORT}


def timefolio_ticker_from_signal(signal: Signal) -> str:
    raw = signal.instrument.raw_symbol.strip().upper().removeprefix("A")
    if raw.isdigit() and len(raw) <= 6:
        return raw.zfill(6)
    raise ValueError(f"Timefolio only supports exact 6-digit stock tickers for now: {signal.instrument.raw_symbol}")


class TimefolioStrategyRunner:
    def __init__(self, config: TimefolioStrategyRunnerConfig, bus: EventBus | None = None, store: RuntimeStore | None = None) -> None:
        self.config = config
        self.settings = get_settings()
        self.store = store or RuntimeStore(self.settings)
        self.bus = bus or NatsJetStreamBus(self.settings)
        self.strategy_module = load_strategy(config.strategy_module)
        self.strategy = self.strategy_module.create()
        self.hot_path = HotPathStore()
        self.history = ParquetHistoryReader(self.settings.parquet.data_root)
        self.view = StrategyDataView(self.hot_path, self.history)
        self.gate = ExecutionSafetyGate()
        self.intent_factory = OrderIntentFactory(self.store)
        self._seen_signals = BoundedDedup(max_entries=100_000)

    async def run(self, duration_sec: float | None = None) -> list[dict[str, Any]]:
        if self.config.submit and not self.settings.timefolio_live_submit_enabled:
            return [{"status": "BLOCKED", "reason": "TIMEFOLIO_LIVE_SUBMIT_ENABLED is false"}]
        if self.config.submit and self.settings.env.get(f"TIMEFOLIO_ORDER_ARMED_{self.config.account_id.upper()}") != "1":
            return [{"status": "BLOCKED", "reason": "account arm token is not enabled"}]
        account = self.settings.timefolio_account(self.config.account_id)
        if not account.has_credentials:
            return [{"status": "SKIPPED_MISSING_CREDENTIALS", "account": self.config.account_id}]

        await self.bus.connect()
        deadline = asyncio.get_running_loop().time() + duration_sec if duration_sec else None
        results: deque[dict[str, Any]] = deque(maxlen=1000)
        async with AsyncTimefolioBrowserManager(
            headless=not self.config.show_browser,
            live_submit_enabled=self.settings.timefolio_live_submit_enabled,
        ) as manager:
            login_status = await manager.login(
                self.config.account_id,
                TimefolioCredentials(account.username or "", account.password or ""),
            )
            if not login_status.logged_in:
                await self.bus.close()
                return [{"status": "LOGIN_FAILED", "login": login_status.__dict__}]
            messages = self.bus.subscribe("md.>", durable=f"timefolio-{self.config.job_id}", batch=128)
            monitor = asyncio.create_task(heartbeat(self.bus, job_id=self.config.job_id,
                name=self.config.strategy_module, account=self.config.account_id, section="timefolio",
                mode="timefolio" if self.config.submit else "paper", currency="KRW"))
            try:
                while True:
                    if deadline and asyncio.get_running_loop().time() >= deadline:
                        break
                    timeout = min(1.0, max(0.05, deadline - asyncio.get_running_loop().time())) if deadline else 1.0
                    try:
                        message = await asyncio.wait_for(anext(messages), timeout=timeout)
                    except asyncio.TimeoutError:
                        continue
                    except StopAsyncIteration:
                        break
                    try:
                        event = event_from_dict(message.data)
                        if self.hot_path.apply(event):
                            for signal in self.strategy.on_event(event, self.view):
                                result = await self._handle_signal(signal, manager)
                                results.append(result)
                        await message.ack()
                    except Exception:
                        logger.exception("Timefolio strategy runner failed to process message")
                        await message.nak(delay=5)
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
                await self.bus.close()
        return list(results)

    async def _handle_signal(self, signal: Signal, manager: AsyncTimefolioBrowserManager) -> dict[str, Any]:
        if not self._seen_signals.add_if_new(signal.signal_id):
            return {"status": "DUPLICATE_SIGNAL", "signal_id": signal.signal_id}

        side = side_from_signal(signal)
        weight = self.config.exit_weight_pct if is_exit_signal(signal) and self.config.exit_weight_pct is not None else self.config.entry_weight_pct
        exec_config = AccountExecutionConfig(
            job_id=self.config.job_id,
            destination=OrderDestination.TIMEFOLIO,
            account_id=self.config.account_id,
            max_order_notional=self.config.max_order_notional,
            max_position_notional=self.config.max_position_notional,
            entry_weight_pct=self.config.entry_weight_pct,
            exit_weight_pct=self.config.exit_weight_pct,
        )
        failures = self.gate.validate(signal, exec_config)
        if self.store.is_killed("global") or self.store.is_killed(f"account:{self.config.account_id}"):
            failures.append("kill_switch_active")
        if side is Side.UNKNOWN:
            failures.append("unsupported_signal_side")
        try:
            ticker = timefolio_ticker_from_signal(signal)
        except ValueError as exc:
            failures.append(str(exc))
            ticker = signal.instrument.raw_symbol
        intent = self.intent_factory.from_signal(
            signal,
            exec_config,
            side=side,
            weight_pct=weight,
        )
        try:
            await self.bus.publish_order_intent(intent)
        except Exception:
            logger.exception("failed to publish Timefolio intent event")
        if failures:
            self.store.update_order_intent(intent.intent_id, OrderIntentStatus.BLOCKED.value, {"failures": failures})
            return {"status": "BLOCKED", "signal_id": signal.signal_id, "intent_id": intent.intent_id, "failures": failures}

        order_result = await manager.prepare_order(
            self.config.account_id,
            TimefolioOrderIntent(
                account_id=self.config.account_id,
                ticker=ticker,
                side=side.value,
                weight_pct=weight,
                opp_tick=self.config.opp_tick,
                submit=self.config.submit,
            ),
        )
        self._record_result(intent.intent_id, order_result)
        return {
            "status": order_result.status,
            "signal_id": signal.signal_id,
            "intent_id": intent.intent_id,
            "ticker": ticker,
            "result": order_result.result,
        }

    def _record_result(self, intent_id: str, result: TimefolioOrderResult) -> None:
        status = {
            "ACCEPTED": OrderIntentStatus.ACCEPTED,
            "FILLED": OrderIntentStatus.FILLED,
            "REJECTED": OrderIntentStatus.REJECTED,
            "UNKNOWN": OrderIntentStatus.UNKNOWN,
        }.get(result.status, OrderIntentStatus.UNKNOWN)
        self.store.update_order_intent(intent_id, status.value, {"timefolio": result.__dict__})

