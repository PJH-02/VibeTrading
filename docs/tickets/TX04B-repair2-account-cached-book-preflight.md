# TX04B repair2/final — fence actual cached books before any account-wide time advancement

Second and LAST allowed repair delegation for TX04B, same refactor role/opencode/deepseek-v4.1-flash-free. No third repair or model substitution. Source d3ad262 passes142 earlier cases, but independent cross-product review fails2. Codex owns final acceptance. TX04 pure test worker completed against the earlier source; its final independent acceptance waits for this repair. VN13 not started.

Only writable src/vibetrading/strategy_runtime.py (_market ONLY) and tests/refactoring/test_tx04b_continuous.py (append focused regressions). All other methods, new _fence_feed, existing engine/wiring, previous tests/Golden/metadata readonly. No new engine/helper/state/schema, direct private matcher._state edits, or direct SQL mutations; no orders/network/accounts/credentials/install/deploy/push/merge/data deletion. Read only relevant 1–5 files.

Root cause: matcher.on_book/on_trade/advance advance ALL orders in that physical account, not only current instrument. Moreover on_book advances past arrivals BEFORE replacing its old cached book. Post-apply snapshot.latest_book(current_feed) already contains a fresh arriving book and cannot attest the OLD depth being advanced over.

Before ANY operation in _market can move a matcher's clock, preflight actual previously committed matcher books (read existing matcher.ledger.paper_state(), books contain received_ns/source_ns/generation) for ALL its explicitly bound products/reference feeds. No new persistent state/cache. Receipt and optional native timestamp ages against snapshot.now_ns use current runtime max_feed_age_ns. Any cached book that is stale/missing/inconsistent with current valid dependency must be invalidated from last durable clock through EXISTING _fence_feed before moving time. This must cover other instruments as well as the same instrument when a new full book arrives after stale old depth. Use immutable/read-only saved book facts; do not rely solely on the post-apply new book. No normalizations or native-generation inference.

Then process current fresh projected book/trade with original policy. Preserve unaffected fresh feed independence. If an incoming current full book carries an explicit NEW generation after the stale fence, let original on_book verify it and clear that feed's invalid flag ONLY after on_book succeeds so it can reopen. Same-generation stale resync stays conservatively closed under existing native generation policy; never invent a generation, silently repair state or fill stale depth. Error paths still use existing account fault fence. Keep replay/backfill/duplicate/recovery semantics and economics/IDs exact.

Regressions (seed100000, freshness5ns, native stale1e9, BTC IOC BUY1@101 arrivalT+10 from BTC bookT):
- fresh ETH full bookT+11 must not execute BTC; ETH stays valid/independent, BTC closes.
- ETH full bookT+7 then fresh ETH tradeT+11 must not execute BTC; ETH stays valid.
- new BTC full bookT+11 must never execute BTC from its old bookT before replacement; with explicit generation2 it can safely resync/reopen (no account fault), zero fills/cash100000. Without new generation remain conservatively fenced, no fabricated fills.
Existing 142 earlier scoped cases, stale tick/native source and current trade checks remain exact. Prefer adding three focused tests; do not edit Codex reviewer tests or replace expected facts with candidate calculations.

Exact command:
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_local_engine.py tests/refactoring/test_local_runtime.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_paper_execution.py tests/refactoring/test_tx02_joint_gate.py tests/refactoring/test_tx04a_alignment.py tests/refactoring/test_tx04b_continuous.py

Report real exit/count/scoped diff and stop; no self-approval/recursive delegation or new ticket work.
