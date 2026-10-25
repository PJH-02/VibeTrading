# VN19 — rollback preparation, no rollback performed

Preserve the accepted source SHA and exact per-file hashes returned by
`build_local` alongside the current source/role review package. Before any future
operator rollback, verify actual mount/device ownership, current immutable image
and rollback digest, latest journal position and current account facts. These
external gates are NOT_RUN in this local batch.

A source rollback does not authorize restoring an older trading database over
current fills, balances or reservations. Keep the latest durable account facts;
use the existing coupled checkpoint owner only after its exact-cut, plugin and
readiness proofs succeed. On mismatch, retain UNKNOWN reservations and remain
blocked. `local_paper_assembly.md` describes caller-owned Paper ledger lifetime.
Graceful role shutdown may add journal facts and invalidate an earlier checkpoint;
positive exact-cut recovery tests close the engine directly before reopening the
latest owned database. They do not prove automatic graceful role checkpointing.

Existing `docs/refactoring/rollback.md` and snapshot review APIs remain available.
This document runs no source replacement, deployment, order, data deletion, NATS
reset or database restore. VN20_APPLIED=false; server_deployed=false; live_active=false.
