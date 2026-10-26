# VN09 repair1 — Secure proposed-intent publication and bounded evidence input

Initial worker exit0 and selected183 PASS. Independent205 PASS/6 FAIL (3 defects in both formats). Parent VN09 proof-only contract unchanged. No production source was touched by red tests: only disposable owned fixtures.

## Allowed writes
MODIFY ONLY src/vibetrading/archive.py and src/vibetrading/local_storage.py. No existing methods changed; only VN09 new helpers/method may change. No test edits, metadata, configs, packages, git, network, broker code or deletion service. Source tests/docs read-only, including docs/tickets/VN09-reviewer-counterexamples.py (28 cases).

## Fix1: intent path can change during remote readback
Currently intent path is validated only before callbacks, then _write_checkpoint_file directly overwrites it. Two reproduced failures:
- backend.verify creates an existing destination evidence file; it gets overwritten.
- backend.verify replaces the evidence parent directory with a symlink to source spool; target checkpoint.json overwrites the real synthetic producer checkpoint.
Do NOT merely catch errors after overwrite. Preserve all preexisting files, source bytes, source-root directory entries and callback-created evidence. Failed guard/publication returns eligible=[] and blocked reason. Existing v1 writer/codec/uploader behavior untouched.

Required design: pin evidence-parent device/inode at preflight; before publication revalidate no symlink ancestry, outside data/spool roots, same parent identity, and absent destination. Then open parent with O_DIRECTORY/O_NOFOLLOW and compare fstat against pinned identity. Avoid pathname reuse during IO: on supported Linux use a verified directory FD and /proc/self/fd/<fd>/ for the existing _write_checkpoint_file, writing to a unique OWNED STAGING artifact (not final destination). Publish final name atomically with os.link(...,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False): FileExistsError means blocked and preserves the competing target. fsync parent and bounded readback final bytes via the same dir_fd. No os.replace of final destination. Unsupported safe primitives fail closed. Always close FDs on all paths.
Exception to parent ticket's blanket no-unlink statement: cleanup of ONLY the unique owned temporary evidence staging link/file under the verified evidence directory is authorized. No source, checkpoint, SQLite, existing intent or other owner's file unlink. Cleanup must use the pinned dir_fd and known owned staging name; never user-supplied paths. Existing checkpoint helper's own temporary cleanup is retained. This small private artifact cleanup is NOT GC/data deletion. No new framework or executor.
No source output if parent changed or target appeared during callback; detect before staging. On failure after a final audit artifact was safely linked, return blocked (artifact is always PROPOSED_DRY_RUN/executable=False/requires_revalidation=True, never deletion authority).

## Fix2: evidence input is materialized before its500-record bound
LocalSpoolStorage.spool_gc_evidence currently tuple(records) before len check; an unbounded iterable is fully consumed. Collect at most501 items (itertools.islice is fine), then reject >500 BEFORE materializing beyond that or querying/mutating the DB. Keep bounded valid iterable support if simple. Existing payload/aggregate byte limits and source/progress checks unchanged. The independent generator raises AssertionError if consumed beyond501 values and must instead receive a normal bounded rejection. No cache or false-success workaround.

## Exact command
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_spool_gc.py tests/refactoring/test_spool_gc_storage.py tests/refactoring/test_retained_spool.py tests/refactoring/test_retained_storage.py tests/refactoring/test_spool_recovery.py tests/refactoring/test_local_storage.py tests/refactoring/test_archive_integrity.py docs/tickets/VN09-reviewer-counterexamples.py

Run exact command and return actual exit, only scoped changes and remaining limits. No self-acceptance. Repair1 of maximum2.
