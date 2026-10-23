Based on my analysis of the three source files, here's my audit:

## Confirmed Issue

**Segment identity validation gap in checkpoint startup replay** (`local_runtime.py:190-259`):

The `replay_checkpoint_startup` function creates a `SpoolReader` per producer to verify the checkpoint cursor (lines 202-210). This reader captures the identity (`st_dev`, `st_ino`) of **only the segment containing the checkpoint cursor** during `_locate_start` (`market_spool.py:371-394`).

During suffix replay (lines 222-259), `SpoolReader.read_batch` validates each newly opened segment against the **previously read segment's identity** (`market_spool.py:438-441`), not against a pre-captured identity for that specific segment number. When the reader advances to the next segment, it resets `identity = None` (`market_spool.py:480`), so the first read of a new segment has no identity to validate against.

**Result**: Replacing the oldest sealed segment (containing checkpoint cursor) raises "identity changed" (tested). Replacing the newest unopened sealed suffix segment with a byte-identical file does **not** raise (the gap).

## Proposed Minimum Correction

Per audit.md architecture: Capture bounded segment identity proof **before** C verification/restore (before `engine.restore_virgin` at line 220).

1. **In `local_runtime.py`**: After `_scan` (line 192) but before `restore_virgin` (line 220), enumerate all segments from `checkpoint.cursor.sequence` to `report.durable_sequence` and capture their `(st_dev, st_ino, st_size, st_mtime_ns)` via `os.fstat` on opened fds.

2. **Local-runtime-specific SpoolReader subclass/helper**: Accept a `frozen_identities: dict[int, tuple[int, int, int, int]]` mapping segment_number → (dev, ino, size, mtime_ns). In `read_batch`, after opening each segment, `os.fstat` the fd and compare against the pinned identity for that segment number. Permit active→sealed same-inode rename; reject any identity/size/mtime mismatch.

3. **TOCTOU mitigation**: Compare the **opened descriptor** (`os.fstat(fd)`), not pre-open path stat. Capture identities by opening each segment, fstat, and closing — atomically per segment.

## Counterexamples to Test

| Case | Expected | Current |
|------|----------|---------|
| Replace oldest sealed segment (has checkpoint cursor) | `identity changed` | ✅ Caught |
| Replace newest sealed suffix segment (unopened) | `identity changed` | ❌ **Gap** |
| Replace middle suffix segment (if multi-segment suffix) | `identity changed` | ❌ **Gap** |
| Active→sealed same-inode rename | Permit | ✅ Works |
| Append beyond frozen highwater | Permit | ✅ Works |

## Uncertainties / Contract Decisions Needed

1. **Race: proof capture vs concurrent writer**: Between scanning segments and capturing identities, a writer may seal a new segment. Fix: capture identities up to `report.durable_sequence` (the checkpoint's durable boundary), not beyond. Writer's new segments are outside the frozen prefix.

2. **Race: stat vs open in validation**: The validation must `os.fstat` the **opened fd** (not `path.stat()`). Current `read_batch` does this correctly (`market_spool.py:438`). The proof capture must do the same.

3. **Two-ledger atomicity**: Audit explicitly says "Do not assert two ledger atomicity." The fix must not assume checkpoint metadata and spool segments are atomically consistent.

4. **If local-only fix is unsafe**: If a writer can race segment creation between `_scan` and identity capture such that a segment appears in `_scan` but vanishes before capture, the fix must either (a) retry capture, (b) treat missing segment as corruption, or (c) require a contract decision: "Is a segment that was durable at checkpoint time but missing at restore time a corruption or a valid GC outcome?" Current code has no retained-base semantics change — this needs explicit contract ruling.

## Files in Scope
- `local_runtime.py` (lines 180-259): `replay_checkpoint_startup`
- `test_checkpoint_startup.py` (lines 354-377): Add test for newest-segment replacement
- `market_spool.py` (lines 317-493): `SpoolReader` — **read-only reference**, not to be modified per audit.md