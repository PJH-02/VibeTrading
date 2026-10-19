from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from collections import deque
from decimal import Decimal
from typing import Any

from vibetrading.bus import EventBus, NatsJetStreamBus
from vibetrading.brokers import broker_for_name
from vibetrading.execution import AccountExecutionConfig, BrokerAdapter, ExecutionSafetyGate, OrderIntentFactory
from vibetrading.models import OrderDestination, OrderIntentStatus, Side, Signal, SignalAction
from vibetrading.settings import get_settings
from vibetrading.sqlite_store import RuntimeStore
from vibetrading.dedup import BoundedDedup


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RealExecutionRunnerConfig:
    job_id: str
    account_id: str
    destination_name: str
    max_order_notional: Decimal
    max_position_notional: Decimal
    default_quantity: Decimal | None = None
    default_weight_pct: Decimal | None = None
    submit: bool = False


def side_from_signal(signal: Signal) -> Side:
    if signal.action in {SignalAction.ENTER_LONG, SignalAction.EXIT_SHORT}:
        return Side.BUY
    if signal.action in {SignalAction.EXIT_LONG, SignalAction.ENTER_SHORT}:
        return Side.SELL
    return Side.UNKNOWN


class RealExecutionRunner:
    def __init__(
        self,
        config: RealExecutionRunnerConfig,
        *,
        bus: EventBus | None = None,
        store: RuntimeStore | None = None,
        broker: BrokerAdapter | None = None,
    ) -> None:
        self.config = config
        self.settings = get_settings()
        self.store = store or RuntimeStore(self.settings)
        self.bus = bus or NatsJetStreamBus(self.settings)
        self.broker = broker or (broker_for_name(config.destination_name, self.settings) if config.submit else None)
        self.gate = ExecutionSafetyGate()
        self.intent_factory = OrderIntentFactory(self.store)
        self._seen_signals = BoundedDedup(max_entries=100_000)

    async def run(self, duration_sec: float | None = None) -> list[dict[str, Any]]:
        if self.config.submit and not self.settings.live_order_enabled:
            return [{"status": "BLOCKED", "reason": "LIVE_ORDER_ENABLED is false"}]
        if self.config.submit and self.settings.env.get(f"LIVE_ORDER_ARMED_{self.config.account_id.upper()}") != "1":
            return [{"status": "BLOCKED", "reason": "account arm token is not enabled"}]
        await self.bus.connect()
        deadline = asyncio.get_running_loop().time() + duration_sec if duration_sec else None
        results: deque[dict[str, Any]] = deque(maxlen=1000)
        messages = self.bus.subscribe("signal.>", durable=f"real-exec-{self.config.job_id}", batch=128)
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
                    signal = Signal.model_validate(message.data)
                    results.append(await self._handle_signal(signal))
                    await message.ack()
                except Exception:
                    logger.exception("real execution runner failed to process signal")
                    await message.nak(delay=5)
        finally:
            await self.bus.close()
        return list(results)

    async def _handle_signal(self, signal: Signal) -> dict[str, Any]:
        if not self._seen_signals.add_if_new(signal.signal_id):
            return {"status": "DUPLICATE_SIGNAL", "signal_id": signal.signal_id}
        side = side_from_signal(signal)
        exec_config = AccountExecutionConfig(
            job_id=self.config.job_id,
            destination=OrderDestination.REAL,
            account_id=self.config.account_id,
            max_order_notional=self.config.max_order_notional,
            max_position_notional=self.config.max_position_notional,
        )
        failures = self.gate.validate(signal, exec_config)
        if self.store.is_killed("global") or self.store.is_killed(f"account:{self.config.account_id}"):
            failures.append("kill_switch_active")
        if side is Side.UNKNOWN:
            failures.append("unsupported_signal_side")
        if self.config.default_quantity is None and self.config.default_weight_pct is None:
            failures.append("sizing_missing")
        intent = self.intent_factory.from_signal(
            signal,
            exec_config,
            side=side,
            quantity=self.config.default_quantity,
            weight_pct=self.config.default_weight_pct,
        )
        # Publish every intent (including blocked/dry-run) for observability;
        # the execution safety gate remains authoritative and live submission
        # is still disabled unless all runtime arms are enabled.
        try:
            await self.bus.publish_order_intent(intent)
        except Exception:
            logger.exception("failed to publish order intent event")
        if failures:
            self.store.update_order_intent(intent.intent_id, OrderIntentStatus.BLOCKED.value, {"failures": failures})
            await self._publish_intent_status(intent, OrderIntentStatus.BLOCKED.value, {"failures": failures})
            return {"status": "BLOCKED", "signal_id": signal.signal_id, "intent_id": intent.intent_id, "failures": failures}
        if not self.config.submit:
            self.store.update_order_intent(intent.intent_id, OrderIntentStatus.BLOCKED.value, {"reason": "dry_run_no_submit"})
            await self._publish_intent_status(intent, OrderIntentStatus.BLOCKED.value, {"reason": "dry_run_no_submit"})
            return {"status": "DRY_RUN", "signal_id": signal.signal_id, "intent_id": intent.intent_id}
        if self.broker is None:
            self.store.update_order_intent(intent.intent_id, OrderIntentStatus.BLOCKED.value, {"reason": "broker_adapter_missing"})
            await self._publish_intent_status(intent, OrderIntentStatus.BLOCKED.value, {"reason": "broker_adapter_missing"})
            return {"status": "BLOCKED", "signal_id": signal.signal_id, "intent_id": intent.intent_id, "failures": ["broker_adapter_missing"]}
        await self.broker.connect()
        response = await self.broker.submit_order(intent)
        self.store.update_order_intent(intent.intent_id, OrderIntentStatus.SUBMITTED.value, {"broker_response": response})
        await self._publish_intent_status(intent, OrderIntentStatus.SUBMITTED.value, {"broker_response": response})
        return {"status": "SUBMITTED", "signal_id": signal.signal_id, "intent_id": intent.intent_id}

    async def _publish_intent_status(self, intent: Any, status: str, extra: dict[str, Any]) -> None:
        payload = intent.model_dump(mode="json")
        payload.update(extra)
        payload["status"] = status
        try:
            await self.bus.publish_order_intent(payload)
        except Exception:
            logger.exception("failed to publish real order status")

