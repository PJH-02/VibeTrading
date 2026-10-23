Implemented TX01.

Changed paths
- `src/vibetrading/strategy_runtime.py` — only `_validate`: added NEW-action guards for complete execution-product instrument identity, signal-to-reference-feed identity, bounded intent/client/job/signal IDs, causal `created_ts_ns`, and a valid nonnegative integer `now`. All existing checks and CANCEL/REPLACE behavior preserved.
- `tests/refactoring/test_tx01_signal_binding.py` — added 28 scoped regression cases reusing existing runtime/matcher/ledger helpers.

Coverage added
- Same-key but different full intent instrument identity.
- Wrong signal reference identity/currency/raw symbol; reference compares to `known[reference_key]`, not execution instrument.
- Future, backdated, bool, non-integer, and negative intent creation timestamps.
- Invalid `now` boundary values, including `True`.
- Empty/oversized `intent_id`, `client_order_id`, and `signal_id`.
- Legacy wire dictionaries unchanged before/after validation, including Decimal metadata and `destination=real`.
- Valid direct dispatch and Kiwoom-reference → KIS ETF dispatch through the existing PAPER matcher.
- Rejected binding raises before `_execute`, with no `@action` receipt or order admission.
- Same-ID/same-payload deduplication and changed-payload collision rejection.
- Invalid lot admission rolls back ledger and matcher state.

Exact command run
```
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_tx00_toss_data_only.py tests/refactoring/test_kr_strategy_extraction.py tests/refactoring/test_tx01_signal_binding.py
```

Result: `264 passed`, exit code `0`.

Limitations
- Reproducer behavior was not separately executed against the unchanged base in this session; the new cases target only the newly added guards, and Codex should perform the independent base-vs-candidate comparison.
- No live/sandbox execution, external clients, or metadata/status changes were made.
