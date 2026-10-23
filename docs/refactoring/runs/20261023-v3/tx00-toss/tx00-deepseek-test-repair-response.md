Implemented the final scoped fix.

**Root cause**
`tests/test_collector_recovery_20261007.py::test_rest_refresh_bounded_and_orders_not_replayed[POST-1-1]` still expected a POST to reach `_request_once`, but the TOSS `DATA_ONLY` gate correctly rejects all non-GET requests with `RuntimeError` before authentication.

**Bounded changes**
Only `tests/test_collector_recovery_20261007.py`:
- Removed the `('POST',1,1)` parametrization; retained both existing GET 401 retry cases and assertions unchanged.
- Added `test_data_only_rejects_post_before_token_refresh_and_transport`, proving POST raises `RuntimeError` matching `TOSS DATA_ONLY: request rejected` before `access_token`, `refresh_rejected_token`, and `_request_once`, with all callback counts `0`, driven by `asyncio.run`.

No source, wire models, new tests, metadata, or other files were changed.

**Commands / results**
- Baseline reproduction: `41 passed, 1 failed` — only the legacy POST case.
- Exact requested command:
  `python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_tx00_toss_data_only.py tests/test_collector_recovery_20261007.py`
- Final result: **42 passed, exit 0**.
