from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from collections import deque
from decimal import Decimal
from typing import Any

from vibetrading.bus import EventBus, NatsJetStreamBus
from vibetrading.brokers import broker_for_name
from vibetrading.execution import AccountExecutionConfig, BrokerAdapter, ExecutionSafetyGate, OrderIntentFactory
from core.models import OrderDestination, OrderIntentStatus, Side, Signal, SignalAction
from vibetrading.settings import get_settings
from vibetrading.sqlite_store import RuntimeStore
from core.dedup import BoundedDedup
from vibetrading.execution_owner import ExecutionOwner
from vibetrading.runtime_control import GateBlocked
from vibetrading.execution_ledger import exact_product


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
    execution_environment: str | None = None


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
        owner: ExecutionOwner | None = None,
        readiness: Any = None,
        settings: Any = None,
    ) -> None:
        self.config = config
        self.settings = settings or get_settings()
        self.store = store or RuntimeStore(self.settings)
        self.bus = bus or NatsJetStreamBus(self.settings)
        self.broker = broker or (broker_for_name(config.destination_name, self.settings) if config.submit else None)
        self.gate = ExecutionSafetyGate()
        self.intent_factory = OrderIntentFactory(self.store)
        self._seen_signals = BoundedDedup(max_entries=100_000)
        self.owner, self.readiness = owner, readiness

    async def run(self, duration_sec: float | None = None) -> list[dict[str, Any]]:
        if duration_sec is not None and (not math.isfinite(duration_sec) or duration_sec < 0):
            raise ValueError("duration_sec must be finite and nonnegative")
        if self.config.submit and not self.settings.live_order_enabled:
            return [{"status": "BLOCKED", "reason": "LIVE_ORDER_ENABLED is false"}]
        if self.config.submit and self.settings.env.get(f"LIVE_ORDER_ARMED_{self.config.account_id.upper()}") != "1":
            return [{"status": "BLOCKED", "reason": "account arm token is not enabled"}]
        if self.config.submit and (self.owner is None or not self.owner.supports_offline_handoff()):
            return [{"status": "BLOCKED", "reason": "verified offline execution owner is required; live activation unavailable"}]
        await self.bus.connect()
        deadline = asyncio.get_running_loop().time() + duration_sec if duration_sec is not None else None
        results: deque[dict[str, Any]] = deque(maxlen=1000)
        messages = self.bus.subscribe("signal.>", durable=f"real-exec-{self.config.job_id}", batch=128)
        next_message = None
        try:
            while True:
                if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                    break
                timeout = min(1.0, max(0.0, deadline - asyncio.get_running_loop().time())) if deadline is not None else 1.0
                if next_message is None:
                    next_message = asyncio.create_task(anext(messages))
                completed, _ = await asyncio.wait((next_message,), timeout=timeout)
                if not completed:
                    continue
                try:
                    message = next_message.result()
                except StopAsyncIteration:
                    break
                finally:
                    next_message = None
                try:
                    signal = Signal.model_validate(message.data)
                    results.append(await self._handle_signal(signal))
                    await message.ack()
                except Exception:
                    logger.exception("real execution runner failed to process signal")
                    await message.nak(delay=5)
        finally:
            if next_message is not None:
                next_message.cancel()
                await asyncio.gather(next_message, return_exceptions=True)
            if hasattr(messages, "aclose"):
                await messages.aclose()
            await self.bus.close()
        return list(results)

    async def _handle_signal(self, signal: Signal) -> dict[str, Any]:
        if not self.config.submit and signal.signal_id in self._seen_signals:
            return {"status": "DUPLICATE_SIGNAL", "signal_id": signal.signal_id}
        side = side_from_signal(signal)
        exec_config = AccountExecutionConfig(
            job_id=self.config.job_id,
            destination=OrderDestination.REAL,
            account_id=self.config.account_id,
            max_order_notional=self.config.max_order_notional,
            max_position_notional=self.config.max_position_notional,
            environment=self.config.execution_environment,
        )
        failures = self._guard_failures(signal, exec_config, side)
        intent = self.intent_factory.from_signal(
            signal,
            exec_config,
            side=side,
            quantity=self.config.default_quantity,
            weight_pct=self.config.default_weight_pct,
            persist=False,
        )
        # Legacy CREATED can mean a previous send followed by a failed status
        # write. Keep its durable facts; never infer that it was never sent.
        legacy = self.store._conn.execute("SELECT status,payload FROM order_intents WHERE intent_id=?", (intent.intent_id,)).fetchone()
        if legacy is None:
            self.store.record_order_intent(intent)
        # Publish every intent (including blocked/dry-run) for observability;
        # the execution safety gate remains authoritative and live submission
        # is still disabled unless all runtime arms are enabled.
        try:
            await self.bus.publish_order_intent(intent)
        except Exception:
            logger.exception("failed to publish order intent event")
        # Observation/authentication awaits cannot retain an old safety verdict.
        failures = self._guard_failures(signal, exec_config, side)
        if failures:
            if legacy is None:
                self.store.update_order_intent(intent.intent_id, OrderIntentStatus.BLOCKED.value, {"failures": failures})
            await self._publish_intent_status(intent, OrderIntentStatus.BLOCKED.value, {"failures": failures})
            return {"status": "BLOCKED", "signal_id": signal.signal_id, "intent_id": intent.intent_id, "failures": failures}
        if not self.config.submit:
            if legacy is not None:
                return {"status": "DUPLICATE_SIGNAL", "signal_id": signal.signal_id,
                        "intent_id": intent.intent_id, "legacy_status": legacy[0]}
            self.store.update_order_intent(intent.intent_id, OrderIntentStatus.BLOCKED.value, {"reason": "dry_run_no_submit"})
            await self._publish_intent_status(intent, OrderIntentStatus.BLOCKED.value, {"reason": "dry_run_no_submit"})
            self._seen_signals.add_if_new(signal.signal_id)
            return {"status": "DRY_RUN", "signal_id": signal.signal_id, "intent_id": intent.intent_id}
        known = self.owner.ledger.existing(intent)
        if legacy is not None and known is None:
            from core.models import OrderIntent
            try:
                prior = OrderIntent.model_validate_json(legacy[1])
                if self.owner.ledger.fingerprint(prior) != self.owner.ledger.fingerprint(intent):
                    raise ValueError("Legacy intent payload/account/environment conflicts with request")
            except ValueError:
                self.owner.control.fail_closed()
                return {"status": "BLOCKED", "intent_id": intent.intent_id,
                        "signal_id": signal.signal_id, "reason": "legacy_payload_requires_reconciliation"}
        if legacy is not None and known is None and legacy[0] in {"blocked", "rejected", "filled"}:
            return {"status": "DUPLICATE_SIGNAL", "signal_id": signal.signal_id,
                    "intent_id": intent.intent_id, "legacy_status": legacy[0]}
        try:
            result = await self.owner.dispatch(intent, expiry_ns=signal.valid_until_ts_ns,
                evidence=signal.metadata.get("decision_evidence"), reserve_unit=signal.reference_price,
                readiness=self.readiness, guard=lambda: self._guard_failures(signal, exec_config, side),
                legacy_present=legacy is not None and known is None)
        except GateBlocked as error:
            result = {"status": "BLOCKED", "intent_id": intent.intent_id, "reason": str(error)}
        status = result["status"]
        report_status = (OrderIntentStatus.SUBMITTED.value if status == "SUBMITTED" else
                         OrderIntentStatus.UNKNOWN.value if status in {"UNKNOWN", "LEGACY_UNKNOWN", "HANDOFF", "AUTHORIZED"} else
                         OrderIntentStatus.FILLED.value if status == "FILLED" else OrderIntentStatus.BLOCKED.value)
        if legacy is None or known is not None:
            self.store.update_order_intent(intent.intent_id, report_status, {"execution_owner_state": status})
        await self._publish_intent_status(intent, report_status, {"execution_owner_state": status})
        return dict(result, signal_id=signal.signal_id)

    def _guard_failures(self, signal, config, side) -> list[str]:
        failures = self.gate.validate(signal, config)
        if self.store.is_killed("global") or self.store.is_killed(f"account:{self.config.account_id}") or self.store.is_killed(f"strategy:{signal.strategy_id}"):
            failures.append("kill_switch_active")
        if side is Side.UNKNOWN:
            failures.append("unsupported_signal_side")
        if self.config.default_quantity is None and self.config.default_weight_pct is None:
            failures.append("sizing_missing")
        if self.config.submit:
            if not self.settings.live_order_enabled or self.settings.env.get(f"LIVE_ORDER_ARMED_{self.config.account_id.upper()}") != "1":
                failures.append("runtime_account_arm_disabled")
            if (self.owner is None or self.readiness is None or self.broker is not self.owner.adapter
                    or not self.owner.supports_offline_handoff()
                    or self.config.execution_environment != "offline"
                    or self.owner.ledger.scope.account != self.config.account_id):
                failures.append("verified_offline_owner_required")
            quantity = self.config.default_quantity
            if quantity is None or not quantity.is_finite() or quantity <= 0 or not signal.reference_price.is_finite() or signal.reference_price <= 0:
                failures.append("verified_quantity_and_reservation_price_required")
            elif self.config.max_order_notional is not None and exact_product(quantity, signal.reference_price) > self.config.max_order_notional:
                failures.append("order_notional_limit")
        return failures

    async def _publish_intent_status(self, intent: Any, status: str, extra: dict[str, Any]) -> None:
        payload = intent.model_dump(mode="json")
        payload.update(extra)
        payload["status"] = status
        try:
            await self.bus.publish_order_intent(payload)
        except Exception:
            logger.exception("failed to publish real order status")

