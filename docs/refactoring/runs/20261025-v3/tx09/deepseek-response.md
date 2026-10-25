​
​
​
​
​
​
​
​
​
​
​Implemented TX09 within the allowed files.

**Changes**
- `src/vibetrading/telegram.py`
  - Added imports for VN15 `paper_projection` / `paper_projection_paths` and local snapshot config/reader.
  - Extended `TelegramController.__init__` with synchronous-only `projection_reader`.
  - Extended `_handle` to route `/feeds`, `/strategies`, `/orders`, `/positions`, `/pnl` after the existing numeric ACL, append bounded paper status summary, and list new help commands.
  - Added `_read_projection` using explicit `PAPER_PROJECTION_DATABASES` with VN15 reader limit `500`, and local snapshot-only `/feeds`.
  - Added async `_projection_reply` using `asyncio.to_thread`, fixed `UNAVAILABLE` failures, sanitized errors, `allow_nan=False`, and a 3500-byte response bound with `response_budget_exceeded`.
- `tests/refactoring/test_tx09_paper_queries.py`
  - Added coverage for construction validation, real VN15 Paper DB queries, snapshot/stale feeds, secret exclusion, reader failures/nonfinite/oversized, event-loop non-blocking, numeric ACL, status/trade regression, and real UDS owner control independence/outbox dedup.

**Limits preserved**
- `_control_command`, runtime control, owner, TTL, epoch/ACL/dedup/outbox protocols unchanged.
- No live Telegram HTTP, account, venue, install, network, or metadata changes.

**Test execution**
```
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_vn15_paper_projection.py tests/refactoring/test_vnext_vn14b.py tests/refactoring/test_control_inbox.py tests/refactoring/test_outbox_isolation.py tests/test_telegram_status.py tests/test_dual_mock_and_assumed.py tests/refactoring/test_tx09_paper_queries.py
```
Exit: `0` — **102 passed** in 1.53s.
