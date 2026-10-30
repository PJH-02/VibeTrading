from __future__ import annotations

import asyncio
import logging
import math
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from runtime.bus import EventBus
from execution.contracts import AccountExecutionConfig, ExecutionSafetyGate, OrderIntentFactory
from market.hot_path import HotPathStore, StrategyDataView
from core.models import BaseMarketEvent, EventType, OrderDestination, OrderIntentStatus, Side, Signal, SignalAction, event_from_dict
from storage.parquet_store import ParquetHistoryReader
from runtime.settings import get_settings
from storage.sqlite_store import RuntimeStore
from strategies import load_strategy
from strategies.contract import action_protocol_version
from timefolio.client import AsyncTimefolioBrowserManager, TimefolioCredentials, TimefolioOrderIntent, TimefolioOrderResult
from core.dedup import BoundedDedup
from operations.dashboard.telemetry import heartbeat


logger = logging.getLogger(__name__)
_POLL_INTERVAL_SECONDS = 1.0


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
    def __init__(
        self,
        config: TimefolioStrategyRunnerConfig,
        bus: EventBus | None = None,
        store: RuntimeStore | None = None,
        *,
        event_source: Any = None,
    ) -> None:
        self.config = config
        self.settings = get_settings()
        self.store = store or RuntimeStore(self.settings)
        self.bus = bus
        self._event_source = event_source
        self.strategy_module = load_strategy(config.strategy_module)
        if action_protocol_version(getattr(self.strategy_module, "module", self.strategy_module)) is not None:
            raise ValueError("Timefolio runner cannot consume ACTION_PROTOCOL_VERSION=1 plans")
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
        if self._event_source is None or self.bus is None:
            return [{"status": "BLOCKED", "reason": "local market event source and observation bus are required"}]
        if duration_sec is not None and not math.isfinite(duration_sec):
            raise ValueError("duration_sec must be finite")
        account = self.settings.timefolio_account(self.config.account_id)
        if not account.has_credentials:
            return [{"status": "SKIPPED_MISSING_CREDENTIALS", "account": self.config.account_id}]
        if duration_sec is not None and duration_sec <= 0:
            return []

        source = self._event_source
        results: deque[dict[str, Any]] = deque(maxlen=1000)
        next_task: asyncio.Task[dict[str, Any]] | None = None
        monitor: asyncio.Task[Any] | None = None
        connected = False
        try:
            await self.bus.connect()
            connected = True
            async with AsyncTimefolioBrowserManager(
                headless=not self.config.show_browser,
                live_submit_enabled=self.settings.timefolio_live_submit_enabled,
            ) as manager:
                login_status = await manager.login(
                    self.config.account_id,
                    TimefolioCredentials(account.username or "", account.password or ""),
                )
                if not login_status.logged_in:
                    return [{"status": "LOGIN_FAILED", "login": login_status.__dict__}]
                monitor = asyncio.create_task(heartbeat(
                    self.bus, job_id=self.config.job_id, name=self.config.strategy_module,
                    account=self.config.account_id, section="timefolio",
                    mode="timefolio" if self.config.submit else "paper", currency="KRW",
                ))
                deadline = (
                    asyncio.get_running_loop().time() + duration_sec
                    if duration_sec is not None
                    else None
                )
                while True:
                    timeout = _POLL_INTERVAL_SECONDS
                    if deadline is not None:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            break
                        timeout = min(timeout, max(0.001, remaining))
                    if next_task is None:
                        next_task = asyncio.create_task(anext(source))
                    done, _ = await asyncio.wait({next_task}, timeout=timeout)
                    if not done:
                        continue
                    try:
                        payload = next_task.result()
                    except StopAsyncIteration:
                        next_task = None
                        break
                    next_task = None
                    event = event_from_dict(payload)
                    if self.hot_path.apply(event):
                        for signal in self.strategy.on_event(event, self.view):
                            results.append(await self._handle_signal(signal, manager))
        finally:
            if next_task is not None:
                next_task.cancel()
                await asyncio.gather(next_task, return_exceptions=True)
            if monitor is not None:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            try:
                aclose = getattr(source, "aclose", None)
                if callable(aclose):
                    await aclose()
            finally:
                if connected:
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
            persist=not self.config.submit,
        )
        try:
            await self.bus.publish_order_intent(intent)
        except Exception:
            logger.exception("failed to publish Timefolio intent event")
        if failures:
            if not self.config.submit:
                self.store.update_order_intent(intent.intent_id, OrderIntentStatus.BLOCKED.value, {"failures": failures})
            return {"status": "BLOCKED", "signal_id": signal.signal_id, "intent_id": intent.intent_id, "failures": failures}

        if self.config.submit:
            claim = getattr(self.store, "claim_timefolio_intent", None)
            if not callable(claim):
                return {"status": "BLOCKED", "signal_id": signal.signal_id, "intent_id": intent.intent_id,
                        "reason": "timefolio_durable_claim_unavailable"}
            try:
                claim_result = claim(intent, self.config.account_id)
            except Exception:
                return {"status": "BLOCKED", "signal_id": signal.signal_id, "intent_id": intent.intent_id,
                        "reason": "timefolio_durable_claim_failed"}
            if not claim_result.get("claimed"):
                return {"status": "BLOCKED", "signal_id": signal.signal_id, "intent_id": intent.intent_id,
                        "reason": str(claim_result.get("reason") or "timefolio_claim_rejected")}

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
        recorded_status = self._record_result(intent.intent_id, order_result)
        return {
            "status": recorded_status,
            "signal_id": signal.signal_id,
            "intent_id": intent.intent_id,
            "ticker": ticker,
            "result": order_result.result,
        }

    def _record_result(self, intent_id: str, result: TimefolioOrderResult) -> str:
        if self.config.submit:
            record = getattr(self.store, "record_timefolio_result", None)
            if not callable(record):
                return OrderIntentStatus.UNKNOWN.value.upper()
            try:
                stored = record(intent_id, result)
            except Exception:
                # The durable pre-browser UNKNOWN claim remains the safety fence.
                return OrderIntentStatus.UNKNOWN.value.upper()
            return str(stored).upper()
        status = {
            "ACCEPTED": OrderIntentStatus.ACCEPTED,
            "FILLED": OrderIntentStatus.FILLED,
            "REJECTED": OrderIntentStatus.REJECTED,
            "UNKNOWN": OrderIntentStatus.UNKNOWN,
        }.get(result.status, OrderIntentStatus.UNKNOWN)
        self.store.update_order_intent(intent_id, status.value, {"timefolio": self._result_payload(result)})
        return status.value.upper()

    @staticmethod
    def _result_payload(result: TimefolioOrderResult) -> dict[str, Any]:
        return {
            "status": str(result.status),
            "ticker": str(result.ticker),
            "side": str(result.side),
            "weight_pct": str(result.weight_pct),
            "result": str(result.result),
            "filled": bool(result.filled),
            "accepted": bool(result.accepted),
            "pending": bool(result.pending),
        }

