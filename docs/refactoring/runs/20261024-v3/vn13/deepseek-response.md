Implemented VN13 scope only.

## Scoped diff

- `src/vibetrading/strategy_runtime.py`
  - Imported existing canonical `load_strategy`.
  - Added one `PaperStrategyRuntime.register_configured(document, *, action_builder)` method.
  - Enforces the exact registration field set, mapping/list/bool/risk shapes, and `max_config_bytes` before import or durable claims.
  - Converts explicit risk mappings to existing `StrategyRisk`, loads via the existing loader, and delegates to existing `register`.
  - Existing methods, dataclasses, and callers unchanged.

- `tests/refactoring/test_vn13_file_registration.py`
  - Added 29 scoped tests using real temporary `.py` files under an extended canonical package path.
  - Covers two independent files sharing one snapshot/account, warmup, false permission, replay, latest-ledger restart blocking, duplicate `STRATEGY_ID`, oversize rejection before import, and malformed/traversal/legacy/research/bad-risk/bad-config failures without new durable claims.

- `docs/examples/paper_strategy_registration.md`
  - Added the strategy file contract, supported loader spellings, complete explicit document, injected `action_builder`, and existing `run_engine(paper_runtime=host)` seam.
  - Explicitly states files are not auto-discovered/enabled and process isolation/live promotion remain separate.

## Commands and actual results

```text
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_vn13_file_registration.py
29 passed in 1.67s
```

Exact required command:

```text
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/refactoring/test_strategy_dispatch.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_tx04b_continuous.py tests/refactoring/test_tx04_joint_gate.py tests/refactoring/test_kr_strategy_extraction.py tests/refactoring/test_vn13_file_registration.py
225 passed in 5.32s
```

## Migration risks

- Registration remains cooperative/local only; no process isolation or live promotion.
- Restart behavior is intentionally conservative: latest-ledger reopen stays `strategy_state_restore_unverified` until the existing coupled checkpoint proves state.
- Callers must supply complete explicit risk/research/permission/account/feed fields; no defaults are inferred.
- No commit, push, merge, deploy, or live operation was performed.
