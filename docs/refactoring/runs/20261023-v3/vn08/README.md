# VN08 local acceptance

Accepted product/test source `8f3e3b761be794f9636a81603b8073074c293b79`. VN08 is COMPLETE_LOCAL_IMPLEMENTATION; VN09 was not started.

- Added explicit pinned retained-prefix proof, strict contiguous bounded reader and resume via existing storage progress/delivery/fact attestation. No inferred/virgin base and no fabricated input at the cut.
- A full original prefix is needed for read-only capture. Expected proof hash/generation must be independently pinned by the trusted owner. Proof is not archive/remote/GC approval. Default v1 writer, inventory, checkpoint parser and frame codec AST are identical.
- Initial OpenCode direct DeepSeek implementation selected260 PASS but six independent defects were rejected. Two bounded repairs corrected monotonicity/capacity/absolute bounds/inventory and identical consumer positions. Actual process exits initial-15 (owned stop after tests), repair1=0, repair2=0. Codex made no product edits. Only a new worker test's incorrect same-cursor expectation was corrected; historical tests/goldens unchanged.
- FreeLLMAPI `glm-4.5-flash` gateway returned HTTP200 with the matching model tag, then stream errors/HTTP429. Three bounded extra-test attempts exited1 and produced no new file contents; no provider/model fallback or artifact was integrated. VN08Q extra tests are BLOCKED, not claimed passed. The gateway worker ran existing storage tests, but this is not completion of its new-test ticket.
- Independent scope275 PASS (15 critical probes); guarded product1886 PASS + OS-isolated tooling169 PASS =2055, two external live-data tests excluded. Scoped counts overlap the full suite. The15 default review tests run once; the docs copy is provenance. Full profiles pinned identical source bytes and in-flight metadata; final canonical status checked separately.
- A reproducible baseline DATA_ROOT collision was fixed in the test harness only; corrected original scope239 PASS. No old test/guard or source was changed for that harness fix.
- Resource limits are explicit:8192-byte proof,17 cursors,64-bit absolute cursor, early inventory cap, bounded1MiB file hashing. Inventory metadata scales with bounded segment count; each nonempty batch hashes the anchor again. No production CPU/RSS sizing claim.
- No pruning/delete API, actual source deletion, broker/cloud rewrite, automatic retention/cutover or LIVE grant. Existing producer writer still rejects trimmed directories. Production gate BLOCKED; later wiring/proof-only GC tasks remain separate.

See acceptance.json, acceptance-matrix.json, independent-final.json, full-regression.json, verification-commands.json and worker-exits.json. Raw artifacts remain private.
