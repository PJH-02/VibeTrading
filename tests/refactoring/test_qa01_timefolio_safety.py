"""QA regressions for Timefolio UNKNOWN fencing and browser submit boundaries.

All account, source, bus and browser behavior is fake/local. No network or live
account is accessed.
"""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from timefolio.client import AsyncTimefolioBrowserManager, TimefolioOrderIntent, TimefolioOrderResult
from timefolio.runner import TimefolioStrategyRunner, TimefolioStrategyRunnerConfig
from core.models import OrderDestination, OrderIntentStatus, Side, SignalAction
from execution.contracts import AccountExecutionConfig, OrderIntentFactory
from test_timefolio_local_source import (
    FakeBrowser,
    FakeBus,
    FakeSettings,
    FakeStore,
    FakeStrategy,
    FiniteSource,
    install,
    make_config,
    make_runner,
    make_signal,
    trade_payload,
)


class UnknownBrowser(FakeBrowser):
    async def prepare_order(self, account_id, intent):
        self.prepare_calls.append((account_id, intent))
        return TimefolioOrderResult(
            "UNKNOWN", intent.ticker, intent.side, intent.weight_pct,
            "ambiguous fake submit", accepted=False, pending=True,
        )


class SequentialSignals:
    def __init__(self, signals):
        self.signals = list(signals)
        self.index = 0

    def on_event(self, event, view):
        del event, view
        signal = self.signals[self.index]
        self.index += 1
        return [signal]


def armed_settings():
    return FakeSettings(
        live_submit=True,
        env={"TIMEFOLIO_ORDER_ARMED_CONTEST": "1"},
    )


def signal_with_id(signal_id):
    return make_signal().model_copy(update={"signal_id": signal_id})


@pytest.mark.asyncio
async def test_unknown_result_fences_later_signal_in_same_runner(monkeypatch, tmp_path):
    """A second signal must not cause another browser prepare after UNKNOWN."""
    from storage.sqlite_store import RuntimeStore

    settings = armed_settings()
    settings.state_dir = tmp_path
    store = RuntimeStore(settings=settings, path=tmp_path / "timefolio.sqlite3")
    class ClaimObservedBrowser(UnknownBrowser):
        async def prepare_order(self, account_id, intent):
            status = store._conn.execute("SELECT status FROM order_intents").fetchone()[0]
            assert status == OrderIntentStatus.UNKNOWN.value, "claim must commit before browser mutation"
            return await super().prepare_order(account_id, intent)

    browser = ClaimObservedBrowser(live_submit_enabled=True)
    strategy = SequentialSignals([signal_with_id("sig-first"), signal_with_id("sig-next")])
    install(monkeypatch, settings=settings, strategy=strategy, browser=browser,
            browser_factory=lambda **kwargs: browser, store=store)
    source = FiniteSource([trade_payload("event-1"), trade_payload("event-2")])
    runner = make_runner(config=make_config(submit=True), bus=FakeBus(), store=store, source=source)

    results = await runner.run(duration_sec=0.3)

    assert results[0]["status"] == "UNKNOWN"
    assert len(browser.prepare_calls) == 1, "UNKNOWN must fence subsequent account submission"
    rows = store._conn.execute("SELECT status FROM order_intents ORDER BY created_ts_ns").fetchall()
    assert [row[0] for row in rows] == [OrderIntentStatus.UNKNOWN.value]
    store.close()


@pytest.mark.asyncio
async def test_unknown_intent_is_not_submitted_again_after_runner_restart(monkeypatch, tmp_path):
    """A durable SQLite UNKNOWN must not turn into a retry after reconstruction."""
    from storage.sqlite_store import RuntimeStore

    settings = armed_settings()
    settings.state_dir = tmp_path
    store_path = tmp_path / "timefolio.sqlite3"
    store = RuntimeStore(settings=settings, path=store_path)
    first_browser = UnknownBrowser(live_submit_enabled=True)
    first_source = FiniteSource([trade_payload("event-first")])
    install(monkeypatch, settings=settings, strategy=FakeStrategy([signal_with_id("stable-signal")]),
            browser=first_browser, browser_factory=lambda **kwargs: first_browser, store=store)
    first = make_runner(config=make_config(submit=True), bus=FakeBus(), store=store, source=first_source)
    first_results = await first.run(duration_sec=0.3)
    assert first_results[0]["status"] == "UNKNOWN"
    assert len(first_browser.prepare_calls) == 1
    first_rows = store._conn.execute("SELECT intent_id,status FROM order_intents").fetchall()
    assert len(first_rows) == 1 and first_rows[0]["status"] == OrderIntentStatus.UNKNOWN.value
    first_intent_id = first_rows[0]["intent_id"]
    store.close()

    reopened = RuntimeStore(settings=settings, path=store_path)
    second_browser = UnknownBrowser(live_submit_enabled=True)
    second_source = FiniteSource([trade_payload("event-after-restart")])
    install(monkeypatch, settings=settings, strategy=FakeStrategy([signal_with_id("stable-signal")]),
            browser=second_browser, browser_factory=lambda **kwargs: second_browser, store=reopened)
    second = make_runner(config=make_config(submit=True), bus=FakeBus(), store=reopened, source=second_source)
    second_results = await second.run(duration_sec=0.3)

    assert second_results and second_results[0]["status"] == "BLOCKED"
    assert second_results[0]["reason"] == "timefolio_intent_already_recorded"
    assert second_browser.prepare_calls == [], "retained UNKNOWN intent must not reach browser after restart"
    persisted = reopened._conn.execute("SELECT intent_id,status FROM order_intents").fetchall()
    assert [(row["intent_id"], row["status"]) for row in persisted] == [
        (first_intent_id, OrderIntentStatus.UNKNOWN.value)
    ]
    reopened.close()


@pytest.mark.asyncio
async def test_actual_runtime_store_serializes_exact_decimal_and_result_flags(monkeypatch, tmp_path):
    """The real SQLite store preserves exact Decimal text and factual flags."""
    import json
    from storage.sqlite_store import RuntimeStore

    class AcceptedBrowser(FakeBrowser):
        async def prepare_order(self, account_id, intent):
            self.prepare_calls.append((account_id, intent))
            return TimefolioOrderResult("ACCEPTED", intent.ticker, intent.side,
                                        intent.weight_pct, "working", accepted=True, pending=True)

    settings = armed_settings()
    settings.state_dir = tmp_path
    store = RuntimeStore(settings=settings, path=tmp_path / "timefolio.sqlite3")
    browser = AcceptedBrowser(live_submit_enabled=True)
    install(monkeypatch, settings=settings, strategy=FakeStrategy([signal_with_id("sqlite-accepted")]),
            browser=browser, browser_factory=lambda **kwargs: browser, store=store)
    runner = make_runner(config=make_config(submit=True, entry_weight_pct=Decimal("5.2300")), bus=FakeBus(), store=store,
                         source=FiniteSource([trade_payload("sqlite-event")]))

    results = await runner.run(duration_sec=0.3)

    assert results[0]["status"] == "ACCEPTED"
    row = store._conn.execute("SELECT status,payload FROM order_intents").fetchone()
    assert row["status"] == OrderIntentStatus.ACCEPTED.value
    payload = json.loads(row["payload"])
    assert payload["timefolio"] == {
        "status": "ACCEPTED", "ticker": "005930", "side": "buy",
        "weight_pct": "5.2300", "result": "working",
        "filled": False, "accepted": True, "pending": True,
    }
    store.close()


def build_timefolio_intent(store, *, account_id, job_id, signal_id):
    config = AccountExecutionConfig(
        job_id=job_id, destination=OrderDestination.TIMEFOLIO, account_id=account_id,
        max_order_notional=Decimal("100000"), max_position_notional=Decimal("100000"),
        entry_weight_pct=Decimal("5"),
    )
    return OrderIntentFactory(store).from_signal(
        signal_with_id(signal_id), config, side=Side.BUY, weight_pct=Decimal("5"), persist=False,
    )


def test_claim_payload_and_status_column_are_both_unknown(tmp_path):
    import json
    from storage.sqlite_store import RuntimeStore

    settings = armed_settings()
    settings.state_dir = tmp_path
    store = RuntimeStore(settings=settings, path=tmp_path / "timefolio.sqlite3")
    intent = build_timefolio_intent(store, account_id="contest", job_id="job-a", signal_id="claim-state")

    assert store.claim_timefolio_intent(intent, "contest")["claimed"] is True
    row = store._conn.execute("SELECT status,payload FROM order_intents").fetchone()

    assert json.loads(row["payload"])["status"] == row["status"] == OrderIntentStatus.UNKNOWN.value
    store.close()


def test_legacy_created_intent_remains_fenced_without_status_rewrite(tmp_path):
    import json
    from storage.sqlite_store import RuntimeStore

    settings = armed_settings()
    settings.state_dir = tmp_path
    store = RuntimeStore(settings=settings, path=tmp_path / "timefolio.sqlite3")
    legacy = build_timefolio_intent(store, account_id="contest", job_id="old-job", signal_id="legacy")
    store.record_order_intent(legacy)
    before = store._conn.execute("SELECT status,payload FROM order_intents").fetchone()
    assert before["status"] == OrderIntentStatus.CREATED.value
    assert json.loads(before["payload"])["status"] == OrderIntentStatus.CREATED.value

    next_intent = build_timefolio_intent(store, account_id="contest", job_id="new-job", signal_id="new")
    result = store.claim_timefolio_intent(next_intent, "contest")

    after = store._conn.execute("SELECT status,payload FROM order_intents").fetchone()
    assert result["claimed"] is False
    assert result["reason"] == "timefolio_account_has_unresolved_intent"
    assert after["status"] == before["status"] == OrderIntentStatus.CREATED.value
    assert after["payload"] == before["payload"]
    assert store._conn.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 1
    store.close()


@pytest.mark.parametrize("metadata", [{}, {"account_id": ""}, {"account_id": 123}])
def test_malformed_unresolved_account_scope_fences_all_new_claims(tmp_path, metadata):
    import json
    from storage.sqlite_store import RuntimeStore

    settings = armed_settings()
    settings.state_dir = tmp_path
    store = RuntimeStore(settings=settings, path=tmp_path / "timefolio.sqlite3")
    first = build_timefolio_intent(store, account_id="contest", job_id="job-a", signal_id="old")
    assert store.claim_timefolio_intent(first, "contest")["claimed"] is True
    row = store._conn.execute("SELECT payload FROM order_intents").fetchone()
    body = json.loads(row["payload"])
    body["metadata"] = metadata
    store._conn.execute("UPDATE order_intents SET payload=?", (json.dumps(body),))

    second = build_timefolio_intent(store, account_id="contest", job_id="job-b", signal_id="new")
    result = store.claim_timefolio_intent(second, "contest")

    assert result["claimed"] is False
    assert result["reason"] == "timefolio_unreadable_unresolved_intent"
    store.close()


@pytest.mark.parametrize(("ticker", "side", "weight"), [
    ("000660", "buy", Decimal("5")),
    ("005930", "sell", Decimal("5")),
    ("005930", "buy", Decimal("6")),
])
def test_filled_result_identity_mismatch_preserves_unknown_fence(tmp_path, ticker, side, weight):
    from storage.sqlite_store import RuntimeStore

    settings = armed_settings()
    settings.state_dir = tmp_path
    store = RuntimeStore(settings=settings, path=tmp_path / "timefolio.sqlite3")
    intent = build_timefolio_intent(store, account_id="contest", job_id="job-a", signal_id="identity")
    assert store.claim_timefolio_intent(intent, "contest")["claimed"] is True
    result = TimefolioOrderResult("FILLED", ticker, side, weight, "wrong order facts",
                                  filled=True, accepted=True, pending=False)

    assert store.record_timefolio_result(intent.intent_id, result) == OrderIntentStatus.UNKNOWN.value
    assert store._conn.execute("SELECT status FROM order_intents").fetchone()[0] == OrderIntentStatus.UNKNOWN.value
    next_intent = build_timefolio_intent(store, account_id="contest", job_id="job-b", signal_id="next")
    assert store.claim_timefolio_intent(next_intent, "contest")["claimed"] is False
    store.close()


def test_same_account_claim_is_atomic_across_connections_and_other_account_is_independent(tmp_path):
    from storage.sqlite_store import RuntimeStore

    settings = armed_settings()
    settings.state_dir = tmp_path
    path = tmp_path / "timefolio.sqlite3"
    left = RuntimeStore(settings=settings, path=path)
    right = RuntimeStore(settings=settings, path=path)
    same_a = build_timefolio_intent(left, account_id="contest", job_id="job-a", signal_id="race-a")
    same_b = build_timefolio_intent(right, account_id="contest", job_id="job-b", signal_id="race-b")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(left.claim_timefolio_intent, same_a, "contest"),
            pool.submit(right.claim_timefolio_intent, same_b, "contest"),
        ]
        results = [future.result() for future in futures]
    assert sum(bool(result["claimed"]) for result in results) == 1
    other = build_timefolio_intent(right, account_id="other-contest", job_id="job-c", signal_id="other-account")
    assert right.claim_timefolio_intent(other, "other-contest")["claimed"] is True
    left.close()
    right.close()


def test_claim_crashcut_duplicate_terminal_operator_resolution_and_new_intent(tmp_path):
    from storage.sqlite_store import RuntimeStore

    settings = armed_settings()
    settings.state_dir = tmp_path
    path = tmp_path / "timefolio.sqlite3"
    store = RuntimeStore(settings=settings, path=path)
    uncertain = build_timefolio_intent(store, account_id="contest", job_id="old-job", signal_id="old-signal")
    assert store.claim_timefolio_intent(uncertain, "contest")["claimed"] is True
    store.close()  # simulated crash after durable claim and before browser interaction

    reopened = RuntimeStore(settings=settings, path=path)
    duplicate = build_timefolio_intent(reopened, account_id="contest", job_id="old-job", signal_id="old-signal")
    assert reopened.claim_timefolio_intent(duplicate, "contest")["claimed"] is False
    blocked = build_timefolio_intent(reopened, account_id="contest", job_id="new-job", signal_id="new-signal")
    assert reopened.claim_timefolio_intent(blocked, "contest")["reason"] == "timefolio_account_has_unresolved_intent"

    reopened.update_order_intent(uncertain.intent_id, OrderIntentStatus.FILLED.value, {"operator_resolution": "verified"})
    assert reopened.claim_timefolio_intent(duplicate, "contest")["reason"] == "timefolio_intent_already_recorded"
    allowed_new = build_timefolio_intent(reopened, account_id="contest", job_id="new-job", signal_id="after-resolution")
    assert reopened.claim_timefolio_intent(allowed_new, "contest")["claimed"] is True
    reopened.close()


def test_mismatched_or_nonboolean_fill_flags_keep_unknown_fence(tmp_path):
    from storage.sqlite_store import RuntimeStore

    settings = armed_settings()
    settings.state_dir = tmp_path
    store = RuntimeStore(settings=settings, path=tmp_path / "timefolio.sqlite3")
    intent = build_timefolio_intent(store, account_id="contest", job_id="job", signal_id="bad-flags")
    assert store.claim_timefolio_intent(intent, "contest")["claimed"] is True
    malformed = TimefolioOrderResult("FILLED", "005930", "buy", Decimal("5"), "bad flags",
                                     filled="false", accepted="false", pending=False)

    assert store.record_timefolio_result(intent.intent_id, malformed) == OrderIntentStatus.UNKNOWN.value
    assert store._conn.execute("SELECT status FROM order_intents").fetchone()[0] == OrderIntentStatus.UNKNOWN.value
    store.close()


@pytest.mark.asyncio
async def test_browser_exception_cancellation_and_result_write_failure_leave_fence(monkeypatch, tmp_path):
    import asyncio
    from storage.sqlite_store import RuntimeStore

    settings = armed_settings()
    settings.state_dir = tmp_path
    store = RuntimeStore(settings=settings, path=tmp_path / "timefolio.sqlite3")

    class RaisingBrowser(FakeBrowser):
        async def prepare_order(self, account_id, intent):
            self.prepare_calls.append((account_id, intent))
            raise RuntimeError("synthetic browser failure")

    raising = RaisingBrowser(live_submit_enabled=True)
    install(monkeypatch, settings=settings, strategy=FakeStrategy([signal_with_id("raises")]),
            browser=raising, browser_factory=lambda **kwargs: raising, store=store)
    runner = make_runner(config=make_config(submit=True), bus=FakeBus(), store=store,
                         source=FiniteSource([trade_payload("raises-event")]))
    with pytest.raises(RuntimeError, match="synthetic browser failure"):
        await runner.run(duration_sec=0.3)
    assert store._conn.execute("SELECT status FROM order_intents").fetchone()[0] == OrderIntentStatus.UNKNOWN.value
    first_id = store._conn.execute("SELECT intent_id FROM order_intents").fetchone()[0]
    store.update_order_intent(first_id, OrderIntentStatus.REJECTED.value, {"operator_resolution": "test reset"})

    class WaitingBrowser(FakeBrowser):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.started = asyncio.Event()
        async def prepare_order(self, account_id, intent):
            self.prepare_calls.append((account_id, intent))
            self.started.set()
            await asyncio.Future()

    waiting = WaitingBrowser(live_submit_enabled=True)
    install(monkeypatch, settings=settings, strategy=FakeStrategy([signal_with_id("cancelled")]),
            browser=waiting, browser_factory=lambda **kwargs: waiting, store=store)
    cancelled_runner = make_runner(config=make_config(submit=True), bus=FakeBus(), store=store,
                                   source=FiniteSource([trade_payload("cancel-event")]))
    task = asyncio.create_task(cancelled_runner.run(duration_sec=30))
    await asyncio.wait_for(waiting.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = store._conn.execute("SELECT status FROM order_intents ORDER BY created_ts_ns").fetchall()
    assert [row[0] for row in rows] == [OrderIntentStatus.REJECTED.value, OrderIntentStatus.UNKNOWN.value]
    cancelled_id = store._conn.execute("SELECT intent_id FROM order_intents WHERE signal_id=?", ("cancelled",)).fetchone()[0]
    store.update_order_intent(cancelled_id, OrderIntentStatus.REJECTED.value, {"operator_resolution": "test reset"})

    store.record_timefolio_result = lambda *_args: (_ for _ in ()).throw(OSError("synthetic result persistence failure"))
    accepted = FakeBrowser(live_submit_enabled=True)
    install(monkeypatch, settings=settings, strategy=FakeStrategy([signal_with_id("write-fails")]),
            browser=accepted, browser_factory=lambda **kwargs: accepted, store=store)
    write_runner = make_runner(config=make_config(submit=True), bus=FakeBus(), store=store,
                               source=FiniteSource([trade_payload("write-event")]))
    result = await write_runner.run(duration_sec=0.3)
    assert result[0]["status"] == "UNKNOWN"
    assert store._conn.execute("SELECT status FROM order_intents ORDER BY created_ts_ns DESC LIMIT 1").fetchone()[0] == OrderIntentStatus.UNKNOWN.value
    store.close()


@pytest.mark.asyncio
async def test_submit_true_without_durable_claim_api_fails_closed(monkeypatch):
    browser = UnknownBrowser(live_submit_enabled=True)
    store = FakeStore()
    install(monkeypatch, settings=armed_settings(), strategy=FakeStrategy([signal_with_id("no-claim-api")]),
            browser=browser, browser_factory=lambda **kwargs: browser, store=store)
    runner = make_runner(config=make_config(submit=True), bus=FakeBus(), store=store,
                         source=FiniteSource([trade_payload("no-claim-event")]))

    result = await runner.run(duration_sec=0.3)

    assert result[0]["status"] == "BLOCKED"
    assert result[0]["reason"] == "timefolio_durable_claim_unavailable"
    assert browser.prepare_calls == []


@pytest.mark.asyncio
async def test_entry_exit_weight_ticker_side_and_opp_tick_are_preserved(monkeypatch):
    entry = signal_with_id("weight-entry")
    exit_signal = signal_with_id("weight-exit").model_copy(update={"action": SignalAction.EXIT_LONG})
    browser = FakeBrowser()
    strategy = SequentialSignals([entry, exit_signal])
    install(monkeypatch, strategy=strategy, browser=browser, browser_factory=lambda **kwargs: browser)
    config = make_config(submit=False, entry_weight_pct=Decimal("3.250"),
                         exit_weight_pct=Decimal("7.750"), opp_tick=4)
    runner = make_runner(config=config, bus=FakeBus(), source=FiniteSource([
        trade_payload("weight-event-1"), trade_payload("weight-event-2")]))

    await runner.run(duration_sec=0.3)

    assert [(intent.ticker, intent.side, intent.weight_pct, intent.opp_tick) for _, intent in browser.prepare_calls] == [
        ("005930", "buy", Decimal("3.250"), 4),
        ("005930", "sell", Decimal("7.750"), 4),
    ]


@pytest.mark.asyncio
async def test_dashboard_monitor_never_enables_submission_or_calls_order_methods(monkeypatch, tmp_path):
    import asyncio
    import json
    import operations.dashboard.timefolio_monitor as monitor

    monitor_root = tmp_path / "monitor"
    monitor_root.mkdir()
    (monitor_root / "private.pem").write_text("synthetic-test-key", encoding="utf-8")
    monkeypatch.setenv("DASHBOARD_MONITOR_ROOT", str(monitor_root))
    monkeypatch.setenv("TIMEFOLIO_ID", "synthetic-user")
    monkeypatch.setenv("TIMEFOLIO_PW", "synthetic-password")
    monkeypatch.setenv("VIEWER_TOKEN", "unrelated-viewer-token")
    monkeypatch.setattr(monitor.serialization, "load_pem_private_key", lambda *_args, **_kwargs: object())
    writes = []
    monkeypatch.setattr(monitor, "write_json", lambda path, value: writes.append((path, value)))
    calls = []

    class MonitorPage:
        async def wait_for_function(self, *_args, **_kwargs):
            return None

    class MonitorBrowser:
        def __init__(self, *, headless, live_submit_enabled):
            assert headless is True
            assert live_submit_enabled is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
        async def login(self, account_id, credentials):
            calls.append(("login", account_id, credentials.username))
            return SimpleNamespace(logged_in=True)
        async def page_for(self, _account_id):
            return MonitorPage()
        async def scrape_summary(self, _page):
            return {"positions": [], "text": ""}
        async def prepare_order(self, *_args):
            raise AssertionError("dashboard monitor attempted prepare_order")
        async def _submit_order(self, *_args):
            raise AssertionError("dashboard monitor attempted submit")

    monkeypatch.setattr(monitor, "AsyncTimefolioBrowserManager", MonitorBrowser)
    class StopPoll(Exception):
        pass
    async def stop_after_iteration(_seconds):
        raise StopPoll
    monkeypatch.setattr(monitor.asyncio, "sleep", stop_after_iteration)

    with pytest.raises(StopPoll):
        await monitor.run()

    assert calls == [("login", "default", "synthetic-user")]
    assert writes and all(path.name == "timefolio.json" for path, _ in writes)
    assert "unrelated-viewer-token" not in json.dumps([value for _, value in writes])


class ImmediatePage:
    async def wait_for_timeout(self, milliseconds):
        del milliseconds

    async def wait_for_load_state(self, *args, **kwargs):
        del args, kwargs


@pytest.mark.asyncio
async def test_actual_client_submit_false_prepares_but_never_submits(monkeypatch):
    manager = AsyncTimefolioBrowserManager(live_submit_enabled=True)
    page = ImmediatePage()
    monkeypatch.setattr(manager, "page_for", lambda _account: _resolved(page))
    monkeypatch.setattr(manager, "scrape_summary", lambda _page=None: _resolved({"positions": []}))
    for name in ("_open_order_ticket", "_select_ticker", "_choose_side", "_fill_weight", "_choose_default_price_type"):
        monkeypatch.setattr(manager, name, lambda *_args, **_kwargs: _resolved(None))
    submit_calls = []
    monkeypatch.setattr(manager, "_submit_order", lambda *_args: _append_and_resolve(submit_calls))
    intent = TimefolioOrderIntent("contest", "005930", "buy", Decimal("5"), submit=False)

    result = await manager.prepare_order("contest", intent)

    assert result.status == "ACCEPTED" and result.accepted is False
    assert submit_calls == []


@pytest.mark.asyncio
async def test_actual_client_live_submit_disabled_never_calls_submit(monkeypatch):
    manager = AsyncTimefolioBrowserManager(live_submit_enabled=False)
    page = ImmediatePage()
    monkeypatch.setattr(manager, "page_for", lambda _account: _resolved(page))
    submit_calls = []
    monkeypatch.setattr(manager, "_submit_order", lambda *_args: _append_and_resolve(submit_calls))
    intent = TimefolioOrderIntent("contest", "005930", "buy", Decimal("5"), submit=True)

    result = await manager.prepare_order("contest", intent)

    assert result.status == "REJECTED"
    assert "disabled" in result.result
    assert submit_calls == []


@pytest.mark.asyncio
async def test_actual_client_ambiguous_confirmation_returns_unknown_after_single_submit(monkeypatch):
    manager = AsyncTimefolioBrowserManager(live_submit_enabled=True)
    page = ImmediatePage()
    monkeypatch.setattr(manager, "page_for", lambda _account: _resolved(page))
    monkeypatch.setattr(manager, "scrape_summary", lambda _page=None: _resolved({"positions": []}))
    for name in ("_open_order_ticket", "_select_ticker", "_choose_side", "_fill_weight", "_choose_default_price_type", "_ensure_no_dialog"):
        monkeypatch.setattr(manager, name, lambda *_args, **_kwargs: _resolved(None))
    monkeypatch.setattr(manager, "detect_and_close_tms_error", lambda _page: _resolved(None))
    monkeypatch.setattr(manager, "_working_order_exists", lambda *_args: _resolved(False))
    submit_calls = []
    monkeypatch.setattr(manager, "_submit_order", lambda *_args: _append_and_resolve(submit_calls))
    intent = TimefolioOrderIntent("contest", "005930", "buy", Decimal("5"), submit=True)

    result = await manager.prepare_order("contest", intent)

    assert result.status == "UNKNOWN"
    assert len(submit_calls) == 1, "ambiguous confirmation must not click submit again"


class _Dialog:
    def get_by_role(self, *_args, **_kwargs):
        return "submit-button"


class _Dialogs:
    @property
    def last(self):
        return _Dialog()


class _SubmitPage:
    def locator(self, selector):
        assert selector == '[role="dialog"]'
        return _Dialogs()

    def get_by_role(self, *_args, **_kwargs):
        return "confirmation-button"


@pytest.mark.asyncio
async def test_actual_submit_method_invokes_one_submit_click_without_retry(monkeypatch):
    manager = AsyncTimefolioBrowserManager(live_submit_enabled=True)
    click_targets = []

    async def click_once(locator, *, timeout):
        click_targets.append(locator)
        return len(click_targets) == 1

    monkeypatch.setattr(manager, "_click_first_visible", click_once)

    await manager._submit_order(_SubmitPage())

    assert click_targets.count("submit-button") == 1
    assert len(click_targets) == 4  # one submit click, then three confirmation lookups


async def _resolved(value):
    return value


async def _append_and_resolve(target):
    target.append("submit")
    return None
