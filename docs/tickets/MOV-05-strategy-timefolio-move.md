# MOV-05 — Strategy namespace ownership, Timefolio preserve, 4-bundle disposition

Origin HEAD 7f0814f864325f4a1fe2ff3361cf9dbc6c250207, prior MOV-03/04 independently accepted. Final third requested
ticket, no MOV-06 implementation in this batch. Worker exact model
opencode/flint-40b-free, role debug. Verified OpenCode free catalog ID. No paid
routes/account changes/Codex model changes. Workers cannot switch providers.

Codex owns final ownership choices, source moves, audit/authority/status/acceptance.
Frozen MOV-00 map remains byte-immutable. Supplemental accepted resolution is
scripts/refactoring/MOV-05-path-resolution.json (parent map SHA pinned). 30 selected
moves: canonical strategies and ETF history/reference -> src/strategies, Timefolio
client/runner -> src/timefolio, companion monitor -> src/operations/dashboard,
existing root turtle identity alias -> single src/strategies root. Root empty init
and duplicate example identity alias are archived byte-exact as inert .py.txt
artifacts after identity proof. No duplicate implementation or sys.path workaround.
All four inactive fv_mm/liquidity_mm/fv_pilot/mm_state original bundles remain
unmoved; 286 functions receive owner/reuse_oracle/status/callers/tests/rollback
records. Mixed/research mm_state blanket package move is explicitly deferred to
STR-02/CAP/CLN-02; no runtime/apps and no automatic research activation. Original
sources and existing oracles remain until semantic reuse/retirement is accepted.
This is MOVE_ONLY; no C3/KP/KD/signals/sizing/orders/Arm/browser policy changes.
Timefolio NATS-free data integration is still MOV-06/QA; no full release PASS.

## Worker exact bounded task: fix two codemod dynamic-path gaps
ONLY editable:
 scripts/refactoring/flat_domain_move.py
 tests/refactoring/test_flat_domain_move.py
Read exactly this ticket and those two files; no product sources, configs,
authority/status, project exploration, other files, packages, network, git moves,
recursive agents or acceptance claims. Code is stdlib-only, small targeted patch;
public build_plan/apply_plan API, CAS hashes, clean-tree/symlink/target guards stay.

Existing _literal_edits only handles Constant arguments. Add safe support for:
1. Leading module namespace in AST JoinedStr, e.g.
   f"vibetrading.strategies.legacy.candle.{name}" ->
   f"strategies.legacy.candle.{name}".
Preserve ALL FormattedValue expressions/conversions/format specs and bytes/eol
outside the exact prefix. Use source span + raw token (thread raw bytes through
private helper if needed), change only a literal unescaped leading prefix that
_maps to a selected module. Do not replace an entire fstring with a JSON string,
AST-unparse the file, or use Constant.span inside JoinedStr (it covers whole token).
Keep old/new prefix pairs for business AST verification. Reject unsupported escaped
prefix or overlapping edits before mutation; do not guess. No arbitrary fstring
rewrite; only exact frozen module/path-prefix mapping.
2. Actual import_module/__import__/find_spec argument of form
   "vibetrading.strategies.research.state." + names[key].
Rewrite ONLY constant left prefix when selected mapping applies, leaving dynamic
right expression unchanged. Record dynamic_moved and pairs. Variable-only imports
and synthetic source strings must remain untouched. Existing skip of inventory
fixtures remains. No config/global string replacement.

Add three or four meaningful fixture tests: real fake-repo build/apply fstring
module-name function + live import resolves and business expression unchanged;
BinOp module prefix import_module with dynamic right preserved; UTF8+CRLF bytes
preserved outside prefixes; unmapped string/variable argument unchanged and
escaped/overlapping unsafe prefix fail-closed. Use existing fixture helpers.
No actual product tree moves. Exact worker test command, then STOP:
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_flat_domain_move.py
Return actual changed functions/count/exit/unresolved syntax only. Codex accepts.

## Codex full MOV-05 acceptance
Review helper diff + independently test before application. Archive root aliases
with source+identity evidence, generate full function disposition, plan/apply only
supplemental30 moves, compare normalized business AST and map hashes. Fix exact
entrypoint dynamic strings/packaging/Compose module paths, no economics. Test KR
Golden and loader identity, existing side-effect-free init, Timefolio submit=false,
missing arm and fake browser results; tests/test_timefolio.py and direct callers,
all legacy oracle suites, root vs src collision0, canonical/wheel import from root
and outside cwd, actual active legacy auto-registration0. No real browser/API/order.
Static active imported retired strategies/timefolio paths0 with named compatibility
alias and protected-history exceptions. Default configs strategies=[] unchanged.
Original manifest04 and historical receipts unchanged. Record exact provider/model,
real exits, failed partial drafts, independent tests, source/diff/hash provenance.
