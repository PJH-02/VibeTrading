# MOV-00 — reproducible source ownership inventory, no product edits

Base SHA 9ab1d7f9899179c442eaa533353c8ee552f88b79; branch codex/v3-local-candidate.
Role implement; exact model opencode/deepseek-v4.1-flash-free (priority2;
priority1 FreeLLMAPI configured gateway TCP refused, inspected, no partial work).
GOV-01 and OPS-01 independently accepted. Codex alone accepts/records status.

Objective: implement a stdlib readonly inventory/codegen-map generator and tests.
Only writes allowed:
 scripts/refactoring/final_move_inventory.py
 tests/refactoring/test_final_move_inventory.py
No product/metadata/config/dependency changes. No subagents, model fallback,
network, installs, commits, server/production/data access. One bounded ticket.
Read this ticket, supplied 02 B1/B2 excerpt, pyproject.toml and (if useful)
models.py/trading_contracts.py/execution_ledger.py ownership excerpts. Do not
read the whole repository or old plans. Unit fixtures should be tiny fake repos.

CLI: python3 scripts/refactoring/final_move_inventory.py --root ROOT --output FILE
Output JSON is codegen input plus evidence, not an acceptance/self-approval.
Stdlib only; git ls-files -z is inventory authority; no filesystem-wide glob walks,
no untracked user data, HOME, secrets, runtime_data, credentials, .env* inspection.
Read supplied docs/final/02_DEVELOPER_TICKETS.md explicitly (user untracked authority).
Use tracked active Python paths: src/, root .py, services/, shared/, strategies/,
strategy/, backtest/, scripts/, tests/. Exclude docs raw logs/artifacts completely.

Implementation requirements:
- Parse B1 77 root rows and match actual tracked src/vibetrading/*.py exactly.
  Reject missing/extra names, duplicate targets, existing destination collisions,
  syntax errors and unknown product subpackages. Real repo has164 product Python
  files:77 roots +87 nested,8 packages; root legacy37 Python files.
- Compile B1 paths to src/<domain>/...; map providers->feeds/providers,
  strategies->strategies, dashboard->operations/dashboard, timefolio->timefolio.
- Codex ownership exception: trading_contracts.py belongs at
  src/execution/trading_contracts.py, NOT core: actual line8 imports AccountScope
  and positive_decimal from execution_ledger. MOV-01 moves only models+dedup;
  ledger/order/account logic stays execution-owned. Do not extract/rewrite types.
- __init__.py retirement and strategy_contract.py canonical alias are explicit
  deferred exceptions, not immediate deletions. Four MM bundles fv_mm/fv_pilot/
  liquidity_mm/mm_state: DEFERRED_WITH_BLOCKER, new_path null, reason identifies
  mixed/research ownership; require symbol reuse/caller audit in MOV-05/CLN-02.
  Never create runtime/apps. Root legacy services/shared/strategies/strategy
  similarly legacy_deferred until MOV-05/06/CLN-02, preserving all current callers.
  Infer owning domain from existing roles (services/data_feed=feeds,
  execution/risk=execution, monitoring=operations, signal_gen=strategies;
  mixed root wrappers explicitly deferred). Record exceptions and blocking waves.
- Each product/root-legacy record must contain source_sha,old_path,new_path,
  source_blob_sha (HEAD),worktree_sha256,source_ast_sha256,owner,owning_symbol,
  active_callers,tests,entrypoint,config_refs,docker_refs,rollback_refs,
  dynamic_refs,disposition,reason. Store qualified top-level class/function names.
  Hash bytes from working source separately from HEAD; preserve existing dirty
  changes. Do not label dirty preexisting bytes as worker changes.
- AST inventory imports/from/relative and import_module/__import__ constant or
  UNKNOWN expressions. Resolve relative imports correctly. Index direct callers
  and tests by normalized module; record dynamic unknowns as explicit deferred
  exceptions with owner/blocking wave, not silently resolved or removed.
- Inventory root Dockerfile*, docker-compose.yml, deploy Dockerfiles/compose yml,
  .service, scripts/build_release_images.py if present, config/*.yaml and pyproject.
  Include every command/entrypoint/COPY/health/image/profile relevant line even
  if it has no vibetrading token; report line number, keyword, old/new module/path
  reference tokens only, not raw config values/secret-bearing lines.
  Static source/config/docker references must include module AND file path forms.
  Historical/rollback references remain read-only and explicitly protected.
- Record collisions root strategies vs src/strategies, execution.py/package,
  runtime.py/package and old namespace; include migration-wave resolution and
  baseline cwd/wheel/import precedence obligations. No sys.path workaround.
- Reuse historical R00/path_map ONLY as provenance (schema/count/SHA/hash), not
  authoritative targets; do not dump or copy their huge contents to model.
- Summary counts: root_rows,product_files,legacy_files,subpackages,
  unclassified,duplicate_targets,old_path_mismatch,missing_or_ambiguous.
  Explicit justified deferred rows are classified; all real missing/ambiguous
  paths or errors must increment missing_or_ambiguous. CLI returns2 when >0,
  otherwise0 with state PENDING_CODEX_REVIEW. No automatic acceptance.
- Tests: mapping+relative imports, core ownership exception, all8 packages,
  legacy deferred, dynamic constant/unknown, target collision, root drift,
  duplicate target, syntax failure, hash/dirty source distinction and safe inputs.
  Use small synthetic matrices so tests don't need77 duplicate fixture files.

Exact test:
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_final_move_inventory.py

Return actual command/exit, changed functions and unresolved issues. Stop.
