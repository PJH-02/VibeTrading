I'll start by reading exactly the three read_paths specified in the ticket: the top of the old README, current status doc, and the context index.
I have the three permitted sources. All facts I can assert come from `docs/status.md`, the context JSON facts/index, and the old README. Now I'll write the three edit-path files: root `README.md`, `docs/architecture.md`, `docs/archive/README.md`. Every local markdown link must resolve to a path in the context `tracked_markdown_index` / `relevant_paths`, or be one of the new doc files; non-markdown items stay as inline code; no start/deploy/live commands.
The first two files are written. I need to fix a typo in the README ("병제" is not Korean — should be 복제) and then write the archive index.
Let me re-read the final section of the README to correct that edit cleanly.
Now the third file — the preservation/quarantine index at `docs/archive/README.md`, with relative links from that directory level.
Let me re-read the archive README to catch and fix typos I may have introduced.
Now a final review pass of the full README and architecture doc for typos and link correctness.
The docs are complete. Two minor wording fixes in architecture.md:
All three files are written. To confirm diff scope (R00 preservation contract — only my three edit paths changed), I'll run a read-only git status check:
Bash is restricted to pytest in this environment; since this is a docs-only ticket with no tests in scope (and the ticket forbids adding helpers or claiming tests were run), no commands were executed. Diff scope is affirmed by tool usage: I only wrote the three files listed in `edit_paths`.

## 변경 사항 (Changed files)

1. **`README.md`** — replaced (114 lines, Korean, within the 100–180 target)
   - Concise intro; current v3 status from `docs/status.md` (R00/R01/O00 ACCEPTED, R02 READY, O01 next, no product ticket dispatched).
   - Actual failing baseline stated without readiness claims: **1531 passed / 1 failed / 2 deselected**, checkpoint unopened suffix identity gap confirmed/audited; VN/TX 14/23/14, last VN14B; VN20 cutover external; server uncontacted/UNKNOWN.
   - Explicit separation: last documented deployment = P02 scoped release with NATS retained vs. local NATS-free candidate (partially implemented, not deployed); old PostgreSQL/QuestDB statements labeled historical design, not proof of current commercial operation; no v3/five-venue or paper/live readiness claims.
   - Current real strategy behavior preserved: C3/KP/KD KIS mock ETF trading, KIS Mock/Mock2 routing unchanged, Toss DATA_ONLY, ticker CSV + market-close refresh, 069500 exclusion, web dashboard (external URL preserved from old README).
   - Historical/conditional runbooks linked (ETF, CSV, Bybit-testnet MM, mm_state specs/backtest protocol, crypto/LS data, FV, experiment history, data feed guide).
   - v3 entry points: three authority docs (untouched), canonical JSON (`tickets_production.json`, `stage_status.json`), step docs (01/02/15/07/09/10/11/12/13), conflict register, rollback doc + active rollback script `scripts/refactoring/deploy_p02_schema.py`, archive index, cleanup list.
   - Commands: only `python3 scripts/dev/status.py --root . --json`, `python3 -m vibetrading.cli --help`, and `--help` on existing entrypoints (`scripts/run_backtest.py`, `vibetrading.liquidity_mm`). No start/deploy/live order commands.

2. **`docs/architecture.md`** — new (121 lines)
   - Bounded ownership map: Track 1 legacy documented deployed NATS (P02 scoped release, historical Data Feed→NATS→Signal/Order/Risk + PostgreSQL/QuestDB design, with deploy artifact paths from context as inline code); Track 2 local NATS-free candidate (spool/engine/recovery/checkpoint/storage/order ledger, ownership assigned to v3 contract docs, 14/23/14 status, file-level source locations and dynamic-import inventory explicitly UNKNOWN); Track 3 research/backtest ownership (mm_state specs, experiment history).
   - UNKNOWN list: current server (uncontacted), PostgreSQL/QuestDB commercial status, dynamic imports, paper/live readiness, five-venue capability, full-regression PASS.
   - 2 vCPU / 3.8GiB documented as a **target specification, not a measured server pass**, gated by doc 05.

3. **`docs/archive/README.md`** — new (51 lines)
   - Preservation/quarantine index only; explicitly states **no files moved on this docs ticket** and no archived contents exist yet.
   - PRESERVE until external VN20: `docs/refactoring/runs/`, `docs/evidence/`, `data/production/`, active `scripts/refactoring/deploy_p02_schema.py`, legacy NATS deploy files.
   - QUARANTINE: candidates from `docs/CLEANUP_DELETE_LIST_20261019.md` require per-path evidence (references / dynamic import / deploy boundary / rollback boundary) before any future action; explicit **no blanket archive approval**; dynamic-import inventory UNKNOWN.

## Verification limits

- I read only the three permitted sources (top 160 lines of old `README.md`, `docs/status.md`, `docs/tickets/R02-context.json`); no file contents of any linked document were opened.
- Every local markdown link I added targets a path present in the context JSON `tracked_markdown_index` / `relevant_paths`, or a file created by this ticket (`docs/architecture.md`, `docs/archive/README.md`); relative forms match each file's directory (repo-relative in `README.md`, `docs/`-relative in `architecture.md`, `docs/archive/`-relative for `../` in archive README). Non-markdown paths (deploy `.yml`/`.conf`/`.service`, `scripts/dev/status.py`, `scripts/run_backtest.py`) appear only as inline code, never as link targets.
- **No tests run, no tests added, no self-approval** — per the ticket, link resolution/CLI help checks and diff-scope inspection are the coordinator's validation steps.
- Untouched per contract: the three v3 authority docs, `docs/status.md`, all other indexed docs, product code, data, receipts, rollback script content.
- Commands run: none beyond file reads/writes (bash restricted; pytest not applicable to docs-only scope).
