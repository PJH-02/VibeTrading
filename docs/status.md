# v3 local execution status

This is a derived snapshot. Current index: `python3 scripts/dev/status.py --root . --json`.
Authorities: [tickets](refactoring/vnext/tickets_production.json), [stages](refactoring/vnext/stage_status.json), and the three v3 documents in that directory.

- Source baseline: `0ec79dd983cc299249497ad9b647339932324f64`. Last accepted VN/TX: `VN14B`; tooling: `R00`.
- Original VN/TX: 14 local complete / 23 incomplete / 14 not started. R/O: 9 separate tooling nodes.
- R00 accepted. R01 and O00 are VERIFIED, pending exact-source acceptance; all six model routes qualified for WRITE/pytest/denial checks.
- READY: VN07, VN15, TX00; EXTERNAL: VN20 actual cutover/deletion.
- Baseline: 1531 passed / 1 failed / 2 deselected. Checkpoint unopened-suffix identity replacement is confirmed; no full-regression PASS.
- [Baseline evidence](refactoring/runs/20261023-v3/baseline/result.json), [independent audit](refactoring/runs/20261023-v3/checkpoint-audit/audit-response.md).
- No server deployment, live mutation, push, merge, NATS/data deletion. Existing P02 rollback retained.
