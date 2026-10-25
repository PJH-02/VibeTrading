Implemented VN15 only.

Command run (exact ticket command):

```
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/test_dashboard.py tests/test_dual_mock_and_assumed.py tests/test_telegram_status.py tests/test_trade_exclusions.py tests/test_local_snapshot.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_vn15_paper_projection.py
```

Result: exit `0`, `73 passed` in `3.75s`.

Changed files/functions:

- `src/vibetrading/dashboard/projection.py`
  - Appended `paper_projection_paths(env)`.
  - Appended `paper_projection(paths, *, limit)`.
  - Added internal bounded cold-read helpers for schema/metadata validation, exact decimal strings, source errors, and DTO construction.
  - Existing projection functions unchanged.

- `src/vibetrading/dashboard/bridge.py`
  - Added paper projection imports and local-mode `PAPER_PROJECTION_DATABASES` parsing.
  - Missing `StrategyTelemetryNotConfigured` now only appears when local paper config is unset.
  - Existing cold periodic scan now reads paper facts via `await asyncio.to_thread(paper_projection, ..., limit=500)`, appends orders/strategies, and surfaces fixed per-source errors.
  - Made order sorting null-safe for paper orders with no fabricated timestamps.
  - Legacy/NATS behavior otherwise unchanged.

- `tests/refactoring/test_vn15_paper_projection.py`
  - Added coverage for config validation, limit/path bounds, real ledger/matcher cold reads from another thread, partial fill, opposing strategies, UNKNOWN/reopen, exact per-currency PnL, secret absence, DB immutability, mtime/restart freshness, source failure modes, overflow, and bridge opt-in/unset behavior.

Limits and migration risks:

- Paper projection is restricted to explicit absolute paths, maximum 16 databases, and limit `1..1000`.
- Any overflowing source table rejects that source entirely with `UNAVAILABLE`/`row_limit` rather than returning partial aggregates.
- Freshness and marks remain `UNVERIFIED`/`None`; no trusted mark or fact timestamp is invented.
- Malformed `PAPER_PROJECTION_DATABASES` raises `ValueError` at bridge startup, per ticket.
- No owner/ledger/matcher/hot-path changes, no new timers/processes/HTTP callers, and no existing test weakening.
