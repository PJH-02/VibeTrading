# VN07B final Tech Lead review — protect selected SQLite source paths

A new independent case (synthetic SQLite only) demonstrated that RuntimeCheckpoint.save can be configured with its selected ledger database path as checkpoint output; atomic replace then overwrites that database with JSON. Observed pytest exit1, one failed/231 deselected, recorded in output-path-before.json. No user or production data was used. This must be rejected before any write.

Codex owns security decisions and independent acceptance. The user's AGENTS delegation policy explicitly allows direct small edits when delegation overhead exceeds benefit. Codex may apply this bounded final-review change in the owner worktree while the worker uses a DIFFERENT isolated repository (not take over the delegated implementation). Pin the original worker input hash, never edit its repository, and after the worker exits merge its output with exactly the recorded owner guard:
- Only runtime_checkpoint.py capture preflight: reject an output path resolving to/samefile as any selected Paper or ETF SQLite main file or -wal/-shm/-journal sidecar. Use existing ledger.path / _database_path and pathlib, no new files/services/formats or database changes.
- Add/run a new independent regression using temporary SQLite; preserve existing header, balances and previous bundle behavior. The full suite and worker scope still require independent PASS.
- Existing broker/runtime economics, all older tests/goldens and all other tickets remain unchanged. No real account/live/deploy/data cleanup.

This exception is a small, explicit Tech Lead review correction after external implementation, not a third repair delegation. Do not edit any active worker repository concurrently.
