# SIM-01 — loaded Ponytail review and exact arithmetic wrapper reduction

Prerequisites: CLN-02, STR-02, EXEC-01, VEN-01, CAP-05 accepted locally before source adoption.
Lead review, native gpt-6-luna implementation; external worker routes stopped per measured cache-inclusive overhead policy. No packages, hooks, new worker routes or deployment.

Actual installed official plugin: DietrichGebert/ponytail 4.8.4. Mode: ponytail-review.
Loaded skill: /home/pjh/.codex/plugins/cache/ponytail/ponytail/4.8.4/skills/ponytail-review/SKILL.md
SHA256: 40df33b58fc6ef889b93585733feb9566b76e9586efa7f376785c1e995197ac0.
Official source inspected: https://github.com/DietrichGebert/ponytail . Installed version is deliberately retained; no upgrade/install. Plugin metadata and hook source permissions reviewed; review only, hooks not executed.

Read: operations/dashboard/pnl.py::_exact_add,_exact_product,paper_pnl_projection,nested project_position; execution/execution_ledger.py::exact_sum,exact_product; runtime/selected_execution.py::_freeze_json,_thaw_json; portfolio/portfolio_ledger.py::divide,reserve_product and direct tests.
Write: ONLY src/operations/dashboard/pnl.py and one new regression test tests/refactoring/test_sim01_precision.py. Never authority/status/AGENTS or other financial source files.

Approved finding: INLINE the two forwarding wrappers and REUSE already imported exact_sum/exact_product. Delete wrappers, replace Name references with canonical helpers; preserve argument order/nesting/Decimal rules and every guard. Confirm whole AST equals old AST after precisely this normalization. This is wrapper count 2→0, actual production LOC decrease; no artificial line packing.

KEEP_WITH_REASON: freeze/thaw preserves deep immutability; divide uses precision128; reserve_product uses128 and ROUND_CEILING; nested position transition logic and stale/missing-mark cases remain. No safe stdlib substitute for these semantics. No deferred applicable candidate in bounded owner scope.

Acceptance: full offline regression plus separate guarded fork/lease tests; PnL Golden under low ambient Decimal precision, fee/funding, missing-mark FX, replay and exact return/exception behavior. Exact helper imports unchanged, all safety validation retained. Source/math normalization hash evidence and real test exits in receipt. SIMPLIFY source commit separate from CLN-02.

## Bounded prerequisite test-fixture correction

Lead approved an additional test-only path, `tests/refactoring/test_cap04_adverse.py`,
for the two existing adversarial process scenarios. Diagnostic traces show normal
confined initialization around 0.6s, while the synchronous case previously mixed
startup and callback observation in a nominal 500ms polling window. Await both
jobs' real validated `isolated_initialized` state within the existing 2s init
contract before the first market frame; assert no orders exist before that frame.
Keep every existing callback/ACK/watchdog/cancellation/recovery assertion and the
50ms callback, 100ms ACK, 500-iteration callback polling and 3s fence/cancel limits.
No production process/engine/strategy source change. Do not count a passing trace
as acceptance of the previously intermittent 3s timeout; independently rerun the
unchanged assertions and full regression after fixture correction.
