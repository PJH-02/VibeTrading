from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

from pydantic import BaseModel

from .models import AssetClass, BaseMarketEvent, DataGapEvent, EventType, Signal, utc_now_ns
from .settings import RuntimeSettings, get_settings

logger = logging.getLogger(__name__)
_MARKET_PREFIXES = ("md.trade.", "md.order_book.")


class EventBus(Protocol):
    async def connect(self, *, initialize: bool = False) -> None: ...
    async def close(self) -> None: ...
    async def publish_market_event(self, event: BaseMarketEvent) -> None: ...
    async def publish_signal(self, signal: Signal) -> None: ...
    async def publish_order_intent(self, intent: Any) -> None: ...
    async def publish_system(self, kind: str, payload: dict[str, Any] | None = None) -> None: ...
    async def subscribe(self, subject: str, durable: str, batch: int = 100) -> AsyncIterator["BusMessage"]: ...
    async def subscribe_batches(self, subject: str, durable: str, batch: int = 100) -> AsyncIterator[list["BusMessage"]]: ...


class JsonEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, BaseModel):
            return obj.model_dump(mode="json")
        return super().default(obj)


def dumps(data: Any) -> bytes:
    if isinstance(data, BaseModel):
        return data.model_dump_json(exclude_none=False).encode("utf-8")
    return json.dumps(data, ensure_ascii=False, cls=JsonEncoder, separators=(",", ":")).encode("utf-8")


def loads(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))


def subject_for_event(event: BaseMarketEvent) -> str:
    symbol = re.sub(r"[^A-Z0-9_]+", "_", event.instrument.std_symbol.upper()).strip("_")
    return ".".join(("md", event.event_type.value, event.venue.value, event.instrument.asset_class.value, symbol or "UNKNOWN"))


def subject_for_signal(signal: Signal) -> str:
    strategy = re.sub(r"[^A-Z0-9_]+", "_", signal.strategy_id.upper()).strip("_") or "UNKNOWN"
    symbol = re.sub(r"[^A-Z0-9_]+", "_", signal.instrument.std_symbol.upper()).strip("_") or "UNKNOWN"
    return ".".join(("signal", strategy, signal.instrument.venue.value, signal.instrument.asset_class.value, symbol))


def _is_market_subject(subject: str) -> bool:
    return subject == "md.>" or subject.startswith(_MARKET_PREFIXES)


@dataclass
class BusMessage:
    data: dict[str, Any]
    subject: str
    event_id: str
    stream_name: str = ""
    stream_generation: str = ""
    stream_sequence: int | None = None
    _raw: Any = None

    @property
    def commit_id(self) -> str:
        if self.stream_name and self.stream_generation and self.stream_sequence is not None:
            return f"{self.stream_name}:{self.stream_generation}:{self.stream_sequence}"
        return self.event_id

    async def ack(self) -> None:
        if self._raw is not None:
            await self._raw.ack()

    async def nak(self, delay: float = 5.0) -> None:
        if self._raw is not None:
            try:
                await self._raw.nak(delay=delay)
            except TypeError:
                await self._raw.nak()

    async def in_progress(self) -> None:
        if self._raw is not None and hasattr(self._raw, "in_progress"):
            await self._raw.in_progress()


class NatsJetStreamBus:
    """Separate loss-intolerant market WorkQueue from operational fan-out."""

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self._nc: Any = None
        self._js: Any = None
        self._market_generation = ""

    @property
    def market_stream_name(self) -> str:
        return self.settings.nats.stream_name

    @property
    def operations_stream_name(self) -> str:
        return self.settings.nats.operations_stream_name

    async def connect(self, *, initialize: bool = False) -> None:
        import nats

        self._nc = await nats.connect(self.settings.nats.url, connect_timeout=5, reconnect_time_wait=1,
                                      max_reconnect_attempts=-1, ping_interval=20, max_outstanding_pings=5)
        self._js = self._nc.jetstream()
        if initialize:
            await self.initialize_streams()
        try:
            info = await self._js.stream_info(self.market_stream_name)
            self._market_generation = str(getattr(info, "created", "") or self.market_stream_name)
        except Exception:
            self._market_generation = ""

    async def initialize_streams(self) -> None:
        if self._js is None:
            raise RuntimeError("NATS JetStream is not connected")
        from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig

        await self._ensure_stream(StreamConfig(
            name=self.market_stream_name, subjects=["md.trade.>", "md.order_book.>"],
            retention=RetentionPolicy.WORK_QUEUE, storage=StorageType.FILE,
            max_bytes=int(self.settings.nats.max_bytes), discard=DiscardPolicy.NEW, duplicate_window=120,
        ))
        await self._ensure_stream(StreamConfig(
            name=self.operations_stream_name, subjects=["md.data_gap.>", "signal.>", "order.>", "system.>"],
            retention=RetentionPolicy.LIMITS, storage=StorageType.FILE,
            max_bytes=int(self.settings.nats.operations_max_bytes), discard=DiscardPolicy.OLD, duplicate_window=120,
        ))
        await self.ensure_storage_consumer()

    async def _ensure_stream(self, wanted: Any) -> None:
        try:
            current = (await self._js.stream_info(wanted.name)).config
        except Exception:
            await self._js.add_stream(wanted)
            return
        def policy_value(value: Any) -> str:
            return str(getattr(value, "value", value)).lower()

        same_policy = (
            sorted(str(x) for x in (current.subjects or [])) == sorted(str(x) for x in (wanted.subjects or []))
            and policy_value(current.retention) == policy_value(wanted.retention)
            and policy_value(current.discard) == policy_value(wanted.discard)
            and policy_value(current.storage) == policy_value(wanted.storage)
        )
        if not same_policy:
            raise RuntimeError(f"JetStream stream {wanted.name} has incompatible policy; stop ingestion and reset/migrate it explicitly.")
        if int(current.max_bytes or 0) != int(wanted.max_bytes):
            await self._js.update_stream(wanted)

    async def ensure_storage_consumer(self) -> None:
        if self._js is None:
            raise RuntimeError("NATS JetStream is not connected")
        from nats.js.api import AckPolicy, ConsumerConfig

        wanted = ConsumerConfig(durable_name="parquet-storage", filter_subject="md.>", ack_policy=AckPolicy.EXPLICIT,
                                ack_wait=int(self.settings.nats.ack_wait_seconds),
                                max_ack_pending=int(self.settings.nats.max_ack_pending))
        try:
            current = (await self._js.consumer_info(self.market_stream_name, "parquet-storage")).config
        except Exception as exc:
            if "not found" not in str(exc).lower():
                raise
            await self._js.add_consumer(self.market_stream_name, config=wanted)
            return
        if str(current.filter_subject) != "md.>" or int(current.max_ack_pending or 0) != int(wanted.max_ack_pending) or int(current.ack_wait or 0) != int(wanted.ack_wait):
            raise RuntimeError("parquet-storage consumer policy differs; reset it explicitly while collectors are stopped.")

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:
                await self._nc.close()
        self._nc = self._js = None

    async def _publish(self, subject: str, payload: bytes, message_id: str = "") -> None:
        if self._js is None:
            raise RuntimeError("NATS JetStream is not connected")
        await self._js.publish(subject, payload, headers={"Nats-Msg-Id": message_id} if message_id else None, timeout=5)

    async def publish_market_event(self, event: BaseMarketEvent) -> None:
        if event.event_type == EventType.DATA_GAP:
            await self._publish(subject_for_event(event), dumps(event), event.event_id)
            return
        if event.event_type not in {EventType.TRADE, EventType.ORDER_BOOK} or event.instrument.asset_class in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERP}:
            logger.warning("blocked collection-policy event type=%s instrument=%s", event.event_type.value, event.instrument.key)
            return
        age = time.time_ns() - int(event.source_ts_ns)
        max_age = max(60, int(self.settings.env.get("MARKET_EVENT_MAX_AGE_SECONDS", "172800")))
        skew = max(1, int(self.settings.env.get("MARKET_EVENT_FUTURE_SKEW_SECONDS", "300")))
        if age > max_age * 1_000_000_000 or age < -skew * 1_000_000_000:
            gap = DataGapEvent(instrument=event.instrument, venue=event.venue, provider=event.provider,
                               source_ts_ns=utc_now_ns(), receive_ts_ns=utc_now_ns(), session=event.session,
                               sequence=event.sequence, reason=f"invalid realtime timestamp age_seconds={age // 1_000_000_000}",
                               resync_status="blocked_invalid_timestamp")
            await self._publish(subject_for_event(gap), dumps(gap), gap.event_id)
            return
        await self._publish(subject_for_event(event), dumps(event), event.event_id)

    async def publish_signal(self, signal: Signal) -> None:
        await self._publish(subject_for_signal(signal), dumps(signal), signal.signal_id)

    async def publish_order_intent(self, intent: Any) -> None:
        intent_id = str(getattr(intent, "intent_id", "") or (intent.get("intent_id", "") if isinstance(intent, dict) else ""))
        await self._publish("order.intent", dumps(intent), intent_id)

    async def publish_system(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        await self._publish("system." + kind, dumps({"event_type": "system", "kind": kind, "ts_ns": time.time_ns(), **(payload or {})}))

    async def subscribe(self, subject: str, durable: str, batch: int = 100) -> AsyncIterator[BusMessage]:
        async for items in self.subscribe_batches(subject, durable, batch):
            for item in items:
                yield item

    async def subscribe_batches(self, subject: str, durable: str, batch: int = 100) -> AsyncIterator[list[BusMessage]]:
        if self._js is None:
            raise RuntimeError("NATS JetStream is not connected")
        market = _is_market_subject(subject)
        if market and durable != "parquet-storage":
            raise RuntimeError("MARKET_DATA is storage-only WorkQueue; live strategies need a separate fan-out feed")
        if market:
            await self.ensure_storage_consumer()
        stream = self.market_stream_name if market else self.operations_stream_name
        sub = await self._js.pull_subscribe(subject, durable=durable, stream=stream)
        while True:
            try:
                raw_messages = await sub.fetch(batch=batch, timeout=1)
            except Exception as exc:
                if "timeout" in exc.__class__.__name__.lower():
                    yield []
                    continue
                raise
            output: list[BusMessage] = []
            for raw in raw_messages:
                body = loads(raw.data)
                try:
                    sequence = int(raw.metadata.sequence.stream)
                except Exception:
                    sequence = None
                output.append(BusMessage(body, raw.subject, str(body.get("event_id") or ""),
                                         stream if market else "", self._market_generation if market else "", sequence, raw))
            yield output

    async def market_stream_usage_ratio(self) -> float:
        if self._js is None:
            return 1.0
        try:
            state = (await self._js.stream_info(self.market_stream_name)).state
            return int(getattr(state, "bytes", 0) or 0) / max(1, int(self.settings.nats.max_bytes))
        except Exception:
            return 1.0

    async def market_stream_status(self) -> dict[str, int | float]:
        if self._js is None:
            return {"bytes": 0, "messages": 0, "max_bytes": self.settings.nats.max_bytes, "ratio": 1.0}
        info = await self._js.stream_info(self.market_stream_name)
        return {"bytes": int(info.state.bytes), "messages": int(info.state.messages), "max_bytes": self.settings.nats.max_bytes, "ratio": await self.market_stream_usage_ratio()}


class InMemoryEventBus:
    def __init__(self) -> None:
        self.messages: list[BusMessage] = []

    async def connect(self, *, initialize: bool = False) -> None:
        del initialize

    async def close(self) -> None:
        return None

    async def publish_market_event(self, event: BaseMarketEvent) -> None:
        if event.event_type not in {EventType.TRADE, EventType.ORDER_BOOK, EventType.DATA_GAP}:
            return
        if event.event_type != EventType.DATA_GAP and event.instrument.asset_class in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERP}:
            return
        self.messages.append(BusMessage(event.model_dump(mode="json"), subject_for_event(event), event.event_id))

    async def publish_signal(self, signal: Signal) -> None:
        self.messages.append(BusMessage(signal.model_dump(mode="json"), subject_for_signal(signal), signal.signal_id))

    async def publish_order_intent(self, intent: Any) -> None:
        body = intent.model_dump(mode="json") if isinstance(intent, BaseModel) else dict(intent)
        self.messages.append(BusMessage(body, "order.intent", str(body.get("intent_id") or "")))

    async def publish_system(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        self.messages.append(BusMessage({"event_type": "system", "kind": kind, **(payload or {})}, "system." + kind, ""))

    async def subscribe(self, subject: str, durable: str, batch: int = 100) -> AsyncIterator[BusMessage]:
        async for items in self.subscribe_batches(subject, durable, batch):
            for item in items:
                yield item

    async def subscribe_batches(self, subject: str, durable: str, batch: int = 100) -> AsyncIterator[list[BusMessage]]:
        del durable
        index = 0
        while True:
            if index < len(self.messages):
                source = self.messages[index:]
                index = len(self.messages)
                prefix = subject[:-1] if subject.endswith(">") else subject
                items = [item for item in source if item.subject.startswith(prefix)][:batch]
                yield items
            else:
                await asyncio.sleep(0.05)
                yield []

    async def market_stream_usage_ratio(self) -> float:
        return 0.0
