# MOV-03 Storage/Checkpoint/Cloud mechanical move and persisted-byte verification

Origin HEAD 08619760bae73120c6a0bd291e6b992624901e80. Read accepted docs/final definitions via this bounded packet.
Model EXACT opencode/mimo-v2.6-flash-free, role test, one writer in isolated snapshot.
Gemini requests failed timeout/502/404, MiniMax503; no discovered Kimi free route.
No worker provider switching, agents, account changes, packages or external access.

Codex applies accepted scripts/refactoring/flat_domain_move.py --owners storage:
17 frozen-map files move src/vibetrading -> src/storage; imports/rebased relative
imports and pyproject includes only. No business, schemas, raw data, env paths,
GC/recovery semantics or checkpoint/Parquet cloud keys/credentials changes.
No __file__/pickle-sensitive references exist in the selected 17 sources.
Existing spool retained prefix/read bounds/crash/fsync/GC/cloud tests must pass.
Worker sees mechanically moved credential-free source snapshot; product RO.

## Worker exact write scope (only two files)
 tests/refactoring/test_mov03_storage_golden.py (new, <=110 lines)
 Dockerfile.crypto (one COPY line only)
After src/market COPY add exactly:
COPY --chown=10001:10001 src/storage /app/src/storage
Do not alter any other Docker line; no build/pull/run/install.

Read ONLY ticket, worker-context.txt (provided API/existing test excerpts),
 src/storage/storage_backend.py if needed. No repository searches or exploration.
Do not read whole market_spool.py; exact API excerpt is in worker-context.txt.
Do not edit product, authority/status, other tests, configs, release/rollback helpers.

Create two offline tests and a reusable capture_storage_golden(root: Path) -> dict:
Use MarketSpool/SpoolCursor from storage.market_spool, LocalStorageBackend from
storage.storage_backend. Use explicit fixed producer/incarnation and limits as
provided. Append 2 exact payloads (Decimal strings, Korean Unicode/raw bytes),
assert APPENDED before durable cursor0, flush/seal/reopen then exact payloads and
cursor order/durable2. Capture deterministic files under spool (exclude writer.lock)
as {relative_name: sha256(rawbytes)}, cursor tuples/payload hex/durable boundary.
Use fake LocalStorageBackend(root/'remote'), fixed object key and a local source
payload. put_file(...sha256=...), verify, download_file to root-owned path and
assert exact bytes and size/hash/key; add these stable facts to capture dict.
Do not include temp absolute roots, mtimes/inodes or wallclock fields in golden.
First test asserts literal expected payload bytes/cursors/durable/key/size/digest.
Second test mutates only its own fake archived object after verified put, then
assert verify fails checksum and original local source remains byte-identical.
No Ncloud constructor, network, credentials or production paths. No deletion of
source data; test-owned temp corruption is the required failure fixture.
Codex independently runs capture against frozen BEFORE and AFTER sources and
compares every returned hash/field including persisted segment/checkpoint bytes.

Run exactly once after implementing:
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_mov03_storage_golden.py
Then stop; report actual exit/count and changed paths. Do not self-accept.

## Independent acceptance owned by Codex
Exact relevant regression command:
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_spool_recovery.py tests/refactoring/test_retained_spool.py tests/refactoring/test_retained_storage.py tests/refactoring/test_spool_gc.py tests/refactoring/test_spool_gc_storage.py tests/refactoring/test_spool_gc_review.py tests/refactoring/test_local_storage.py tests/refactoring/test_archive_integrity.py tests/refactoring/test_checkpoint_journal.py tests/refactoring/test_market_checkpoint_bounds.py tests/refactoring/test_checkpoint_runtime.py tests/test_parquet_store.py tests/test_storage_pipeline.py tests/test_durable_pipeline_guards.py
Plus new tests, capture golden before/after equality, canonical imports17, offline
wheel and outside-repository imports, current Docker COPY static coverage.
Read-only P02 rollback CLI import/--help only, never prepare/stage/apply/rollback.
Protect scripts/refactoring/deploy_p02_schema.py and release manifest/history.
Manifest04 immutable; no runtime_data/cloud accesses, live API/orders/push/deploy.
Final state PASS/FAIL/BLOCKED with selected model/exits/diff/hash/private receipt.
