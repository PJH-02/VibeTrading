from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import uuid
from dataclasses import dataclass
from collections import deque
from decimal import Decimal
from collections.abc import Mapping
from typing import Any

from runtime.bus import EventBus, NatsJetStreamBus
from execution.brokers import broker_for_name
from execution.contracts import AccountExecutionConfig, BrokerAdapter, ExecutionSafetyGate, OrderIntentFactory
from core.models import OrderDestination, OrderIntentStatus, Side, Signal, SignalAction
from runtime.settings import get_settings
from storage.sqlite_store import RuntimeStore
from core.dedup import BoundedDedup
from execution.execution_owner import ExecutionOwner
from runtime.runtime_control import GateBlocked
from execution.execution_ledger import AccountScope, canonical, exact_product


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
        group_coordinator: Any = None,
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
        self.group_coordinator = group_coordinator
        self._source_runtime_active: set[str] = set()
        self._source_runtime_ids: dict[str, str] = {}
        self._source_recovery_tokens: dict[str, str] = {}

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

    async def dispatch_strategy_market_event(
        self, source: Any, event: Any, data: Any, *, registry: Any,
        evidence: dict[str, Any], readiness: Any = None, reserve_units: Any = None,
        guard: Any = None, source_trust: str | None = None,
        isolated_worker: Any = None,
    ) -> dict[str, Any]:
        """Route a trusted cooperative callback or a CAP04 worker response.

        Bare callbacks execute in the parent and are accepted only when the
        caller explicitly classifies the selected source as trusted cooperative.
        Untrusted sources must use the asynchronous JSON worker primitive.
        """
        import inspect
        import sys
        from execution.trading_contracts import StrategyActionPlan
        from core.models import BaseMarketEvent
        from market.hot_path import DecisionSnapshot, StrategyDataView
        if (not isinstance(event, BaseMarketEvent) or not isinstance(data, StrategyDataView)
                or not isinstance(data.hot_path, DecisionSnapshot)
                or data.cursor != data.hot_path.cursor or not event.event_id):
            raise GateBlocked("Source dispatch requires a typed market event and matching immutable decision cursor")
        if not self._event_is_in_snapshot(event, data.hot_path):
            raise GateBlocked("Typed market input is not present in its cited decision snapshot")
        feedback_callback = None
        if isolated_worker is not None:
            snapshot = getattr(data, "hot_path", None)
            cursor = getattr(data, "cursor", None)
            execution = getattr(data, "execution", None)
            ready = getattr(data, "ready", None)
            phase = getattr(data, "phase", None)
            strategy_id = getattr(isolated_worker, "strategy_id", None)
            expected = getattr(isolated_worker, "expected", None)
            config_version = evidence.get("config_version") if isinstance(evidence, dict) else None
            if (not isinstance(strategy_id, str) or not strategy_id
                    or not isinstance(expected, dict)
                    or expected.get("strategy_id") != strategy_id
                    or not isinstance(config_version, str) or not config_version
                    or snapshot is None or cursor is None or not isinstance(execution, Mapping)
                    or not all(hasattr(snapshot, name) for name in
                               ("engine_incarnation", "apply_seq", "now_ns", "versions", "books"))
                    or type(ready) is not bool or not isinstance(phase, str)):
                raise GateBlocked("Isolated source registration or decision snapshot is incomplete")
            self._check_isolated_worker(isolated_worker)
            fresh, source_runtime_id = self._claim_source_market_event(
                event, data, strategy_id, config_version, evidence)
            if not fresh:
                return {"status": "DUPLICATE_SOURCE_EVENT", "strategy_id": strategy_id,
                        "event_id": event.event_id}
            request = self._submit_isolated_event(
                isolated_worker, event=event, snapshot=snapshot, execution=execution,
                ready=ready, phase=phase, cursor=cursor)
            result = await self._await_isolated_plan(isolated_worker, request)

            async def feedback_callback(feedback_event, context):
                # The coordinator owns the effects. Return the worker's original
                # plan so it can validate group membership/counts before routing.
                return await self._invoke_order_source_raw(
                    None, feedback_event, context, evidence=evidence,
                    isolated_worker=isolated_worker, isolated_snapshot=snapshot)
        else:
            if source_trust != "trusted_cooperative":
                raise GateBlocked("Parent-process source callbacks require explicit trusted_cooperative selection")
            is_module = inspect.ismodule(source)
            source_module = source if is_module else sys.modules.get(type(source).__module__)
            version = getattr(source, "ACTION_PROTOCOL_VERSION",
                              getattr(source_module, "ACTION_PROTOCOL_VERSION", None))
            strategy_id = getattr(source, "STRATEGY_ID",
                                  getattr(source_module, "STRATEGY_ID", None))
            callback = getattr(source, "on_event", None)
            order_callback = getattr(source, "on_order_event", None)
            if type(version) is not int or version != 1 or not callable(callback):
                raise GateBlocked("Selected source lacks the versioned action-plan market contract")
            if not callable(order_callback):
                raise GateBlocked("Selected source lacks the typed factual order callback")
            config_version = getattr(source, "config_version", None)
            fresh, source_runtime_id = self._claim_source_market_event(
                event, data, strategy_id, config_version, evidence)
            if not fresh:
                return {"status": "DUPLICATE_SOURCE_EVENT", "strategy_id": strategy_id,
                        "event_id": event.event_id}
            result = callback(event, data)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise GateBlocked("Versioned trusted cooperative source callback must be synchronous")
            if isinstance(result, list) and not result:
                self._source_runtime_active.add(strategy_id)
                self._source_runtime_ids[strategy_id] = source_runtime_id
                return {"status": "NO_ACTION", "strategy_id": strategy_id}
            async def feedback_callback(feedback_event, context):
                return await self._invoke_order_source_raw(
                    source, feedback_event, context, evidence=evidence,
                    source_trust="trusted_cooperative")
        if (not isinstance(result, StrategyActionPlan)
                or result.strategy_id != strategy_id
                or result.config_version != config_version
                or (isinstance(evidence, dict) and evidence.get("config_version") != config_version)):
            raise GateBlocked("Selected source returned a non-owned or invalid action plan")
        self._source_runtime_active.add(strategy_id)
        self._source_runtime_ids[strategy_id] = source_runtime_id
        if result.group_submit_mode is not None or result.group_followup:
            if self.group_coordinator is None:
                raise GateBlocked("Grouped plan requires an explicitly injected coordinator")
            if result.group_followup:
                return await self.group_coordinator.execute_followup(
                    result, registry=registry, readiness=readiness or self.readiness,
                    evidence=evidence, guard=guard)
            return await self.group_coordinator.execute(
                result, registry=registry, readiness=readiness or self.readiness,
                evidence=evidence, reserve_units=reserve_units, guard=guard,
                on_event=feedback_callback)
        if self.owner is None or self.owner.ledger.scope.account != self.config.account_id:
            raise GateBlocked("A same-account execution owner is required")
        return await self.owner.dispatch_plan(
            result, registry=registry, readiness=readiness or self.readiness,
            evidence=evidence, reserve_units=reserve_units, guard=guard,
            on_event=feedback_callback)

    def _claim_source_market_event(self, event, data, strategy_id, config_version, evidence):
        if (self.owner is None or self.owner.ledger.scope.account != self.config.account_id
                or not isinstance(strategy_id, str) or not strategy_id
                or not isinstance(config_version, str) or not config_version
                or not isinstance(evidence, dict)):
            raise GateBlocked("Durable source event claim lacks its strategy, config, evidence, or account binding")
        document = {
            "scope": self.owner.ledger.scope.key,
            "event": event.model_dump(mode="json"),
            "cursor": list(data.cursor),
            "config_version": config_version,
            "evidence": evidence,
        }
        runtime_id = self._source_runtime_ids.get(strategy_id) or uuid.uuid4().hex
        try:
            fresh = self.owner.ledger.claim_source_event(strategy_id, event.event_id, document,
                runtime_id=runtime_id,
                restored_checkpoint_sha256=self._source_recovery_tokens.get(strategy_id))
        except Exception as error:
            raise GateBlocked("Durable source event claim failed closed: " + str(error)) from error
        if fresh:
            self._source_recovery_tokens.pop(strategy_id, None)
        return fresh, runtime_id

    @staticmethod
    def _source_state_document(value, *, depth=0):
        if depth > 32:
            raise GateBlocked("Source checkpoint state exceeds its nesting limit")
        if value is None or type(value) in {bool, int, str}:
            return value
        if type(value) is list:
            if len(value) > 16_384:
                raise GateBlocked("Source checkpoint state exceeds its item limit")
            return [RealExecutionRunner._source_state_document(item, depth=depth + 1)
                    for item in value]
        if type(value) is dict:
            if len(value) > 16_384 or any(type(key) is not str or not key for key in value):
                raise GateBlocked("Source checkpoint state has invalid object keys or size")
            return {key: RealExecutionRunner._source_state_document(item, depth=depth + 1)
                    for key, item in value.items()}
        raise GateBlocked("Source checkpoint state is not finite canonical JSON")

    def _source_checkpoint_identity(self, worker, config_version):
        strategy_id = getattr(worker, "strategy_id", None)
        expected = getattr(worker, "expected", None)
        config = getattr(worker, "config", None)
        registration = getattr(worker, "registration_facts", None)
        module_path = getattr(worker, "module_path", None)
        if (not isinstance(strategy_id, str) or not strategy_id
                or not isinstance(config_version, str) or not config_version
                or not isinstance(expected, dict) or not isinstance(config, Mapping)
                or not isinstance(registration, Mapping)
                or registration.get("config_version") != config_version
                or not isinstance(module_path, str) or not module_path
                or self.owner is None or self.owner.ledger.scope.account != self.config.account_id):
            raise GateBlocked("Source checkpoint identity lacks strategy, config, or account binding")
        source_sha = expected.get("source_sha256")
        if (type(source_sha) is not str or len(source_sha) != 64
                or any(ch not in "0123456789abcdef" for ch in source_sha)):
            raise GateBlocked("Source checkpoint requires a pinned source SHA256 manifest")
        account_key = config.get("account_key")
        accounts = registration.get("accounts")
        selected = accounts.get(account_key) if isinstance(accounts, Mapping) and isinstance(account_key, str) else None
        scope_doc = selected.get("scope") if isinstance(selected, Mapping) else None
        try:
            selected_scope = AccountScope(**scope_doc) if isinstance(scope_doc, Mapping) else None
        except (TypeError, ValueError) as error:
            raise GateBlocked("Source checkpoint registration account scope is malformed") from error
        if (selected_scope is None or selected_scope.key != self.owner.ledger.scope.key
                or selected_scope.account != self.owner.ledger.scope.account):
            raise GateBlocked("Source checkpoint registration differs from the owner's account scope")
        selected_native = selected.get("native_account_id")
        if selected_native is None:
            selected_native = selected_scope.account
        if selected_native != self.owner.ledger.native_account_id:
            raise GateBlocked("Source checkpoint registration differs from the owner's physical account")
        try:
            manifest_identity = hashlib.sha256(canonical({
                "module_name": getattr(worker, "module_name", None),
                "module_path": module_path,
                "source_sha256": source_sha,
                "expected": expected,
            }).encode("utf-8")).hexdigest()
            config_identity = hashlib.sha256(canonical(dict(config)).encode("utf-8")).hexdigest()
            registration_identity = hashlib.sha256(
                canonical(dict(registration)).encode("utf-8")).hexdigest()
        except (TypeError, ValueError) as error:
            raise GateBlocked("Source checkpoint manifest or config is not canonical") from error
        return {
            "strategy_id": strategy_id, "config_version": config_version,
            "manifest_identity": manifest_identity, "source_sha256": source_sha,
            "config_identity": config_identity, "registration_identity": registration_identity,
            "account_scope": self.owner.ledger.scope.key,
            "native_account_id": self.owner.ledger.native_account_id,
        }

    async def checkpoint_strategy_source(self, isolated_worker, *, config_version,
                                         timeout_ns=None):
        """Persist an untrusted worker checkpoint at an unchanged quiescent cut."""
        if isolated_worker is None:
            raise GateBlocked("Untrusted source checkpoint requires the isolated CAP04 worker")
        strategy_id = getattr(isolated_worker, "strategy_id", None)
        if not isinstance(strategy_id, str) or strategy_id not in self._source_runtime_active:
            raise GateBlocked("Source checkpoint requires an active restored worker runtime")
        default_timeout = self._check_isolated_worker(isolated_worker)
        timeout = default_timeout if timeout_ns is None else timeout_ns
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise GateBlocked("Source checkpoint requires an explicit bounded timeout")
        identity = self._source_checkpoint_identity(isolated_worker, config_version)
        ledger = self.owner.ledger
        try:
            before = ledger.source_checkpoint_cut(strategy_id)
        except Exception as error:
            raise GateBlocked("Source checkpoint has no durable market-event cut") from error
        if before["pending_feedback"] != 0:
            raise GateBlocked("Source checkpoint requires all factual feedback to be claimed")
        submit = getattr(isolated_worker, "submit_checkpoint", None)
        if not callable(submit):
            raise GateBlocked("CAP04 worker does not provide checkpoint control")
        try:
            response = await isolated_worker.await_response(submit(), timeout_ns=timeout)
        except Exception as error:
            raise GateBlocked("Source checkpoint callback failed or exceeded its deadline") from error
        if (not isinstance(response, dict) or set(response) != {"state", "sha256"}
                or type(response["sha256"]) is not str or len(response["sha256"]) != 64):
            raise GateBlocked("CAP04 worker returned a malformed source checkpoint")
        state = self._source_state_document(response["state"])
        try:
            encoded_state = json.dumps(state, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise GateBlocked("Source checkpoint state is not canonical finite JSON") from error
        state_digest = hashlib.sha256(encoded_state).hexdigest()
        if state_digest != response["sha256"]:
            raise GateBlocked("CAP04 source checkpoint state hash differs")
        try:
            after = ledger.source_checkpoint_cut(strategy_id)
        except Exception as error:
            raise GateBlocked("Source checkpoint cut became unavailable") from error
        if before != after or after["pending_feedback"] != 0:
            raise GateBlocked("Source checkpoint inputs changed while the worker was quiescing")
        document = {"version": 1, **identity, "cut": after,
                    "state": state, "state_sha256": state_digest}
        try:
            digest = ledger.save_source_checkpoint(strategy_id, document)
        except Exception as error:
            raise GateBlocked("Source checkpoint was not durably committed at its original cut") from error
        return {"strategy_id": strategy_id, "sha256": digest, "cut": after}

    async def restore_strategy_source(self, isolated_worker, *, config_version,
                                      timeout_ns=None):
        """Restore a fresh CAP04 child and reopen new inputs only at the saved exact cut."""
        if isolated_worker is None:
            raise GateBlocked("Untrusted source restore requires the isolated CAP04 worker")
        default_timeout = self._check_isolated_worker(isolated_worker)
        timeout = default_timeout if timeout_ns is None else timeout_ns
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise GateBlocked("Source restore requires an explicit bounded timeout")
        identity = self._source_checkpoint_identity(isolated_worker, config_version)
        strategy_id = identity["strategy_id"]
        ledger = self.owner.ledger
        try:
            checkpoint = ledger.load_source_checkpoint(strategy_id)
            saved = checkpoint["document"]
            before = ledger.source_checkpoint_cut(strategy_id)
        except Exception as error:
            raise GateBlocked("Source restore checkpoint is missing or corrupt") from error
        expected = {"version": 1, **identity}
        if any(saved.get(key) != value for key, value in expected.items()):
            raise GateBlocked("Source restore checkpoint belongs to another source/config/account")
        if before != saved.get("cut") or before["pending_feedback"] != 0:
            raise GateBlocked("Source restore checkpoint is stale against current market/feedback cursors")
        state = saved.get("state")
        if (not isinstance(state, (dict, list, str, int, bool)) and state is not None):
            raise GateBlocked("Source restore checkpoint state has an invalid type")
        state = self._source_state_document(state)
        encoded_state = json.dumps(state, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")
        state_digest = hashlib.sha256(encoded_state).hexdigest()
        if state_digest != saved.get("state_sha256"):
            raise GateBlocked("Source restore checkpoint state hash is invalid")
        submit = getattr(isolated_worker, "submit_restore", None)
        if not callable(submit):
            raise GateBlocked("CAP04 worker does not provide restore control")
        try:
            response = await isolated_worker.await_response(
                submit(state=state, sha256=state_digest), timeout_ns=timeout)
        except Exception as error:
            raise GateBlocked("CAP04 source restore hook failed or exceeded its deadline") from error
        if response != {"state": state, "sha256": state_digest}:
            raise GateBlocked("CAP04 restored source state did not round-trip exactly")
        try:
            after = ledger.source_checkpoint_cut(strategy_id)
        except Exception as error:
            raise GateBlocked("Source restore cut became unavailable") from error
        if before != after or after["pending_feedback"] != 0:
            raise GateBlocked("Source inputs changed while checkpoint restoration was running")
        self._source_runtime_active.add(strategy_id)
        self._source_recovery_tokens[strategy_id] = checkpoint["sha256"]
        return {"status": "RESTORED", "strategy_id": strategy_id, "cut": after}

    @staticmethod
    def _event_is_in_snapshot(event, snapshot):
        matches = []
        for name in ("trades", "books", "index_quotes"):
            values = getattr(snapshot, name, {})
            if isinstance(values, Mapping):
                matches.extend(values.values())
        bars = getattr(snapshot, "bars", {})
        if isinstance(bars, Mapping):
            for values in bars.values():
                if isinstance(values, (tuple, list)):
                    matches.extend(values)
        return any(type(candidate) is type(event)
                   and candidate.event_id == event.event_id
                   and candidate.model_dump(mode="json") == event.model_dump(mode="json")
                   for candidate in matches)

    async def dispatch_strategy_order_event(
        self, source: Any, event: Any, context: Any, *, registry: Any,
        evidence: dict[str, Any], readiness: Any = None, reserve_units: Any = None,
        guard: Any = None, instance: Any = None, source_trust: str | None = None,
        isolated_worker: Any = None, isolated_snapshot: Any = None,
        on_feedback: Any = None,
    ) -> dict[str, Any]:
        """Run the selected source callback and hand its original plan to this owner.

        This is the public source-to-execution entrypoint for versioned order
        feedback. The source retains policy; the owner retains route, risk and
        mutation authority.
        """
        from execution.trading_contracts import (
            StrategyActionPlan, StrategyOrderContext, StrategyOrderEvent,
        )
        if not isinstance(event, StrategyOrderEvent) or not isinstance(context, StrategyOrderContext):
            raise GateBlocked("Selected source lacks the versioned typed order-event contract")
        result = await self._invoke_order_source_raw(source, event, context,
            evidence=evidence, source_trust=source_trust, instance=instance,
            isolated_worker=isolated_worker, isolated_snapshot=isolated_snapshot)
        if result is None or result == []:
            return {"status": "NO_ACTION", "strategy_id": event.strategy_id}
        if not isinstance(result, StrategyActionPlan):
            raise GateBlocked("Selected source returned an invalid action plan")
        if result.group_followup or result.group_submit_mode is not None:
            # Group and group-follow-up plans never dispatch through the
            # single current owner account; the injected coordinator owns
            # their exact child-owner routing.
            if self.group_coordinator is None:
                raise GateBlocked("Grouped plan requires an explicitly injected coordinator")
            if result.group_followup:
                return await self.group_coordinator.execute_followup(
                    result, registry=registry, readiness=readiness or self.readiness,
                    evidence=evidence, guard=guard)
            return await self.group_coordinator.execute(
                result, registry=registry, readiness=readiness or self.readiness,
                evidence=evidence, reserve_units=reserve_units, guard=guard,
                on_event=(on_feedback if isolated_worker is not None else
                          getattr(source, "on_order_event", None)))
        if self.owner is None or self.owner.ledger.scope.account != self.config.account_id:
            raise GateBlocked("A same-account execution owner is required")
        return await self.owner.dispatch_plan(
            result, registry=registry, readiness=readiness or self.readiness,
            evidence=evidence, reserve_units=reserve_units, guard=guard,
            on_event=(on_feedback if isolated_worker is not None else
                      getattr(source, "on_order_event", None)))

    async def _invoke_order_source_raw(self, source, event, context, *, evidence,
                                       source_trust=None, instance=None,
                                       isolated_worker=None, isolated_snapshot=None):
        """Invoke and validate one source callback without routing its plan."""
        import inspect
        import sys
        from execution.trading_contracts import StrategyActionPlan
        callback = None
        strategy_id = None
        if isolated_worker is not None:
            strategy_id = getattr(isolated_worker, "strategy_id", None)
            expected = getattr(isolated_worker, "expected", None)
            if (not isinstance(strategy_id, str) or not isinstance(expected, dict)
                    or expected.get("strategy_id") != strategy_id):
                raise GateBlocked("Isolated source registration is not bound to its manifest")
            snapshot = getattr(context.data, "hot_path", None) if context.data is not None else isolated_snapshot
            if snapshot is None:
                raise GateBlocked("Isolated order feedback requires an immutable decision snapshot")
            ready = bool(context.data.ready) if context.data is not None else False
            phase = context.data.phase if context.data is not None else getattr(snapshot, "phase", None)
            # A CAP04 worker has one active response slot. ACK feedback from
            # sibling owners can arrive concurrently, so hold this worker's
            # own bounded lock across submit+await. The lock is attached to the
            # worker (no process-wide map), leaving unrelated strategies free.
            lock = self._isolated_feedback_lock(isolated_worker)
            async with lock:
                request = self._submit_isolated_feedback(isolated_worker, event=event,
                    context={"snapshot": snapshot, "execution": dict(context.execution),
                             "now_ns": context.now_ns, "cursor": context.cursor,
                             "quality": context.quality, "ready": ready, "phase": phase},
                    cursor=context.cursor)
                result = await self._await_isolated_plan(isolated_worker, request)
        else:
            if source_trust != "trusted_cooperative":
                raise GateBlocked("Parent-process source callbacks require explicit trusted_cooperative selection")
            is_module = inspect.ismodule(source)
            source_module = source if is_module else sys.modules.get(type(source).__module__)
            callback = getattr(source, "on_order_event", None)
            version = getattr(source, "ACTION_PROTOCOL_VERSION", getattr(source_module, "ACTION_PROTOCOL_VERSION", None))
            strategy_id = getattr(source, "STRATEGY_ID", getattr(source_module, "STRATEGY_ID", None))
            if type(version) is not int or version != 1 or not callable(callback):
                raise GateBlocked("Selected source lacks the versioned typed order-event contract")
            result = (callback(event, context, instance)
                      if instance is not None and not inspect.ismethod(callback)
                      else callback(event, context))
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise GateBlocked("Versioned trusted cooperative order callback must be synchronous")
            if isinstance(result, list) and not result:
                return []
        if result is None or result == []:
            return None
        if (not isinstance(result, StrategyActionPlan)
                or result.strategy_id != event.strategy_id
                or (strategy_id is not None and result.strategy_id != strategy_id)
                or result.config_version != event.metadata.get("config_version")
                or result.config_version != (evidence.get("config_version") if isinstance(evidence, dict) else None)):
            raise GateBlocked("Selected source returned an unowned or invalid action plan")
        if context.quality not in {"VALID", "SYNTHETIC", "KIS_JOURNAL_FACTS"}:
            from execution.trading_contracts import ActionKind
            cancel_only_followup = (result.group_followup and bool(result.actions)
                and all(action.action is ActionKind.CANCEL for action in result.actions))
            if not cancel_only_followup:
                raise GateBlocked("Selected source returned new risk from unproven order context")
        return result

    @staticmethod
    def _isolated_feedback_lock(worker):
        import asyncio
        lock = getattr(worker, "_cap05_feedback_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            try:
                setattr(worker, "_cap05_feedback_lock", lock)
            except (AttributeError, TypeError) as error:
                raise GateBlocked("Isolated worker cannot retain its bounded feedback serializer") from error
        if not isinstance(lock, asyncio.Lock):
            raise GateBlocked("Isolated worker feedback serializer is invalid")
        return lock

    @staticmethod
    def _check_isolated_worker(worker):
        if (not callable(getattr(worker, "submit_event", None))
                or not callable(getattr(worker, "submit_feedback", None))
                or not callable(getattr(worker, "await_response", None))
                or getattr(worker, "alive", False) is not True
                or getattr(worker, "inflight", True)
                or getattr(worker, "queued", 1) != 0):
            raise GateBlocked("Isolated strategy worker is unavailable or already processing a callback")
        budgets = getattr(worker, "budgets", None)
        timeout = getattr(budgets, "callback_timeout_ns", None)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise GateBlocked("Isolated strategy worker lacks an explicit callback deadline")
        return timeout

    @classmethod
    def _submit_isolated_event(cls, worker, **kwargs):
        cls._check_isolated_worker(worker)
        try:
            return worker.submit_event(**kwargs)
        except Exception as error:
            raise GateBlocked("Isolated market event could not enter the bounded worker queue") from error

    @classmethod
    def _submit_isolated_feedback(cls, worker, **kwargs):
        cls._check_isolated_worker(worker)
        try:
            return worker.submit_feedback(**kwargs)
        except Exception as error:
            raise GateBlocked("Isolated order feedback could not enter the bounded worker queue") from error

    @classmethod
    async def _await_isolated_plan(cls, worker, request):
        budgets = getattr(worker, "budgets", None)
        timeout_ns = getattr(budgets, "callback_timeout_ns", None)
        if (not callable(getattr(worker, "await_response", None))
                or getattr(worker, "alive", False) is not True
                or isinstance(timeout_ns, bool) or not isinstance(timeout_ns, int)
                or timeout_ns <= 0):
            raise GateBlocked("Isolated strategy worker is unavailable while awaiting its response")
        try:
            response = await worker.await_response(request, timeout_ns=timeout_ns)
            from runtime.strategy_process import result_from_document
            result = result_from_document(response)
        except Exception as error:
            raise GateBlocked("Isolated strategy callback failed or exceeded its bounded deadline") from error
        return result

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

