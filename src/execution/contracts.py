from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from core.models import InstrumentId, OrderDestination, OrderIntent, OrderIntentStatus, Side, Signal, stable_hash, utc_now_ns
from storage.sqlite_store import RuntimeStore


class BrokerAdapter(Protocol):
    name: str

    async def connect(self) -> None: ...
    async def account_state(self) -> dict: ...
    async def submit_order(self, intent: OrderIntent) -> dict: ...
    async def order_status(self, client_order_id: str) -> dict: ...


@dataclass(frozen=True)
class AccountExecutionConfig:
    job_id: str
    destination: OrderDestination
    account_id: str
    max_order_notional: Decimal | None = None
    max_position_notional: Decimal | None = None
    entry_weight_pct: Decimal | None = None
    exit_weight_pct: Decimal | None = None
    environment: str | None = None


class ExecutionSafetyGate:
    def validate(self, signal: Signal, config: AccountExecutionConfig) -> list[str]:
        failures: list[str] = []
        if config.max_order_notional is None:
            failures.append("max_order_notional_missing")
        if config.max_position_notional is None:
            failures.append("max_position_notional_missing")
        for label, value in (("max_order_notional", config.max_order_notional),
                             ("max_position_notional", config.max_position_notional)):
            if value is not None and (not value.is_finite() or value <= 0):
                failures.append(label + "_invalid")
        if config.destination == OrderDestination.TIMEFOLIO and config.entry_weight_pct is None:
            failures.append("timefolio_entry_weight_missing")
        if signal.valid_until_ts_ns < utc_now_ns():
            failures.append("signal_expired")
        return failures


class OrderIntentFactory:
    def __init__(self, store: RuntimeStore | None = None) -> None:
        self.store = store or RuntimeStore()

    def from_signal(
        self,
        signal: Signal,
        config: AccountExecutionConfig,
        *,
        side: Side,
        instrument: InstrumentId | None = None,
        quantity: Decimal | None = None,
        weight_pct: Decimal | None = None,
        limit_price: Decimal | None = None,
        intent_index: int = 0,
        persist: bool = True,
    ) -> OrderIntent:
        client_order_id = stable_hash([config.job_id, signal.signal_id, config.destination.value, intent_index])[:32]
        intent_id = stable_hash(["intent", client_order_id])
        intent = OrderIntent(
            intent_id=intent_id,
            job_id=config.job_id,
            signal_id=signal.signal_id,
            destination=config.destination,
            instrument=instrument or signal.instrument,
            side=side,
            client_order_id=client_order_id,
            quantity=quantity,
            weight_pct=weight_pct,
            limit_price=limit_price,
            status=OrderIntentStatus.CREATED,
            metadata={"account_id": config.account_id, "strategy_id": signal.strategy_id,
                      "environment": config.environment or ("timefolio" if config.destination == OrderDestination.TIMEFOLIO else "unverified")},
        )
        if persist:
            self.store.record_order_intent(intent)
        return intent


class UnimplementedBrokerAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    async def connect(self) -> None:
        raise NotImplementedError(f"{self.name} BrokerAdapter is not implemented yet")

    async def account_state(self) -> dict:
        raise NotImplementedError(f"{self.name} account_state is not implemented yet")

    async def submit_order(self, intent: OrderIntent) -> dict:
        del intent
        raise NotImplementedError(f"{self.name} submit_order is not implemented yet")

    async def order_status(self, client_order_id: str) -> dict:
        del client_order_id
        raise NotImplementedError(f"{self.name} order_status is not implemented yet")
