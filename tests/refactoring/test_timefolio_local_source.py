"""Timefolio consumes an explicit local engine iterator and observation sink."""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

import timefolio.runner as runner_module
from core.models import AssetClass, InstrumentId, Signal, SignalAction, TradeEvent, Venue
from timefolio.client import TimefolioOrderResult
from timefolio.runner import TimefolioStrategyRunner, TimefolioStrategyRunnerConfig


INSTRUMENT = InstrumentId(
    venue=Venue.KIWOOM,
    asset_class=AssetClass.ETF,
    raw_symbol="005930",
    std_symbol="005930",
)
BLOCKED_REASON = "local market event source and observation bus are required"
_UNSET = object()


def make_config(**updates) -> TimefolioStrategyRunnerConfig:
    values = dict(
        job_id="job-1",
        account_id="contest",
        strategy_module="fake_strategy",
        entry_weight_pct=Decimal("5"),
        max_order_notional=Decimal("100000"),
        max_position_notional=Decimal("100000"),
        exit_weight_pct=None,
        submit=False,
    )
    values.update(updates)
    return TimefolioStrategyRunnerConfig(**values)


def trade_payload(event_id: str = "event-1") -> dict:
    return TradeEvent(
        event_id=event_id,
        instrument=INSTRUMENT,
        venue=Venue.KIWOOM,
        provider="kiwoom",
        source_ts_ns=1,
        receive_ts_ns=1,
        price=Decimal("70000"),
        quantity=Decimal("1"),
    ).model_dump(mode="json")


def make_signal() -> Signal:
    return Signal(
        strategy_id="fake_strategy",
        instrument=INSTRUMENT,
        action=SignalAction.ENTER_LONG,
        event_ts_ns=1,
        reference_price=Decimal("70000"),
        valid_until_ts_ns=time.time_ns() + 60_000_000_000,
    )


class FakeSettings:
    def __init__(self, *, live_submit: bool = False, env: dict | None = None, credentials: bool = True) -> None:
        self.timefolio_live_submit_enabled = live_submit
        self.env = dict(env or {})
        self.parquet = SimpleNamespace(data_root="unused")
        self._credentials = credentials

    def timefolio_account(self, account_id: str) -> SimpleNamespace:
        del account_id
        return SimpleNamespace(
            username="fake-user" if self._credentials else None,
            password="fake-password" if self._credentials else None,
            has_credentials=self._credentials,
        )


class FakeStore:
    def __init__(self) -> None:
        self.intents: dict[str, object] = {}
        self.updates: list[tuple[str, str, dict]] = []

    def record_order_intent(self, intent: object) -> None:
        self.intents[intent.intent_id] = intent

    def update_order_intent(self, intent_id: str, status: str, payload: dict) -> None:
        self.updates.append((intent_id, status, payload))

    def is_killed(self, key: str) -> bool:
        del key
        return False


class FakeHotPath:
    def __init__(self) -> None:
        self.applied: list[object] = []

    def apply(self, event: object) -> bool:
        self.applied.append(event)
        return True


class FakeStrategy:
    def __init__(self, signals: list[Signal] | None = None) -> None:
        self.events: list[object] = []
        self.signals = list(signals or [])

    def on_event(self, event: object, view: object) -> list[Signal]:
        del view
        self.events.append(event)
        return list(self.signals)


class FakeModule:
    def __init__(self, strategy: FakeStrategy) -> None:
        self.strategy = strategy

    def create(self) -> FakeStrategy:
        return self.strategy


class FakeBus:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.intents: list[object] = []
        self.system: list[tuple[str, dict | None]] = []
        self.subscribe_calls = 0

    async def connect(self, *, initialize: bool = False) -> None:
        del initialize
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def publish_order_intent(self, intent: object) -> None:
        self.intents.append(intent)

    async def publish_system(self, kind: str, payload: dict | None = None) -> None:
        self.system.append((kind, payload))

    async def subscribe(self, *args, **kwargs):
        del args, kwargs
        self.subscribe_calls += 1
        raise AssertionError("local source runner must not subscribe to NATS")


class FakeBrowser:
    def __init__(
        self,
        *,
        headless: bool = True,
        live_submit_enabled: bool = False,
        login_ok: bool = True,
        enter_error: Exception | None = None,
    ) -> None:
        self.headless = headless
        self.live_submit_enabled = live_submit_enabled
        self.login_ok = login_ok
        self.enter_error = enter_error
        self.entered = False
        self.exited = False
        self.login_calls: list[tuple[str, object]] = []
        self.prepare_calls: list[tuple[str, object]] = []

    async def __aenter__(self) -> "FakeBrowser":
        if self.enter_error is not None:
            raise self.enter_error
        self.entered = True
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.exited = True

    async def login(self, account_id: str, credentials: object) -> SimpleNamespace:
        self.login_calls.append((account_id, credentials))
        return SimpleNamespace(logged_in=self.login_ok)

    async def prepare_order(self, account_id: str, intent: object) -> TimefolioOrderResult:
        self.prepare_calls.append((account_id, intent))
        return TimefolioOrderResult(
            "ACCEPTED",
            intent.ticker,
            intent.side,
            intent.weight_pct,
            "fake prepared order",
            accepted=False,
        )


class FiniteSource:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.index = 0
        self.yields = 0
        self.next_calls = 0
        self.closed = False

    def __aiter__(self) -> "FiniteSource":
        return self

    async def __anext__(self) -> dict:
        self.next_calls += 1
        if self.index >= len(self.payloads):
            raise StopAsyncIteration
        self.index += 1
        self.yields += 1
        return self.payloads[self.index - 1]

    async def aclose(self) -> None:
        self.closed = True


class ErrorSource:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.closed = False

    def __aiter__(self) -> "ErrorSource":
        return self

    async def __anext__(self) -> dict:
        raise self.error

    async def aclose(self) -> None:
        self.closed = True


class IdleSource:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.closed = False

    def __aiter__(self) -> "IdleSource":
        return self

    async def __anext__(self) -> dict:
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def aclose(self) -> None:
        self.closed = True


def install(monkeypatch, *, settings=None, strategy=None, browser=None, browser_factory=None, store=None, hot_path=None):
    settings = settings or FakeSettings()
    strategy = strategy or FakeStrategy()
    store = store or FakeStore()
    hot_path = hot_path or FakeHotPath()
    if browser is None and browser_factory is None:
        browser = FakeBrowser()
        browser_factory = lambda **kwargs: browser
    monkeypatch.setattr(runner_module, "get_settings", lambda: settings)
    monkeypatch.setattr(runner_module, "RuntimeStore", lambda _settings: store)
    monkeypatch.setattr(runner_module, "load_strategy", lambda _name: FakeModule(strategy))
    monkeypatch.setattr(runner_module, "HotPathStore", lambda: hot_path)
    monkeypatch.setattr(runner_module, "ParquetHistoryReader", lambda _root: SimpleNamespace())
    monkeypatch.setattr(runner_module, "AsyncTimefolioBrowserManager", browser_factory)
    monkeypatch.setattr(runner_module, "_POLL_INTERVAL_SECONDS", 0.01)
    return SimpleNamespace(
        settings=settings,
        strategy=strategy,
        store=store,
        hot_path=hot_path,
        browser=browser,
    )


def make_runner(config=None, *, bus=_UNSET, store=None, source=None) -> TimefolioStrategyRunner:
    if bus is _UNSET:
        bus = FakeBus()
    store = store if store is not None else FakeStore()
    return TimefolioStrategyRunner(
        config or make_config(),
        bus=bus,
        store=store,
        event_source=source,
    )


class BombBus:
    async def connect(self, *, initialize: bool = False) -> None:
        del initialize
        raise AssertionError("observation sink must not connect")


class BombBrowser:
    def __init__(self, **kwargs) -> None:
        del kwargs
        raise AssertionError("browser must not start")


@pytest.mark.asyncio
async def test_missing_local_source_or_observation_sink_blocks_before_credentials_and_browser(monkeypatch):
    install(
        monkeypatch,
        settings=FakeSettings(credentials=False),
        browser_factory=BombBrowser,
    )

    missing_source = make_runner(bus=BombBus(), source=None)
    assert await missing_source.run() == [{"status": "BLOCKED", "reason": BLOCKED_REASON}]

    missing_sink = make_runner(bus=None, source=FiniteSource([trade_payload()]))
    assert await missing_sink.run() == [{"status": "BLOCKED", "reason": BLOCKED_REASON}]


@pytest.mark.asyncio
async def test_existing_submit_and_account_arm_gates_precede_wiring_gate(monkeypatch):
    settings = FakeSettings(live_submit=False, env={}, credentials=False)
    install(monkeypatch, settings=settings, browser_factory=BombBrowser)

    flag_blocked = make_runner(config=make_config(submit=True), bus=BombBus(), source=None)
    assert await flag_blocked.run() == [
        {"status": "BLOCKED", "reason": "TIMEFOLIO_LIVE_SUBMIT_ENABLED is false"}
    ]

    settings.timefolio_live_submit_enabled = True
    arm_blocked = make_runner(config=make_config(submit=True), bus=BombBus(), source=None)
    assert await arm_blocked.run() == [
        {"status": "BLOCKED", "reason": "account arm token is not enabled"}
    ]

    settings.env["TIMEFOLIO_ORDER_ARMED_CONTEST"] = "1"
    wiring_blocked = make_runner(config=make_config(submit=True), bus=BombBus(), source=None)
    assert await wiring_blocked.run() == [{"status": "BLOCKED", "reason": BLOCKED_REASON}]


@pytest.mark.asyncio
async def test_actual_local_payload_is_delivered_once_and_publish_path_is_preserved(monkeypatch):
    payload = trade_payload()
    source = FiniteSource([payload])
    strategy = FakeStrategy([make_signal()])
    bus = FakeBus()
    store = FakeStore()
    harness = install(monkeypatch, strategy=strategy, store=store)

    runner = make_runner(bus=bus, store=store, source=source)
    results = await runner.run(duration_sec=0.3)

    assert results and results[0]["status"] == "ACCEPTED"
    assert strategy.events[0].event_id == payload["event_id"]
    assert len(strategy.events) == 1
    assert source.yields == 1
    assert bus.subscribe_calls == 0
    assert len(bus.intents) == 1
    assert len(harness.browser.prepare_calls) == 1
    assert harness.browser.prepare_calls[0][1].ticker == "005930"
    assert store.updates[-1][1] == "accepted"
    assert source.closed is True
    assert bus.connected is True and bus.closed is True
    assert harness.browser.entered is True and harness.browser.exited is True


@pytest.mark.asyncio
async def test_idle_longer_than_polling_interval_keeps_the_same_next_task(monkeypatch):
    payload = trade_payload("slow-event")
    observed: list[str] = []
    source = _slow_source(payload, 0.06, observed)
    strategy = FakeStrategy()
    harness = install(monkeypatch, strategy=strategy)

    runner = make_runner(bus=FakeBus(), source=source)
    results = await runner.run(duration_sec=0.3)

    assert results == []
    assert observed[:1] == ["yield"]
    assert "closed" in observed
    assert len(strategy.events) == 1
    assert strategy.events[0].event_id == "slow-event"
    assert harness.browser.entered is True and harness.browser.exited is True


async def _slow_source(payload: dict, delay: float, observed: list[str]):
    try:
        await asyncio.sleep(delay)
        observed.append("yield")
        yield payload
    finally:
        observed.append("closed")


@pytest.mark.asyncio
async def test_empty_finite_source_finishes_without_orders(monkeypatch):
    source = FiniteSource([])
    harness = install(monkeypatch)

    runner = make_runner(bus=FakeBus(), source=source)
    results = await runner.run(duration_sec=0.3)

    assert results == []
    assert source.closed is True
    assert harness.browser.login_calls
    assert harness.browser.exited is True
    assert runner.bus.closed is True


@pytest.mark.asyncio
async def test_duration_zero_does_no_work_but_still_applies_submit_gate(monkeypatch):
    source = FiniteSource([trade_payload()])
    harness = install(monkeypatch, browser_factory=BombBrowser)
    bus = FakeBus()

    runner = make_runner(bus=bus, source=source)
    assert await runner.run(duration_sec=0) == []
    assert source.next_calls == 0
    assert bus.connected is False and bus.closed is False
    assert harness.strategy.events == []

    settings = FakeSettings(live_submit=False)
    install(monkeypatch, settings=settings, browser_factory=BombBrowser)
    blocked = make_runner(config=make_config(submit=True), bus=bus, source=source)
    assert await blocked.run(duration_sec=0) == [
        {"status": "BLOCKED", "reason": "TIMEFOLIO_LIVE_SUBMIT_ENABLED is false"}
    ]
    assert source.next_calls == 0
    assert bus.connected is False


@pytest.mark.asyncio
async def test_source_error_propagates_and_closes_sink_source_and_browser(monkeypatch):
    source = ErrorSource(RuntimeError("source failed"))
    bus = FakeBus()
    harness = install(monkeypatch)

    runner = make_runner(bus=bus, source=source)
    with pytest.raises(RuntimeError, match="source failed"):
        await runner.run()

    assert source.closed is True
    assert bus.connected is True and bus.closed is True
    assert harness.browser.entered is True and harness.browser.exited is True
    assert harness.strategy.events == []


@pytest.mark.asyncio
async def test_login_failure_closes_sink_source_and_browser(monkeypatch):
    source = FiniteSource([trade_payload()])
    browser = FakeBrowser(login_ok=False)
    bus = FakeBus()
    harness = install(monkeypatch, browser=browser, browser_factory=lambda **kwargs: browser)

    runner = make_runner(bus=bus, source=source)
    result = await runner.run()

    assert result[0]["status"] == "LOGIN_FAILED"
    assert source.next_calls == 0
    assert source.closed is True
    assert bus.connected is True and bus.closed is True
    assert harness.browser.exited is True
    assert harness.strategy.events == []


@pytest.mark.asyncio
async def test_browser_context_error_closes_sink_and_source(monkeypatch):
    source = FiniteSource([trade_payload()])
    browser = FakeBrowser(enter_error=RuntimeError("browser failed"))
    bus = FakeBus()
    install(monkeypatch, browser=browser, browser_factory=lambda **kwargs: browser)

    runner = make_runner(bus=bus, source=source)
    with pytest.raises(RuntimeError, match="browser failed"):
        await runner.run()

    assert source.closed is True
    assert bus.connected is True and bus.closed is True


@pytest.mark.asyncio
async def test_cancellation_cancels_the_pending_next_and_acloses_source(monkeypatch):
    source = IdleSource()
    bus = FakeBus()
    harness = install(monkeypatch)

    runner = make_runner(bus=bus, source=source)
    task = asyncio.create_task(runner.run())
    await asyncio.wait_for(source.started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert source.cancelled.is_set()
    assert source.closed is True
    assert bus.closed is True
    assert harness.browser.exited is True


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
async def test_nonfinite_duration_is_rejected_before_browser_or_observation_connect(monkeypatch, duration):
    install(monkeypatch, browser_factory=BombBrowser)
    runner = make_runner(bus=BombBus(), source=FiniteSource([]))
    with pytest.raises(ValueError, match="duration_sec must be finite"):
        await runner.run(duration_sec=duration)
