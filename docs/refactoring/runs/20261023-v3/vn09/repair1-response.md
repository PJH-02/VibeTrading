I'll start by reading the relevant source files and understanding the current implementation.
I'll use read and grep tools directly.
Now I understand the required changes. Let me implement the secure intent publication helper in `archive.py` and the bounded evidence collection in `local_storage.py`.
Implemented both VN09 repair1 fixes.

## Scoped changes

**`src/vibetrading/archive.py`**
- Added `_GcIntentTarget` plus new VN09 helpers:
  - `_gc_pin_intent_path`: validates the destination preflight and pins evidence-parent device/inode.
  - `_gc_publish_intent`: revalidates symlink ancestry, protected roots, parent identity, and destination absence after callbacks; opens the parent with `O_DIRECTORY | O_NOFOLLOW`; verifies `fstat`; writes only to a unique owned staging name through `/proc/self/fd/<fd>/` using the existing `_write_checkpoint_file`; publishes with `os.link(src_dir_fd=..., dst_dir_fd=..., follow_symlinks=False)`; fsyncs and reads back via the same FD; cleans up only the owned staging link.
- Updated `plan_spool_gc` intent handling to use those helpers. Failed guards/publication return `eligible=[]` with `intent_publication_failed`.

**`src/vibetrading/local_storage.py`**
- `spool_gc_evidence` now consumes at most 501 items with `itertools.islice` and rejects `>500` before any further materialization or DB access.

No other files were modified.

## Exact command run

```bash
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_spool_gc.py tests/refactoring/test_spool_gc_storage.py tests/refactoring/test_retained_spool.py tests/refactoring/test_retained_storage.py tests/refactoring/test_spool_recovery.py tests/refactoring/test_local_storage.py tests/refactoring/test_archive_integrity.py docs/tickets/VN09-reviewer-counterexamples.py
```

Actual result: **exit 0 — 211 passed in 2.70s**.

## Remaining limits
- Secure intent publication requires Linux directory-FD, `linkat`, and `/proc/self/fd` support; missing primitives fail closed as a blocked plan.
- If the final artifact was successfully linked but post-link fsync/readback fails, the plan returns blocked and leaves that already-published `PROPOSED_DRY_RUN` artifact in place, per the ticket.
- Staging cleanup is limited to the generated owned staging name under the pinned verified evidence directory.
