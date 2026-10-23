# VN07 whole local acceptance

Accepted source `79501a9c1484daa6528e129bab20d570dc66fcf9`; VN07 is COMPLETE_LOCAL_IMPLEMENTATION. VN08 and all other ticket implementations were not started.

- Bounded atomic JSON bundle couples existing market cursor, opt-in plugin state and read-only seals over actual existing Paper/ETF journals. Economic journals remain untouched by restore.
- Consistent exact cut restores; mismatched account/input, unknown handoff, corrupt/missing newest, or consumer-unproven suffix quarantines all selected consumers. No cross-SQLite atomic commit/rollback or continuous Paper suffix replay claim. Existing working orders, partial fills, reservation and claims stay authoritative.
- Fresh causal input is required before matching cached depth. Explicit combined readiness precedes transport/timers; actual run_engine path and ETF gates tested.
- OpenCode direct `opencode/deepseek-v4.1-flash-free` implemented the scoped change with two repair delegations. Actual worker exits: initial124 / repair1=0 / repair2=124; timeouts and failed independent reviews are preserved. Codex accepted only independently tested final bytes, plus a documented small12-line output destination guard and independent probes. No provider substitution.
- Independent scoped:237 PASS including22 critical probes. Full guarded product:1850 PASS; OS-isolated tooling:169 PASS; two external live-data tests excluded. These counts overlap the scoped suite. Real two-account restore/fault tests are included. New default acceptance tests duplicate the frozen reviewer file for provenance, but the full suite runs them only once.
- Existing tests/Goldens, broker calculations, cloud uploader, protected data and historical guard unchanged. Original replay helper AST preserved; wallclock regression rejected without clamping. Final status metadata checked separately. Production gate BLOCKED; no deployment or live mutations.

See acceptance.json, acceptance-matrix.json, verification-commands.json, full-regression.json, independent-final.json and worker-exits.json. Raw worker/test logs remain private.

Original Windows checkout application verified42 changed paths, preserving174 preexisting dirty entries (including153 newline-only representations), substantive edits, deleted documents and untracked documents. See original-application.json. Candidate branch only; main is not merged.
