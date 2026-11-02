# RET-01 — authorized only after CUT-01 operational acceptance

User explicitly requests RET-01 after CUT-01 in the same 2026-11-02 instruction. This is conditional retirement authorization, not permission to bypass prerequisites. General unnecessary-file cleanup and env key rotation are postponed by the user. Original-data deletion is not authorized.

Do not retire active NATS/legacy paths during CUT-01 preflight. For each proposed service/config/image/code path prove: actual production callers0, rollback dependency0 and rollback/retention authority ended, unique data0 with retained-prefix/remote readback proofs, current recovery still works, exact backup/source lineage. CUT-01 must be OPERATIONAL_VERIFIED first. Keep old NATS volumes/DB/backlog and all irrecoverable source data until independently proven policy allows changes. Do not confuse repository housekeeping with server retirement.

Lead records path-specific evidence and bounded actions in final05/receipt; no worker self-approval. No docker prune, compose down -v, source-volume deletion or old SQLite restore. If actual soak/retention/rollback conditions are not yet satisfied, keep RET-01 pending with concrete evidence and continue useful read-only readiness audit. Do not claim completion merely because a container stopped or source file was removed.
