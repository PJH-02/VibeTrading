import asyncio
from decimal import Decimal
from types import ModuleType, SimpleNamespace

import pytest

from runtime.bus import BusMessage
from market.hot_path import HotPathStore
from core.models import AssetClass, Bar1mEvent, InstrumentId, Side, Signal, SignalAction, TradeEvent, Venue
from vibetrading.strategy_contract import StrategyModule, compile_subscriptions, compile_warmup, load_strategy
from runtime.strategy_runner import SharedStrategyRunner

NOW = 1_792_281_600_000_000_000
MINUTE = 60_000_000_000


def instrument(symbol="069500"):
    return InstrumentId(venue=Venue.KIWOOM, asset_class=AssetClass.ETF,
                        raw_symbol=symbol, std_symbol=symbol)


def trade(index, symbol="069500"):
    return TradeEvent(event_id=f"trade-{symbol}-{index}", instrument=instrument(symbol),
                      venue=Venue.KIWOOM, provider="kiwoom", source_ts_ns=NOW+index,
                      receive_ts_ns=NOW+index, price=Decimal(100+index),
                      quantity=Decimal(1), aggressor_side=Side.BUY)


def bar(index, close=100):
    end = NOW+(index+1)*MINUTE
    return Bar1mEvent(event_id=f"bar-{index}", instrument=instrument(), venue=Venue.KIWOOM,
                      provider="kiwoom", source_ts_ns=end, receive_ts_ns=end+1,
                      open_ts_ns=end-MINUTE, close_ts_ns=end, open=Decimal(99),
                      high=Decimal(101), low=Decimal(98), close=Decimal(close), volume=Decimal(10))


def definition(factory, subscriptions=None, warmup=None):
    raw = ModuleType("synthetic_strategy")
    raw.create_strategy = factory
    return StrategyModule(raw, "synthetic", subscriptions if subscriptions is not None else [instrument().key],
                          warmup or {})


class Emits:
    def __init__(self, fixed_id=None):
        self.calls = []
        self.fixed_id = fixed_id

    def on_event(self, event, view):
        self.calls.append((event.event_id, view.cursor, view.ready, view.phase, id(view.hot_path)))
        return [Signal(signal_id=self.fixed_id or "signal-"+event.event_id,
                       strategy_id="synthetic", instrument=event.instrument,
                       action=SignalAction.ENTER_LONG, event_ts_ns=event.source_ts_ns,
                       reference_price=getattr(event, "price", Decimal(100)),
                       valid_until_ts_ns=NOW+10**15, source_event_id=event.event_id)]


class FakeBus:
    def __init__(self, events=(), quiet=0):
        self.events, self.quiet = list(events), quiet
        self.connected = self.subscribed = self.unsubscribed = self.closed = 0
        self.cancelled_reads = 0
        self.names = []

    async def connect(self):
        self.connected += 1

    async def close(self):
        self.closed += 1

    async def publish_signal(self, signal):
        raise AssertionError("replay/shadow must not publish a production signal")

    async def publish_system(self, kind, payload):
        self.names.append(payload["name"])

    async def subscribe_live(self, subject):
        self.subscribed += 1
        try:
            for event in self.events:
                try:
                    await asyncio.sleep(self.quiet)
                except asyncio.CancelledError:
                    self.cancelled_reads += 1
                    raise
                yield BusMessage(event.model_dump(mode="json"), subject, event.event_id)
        finally:
            self.unsubscribed += 1


def engine(jobs, **kwargs):
    return SharedStrategyRunner(jobs, bus=kwargs.pop("bus", FakeBus()),
                                settings=SimpleNamespace(), now_ns=kwargs.pop("now_ns", lambda: NOW+10**12),
                                **kwargs)


@pytest.mark.parametrize("count", [1, 5, 10])
def test_one_apply_and_same_snapshot_for_one_five_ten_strategies(count):
    class CountingStore(HotPathStore):
        def __init__(self):
            super().__init__()
            self.apply_calls = 0
        def apply_versioned(self, event):
            self.apply_calls += 1
            return super().apply_versioned(event)
    store = CountingStore()
    runner = engine([(str(i), definition(Emits)) for i in range(count)], hot_path=store, max_jobs=count)
    output = runner.process_event(trade(0))
    assert len(output) == count and store.apply_calls == 1
    snapshots = {job.strategy.calls[0][-1] for job in runner._jobs}
    assert len(snapshots) == 1
    assert {decision.cursor for decision in output} == {(store.engine_incarnation, 1)}
    runner.process_event(trade(1))
    assert store.apply_calls == 2


def test_routing_and_signal_dedupe_are_per_job():
    runner = engine([("a", definition(lambda: Emits("fixed"))),
                     ("b", definition(lambda: Emits("fixed"))),
                     ("other", definition(Emits, [instrument("229200").key]))])
    assert {result.job_id for result in runner.process_event(trade(0))} == {"a", "b"}
    assert runner.process_event(trade(1)) == ()
    assert not runner._jobs[2].strategy.calls
    assert len(runner._jobs[0].produced) == len(runner._jobs[1].produced) == 1
    assert len(runner.process_event(trade(2, "229200"))) == 1


def test_shared_preload_once_live_thirtieth_bar_and_replay_gate():
    events = [bar(i) for i in range(29)]
    class Reader:
        def __init__(self):
            self.calls = 0
            self.locked = False
        def history(self, **kwargs):
            assert not self.locked, "reader used inside live callback"
            self.calls += 1
            return events
    reader = Reader()
    module = definition(Emits, [{"venue": "kiwoom", "symbol": "069500", "event_type": "bar_1m"}], {"bars": 30})
    runner = engine([("a", module), ("b", module)], history_reader=reader,
                    known_instruments=[instrument()], now_ns=lambda: NOW+31*MINUTE)
    runner.prepare()
    assert reader.calls == 1
    reader.locked = True
    output = runner.process_event(bar(29))
    assert len(output) == 2 and all(job.state == "SHADOW" for job in runner._jobs)
    replay = engine([("r", module)], mode="replay", history_reader=Reader(),
                    known_instruments=[instrument()], now_ns=lambda: NOW+31*MINUTE)
    assert replay.process_event(bar(29)) == ()
    assert replay._jobs[0].produced == type(replay._jobs[0].produced)(maxlen=1000)
    assert not replay._jobs[0].seen._items


def test_warmup_and_conflicting_revision_cannot_emit():
    module = definition(Emits, [instrument().key], {"bars": 2})
    runner = engine([("a", module)])
    assert runner.process_event(bar(0)) == ()
    assert len(runner.process_event(bar(1))) == 1
    assert runner.process_event(bar(1, close=200).model_copy(update={"event_id": "conflict"})) == ()
    assert runner._jobs[0].state == "INVALID"


def test_event_id_content_conflict_cannot_emit_or_recover_implicitly():
    runner = engine([("a", definition(Emits))])
    event = trade(0)
    assert len(runner.process_event(event)) == 1
    assert runner.process_event(event.model_copy(update={"price": Decimal(999)})) == ()
    assert runner.process_event(trade(1)) == ()
    assert runner.hot_path.snapshot(NOW).quality[event.instrument.key] == "EVENT_ID_CONFLICT"


def test_strategy_fault_isolated_without_reapplying_input():
    class Broken:
        def on_event(self, event, view):
            raise RuntimeError("fixture fault")
    runner = engine([("bad", definition(Broken)), ("good", definition(Emits))])
    output = runner.process_event(trade(0))
    assert [result.job_id for result in output] == ["good"]
    assert runner.hot_path.apply_seq == 1 and runner._jobs[0].state == "INVALID"


@pytest.mark.asyncio
async def test_quiet_intervals_keep_one_subscription_and_heartbeat_uses_module_id():
    bus = FakeBus([trade(0)], quiet=.04)
    runner = engine([("job", definition(Emits))], bus=bus, tick_seconds=.005)
    output = await runner.run()
    assert output == {"job": ["signal-trade-069500-0"]}
    assert (bus.connected, bus.subscribed, bus.unsubscribed, bus.closed) == (1, 1, 1, 1)
    assert bus.cancelled_reads == 0
    assert bus.names and set(bus.names) == {"synthetic"}


@pytest.mark.asyncio
async def test_shutdown_cancels_only_pending_read_and_closes_once():
    bus = FakeBus([trade(0)], quiet=10)
    runner = engine([("job", definition(Emits))], bus=bus, tick_seconds=.005)
    assert await runner.run(duration_sec=.025) == {"job": []}
    assert (bus.subscribed, bus.unsubscribed, bus.closed, bus.cancelled_reads) == (1, 1, 1, 1)


def test_registration_rejects_unverified_expressions_and_live_mode():
    for value in ({"seconds": 10}, {"bars": True}, {"bars": -1}):
        with pytest.raises(ValueError):
            compile_warmup(value)
    for value in ([{"unknown": "x"}], [{}], [""], [{"venue": "no-venue"}]):
        with pytest.raises(ValueError):
            compile_subscriptions(value)
    with pytest.raises(ValueError, match="replay/shadow"):
        engine([("job", definition(Emits))], mode="live")


def test_example_bar_api_compatibility_preserves_breakout_economics(monkeypatch):
    monkeypatch.setattr("strategies.example_signal.utc_now_ns", lambda: NOW)
    module = load_strategy("example_signal")
    runner = engine([("example", module)], now_ns=lambda: NOW+32*MINUTE)
    btc = InstrumentId(venue=Venue.BINANCE, asset_class=AssetClass.CRYPTO_SPOT,
                       raw_symbol="BTCUSDT", std_symbol="BTCUSDT")
    def example_bar(index, close=100):
        return bar(index, close).model_copy(update={"instrument": btc, "venue": Venue.BINANCE,
                                                    "provider": "synthetic-binance"})
    for i in range(29):
        assert runner.process_event(example_bar(i)) == ()
    output = runner.process_event(example_bar(29, close=102))
    assert len(output) == 1
    signal = output[0].signal
    assert (signal.action, signal.reference_price, signal.event_ts_ns) == (
        SignalAction.ENTER_LONG, Decimal(102), bar(29).close_ts_ns)
    assert signal.valid_until_ts_ns == NOW+MINUTE
    assert signal.metadata == {"trigger": "close_above_prev_high"}
    signal.metadata["mutated"] = True
    assert "mutated" not in output[0].signal.metadata
