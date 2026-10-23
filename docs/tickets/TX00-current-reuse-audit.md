# TX00 — close the current-source reuse audit, no product changes

## Ownership and objective

Codex selects the next DAG node TX00 after VN09. Close the **static local reuse inventory** prerequisite for TX01, without rebuilding any exchange path. This is documentation/evidence tooling only. Existing working trading/cloud/strategy behavior is authoritative; historical raw audits are retained. Model `opencode/deepseek-v4.1-flash-free`, role `refactor` (build agent, because this task writes a bounded evidence checker). Codex independently accepts. Do not claim live capability, self-accept, dispatch another ticket, or recursively delegate.

Read `docs/tickets/TX00-current-audit-baseline.json` as machine data. Its audit_source_sha is the product source being audited; the sandbox Git HEAD is merely a snapshot, NOT that source SHA. Its historical AST hashes were extracted by Codex from the immutable historical Git commit. Read `execution_reuse_matrix.md` and historical `function_dispositions.json` under `docs/refactoring/vnext/reuse_audit/`. Prior three function-level audit reports remain valid historical evidence; current code differs in only ETF/Paper recovery seams. Inspect 1–5 relevant source files at a time. Do not read credentials, runtime data, .env, account config or server files.

## Only writable paths (create these six files)

- `docs/refactoring/vnext/reuse_audit/tx00_current/check.py`
- `docs/refactoring/vnext/reuse_audit/tx00_current/inventory.json`
- `docs/refactoring/vnext/reuse_audit/tx00_current/dispositions.json`
- `docs/refactoring/vnext/reuse_audit/tx00_current/callers.json`
- `docs/refactoring/vnext/reuse_audit/tx00_current/delta.json`
- `docs/refactoring/vnext/reuse_audit/tx00_current/closure.md`

All product, tests, configuration, original audit evidence, baseline/ticket, canonical tickets_production.json/stage_status.json and .git are READ ONLY. No installs, deletion/rename/retirement, cloud/network/account requests, orders, deployment, git mutation, process launching other than the exact checker below, or external messages.

## Implementation

1. Add a standalone standard-library-only checker, CLI `--root PATH --write` / `--root PATH --check`. Restrict outputs to the four named JSON siblings. `--write` generates them; `--check` independently recomputes the same objects, compares EXACT parsed JSON, prints concise counts and exits nonzero on mismatch/missing source/malformed baseline. Never rewrite expected data in `--check`. No imports/execution of product modules. Deterministic ordering, UTF-8 JSON, LF, no timestamp/self-referential output hash. No generic registry or runtime code.
2. Use EXACT 47 scope_paths from baseline, validate unique relative .py paths; enumerate module-level and class-body functions/methods the same way as historical inventory (nested local definitions are within their parent AST hash, not separate rows). Include path, qualified symbol, current line/end_line, async, signature, AST SHA-256. Current source files and source_set digest must be bound by raw SHA-256, along with audit_source_sha and historical_source_sha. Every symbol unique per path. Do not force historical 692 count if current has additions.
3. Compare function ASTs by `(path,symbol)` with historical_functions: retained_unchanged, retained_changed, added, removed. Each current function receives historical KEEP/REUSE/ADAPT/etc classification by exact key; additions under existing ETF/Paper recovery seams are REUSE with their accepted VN07/VN08 context and an honest code-only label. Changed old functions retain their preservation disposition and explicitly say changed since historical audit, no blanket AST preservation claim. No replacement/retirement approval. Missing history disposition for an old symbol is an error. Missing symbols are visible in delta, never silently dropped.
4. Recompute direct AST **import-qualified constructor/function references**, not dynamic receiver inference, across tracked Python inputs in src/, services/, scripts/, tests/ only (`git ls-files -- '*.py'`, read only). Resolve top-level imports and function-local imports with lexical scope; relative imports must use correct package context for __init__.py. Include module-level calls, class-body calls and nested function calls exactly once with precise caller_symbol. Shadowed/reassigned names and unknown receiver calls must be marked unresolved or conservatively omitted with explicit limitations. Never resolve every `self.submit` as a venue function or call imports runtime proof. Count only references into the 47 audited modules; constructor calls can map to methods via class identity but aren't proof methods executed. Include candidate input paths/hashes so coverage is explicit. Do not read .env/secrets/untracked files, and don't copy raw source text to JSON.
5. delta.json identifies changed files/functions, new recovery functions and removed functions, compares with the frozen historical source. Explain known accepted local recovery changes versus unchanged exchange wrappers. Current line numbers come from current AST; historical report/matrix lines remain historical references. Keep historical JSONs/raw reports unchanged.
6. closure.md is a concise human report: link all 22 route rows (matrix A1–A17 and five normalized Perp adapters), distinguish venue/product/testnet-vs-live/account domain/position-margin mode; UNKNOWN/N/A are explicit, no implied unsupported claim. Point every route to current `inventory.json` path + qualified function symbols, with current source path:line links. Existing matrix may be referenced for full function comparison, but give current links for submit/cancel/status/fill/private/position/reconcile/account and callers where present. Reuse existing prior audit rather than re-read all functions manually. Labels `SUPPORTED_CODE`, `FIXTURE`, `SANDBOX_REQUIRED`, `LIVE_REQUIRED`, `UNSUPPORTED`, `UNKNOWN` are evidence labels, not operational certification. Distinguish private transport from normalize-only and query-only reconciliation. TOSS stays DATA_ONLY; no active order API. Existing working cloud upload/download/hash transport stays KEEP. Record static/lexical/dynamic limitations and minimum subsequent gap TX01 (existing Signal/OrderIntent/Paper bridge); no product work or deletion authority. Do not demand all-five venue implementation, generic margin engine, new cloud uploader or universal platform.

## Acceptance and exact commands

Run in order:

```
python3 docs/refactoring/vnext/reuse_audit/tx00_current/check.py --root . --write
python3 docs/refactoring/vnext/reuse_audit/tx00_current/check.py --root . --check
```

Both must return actual exit0. Final response states exact commands/exit codes, current counts, changed/added/removed symbols, limitations, and files written. Codex will compare all current definitions and historical hashes independently, check caller references in current AST, reject stale line links/false live claims, run existing TOSS regression in a credential-free offline sandbox, and update canonical metadata only after acceptance. **No product test or live PASS may be invented.**
