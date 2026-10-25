Implemented the TX04B repair within the allowed files.

**Changes**
- `src/vibetrading/strategy_runtime.py` — only `_market`:
  - Added per-account preflight before any matcher clock advancement.
  - Reads actual durable matcher books from `matcher.ledger.paper_state()` and checks `received_ns` / optional `source_ns` ages against `snapshot.now_ns` and `RuntimeBudgets.max_feed_age_ns` for every venue-bound product/feed.
  - Fences stale cached depth via the existing `_fence_feed` from the matcher’s last durable clock.
  - Fences a missing cached book where committed orders still depend on it.
  - Clears a feed’s invalid flag only after `matcher.on_book(...)` succeeds, allowing explicit new-generation resync while leaving same-generation stale resync conservatively fenced by the matcher.
- `tests/refactoring/test_tx04b_continuous.py` — appended three focused regressions:
  - Fresh ETH book must not fill BTC stale depth.
  - Fresh ETH book then fresh ETH trade must not fill BTC.
  - New BTC book never fills old depth; explicit generation 2 reopens safely, same generation stays fenced.

**Commands and results**
- Exact ticket command executed:
  ```
  python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_local_engine.py tests/refactoring/test_local_runtime.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_paper_execution.py tests/refactoring/test_tx02_joint_gate.py tests/refactoring/test_tx04a_alignment.py tests/refactoring/test_tx04b_continuous.py
  ```
- Real result: **exit 0, 142 passed in 5.30s**.
- Scoped count: 142 total collected = 139 pre-existing tests in this checkout + 3 appended regressions. No failures.

**Risks**
- Preflight adds a read-only durable matcher-state read per active account per market input; no new cache/state/schema.
- Stale cached books are fenced even without pending orders, which is intentionally conservative.
- Missing books without committed orders defer to native first-book generation handling rather than inventing a generation.
