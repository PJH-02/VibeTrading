# MOV-01 — Core atomic move tool and regression tests

Base source HEAD 7c595d1a33c5ddb7aded82d6fec86439bc2b7d2d; isolated managed
checkout codex/move-core-feeds. MOV-00 accepted. Role migrate, pinned model
opencode/deepseek-v4.1-flash-free; FreeLLMAPI gateway remains unreachable.
Codex owns application and independent acceptance. No fallback/subagents.

Implement a SMALL stdlib deterministic move tool, not a new runner/framework.
Aim <=550 lines of implementation; reject unsupported syntax rather than guess.
Allowed worker edits ONLY:
 scripts/refactoring/flat_domain_move.py
 tests/refactoring/test_flat_domain_move.py
Read this packet, pyproject.toml, supplied compact map context and fixture tests.
Do not read product sources/repository broadly. No Bash except exact pytest.
No metadata, product, settings, credentials, dependencies, live/network commands,
server, git push/merge/reset, actual repository moves or self-approval by worker.
Worker tests may create disposable fake repos and apply the move tool there.

API/CLI:
 build_plan(root,map_path,owners) -> dict
 apply_plan(root,plan) -> no acceptance claim
 python3 scripts/refactoring/flat_domain_move.py --root ROOT --map MAP
   --owners core --output PLAN_JSON                 # readonly plan
 python3 scripts/refactoring/flat_domain_move.py --root ROOT --apply PLAN_JSON
 Plan pins exact HEAD, map SHA256, source/current caller hashes and target absence;
 writes contain base64 bytes before/after for private audit/undo. CLI prints only
 summary counts/paths/hashes, never source content. Output must be outside ROOT.
 Apply refuses any drift/target collision/unapproved file before first mutation;
 require clean git worktree before planning/application. No source/data deletes
 other than selected move sources; git mv must be used. No package installs.

Frozen map schema: {schema_version:1,source_sha,files:[{old_path,new_path,owner,
 disposition,worktree_sha256,...}],core_compatibility_aliases:[module,...]}.
Select only MOVE_ONLY_CANDIDATE, nonnull new_path, selected owner, old!=new.
MOV-00 resolved trading_contracts as execution-owned; core selection must be
ONLY src/vibetrading/{models,dedup}.py -> src/core/{models,dedup}.py.
Future same tool should accept --owners feeds market and providers package rows.
Do not change domain decisions, move deferred legacy/MM or create runtime/apps.

Transformation constraints:
1. Inventory tracked Python callers using git ls-files (src/, tests/, scripts/,
   services/shared/strategies/strategy/backtest and root *.py). Skip protected
   deploy/rollback helpers and docs artifacts. Never inspect .env/runtime_data.
2. Use AST source ranges/UTF8 byte offsets, preserve bytes outside exact edits.
   Do not ast.unparse entire modules or normalize whole-file CRLF/whitespace.
   Parse raw bytes; support UTF8/BOM or fail before mutation on unsupported encoding.
   New/edited import or literal lines may use LF; preserve all untouched bytes.
3. Resolve every relative import in its OLD package context. Rebase ALL relative
   imports inside moved modules, including dependencies NOT moved in this wave
   (e.g. future market.local_engine -> vibetrading.market_spool). Update direct
   import/from/aliased imports and from-package-import-submodule forms exactly.
   Preserve local aliases. Bare dotted import without as may be rejected safely.
4. Module/path string literals: update actual import_module/__import__/find_spec,
   monkeypatch.setattr/mock.patch targets, and module/path assertions in files
   that actually import selected moved modules. Do not rewrite synthetic source
   fixture strings or historical/generated map/old inventory documents.
   Explicitly exclude final_move_inventory.py and its fixture tests from literal
   rewriting; they describe OLD source snapshots, not active runtime imports.
5. For MOV-01, leave ONLY two identity aliases at old models/dedup modules using
   sys.modules[__name__] = canonical_module. No duplicate classes/reexports in old
   package __init__. Aliases are required for old pickle readback until MOV-07.
   All ordinary active callers should import canonical core modules afterwards.
6. New domain __init__.py files must be empty/side-effect-free. Do not create
   src/__init__.py. For moved providers/__init__, preserve its existing body.
7. pyproject: retain distribution name/dependency list and existing discovery
   until MOV-06; add only selected domain/domain.* to packages.find include.
   No removal of legacy packaging in this step, no sys.path runtime hacks.
8. Before returning plan, parse every after-source and compare normalized business
   AST (mask import nodes and known module/path literal substitutions only).
   No changes to function/class bodies, economics, schemas, A1, queue or timing.
   Plan must list changed paths and exact before/after hashes for Codex scope review.
9. Protect unsupported or unsafe mappings (traversal, source not tracked, external
   paths, duplicate/casefold target, existing/untracked target, symlink ancestor,
   missing source, changed HEAD/map/hash). No writes on validation failure.
   Frozen map source_sha may be an ancestor; exact current input hashes/HEAD are
   pinned per plan because prior accepted waves rewrite imports of later sources.

Meaningful fixture tests: real fake-repo git mv with alias identity and old pickle,
relative imports inside moved/unmoved modules, aliased import and package imports,
UTF8+CRLF preservation, dynamic import/patch targets, untouched synthetic fixture
strings, duplicate/occupied/symlink/traversal targets, stale plan HEAD/map/source,
no partial writes on validation failure, pyproject additions only, AST preservation,
Core purity, and future feeds/market cross-domain relative import rebasing.
Use tiny fake modules/fixtures; no actual product/source changes in worker checkout.

Exact command:
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_flat_domain_move.py

Stop with actual changed functions, exact command/exit, unresolved cases. Codex accepts.
