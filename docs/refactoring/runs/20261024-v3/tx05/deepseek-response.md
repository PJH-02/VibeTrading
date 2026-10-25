Scoped diff:
- Added only `tests/refactoring/test_tx05_file_paper_e2e.py`.
- No production, config, existing test, Golden, or metadata changes.
- Reused existing real-file fixture/helpers, `producer`/`append`/`codec`, and actual runtime/engine/checkpoint/Paper classes.

Implemented:
- Real file-backed stateful strategy (`checkpoint_state`/`restore_state` count) loaded through `register_configured`.
- Continuous PCB market path via spool + `LocalMarketEngine.on_frame` -> shared callback -> configured file -> Paper.
- Exact economic checkpoint at spooled cut 2 and true latest-ledger reconstructed restore with actual `RuntimeCheckpoint.restore`, explicit readiness, READY publication, and no callback binding before proof.
- Manual economic assertions for reserves, partial maker fill, exit fill, fees, realized PnL, cash, positions, cancel-via-watchdog, duplicate-frame suppression, and cross-branch economic/ID parity.
- Parameterized real-file raise/slow-return callback isolation against a healthy sibling.

Exact command run:
```bash
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_vn13_file_registration.py tests/refactoring/test_tx04_joint_gate.py tests/refactoring/test_tx04b_continuous.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_strategy_dispatch.py tests/refactoring/test_etf_dispatch_guards.py tests/refactoring/test_local_runtime.py tests/refactoring/test_kr_strategy_extraction.py tests/refactoring/test_tx05_file_paper_e2e.py
```

Actual result:
- Exit code: `0`
- Tests: `286 passed in 5.25s`

Cooperative/local limitations:
- Callback timeout remains after-return only; no hard preemption or process isolation is claimed.
- Production gate remains `BLOCKED`; recovery is local exact-cut verification only.
- All market/account behavior is synthetic PAPER/local, with no external connectivity or credentials.
