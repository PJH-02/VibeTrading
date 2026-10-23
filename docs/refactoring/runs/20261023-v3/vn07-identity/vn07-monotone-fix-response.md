Implemented the FINAL repair within the two allowed files.

Root cause
- For a segment that was active when proofs were captured, `proof.mtime_ns` remains `None`.
- `validate_inventory` could later observe that same inode sealed and record it in `self._sealed_observations`.
- If the segment was then illegally renamed back to `.active`, `_open_segment` only rejected it when `proof.mtime_ns is not None`, so the regression was accepted.

Fix
- In `vibetrading/local_runtime.py`, the not-sealed active branch now rejects any segment ever observed sealed:
  ```python
  elif number in self._sealed_observations:
      raise SpoolCorruption("checkpoint spool sealed segment regressed")
  ```

Regression test
- Added `test_sealed_active_segment_cannot_regress_to_active_after_restore` to `tests/refactoring/test_checkpoint_startup.py`.
- It follows the real temporal path: active segment → checkpoint C1 → active frame 2 → post-restore seal → suffix read renames `.sealed` back to `.active` on the same inode.
- Confirmed this test fails without the fix (`DID NOT RAISE RuntimeError`), then passes with the fix.

Commands and results
1. Pre-fix reproduction:
   ```
   python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_checkpoint_startup.py::test_sealed_active_segment_cannot_regress_to_active_after_restore
   ```
   Result: `1 failed`

2. Required scoped suite:
   ```
   python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_checkpoint_startup.py tests/refactoring/test_checkpoint_runtime.py tests/refactoring/test_vnext_vn07.py tests/refactoring/test_local_runtime.py tests/refactoring/test_replay_handoff.py tests/refactoring/test_spool_recovery.py
   ```
   Result: `182 passed in 4.77s`, exit status 0.
