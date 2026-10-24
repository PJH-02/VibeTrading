I'll start by reading the relevant source files.
Now I'll implement the `_market` freshness guard for trades.
Implemented the TX04B repair1 scope.

## Changes

**`src/vibetrading/strategy_runtime.py` (`_market` only)**
- In the existing `TradeEvent` branch, before `matcher.on_trade(...)`, the reference book from `snapshot.latest_book(reference_key)` must now:
  - exist, and
  - satisfy the existing `_fresh_input(reference_book, snapshot.now_ns)` check.
- If missing/stale: existing `_fence_feed(...)` is called (fencing from the matcher's last durable clock), followed by `matcher.advance(snapshot.now_ns)` for the already-implemented cancellation/expiry path. Cached taker depth is not consumed.
- Fresh full `OrderBookEvent` handling, cross-feed mapping, replay/duplicate/recovery/permission gates, economics, IDs, and maker/taker behavior are unchanged.

**`tests/refactoring/test_tx04b_continuous.py`**
- Added one regression: `test_fresh_trade_cannot_consume_stale_reference_book_for_pending_ioc`
  - book at `T`, registered strategy creates `post_only=False`, IOC BUY qty 1, limit 101, arrival `T+10`
  - freshness budget 5, seed balance 100000
  - fresh trade at `T+11` (source `T+11`, price 100)
  - asserts zero fills, position 0, cash 100000, REJECTED order, zero reservation, feed invalidated.

## Test command run

```text
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_local_engine.py tests/refactoring/test_local_runtime.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_paper_execution.py tests/refactoring/test_tx02_joint_gate.py tests/refactoring/test_tx04a_alignment.py tests/refactoring/test_tx04b_continuous.py
```

Actual result: **139 passed**, exit 0 (`139 passed in 8.49s`).

No other files were modified. Stopping here; no self-approval or delegation.
