I'll start by reading the three product files and the referenced test fixtures.
Now I'll implement the read-only store helper.
Now I'll add the planner method to `ArchiveMaintenance`.
Now I'll write the planner tests.
Now I'll run the exact required pytest command.
The failures are test setup and one missing import. Let me fix them.
## Objective
- Implement VN09: read-only, proof-only spool GC planning (dry-run candidates, never deletion) per `/work/docs/tickets/VN09-proof-only-spool-gc.md`.
- Add planner + read-only store/storage helpers and two new test files, then run the exact regression command and report real results.

## Important Details
- Allowed writes: `src/vibetrading/archive.py`, `src/vibetrading/local_storage.py`, `src/vibetrading/sqlite_store.py`, add `tests/refactoring/test_spool_gc.py`, `tests/refactoring/test_spool_gc_storage.py`.
- No edits to `market_spool.py`, `storage_backend.py`, existing tests/config/deploy; no installs, git mutation, uploads, unlinks, deletion/pruning, NATS, recursive agents, or Codex.
- Bash is denied except `python3 -m pytest*`.
- Planner contract: `ArchiveMaintenance.plan_spool_gc(storage, *, producer, retained_prefix, reader_progress, required_readers, lease_snapshot, remote_keys, expected_root, expected_device, now_ns, retention_ns, max_segments, max_records, max_bytes, dry_run=True, intent_path=None)`.
- Required invariants implemented: `dry_run is True`; `self._store is storage.store`; same resolved `settings.data_root`; re-capture `RetainedPrefix` and byte-compare; candidates = sealed segment IDs `1..first_retained_segment-1`; exact reader mapping + actual `storage` progress; lease snapshot before/after; raw segment archive verification via `remote_keys` under `{settings.archive.prefix}/generations/{catalogue_generation}/`; terminal manifest compaction chain; full `backend.verify` of Parquet + reconstructed `_upload` sidecar; full revalidation; no backend lookup before validation.
- Intent path validation: absolute `.json`, existing non-symlink parent, outside `data_root`/all spools, no collision; publish via `market_checkpoint._write_checkpoint_file`; publication failure must return `eligible=[]`.
- Exact test command:
  `python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_spool_gc.py tests/refactoring/test_spool_gc_storage.py tests/refactoring/test_retained_spool.py tests/refactoring/test_retained_storage.py tests/refactoring/test_spool_recovery.py tests/refactoring/test_local_storage.py tests/refactoring/test_archive_integrity.py`

## Work State
### Completed
- Added `RuntimeStore.delivery_manifest(stream, generation, sequence, event_id)` to `src/vibetrading/sqlite_store.py`; exact legacy/compact joins, returns detached dict or `None`, validates scope first.
- Added `LocalSpoolStorage.spool_gc_evidence(producer, records)` and `_gc_fact_row` to `src/vibetrading/local_storage.py`; bounded <=500 records, contiguous identity, health/quarantine/progress checks, facts/catalogue generations via SELECT, exact market manifest via `delivery_manifest`, observation/DATA_GAP fact binding.
- Fixed `AssetClass.CRYPTO_PERP` typo in local_storage.py.
- Added GC helpers and `ArchiveMaintenance.plan_spool_gc` to `src/vibetrading/archive.py`.
- Fixed terminal manifest snapshot mutation: sidecar bytes/hash now kept in separate `terminal_sidecars` dict instead of mutating `visited_rows`.
- Created `tests/refactoring/test_spool_gc.py` covering eligibility, readers, leases, root/device/budget, retention/future timestamp, remote failures, revalidation mutation, compaction chain/cycle, intent checksum/collision/publication failure, and no legacy maintenance/upload.
- Created `tests/refactoring/test_spool_gc_storage.py` covering `delivery_manifest`, market/GAP/observation evidence, bounds/identity/contiguity, quarantine/close, and missing manifest mapping.
- Added missing `import json` in `test_spool_gc_storage.py`.

### Active
- First full pytest run failed during fixture setup with:
  `vibetrading.market_spool.SpoolRotateRequired: explicit seal required before append`
  at `src/vibetrading/market_spool.py:1223`.
- Cause: both test `_build` helpers append multiple payloads without calling `spool.seal()`, while test `SPOOL` uses `segment_max_records=1`.
- Output was truncated; other failures after fixture setup are unknown until rerun.

### Blocked
- `(none)` besides the failing fixture setup above.

## Next Move
1. In both `tests/refactoring/test_spool_gc.py` and `tests/refactoring/test_spool_gc_storage.py`, change `_build` spool writes from `spool.append(raw)` to `spool.append(raw); spool.seal()` (inside the loop), then rerun the exact pytest command.
2. Inspect/rerun failures iteratively; verify `SpoolRecord` constructor usage in `test_spool_gc_storage.py` and the expected closed-storage error string matched by `pytest.raises(... match="closed/fail-closed")`.

## Relevant Files
- `/work/docs/tickets/VN09-proof-only-spool-gc.md`: authoritative ticket.
- `src/vibetrading/archive.py`: planner and GC helper implementation.
- `src/vibetrading/local_storage.py`: `spool_gc_evidence`, `_gc_fact_row`.
- `src/vibetrading/sqlite_store.py`: `delivery_manifest`.
- `src/vibetrading/market_spool.py`: `RetainedPrefix`, `SpoolCursor`, `SpoolRecord`, `capture_retained_prefix`, `read_spool_records`, `_frames`; unchanged.
- `src/vibetrading/storage_backend.py`: `LocalStorageBackend`, `sha256_file`, `ArchiveBackend.verify`; unchanged.
- `src/vibetrading/market_checkpoint.py`: `_write_checkpoint_file` used for intent publication.
- `tests/refactoring/test_spool_gc.py`: new planner tests; needs `spool.seal()` fix.
- `tests/refactoring/test_spool_gc_storage.py`: new helper tests; needs `spool.seal()` fix.
- `tests/refactoring/test_retained_storage.py`, `test_archive_integrity.py`, `test_local_storage.py`, `test_retained_spool.py`: fixture/reference patterns.
Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed.
Implemented VN09 proof-only spool GC planning.

Changes completed:
- Added `RuntimeStore.delivery_manifest(...)`.
- Added `LocalSpoolStorage.spool_gc_evidence(...)` and exact fact/manifest binding.
- Added `ArchiveMaintenance.plan_spool_gc(...)` with dry-run-only eligibility, prefix/reader/lease/budget revalidation, remote raw segment and manifest verification, intent checksum validation, and no deletion path.
- Added `tests/refactoring/test_spool_gc.py` and `tests/refactoring/test_spool_gc_storage.py`.
- Fixed `SpoolRecord` import, test fixture sealing, missing parent directory creation, namespace mapping construction, compaction expectations, and mutation timing.

Commands run and actual results:
- `python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_spool_gc.py tests/refactoring/test_spool_gc_storage.py` → `32 passed in 1.06s`
- Exact required command:
  `python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_spool_gc.py tests/refactoring/test_spool_gc_storage.py tests/refactoring/test_retained_spool.py tests/refactoring/test_retained_storage.py tests/refactoring/test_spool_recovery.py tests/refactoring/test_local_storage.py tests/refactoring/test_archive_integrity.py` → `183 passed in 2.32s`

No deletion, pruning, upload, config, or metadata changes were made.
