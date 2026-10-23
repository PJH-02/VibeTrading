from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import re
import time
import hashlib
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

from pydantic import BaseModel

from .models import AssetClass, BaseMarketEvent, DataGapEvent, EventType, InstrumentId, Signal, Venue, event_from_dict, utc_now_ns
from .settings import RuntimeSettings, get_settings

logger = logging.getLogger(__name__)
RECOVERY_ETFS = frozenset({'069500','122630','114800','252670','229200','233740','251340'})


def allowed_supplement(event: BaseMarketEvent) -> bool:
    instrument = event.instrument
    if (instrument.asset_class == AssetClass.INDEX and instrument.std_symbol == 'KOSPI200'):
        if event.event_type == EventType.INDEX_QUOTE:
            return event.provider == 'kis' and event.venue == Venue.KIS and instrument.raw_symbol == '2001'
        return (event.event_type == EventType.BAR_1M and event.ingest_mode == 'backfill'
                and event.provider in {'kis','kiwoom'} and event.venue.value == event.provider)
    return (event.event_type == EventType.BAR_1M and event.ingest_mode == 'backfill'
            and event.provider == 'toss' and event.venue == Venue.TOSS
            and instrument.asset_class == AssetClass.ETF and instrument.raw_symbol in RECOVERY_ETFS)
_MARKET_PREFIXES = ("md.trade.", "md.order_book.", "md.bar_1m.", "md.etf_nav.", "md.funding_rate.", "md.open_interest.", "md.participant_flow.")


class EventBus(Protocol):
    async def connect(self, *, initialize: bool = False) -> None: ...
    async def close(self) -> None: ...
    async def publish_market_event(self, event: BaseMarketEvent) -> None: ...
    async def publish_signal(self, signal: Signal) -> None: ...
    async def publish_order_intent(self, intent: Any) -> None: ...
    async def publish_system(self, kind: str, payload: dict[str, Any] | None = None) -> None: ...
    async def subscribe(self, subject: str, durable: str, batch: int = 100) -> AsyncIterator["BusMessage"]: ...
    async def subscribe_batches(self, subject: str, durable: str, batch: int = 100) -> AsyncIterator[list["BusMessage"]]: ...
    async def subscribe_live(self, subject: str = "live.crypto.>") -> AsyncIterator["BusMessage"]: ...
    async def consumer_ack_floor(self, stream: str, durable: str) -> tuple[str, int] | None: ...


@dataclass(frozen=True)
class ConfirmedAckFloor:
    stream: str
    generation: str
    durable: str
    consumer_generation: str
    sequence: int
    observed_ts_ns: int


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
    prefix = "crypto" if event.instrument.asset_class == AssetClass.CRYPTO_PERP else "md"
    return ".".join((prefix, event.event_type.value, event.venue.value, event.instrument.asset_class.value, symbol or "UNKNOWN"))


@dataclass(frozen=True, slots=True)
class FrozenMarketSnapshot:
    """Scalar publication metadata captured before the first suspension.

    InstrumentId is itself frozen and contains only immutable scalar values.
    Mutable event arrays/metadata are represented exclusively by owned bytes.
    """
    event_id: str
    event_type: EventType
    instrument: InstrumentId
    venue: Venue
    provider: str
    source_ts_ns: int | None
    wallclock_ts_ns: int | None
    receive_ts_ns: int | None
    ingest_mode: str
    session: str
    sequence: int | str | None
    fast: bool | None


@dataclass(frozen=True, slots=True)
class MarketEventPacket:
    snapshot: FrozenMarketSnapshot
    subject: str
    payload: bytes
    live_payload: bytes

    @property
    def byte_length(self) -> int:
        return len(self.payload)


@dataclass(frozen=True, slots=True)
class MarketPublication:
    packet: MarketEventPacket
    live: bool
    record_crypto_size: bool


def prepare_market_event(event: BaseMarketEvent) -> MarketEventPacket:
    # Synchronous preparation owns the complete wire representation before an
    # async caller can mutate a list inside its otherwise frozen event model.
    payload = dumps(event)
    live_payload = payload
    if (event.instrument.asset_class == AssetClass.CRYPTO_PERP
            and event.event_type == EventType.ORDER_BOOK
            and getattr(event, "fast", None) is not False
            and getattr(event, "raw_payload", None) is not None):
        live_data = event.model_dump(mode="json")
        live_data.pop("raw_payload", None)
        live_payload = dumps(live_data)
    snapshot = FrozenMarketSnapshot(
        event.event_id, event.event_type, event.instrument, event.venue, event.provider,
        event.source_ts_ns, event.wallclock_ts_ns, event.receive_ts_ns, event.ingest_mode,
        event.session, event.sequence, getattr(event, "fast", None))
    return MarketEventPacket(snapshot, subject_for_event(event), payload, live_payload)


def resolve_market_publication(packet: MarketEventPacket, env: dict[str, str]) -> MarketPublication | None:
    """Make one policy/time decision shared by both publication lanes."""
    event = packet.snapshot
    if event.provider in {"bitget-perp", "bybit-perp", "hyperliquid", "binance"} or (event.venue == Venue.KIS and event.instrument.asset_class == AssetClass.OPTION):
        return
    if event.event_type == EventType.DATA_GAP:
        return MarketPublication(packet, event.instrument.asset_class == AssetClass.CRYPTO_PERP, False)
    if allowed_supplement(event):
        allowed = True
    elif event.instrument.asset_class == AssetClass.CRYPTO_PERP:
        allowed = event.venue.value in {"binance", "arcus", "grvt"} and event.instrument.std_symbol in {"BTCUSDT", "ETHUSDT", "SOLUSDT"} and event.event_type in {EventType.TRADE, EventType.ORDER_BOOK, EventType.FUNDING_RATE, EventType.OPEN_INTEREST}
    elif event.venue == Venue.KIWOOM and event.instrument.asset_class == AssetClass.ETF:
        allowed = event.event_type in {EventType.TRADE, EventType.ORDER_BOOK, EventType.BAR_1M, EventType.ETF_NAV}
    elif (event.instrument.venue == Venue.TOSS and event.event_type == EventType.BAR_1M):
        allowed = (event.provider == "toss" and event.instrument.asset_class == AssetClass.EQUITY
                   and event.instrument.std_symbol in {"TOSS:US:SPY", "TOSS:US:QQQ"})
    elif event.instrument.venue.value in {"hyperliquid", "binance"}:
        allowed = (
            event.event_type in {EventType.ORDER_BOOK, EventType.BAR_1M, EventType.FUNDING_RATE, EventType.OPEN_INTEREST}
            and event.instrument.asset_class == AssetClass.FUTURE
        )
    else:
        allowed = (
            event.event_type in {EventType.TRADE, EventType.ORDER_BOOK, EventType.PARTICIPANT_FLOW}
            and event.instrument.asset_class not in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERP}
        )
    if not allowed:
        logger.warning("blocked collection-policy event type=%s instrument=%s", event.event_type.value, event.instrument.key)
        return
    reference_ts = event.source_ts_ns or event.wallclock_ts_ns or event.receive_ts_ns or time.time_ns()
    age = time.time_ns() - int(reference_ts)
    max_age = max(60, int(env.get("MARKET_EVENT_MAX_AGE_SECONDS", "172800")))
    skew = max(1, int(env.get("MARKET_EVENT_FUTURE_SKEW_SECONDS", "300")))
    if age > max_age * 1_000_000_000 or age < -skew * 1_000_000_000:
        gap = DataGapEvent(instrument=event.instrument, venue=event.venue, provider=event.provider,
                           source_ts_ns=utc_now_ns(), receive_ts_ns=utc_now_ns(), session=event.session,
                           sequence=event.sequence, reason=f"invalid realtime timestamp age_seconds={age // 1_000_000_000}",
                           resync_status="blocked_invalid_timestamp")
        return MarketPublication(prepare_market_event(gap), False, False)
    crypto = event.instrument.asset_class == AssetClass.CRYPTO_PERP
    live = ((event.venue == Venue.KIWOOM and event.instrument.asset_class == AssetClass.ETF)
            or allowed_supplement(event)
            or (crypto and not (event.event_type == EventType.ORDER_BOOK and event.fast is False)))
    return MarketPublication(packet, live, crypto)


async def publish_prepared_market_event(receiver: Any, packet: MarketEventPacket) -> None:
    # __getattr__ wrappers and subclasses with a public override must not be
    # bypassed by discovering an inherited/forwarded packet capability.
    declared = type(receiver).__dict__
    if (declared.get("_market_packet_protocol") == 1
            and "publish_market_packet" in declared
            and declared.get("_packet_public_method") is declared.get("publish_market_event")
            and "publish_market_event" not in getattr(receiver, "__dict__", {})):
        await receiver.publish_market_packet(packet)
    else:
        await receiver.publish_market_event(event_from_dict(loads(packet.payload)))


def subject_for_signal(signal: Signal) -> str:
    strategy = re.sub(r"[^A-Z0-9_]+", "_", signal.strategy_id.upper()).strip("_") or "UNKNOWN"
    symbol = re.sub(r"[^A-Z0-9_]+", "_", signal.instrument.std_symbol.upper()).strip("_") or "UNKNOWN"
    return ".".join(("signal", strategy, signal.instrument.venue.value, signal.instrument.asset_class.value, symbol))


def _is_market_subject(subject: str) -> bool:
    return subject == "md.>" or subject.startswith(_MARKET_PREFIXES)


def _is_crypto_subject(subject: str) -> bool:
    return subject == "crypto.>" or subject.startswith("crypto.")


@dataclass
class BusMessage:
    data: dict[str, Any]
    subject: str
    event_id: str
    stream_name: str = ""
    stream_generation: str = ""
    stream_sequence: int | None = None
    delivery_count: int = 1
    _raw: Any = None
    commit_id: str = ""

    def __post_init__(self) -> None:
        if not self.commit_id:
            if self.stream_name and self.stream_generation and self.stream_sequence is not None:
                self.commit_id = f"{self.stream_name}:{self.stream_generation}:{self.stream_sequence}"
            else:
                self.commit_id = self.event_id

    async def ack(self) -> None:
        if self._raw is not None:
            # JetStream ACK is intentionally one-way.  Waiting for a reply
            # for every high-rate market event turns the consumer into a
            # request storm and can itself cause NATS timeouts.
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


@dataclass(frozen=True)
class StreamProbeResult:
    status: str
    stream: str
    bytes: int | None
    max_bytes: int | None
    ratio: float | None
    generation: str | None
    observed_at: float
    last_success_at: float | None
    error: str | None = None
    config_mismatch: bool = False
    consecutive_failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "status": self.status,
            "stream": self.stream,
            "bytes": self.bytes,
            "max_bytes": self.max_bytes,
            "ratio": self.ratio,
            "generation": self.generation,
            "observed_age_seconds": max(0.0, now - self.observed_at),
            "last_success_age_seconds": (None if self.last_success_at is None
                                         else max(0.0, now - self.last_success_at)),
            "error": self.error,
            "config_mismatch": self.config_mismatch,
            "consecutive_failures": self.consecutive_failures,
        }


class NatsPublishError(RuntimeError):
    def __init__(self, reason: str, cause: BaseException) -> None:
        self.reason = reason
        self.cause_type = type(cause).__name__
        super().__init__(f"{reason}: {self.cause_type}")


class NatsJetStreamBus:
    """Separate loss-intolerant market WorkQueue from operational fan-out."""

    _market_packet_protocol = 1

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self._nc: Any = None
        self._js: Any = None
        self._market_generation = ""
        self._crypto_generation = ""
        # A JetStream control-plane request can briefly lag behind a healthy
        # data plane while the same small disk is being compacted.  Preserve a
        # recent, confirmed stream size so that a single STREAM.INFO timeout
        # does not look like a full stream and pause every collector.
        self._market_usage_ratio: float | None = None
        self._market_usage_checked_at = 0.0
        self._stream_probe_cache: dict[str, StreamProbeResult] = {}
        self._stream_probe_failures: dict[str, int] = {}
        self._stream_probe_lock = asyncio.Lock()
        self._crypto_publish_sizes: deque[tuple[float, int]] = deque()
        self._crypto_publish_started_at = time.monotonic()

    @property
    def recent_publish_bytes_per_second(self) -> float:
        now = time.monotonic()
        cutoff = int(now) - 60
        while self._crypto_publish_sizes and self._crypto_publish_sizes[0][0] < cutoff:
            self._crypto_publish_sizes.popleft()
        elapsed = min(60.0, max(1.0, now - self._crypto_publish_started_at))
        return sum(size for _, size in self._crypto_publish_sizes) / elapsed

    def _record_crypto_publish_size(self, size: int) -> None:
        # Bounded even when nobody reads the rate (the normal fresh-probe
        # path). Include the oldest partial second for conservative capacity
        # estimates. At most 61 buckets replace millions of per-event tuples.
        second = int(time.monotonic())
        while self._crypto_publish_sizes and self._crypto_publish_sizes[0][0] < second - 60:
            self._crypto_publish_sizes.popleft()
        if self._crypto_publish_sizes and self._crypto_publish_sizes[-1][0] == second:
            _, total = self._crypto_publish_sizes.pop()
            self._crypto_publish_sizes.append((second, total + size))
        else:
            self._crypto_publish_sizes.append((second, size))

    @property
    def market_stream_name(self) -> str:
        return self.settings.nats.stream_name

    @property
    def operations_stream_name(self) -> str:
        return self.settings.nats.operations_stream_name

    @property
    def crypto_stream_name(self) -> str:
        return "CRYPTO_DATA"

    async def connect(self, *, initialize: bool = False) -> None:
        import nats

        self._nc = await nats.connect(self.settings.nats.url, connect_timeout=5, reconnect_time_wait=1,
                                      max_reconnect_attempts=-1, ping_interval=20, max_outstanding_pings=5,
                                      drain_timeout=5)
        self._js = self._nc.jetstream()
        if initialize:
            await self.initialize_streams()
        market = await self.stream_probe(self.market_stream_name)
        self._market_generation = market.generation or ""
        if market.status == "fresh" and market.ratio is not None:
            self._market_usage_ratio = market.ratio
            self._market_usage_checked_at = time.monotonic()
        crypto = await self.stream_probe(self.crypto_stream_name)
        self._crypto_generation = crypto.generation or ""

    def _remember_market_usage(self, info: Any) -> float:
        state = getattr(info, "state", None)
        ratio = int(getattr(state, "bytes", 0) or 0) / max(1, int(self.settings.nats.max_bytes))
        self._market_usage_ratio = ratio
        self._market_usage_checked_at = time.monotonic()
        return ratio

    async def initialize_streams(self) -> None:
        if self._js is None:
            raise RuntimeError("NATS JetStream is not connected")
        from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig

        await self._ensure_stream(StreamConfig(
            name=self.market_stream_name,
            subjects=["md.trade.>", "md.order_book.>", "md.bar_1m.>", "md.etf_nav.>", "md.funding_rate.>", "md.open_interest.>", "md.participant_flow.>", "md.index_quote.>"],
            retention=RetentionPolicy.WORK_QUEUE, storage=StorageType.FILE,
            max_bytes=int(self.settings.nats.max_bytes), discard=DiscardPolicy.NEW, duplicate_window=120,
        ))
        await self._ensure_stream(StreamConfig(
            name=self.crypto_stream_name,
            subjects=["crypto.trade.>", "crypto.order_book.>", "crypto.funding_rate.>", "crypto.open_interest.>"],
            retention=RetentionPolicy.WORK_QUEUE, storage=StorageType.FILE,
            max_bytes=int(self.settings.env.get("CRYPTO_NATS_MAX_BYTES", str(self.settings.nats.max_bytes))),
            discard=DiscardPolicy.NEW, duplicate_window=120,
        ))
        await self._ensure_stream(StreamConfig(
            name=self.operations_stream_name, subjects=["md.data_gap.>", "crypto.data_gap.>", "signal.>", "order.>", "system.>"],
            retention=RetentionPolicy.LIMITS, storage=StorageType.FILE,
            max_bytes=int(self.settings.nats.operations_max_bytes), discard=DiscardPolicy.OLD, duplicate_window=120,
        ))
        await self.ensure_storage_consumer()
        await self.ensure_crypto_storage_consumer()

    async def ensure_crypto_storage_consumer(self) -> None:
        from nats.js.api import AckPolicy, ConsumerConfig
        wanted = ConsumerConfig(durable_name="crypto-parquet-storage", filter_subject="crypto.>", ack_policy=AckPolicy.EXPLICIT,
                                ack_wait=int(self.settings.nats.ack_wait_seconds), max_ack_pending=int(self.settings.nats.max_ack_pending))
        try:
            current = (await self._js.consumer_info(self.crypto_stream_name, "crypto-parquet-storage")).config
        except Exception as exc:
            if "not found" not in str(exc).lower():
                raise
            await self._js.add_consumer(self.crypto_stream_name, config=wanted)
            return
        if str(current.filter_subject) != "crypto.>" or int(current.max_ack_pending or 0) != int(wanted.max_ack_pending):
            raise RuntimeError("crypto storage consumer policy differs; stop ingestion and migrate explicitly")

    async def _ensure_stream(self, wanted: Any) -> None:
        try:
            current = (await self._js.stream_info(wanted.name)).config
        except Exception:
            await self._js.add_stream(wanted)
            return
        def policy_value(value: Any) -> str:
            return str(getattr(value, "value", value)).lower()

        current_subjects = {str(x) for x in (current.subjects or [])}
        wanted_subjects = {str(x) for x in (wanted.subjects or [])}
        same_non_subject_policy = (
            policy_value(current.retention) == policy_value(wanted.retention)
            and policy_value(current.discard) == policy_value(wanted.discard)
            and policy_value(current.storage) == policy_value(wanted.storage)
        )
        # A planned additive subject migration is safe for the existing
        # WorkQueue and durable consumer.  Subject removal or policy changes
        # remain fail-closed because they could orphan unconsumed messages.
        if not same_non_subject_policy or not current_subjects.issubset(wanted_subjects):
            raise RuntimeError(f"JetStream stream {wanted.name} has incompatible policy; stop ingestion and reset/migrate it explicitly.")
        if current_subjects != wanted_subjects or int(current.max_bytes or 0) != int(wanted.max_bytes):
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

    async def reset_storage_consumer_if_empty(self) -> dict[str, int | str]:
        """Recreate the storage durable only after all market data is ACKed.

        This is intentionally stricter than checking consumer pending alone:
        WorkQueue stream messages, pending deliveries, and ACK-pending deliveries
        must all be zero before the durable policy can be changed.
        """
        if self._js is None:
            raise RuntimeError("NATS JetStream is not connected")
        stream_info = await self._js.stream_info(self.market_stream_name)
        try:
            consumer_info = await self._js.consumer_info(self.market_stream_name, "parquet-storage")
        except Exception as exc:
            if "not found" not in str(exc).lower():
                raise
            consumer_info = None
        messages = int(getattr(stream_info.state, "messages", 0) or 0)
        pending = int(getattr(consumer_info, "num_pending", 0) or 0) if consumer_info is not None else 0
        ack_pending = int(getattr(consumer_info, "num_ack_pending", 0) or 0) if consumer_info is not None else 0
        if messages or pending or ack_pending:
            raise RuntimeError(
                "refusing storage consumer reset while MARKET_DATA is not empty: "
                f"messages={messages} pending={pending} ack_pending={ack_pending}"
            )
        if consumer_info is not None:
            await self._js.delete_consumer(self.market_stream_name, "parquet-storage")
        await self.ensure_storage_consumer()
        return {
            "status": "recreated",
            "stream": self.market_stream_name,
            "max_ack_pending": int(self.settings.nats.max_ack_pending),
            "ack_wait_seconds": int(self.settings.nats.ack_wait_seconds),
        }

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await asyncio.wait_for(self._nc.drain(),timeout=5)
            except Exception:
                try:
                    await asyncio.wait_for(self._nc.close(),timeout=2)
                except Exception:
                    logger.warning("timed out while closing NATS connection",exc_info=True)
        self._nc = self._js = None

    async def consumer_ack_floor(self, stream: str, durable: str) -> tuple[str, int] | None:
        """Return JetStream's durable ACK floor with the stream generation."""
        if self._js is None:
            return None
        stream_info, consumer_info = await asyncio.gather(
            self._js.stream_info(stream), self._js.consumer_info(stream, durable)
        )
        floor = getattr(consumer_info, "ack_floor", None)
        return str(getattr(stream_info, "created", "") or stream), int(
            getattr(floor, "stream_seq", 0) or 0
        )

    async def confirmed_ack_floor(self, stream: str, durable: str, subject: str) -> ConfirmedAckFloor:
        """Bracket server-confirmed progress with stream/consumer identities.

        This receipt deliberately has separate provenance from legacy locally
        inferred floors. A concurrent stream or durable recreation fails closed.
        """
        if self._js is None:
            raise RuntimeError('JetStream is not connected')
        before = await self._js.stream_info(stream)
        consumer = await self._js.consumer_info(stream, durable)
        after = await self._js.stream_info(stream)
        again = await self._js.consumer_info(stream, durable)
        generation = str(getattr(before, 'created', '') or '')
        incarnation = str(getattr(consumer, 'created', '') or '')
        if (not generation or not incarnation or generation != str(getattr(after, 'created', '') or '')
                or incarnation != str(getattr(again, 'created', '') or '')):
            raise RuntimeError('NATS identity changed while observing ACK floor')
        for info in (consumer, again):
            config = info.config
            policy = lambda name: getattr(getattr(config, name, None), 'value', getattr(config, name, None))
            if (getattr(config, 'durable_name', None) != durable
                    or getattr(config, 'filter_subject', None) != subject
                    or getattr(config, 'filter_subjects', None)
                    or policy('ack_policy') != 'explicit' or policy('deliver_policy') != 'all'
                    or getattr(config, 'headers_only', False)):
                raise RuntimeError('NATS consumer policy does not authorize storage pruning')
        sequence = int(getattr(consumer.ack_floor, 'stream_seq', 0) or 0)
        last_sequence = int(getattr(after.state, 'last_seq', 0) or 0)
        delivered = int(getattr(consumer.delivered, 'stream_seq', 0) or 0)
        if sequence < 0 or sequence > last_sequence or sequence > delivered:
            raise RuntimeError('NATS ACK floor exceeds observed stream/delivery coverage')
        return ConfirmedAckFloor(stream, generation, durable, incarnation, sequence, time.time_ns())

    async def _publish(self, subject: str, payload: bytes, message_id: str = "") -> None:
        if self._js is None:
            raise RuntimeError("NATS JetStream is not connected")
        attempts = 3 if message_id and subject.startswith(('md.','crypto.')) else 1
        for attempt in range(attempts):
            try:
                await asyncio.wait_for(
                    self._js.publish(subject,payload,headers={'Nats-Msg-Id':message_id} if message_id else None,timeout=5),
                    timeout=5.5)
                return
            except (asyncio.TimeoutError, TimeoutError) as exc:
                if attempt+1==attempts:
                    raise NatsPublishError('nats_publish_timeout',exc) from exc
                await asyncio.sleep(.1*(attempt+1))
            except Exception as exc:
                raise NatsPublishError('nats_publish_error',exc) from exc

    async def publish_market_event(self, event: BaseMarketEvent) -> None:
        await self.publish_market_packet(prepare_market_event(event))

    _packet_public_method = publish_market_event

    async def publish_market_packet(self, packet: MarketEventPacket) -> None:
        publication = self.resolve_market_publication(packet)
        if publication is not None:
            await self.publish_market_storage(publication)
            await self.publish_market_live(publication)

    def resolve_market_publication(self, packet: MarketEventPacket) -> MarketPublication | None:
        return resolve_market_publication(packet, self.settings.env)

    async def publish_market_storage(self, publication: MarketPublication) -> None:
        packet = publication.packet
        await self._publish(packet.subject, packet.payload, packet.snapshot.event_id)
        if publication.record_crypto_size:
            self._record_crypto_publish_size(packet.byte_length)

    async def publish_market_live(self, publication: MarketPublication) -> None:
        if publication.live and self._nc is not None:
            packet = publication.packet
            await self._nc.publish("live." + packet.subject, packet.live_payload)

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

    async def subscribe_live(self, subject: str = "live.crypto.>") -> AsyncIterator[BusMessage]:
        if self._nc is None:
            raise RuntimeError("NATS is not connected")
        if not subject.startswith("live.crypto."):
            raise ValueError("only crypto live feed is enabled")
        subscription = await self._nc.subscribe(subject, pending_msgs_limit=256, pending_bytes_limit=8 * 1024 * 1024)
        try:
            async for raw in subscription.messages:
                body = loads(raw.data)
                yield BusMessage(body, raw.subject, str(body.get("event_id") or ""))
        finally:
            await subscription.unsubscribe()

    async def subscribe_batches(self, subject: str, durable: str, batch: int = 100) -> AsyncIterator[list[BusMessage]]:
        if self._js is None:
            raise RuntimeError("NATS JetStream is not connected")
        market = _is_market_subject(subject)
        crypto = _is_crypto_subject(subject)
        if market and durable != "parquet-storage":
            raise RuntimeError("MARKET_DATA is storage-only WorkQueue; live strategies need a separate fan-out feed")
        if crypto and durable != "crypto-parquet-storage":
            raise RuntimeError("CRYPTO_DATA is storage-only WorkQueue")
        if market:
            await self.ensure_storage_consumer()
        if crypto:
            await self.ensure_crypto_storage_consumer()
        stream = self.crypto_stream_name if crypto else self.market_stream_name if market else self.operations_stream_name
        sub = await self._js.pull_subscribe(subject, durable=durable, stream=stream)
        while True:
            try:
                # The server-side fetch timeout normally returns in one
                # second. Keep an outer deadline as well: a stalled control
                # request must not prevent storage from flushing an already
                # durable in-memory batch.
                raw_messages = await asyncio.wait_for(sub.fetch(batch=batch, timeout=1), timeout=2)
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
                try:
                    delivery_count = int(raw.metadata.num_delivered or 1)
                except Exception:
                    delivery_count = 1
                output.append(BusMessage(body, raw.subject, str(body.get("event_id") or ""),
                                         stream if market or crypto else "", self._market_generation if market else str(getattr(self, "_crypto_generation", "")) if crypto else "", sequence, delivery_count, raw))
            yield output

    async def market_stream_usage_ratio(self) -> float:
        probe = await self.stream_probe(self.market_stream_name)
        if probe.ratio is None:
            raise RuntimeError(f"NATS stream probe unavailable: {probe.as_dict()}")
        return probe.ratio

    async def crypto_stream_usage_ratio(self) -> float:
        probe = await self.stream_probe(self.crypto_stream_name)
        if probe.ratio is None:
            raise RuntimeError(f"NATS stream probe unavailable: {probe.as_dict()}")
        return probe.ratio

    async def stream_probe(self, stream_name: str) -> StreamProbeResult:
        """Read actual JetStream capacity, with a short last-known-good cache."""
        now = time.monotonic()
        if self._js is None:
            return StreamProbeResult("unavailable", stream_name, None, None, None, None,
                                     now, None, "not_connected", consecutive_failures=0)
        async with self._stream_probe_lock:
            now = time.monotonic()
            try:
                info = await asyncio.wait_for(self._js.stream_info(stream_name), timeout=2.0)
                state = getattr(info, "state", None)
                config = getattr(info, "config", None)
                byte_count = int(getattr(state, "bytes", -1))
                actual_cap = int(getattr(config, "max_bytes", 0) or 0)
                configured_cap = (int(self.settings.env.get("CRYPTO_NATS_MAX_BYTES", str(self.settings.nats.max_bytes)))
                                  if stream_name == self.crypto_stream_name else int(self.settings.nats.max_bytes))
                if byte_count < 0 or actual_cap <= 0:
                    raise ValueError("stream bytes or actual max_bytes unavailable")
                generation = str(getattr(info, "created", "") or "")
                result = StreamProbeResult(
                    "fresh", stream_name, byte_count, actual_cap, byte_count / actual_cap,
                    generation or None, now, now, None, actual_cap != configured_cap,
                    0,
                )
                self._stream_probe_cache[stream_name] = result
                self._stream_probe_failures[stream_name] = 0
                if stream_name == self.crypto_stream_name:
                    self._crypto_generation = generation
                elif stream_name == self.market_stream_name:
                    self._market_generation = generation
                    self._market_usage_ratio = result.ratio
                    self._market_usage_checked_at = now
                return result
            except Exception as exc:
                failures = self._stream_probe_failures.get(stream_name, 0) + 1
                self._stream_probe_failures[stream_name] = failures
                previous = self._stream_probe_cache.get(stream_name)
                if previous is not None:
                    age = now - previous.observed_at
                    if age <= 15.0:
                        return StreamProbeResult(
                            "cached", stream_name, previous.bytes, previous.max_bytes,
                            previous.ratio, previous.generation, previous.observed_at,
                            previous.last_success_at, f"{type(exc).__name__}; failures={failures}",
                            previous.config_mismatch, failures,
                        )
                return StreamProbeResult(
                    "unavailable", stream_name, None, None, None,
                    previous.generation if previous else None, now,
                    previous.last_success_at if previous else None,
                    f"{type(exc).__name__}; failures={failures}",
                    consecutive_failures=failures,
                )

    async def market_stream_status(self) -> dict[str, Any]:
        probe = await self.stream_probe(self.market_stream_name)
        if probe.status == "unavailable":
            return {"status": probe.status, "ratio": None, "probe": probe.as_dict()}
        return {"status": probe.status, "bytes": int(probe.bytes or 0),
                "max_bytes": int(probe.max_bytes or 0), "ratio": float(probe.ratio or 0),
                "probe": probe.as_dict()}

    async def crypto_stream_status(self) -> dict[str, Any]:
        probe = await self.stream_probe(self.crypto_stream_name)
        if probe.status == "unavailable":
            return {"status": probe.status, "ratio": None, "probe": probe.as_dict()}
        return {"status": probe.status, "bytes": int(probe.bytes or 0),
                "max_bytes": int(probe.max_bytes or 0), "ratio": float(probe.ratio or 0),
                "probe": probe.as_dict()}


class BufferedMarketEventBus:
    """Bounded FIFO shards with explicit ownership of accepted packet bytes.

    The byte budget covers durable payloads queued or in flight, not total RSS.
    There is one worker and one in-flight slot per shard in addition to queues.
    """

    _market_packet_protocol = 1

    def __init__(self, inner: Any, *, shards: int = 4, max_items: int = 128, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.inner = inner
        self.shards = max(1, int(shards))
        self.max_bytes = max(1, int(max_bytes))
        per_shard = max(1, int(max_items) // self.shards)
        self._queues = [asyncio.Queue(maxsize=per_shard) for _ in range(self.shards)]
        self._admission = [asyncio.Lock() for _ in self._queues]
        self._workers: list[asyncio.Task[Any]] = []
        self._queued_bytes = 0
        self._condition = asyncio.Condition()
        self._error: BaseException | None = None
        self._failure = asyncio.Event()
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task[Any] | None = None
        self._accepted = self._published = self._failed = self._in_flight = 0

    @property
    def stats(self)->dict[str,int]:
        return {"accepted": self._accepted, "published": self._published, "failed": self._failed,
                "pending": self._accepted-self._published-self._failed,
                "queued_bytes": self._queued_bytes, "in_flight": self._in_flight,
                "queued_records": sum(queue.qsize() for queue in self._queues)}

    def __getattr__(self,name:str)->Any:return getattr(self.inner,name)

    async def start(self)->None:
        self._check_open()
        if not self._workers:
            self._workers=[asyncio.create_task(self._worker(queue),name=f"nats-publisher-{index}") for index,queue in enumerate(self._queues)]

    async def publish_market_event(self,event:BaseMarketEvent)->None:
        await self.publish_market_packet(prepare_market_event(event))

    _packet_public_method = publish_market_event

    async def publish_market_packet(self, packet: MarketEventPacket) -> None:
        self._check_open()
        size = packet.byte_length
        if size > self.max_bytes:
            raise RuntimeError(f"single market event exceeds publisher byte limit: {size}")
        await self.start()
        key = packet.snapshot.instrument.key.encode("utf-8")
        index = int.from_bytes(hashlib.sha256(key).digest()[:4], "big") % self.shards
        queue = self._queues[index]
        async with self._admission[index]:
            async with self._condition:
                while True:
                    self._check_open()
                    if not queue.full() and self._queued_bytes + size <= self.max_bytes:
                        break
                    await self._condition.wait()
                # No suspension between reservation and ownership transfer.
                self._queued_bytes += size
                try:
                    queue.put_nowait(packet)
                except BaseException:
                    self._queued_bytes -= size
                    self._condition.notify_all()
                    raise
                self._accepted += 1

    async def _worker(self,queue:asyncio.Queue[Any])->None:
        while True:
            packet = await queue.get()
            self._in_flight += 1
            try:
                async with self._condition:
                    self._condition.notify_all()
                await publish_prepared_market_event(self.inner, packet)
                self._published += 1
            except asyncio.CancelledError:
                self._failed += 1
                self._latch_error(RuntimeError("publisher worker cancelled with an unconfirmed event"))
                raise
            except BaseException as exc:
                self._failed += 1
                self._latch_error(exc)
            finally:
                self._queued_bytes -= packet.byte_length
                self._in_flight -= 1
                queue.task_done()
                async with self._condition:
                    self._condition.notify_all()
            if self._error is not None:
                return

    def _latch_error(self, error: BaseException) -> None:
        if self._error is None:
            self._error = error
        self._failure.set()

    def _raise_error(self)->None:
        if self._error is not None:raise RuntimeError(f"buffered NATS publisher failed: {self._error}") from self._error

    def _check_open(self) -> None:
        self._raise_error()
        if self._closing or self._closed:
            raise RuntimeError("buffered NATS publisher is closed")

    async def close(self, timeout_seconds: float = 5.0) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._drain_and_close(timeout_seconds))
        # A caller cancellation must not abandon accepted packet accounting.
        await asyncio.shield(self._close_task)

    async def _drain_and_close(self, timeout_seconds: float) -> None:
        joined = asyncio.gather(*(queue.join() for queue in self._queues))
        failed = asyncio.create_task(self._failure.wait())
        try:
            async with self._condition:
                self._condition.notify_all()
            done, _ = await asyncio.wait((joined, failed), timeout=max(0.1, timeout_seconds),
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                self._latch_error(RuntimeError(f"publisher drain timed out with {self.stats['pending']} pending events"))
            self._raise_error()
        finally:
            joined.cancel()
            failed.cancel()
            for worker in self._workers:
                worker.cancel()
            await asyncio.gather(joined, failed, *self._workers, return_exceptions=True)
            self._workers = []
            for queue in self._queues:
                while not queue.empty():
                    packet = queue.get_nowait()
                    self._queued_bytes -= packet.byte_length
                    self._failed += 1
                    queue.task_done()
            self._closed = True
            async with self._condition:
                self._condition.notify_all()


class InMemoryEventBus:
    def __init__(self) -> None:
        self.messages: list[BusMessage] = []

    async def connect(self, *, initialize: bool = False) -> None:
        del initialize

    async def close(self) -> None:
        return None

    async def consumer_ack_floor(self, stream: str, durable: str) -> tuple[str, int] | None:
        del stream, durable
        return None

    async def publish_market_event(self, event: BaseMarketEvent) -> None:
        if event.event_type == EventType.DATA_GAP:
            pass
        elif allowed_supplement(event):
            pass
        elif event.instrument.asset_class == AssetClass.CRYPTO_PERP:
            if event.venue.value not in {"binance", "bitget", "bybit", "arcus", "grvt"} or event.instrument.std_symbol not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"} or event.event_type not in {EventType.TRADE, EventType.ORDER_BOOK, EventType.FUNDING_RATE, EventType.OPEN_INTEREST}:
                return
        elif (event.instrument.venue == Venue.TOSS and event.event_type == EventType.BAR_1M):
            if not (event.provider == "toss" and event.instrument.asset_class == AssetClass.EQUITY
                    and event.instrument.std_symbol in {"TOSS:US:SPY", "TOSS:US:QQQ"}):
                return
        elif event.instrument.venue.value in {"hyperliquid", "binance"}:
            if event.event_type not in {EventType.ORDER_BOOK, EventType.BAR_1M, EventType.FUNDING_RATE, EventType.OPEN_INTEREST} or event.instrument.asset_class != AssetClass.FUTURE:
                return
        elif event.event_type not in {EventType.TRADE, EventType.ORDER_BOOK, EventType.PARTICIPANT_FLOW} or event.instrument.asset_class in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERP}:
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

    async def crypto_stream_usage_ratio(self) -> float:
        return 0.0

    async def subscribe_live(self, subject: str = "live.crypto.>") -> AsyncIterator[BusMessage]:
        index = 0
        while True:
            source = self.messages[index:]
            index = len(self.messages)
            for item in source:
                if item.subject.startswith("crypto."):
                    yield item
            await asyncio.sleep(0.05)
