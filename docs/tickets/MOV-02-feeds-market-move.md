# MOV-02 — Feeds/Market mechanical move and native wire verification

Base source HEAD ec08b222642b2f64cc43a528327b513c112bcf0e, branch codex/move-core-feeds.
MOV-00/01 accepted. Initial worker freellmapi/glm-4.5-flash failed stream before edits, exit1.
Current exact worker model opencode/deepseek-v4.1-flash-free, role test (repair1).
GLM smoke HTTP200 but coding stream interrupted; Gemini HTTP502. Transition
explicitly approved by existing priority policy after checking changed paths0.
No worker-side model switches or recursive agents.
Codex applies the reviewed MOV-01 worker-built deterministic move tool and owns
all Git moves, review, authority/status and independent acceptance. Worker sees
an isolated credential-free snapshot AFTER mechanical application.

Product scope: exactly 27 map-selected MOVE_ONLY_CANDIDATE files with owner feeds
or market, including providers package. Source paths src/vibetrading/{collection_timing,
etf_csv_sync,feed_recovery,kis_index,local_pipeline,universe_config,hot_path,local_engine,
local_snapshot,market_apply_order,market_fanout,market_policy,market_source_status}.py
and providers/{__init__,base,binance,coingecko,crypto_perp,extended_perp,hyperliquid,
kis,kis_client,kis_master,kiwoom,ls_market,toss,universe}.py. Targets frozen by
scripts/refactoring/final_path_map.json. Exact AST import and dynamic literal
callers only. Empty feeds/market package init, packaging include addition only.
No business/schema/economic/time/sequence/queue changes; no new old-path shims.
Core models/dedup identity aliases remain until MOV-07. Protected deploy/rollback
helpers and historical overlays remain unchanged; active Dockerfile.crypto COPY
coverage is explicitly allowed below. No authority/historical docs or unrelated rewrites. Do not activate KR-DATA.

## Worker bounded implementation
Only editable files:
 tests/refactoring/test_mov02_native_golden.py (new)
 Dockerfile.crypto (3 COPY lines only).
Docker packaging correction: directly after existing src/vibetrading COPY, add
COPY --chown=10001:10001 src/core /app/src/core
COPY --chown=10001:10001 src/feeds /app/src/feeds
COPY --chown=10001:10001 src/market /app/src/market
No other Docker line changes. This is local source packaging only; do not build,
pull or run Docker, install packages, change images or touch deploy/ overlays.
Read directly this ticket and these source files only as needed:
 src/feeds/providers/crypto_perp.py, src/feeds/providers/extended_perp.py,
 src/vibetrading/bus.py, src/core/models.py,
 tests/test_binance_snapshot_bridge.py, tests/test_aligned_derivatives.py.
No broad searches, configs, metadata, secrets, network calls, package installs,
other files, authority writes, git operations or product edits. No self-approval.

Write <=150 lines and 3-5 meaningful offline tests of exact native frame/wire
preservation. Use a minimal capture bus async publish_market_event(event),
fixed connection_id and unittest.mock.patch of time.time_ns/monotonic_ns.
Tests should cover Bitget snapshot/delta then invalid previous-ID gap;
Arcus microsecond timestamp -> ns, raw_payload, sequence and Decimal strings;
GRVT nanosecond timestamp and ACK baseline 0-snapshot -> first advertised live
sequence, raw frame and Decimal precision. Use canonical core/feeds imports only.
Assert literal expected source/receive timestamps, native identifiers, final /
previous update IDs, generations, order, raw frame and Decimal values from fixed
fixtures. Do not assert just that fields exist. Do not change product to make
assertions pass. Do not use network or run collector.run(). Call parser methods
with scripted frames only. Reuse actual private method APIs, not guessed fields.
Codex independently runs tests against frozen pre-move source and moved source.

Exact worker test command (once after implementing):
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_mov02_native_golden.py
If fails, report exact failure; do not broaden scope. Stop after test output.
Return changed file, actual test exit/count, open questions. Do not wait or loop.

## Codex acceptance
Review exact planned bytes and changed paths, normalized business AST equality,
old active import0 with explicit protected/historical exclusions. Run new native
fixtures before/after, full existing native/book gap/replay/durable/local engine/
TOSS DATA_ONLY/ETF closed-bar tests, previous Core 81 regressions, import smoke,
CLI --help offline, Docker/Compose target static resolution, offline wheel and
outside repository canonical imports. No live requests/order/cancel/testnet.
Keep producer/source/bar-end config, default TOSS active producers0 unchanged.
Record provider x segment x channel actual supported matrix and duplication
candidates for CLN-02; no broad venue support claims or parser refactors.

Known DATA-BARRIER-FINDING (preserve code): LocalMarketEngine.event_source default
required_producers=None retains all-feed gate; _initial_proofs_ready uses all
feeds. Explicit dependencies/empty tuple are available. Record caller/source and
existing tests for QA-01/PERF-01; do not change behavior in this move-only wave.

Independent commands:
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/test_crypto_perp_pipeline.py tests/test_storage_pipeline.py tests/test_durable_pipeline_guards.py tests/test_toss_daymarket.py tests/refactoring/test_local_pipeline.py tests/refactoring/test_local_engine.py tests/refactoring/test_tx00_toss_data_only.py tests/refactoring/test_apply_order_lifecycle.py tests/test_binance_snapshot_bridge.py tests/test_aligned_derivatives.py tests/test_local_snapshot.py tests/test_participant_flow.py tests/test_universe.py tests/test_ls_market.py tests/test_etf_outage_recovery.py tests/refactoring/test_mov02_native_golden.py
python3 -m vibetrading.cli --help
python3 -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir /work/dist
Codex sole decision PASS/FAIL/BLOCKED, private artifacts with actual exits and SHA.
