Implemented the repair in `src/vibetrading/strategy_runtime.py` only.

Changes:
- Added independent `permission_blocked` job state so missing PAPER permission remains sticky and cannot be bypassed by pause/resume/halt, warmup, or REPLAY dispatch.
- Durable restart evidence is now recorded even when `paper_permission=False`, so the restart fence remains effective independently of the displayed permission reason.
- `pause`/`halt` retain risk-reducing `_cancel_owned` behavior for blocked/restored jobs while keeping the sticky `BLOCKED` no-new-risk barrier.
- Registration still does not cancel existing orders.
- Normal job pause/halt behavior is unchanged.

Command run:
```bash
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py docs/tickets/VN07A-reviewer-counterexamples.py
```

Actual result:
```text
67 passed in 0.67s
```

Exit code: `0`.

No files outside `src/vibetrading/strategy_runtime.py` were edited. No Git, network, metadata, or deployment operations were performed.
