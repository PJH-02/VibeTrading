Implemented in `src/vibetrading/archive.py` only.

Changes:
- Added bounded raw archive evidence to the returned plan and persisted intent:
  - per candidate segment: `segment_id`, verified `remote_key`, `bytes`, `sha256`
  - `catalogue_generation`
  - `full_readback_verified: True`
- Evidence is included before `_gc_finalize`, so it is bound by `plan_sha256`.
- Added a bounded value snapshot for the supplied `remote_keys` mapping:
  - max 128 entries
  - nonbool bounded positive integer segment IDs
  - bounded nonempty string values
  - original namespace guard preserved for candidate segments
- Revalidated the complete mapping after all backend callbacks and before body construction/persistence. Any missing, additional, or changed mapping entry returns `eligible=[]` with `raw_segment_mapping_changed`.

Command run:

```bash
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_spool_gc.py tests/refactoring/test_spool_gc_storage.py tests/refactoring/test_retained_spool.py tests/refactoring/test_retained_storage.py tests/refactoring/test_spool_recovery.py tests/refactoring/test_local_storage.py tests/refactoring/test_archive_integrity.py docs/tickets/VN09-reviewer-counterexamples.py
```

Actual result: `215 passed in 2.88s`, exit code `0`.
