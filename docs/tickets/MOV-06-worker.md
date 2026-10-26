# MOV-06 worker bounded functional packet
Origin HEAD 9478c02a39ff85bc527fa7fe32f2f2508a59dcc0; actual snapshot head is pinned in origin.json.
Selected exact model freellmapi/glm-4.5-flash. Role implement. Do not change model/provider.
ONLY edit src/timefolio/runner.py and tests/refactoring/test_timefolio_local_source.py.
Read those two paths, src/timefolio/client.py, tests/test_timefolio.py, and worker-context.txt.
No recursive agents, installs, network, git moves/commits, source exploration, status or acceptance edits.
Codex will move dependencies mechanically separately. Use canonical imports runtime.bus,
runtime.settings, operations.dashboard.telemetry in runner. Other existing imports unchanged.

Implement the SPECIFIED interface, not a new architecture:
TimefolioStrategyRunner.__init__(config,bus=None,store=None,*,event_source=None):
- self.bus=bus (observation sink only, never NatsJetStreamBus default).
- self._event_source=event_source, async iterator of validated engine payload dicts.
- Preserve all other initialization and whole _handle_signal/_record_result functions.
run: preserve existing live-submit flag and account arm gates before any browser.
If source or observation sink absent, return [{"status":"BLOCKED","reason":"local market event source and observation bus are required"}], before credentials/browser/connect.
Credential checks remain. Consume event_source with event_from_dict + hot_path.apply + strategy.on_event unchanged.
Local engine already owns durability/delivery; no bus.subscribe, message.ack/nak, NATS fallback.
Connect/close injected observation sink and existing heartbeat only. No new queue.
Keep an outstanding anext task across idle timer timeouts; wait_for(generator) would cancel/close it.
Honor duration_sec, including zero immediate no work; cancel+await pending anext/heartbeat in finally;
aclose source in cleanup if supported; always close connected sink on login/context/iteration failure.
Processing/source error must propagate and stop; never retry an already uncertain browser order.
Reuse AsyncTimefolioBrowserManager unchanged. Submit=false default, weights, duplicate, risk gates,
UNKNOWN and order result persistence MUST remain unchanged. Do not add live integrations.

Add focused pytest tests using monkeypatch fake settings/store/strategy/hot_path/browser/observation
and credential-free async sources: no source blocks without browser, submit gates preserved, actual
local payload delivered once and publish path preserved, idle > one polling interval does not kill
source, finite/empty source, duration zero no event order, source error + browser/login error closes
sink/source, cancellation cleans pending next task. Patch slow timer with bounded short durations
where possible. Existing tests use pytest_asyncio. Never real browser/API/socket.
EXACT only Bash test command (no setup probes):
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/test_timefolio.py tests/refactoring/test_timefolio_local_source.py
Return actual changes/tests/exit. Codex alone accepts after independent verification.
