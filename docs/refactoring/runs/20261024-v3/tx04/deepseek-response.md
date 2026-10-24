Implemented `tests/refactoring/test_tx04_joint_gate.py` only.

Commands run:
- `python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_tx04_joint_gate.py` → `1 passed`
- Exact required command:

```bash
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_paper_execution.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_runtime_checkpoint_acceptance.py tests/refactoring/test_tx04a_alignment.py tests/refactoring/test_tx04b_continuous.py tests/refactoring/test_tx04_joint_gate.py
```

Result: exit `0`, `93 passed in 4.21s`.

Coverage added:
- Real `LocalMarketEngine.on_frame` → shared hot path → `PaperRuntime.process_applied` → matcher/ledger flow with verified virgin fresh-start flags.
- Exact expected economics: cash `100018`, virtual `+1/-1`, physical `0`, reservations `100.98/103.02`, total `204`, simulated margin `200`.
- Duplicate transport frame and duplicate market event do not re-fill.
- Alpha cancel requested at `T+13`, made effective by the real engine watchdog `on_tick=host.advance` at `T+18`, with no new market event; injected monotonic clock keeps the lease live.
- Ledger/matcher reopen shows beta `UNKNOWN`, leaves `1`, reservation `103.02`, preserved attributed positions.
- Replay phase warms callbacks but does not mutate matcher/ledger state or submit actions, and restart evidence remains `UNVERIFIED` / `MISSING_EVIDENCE`.

Limitations: local Paper contract only; no claim of live/server execution, arbitrary callback crash replay, or continuous cursor recovery from a bare matcher reopen. The coupled-checkpoint integration suite remains the authority for verified strategy-state restoration. TX04B regression passed in the same required command.
