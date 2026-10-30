# MOV-00 repair1 — finish scoped inventory after timeout, fix independent failures

Role debug; exact model opencode/deepseek-v4.1-flash-free. Original worker exit124
at600s. Partial code inspected; independent base tests14 PASS, actual repo
77 root/164 product/37 legacy/8 packages classified. Eight independent probes fail.
Do not reimplement/rewrite. Repair only the existing helpers and add regressions.
Codex alone accepts; no metadata/product/config changes or model fallback.

Allowed edits ONLY:
 scripts/refactoring/final_move_inventory.py
 tests/refactoring/test_final_move_inventory.py
Read the frozen tests/refactoring/test_mov00_independent_probes.py (do not edit),
relevant existing helpers (_inventory_category,_line_tokens,read_worktree,
historical_provenance,_result,run_inventory) and current tests. No broad exploration.

Fixes:
1. Inventory ALL deploy/*.yml and *.yaml overlays (including crypto-profile,
   dashboard-compose, ls-market-profile, release1-overlay), not only docker-compose
   named files. Keep root Dockerfiles/compose, deploy Dockerfiles and .service.
2. Existing UNTRACKED destination or destination-parent file/symlink must BLOCK.
   lstat/existence only; never read untracked file contents. Also detect directory
   symlinks before resolving source/destination paths; source hashes must refer
   to exact tracked regular files, no substituted symlink input.
3. A tracked product/legacy source missing/unreadable/nonregular must increment
   missing_or_ambiguous, report an explicit error and return2; no success with
   null worktree/AST hash. Preserve dirty file bytes and distinct HEAD hashes.
4. _line_tokens must only emit actual project module/path references, NOT generic
   dotted config/image/token strings (independent fake.jwt.signature case).
   Restrict module namespaces to project namespaces; no arbitrary JWT/URL echo.
5. Duplicate B1 old source rows are ambiguous and fail even if targets differ.
   Catch normalized/casefold duplicate target paths; old path case errors fail.
6. Historical provenance must whitelist exactly existing
   docs/refactoring/vnext/{repo_inventory,path_map}.json (allow explicitly supplied
   fixture path for a unit test, but never scan raw docs/refactoring/runs logs).
   Include actual R00 repo_inventory provenance; no old metadata traversal.
7. Summary subpackages is actual tracked package count, not constant8.
   All missing/ambiguous record states must correspond to nonzero failure count.
8. Correct reason text: trading_contracts ownership execution, moves in MOV-04;
   MOV-01 moves only models/dedup. The old root __init__ retirement is MOV-07.
9. CLI output must refuse overwriting ANY tracked input or the protected five
   docs/final authority files (also symlink destinations); output is private
   artifact or explicit new codegen-map file, never source/data/credentials.
10. Add persistent tests for these boundaries in allowed test file. Preserve the
    frozen independent probes; remove temporary print-only debug tests if present.

Exact command:
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_final_move_inventory.py tests/refactoring/test_mov00_independent_probes.py

Return actual exit/tests/functions/unresolved. No self-approval. Finish and stop.
