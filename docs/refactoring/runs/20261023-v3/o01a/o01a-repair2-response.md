Implemented the scoped repair in exactly:
- `scripts/dev/agent_task.py`
- `tests/refactoring/test_agent_task.py`

Changes:
1. `write_record` now uses a Linux dirfd walk:
   - Opens path components with `O_DIRECTORY | O_NOFOLLOW`, creating missing components with `mkdir(..., 0o700, dir_fd=...)` and reopening nofollow.
   - Keeps the final leaf fd open while creating receipts with `O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW`, `dir_fd=leaf_fd`.
   - Uses `fchmod` only on the leaf and receipt fd; does not chmod ancestors.
   - Rejects symlink/non-directory components, checks leaf ownership with `fstat`, and closes every fd on all paths.
   - Added deterministic race tests for component-open, component-create, and post-open leaf-swap cases; victim permissions/files remain unchanged.
2. `make_packet` and `verify_source` now validate root/ancestor components for symlinks before any canonical read or git call. Both canonical JSON paths are checked as existing regular, non-symlink files before `load_status`/git. Empty and missing roots fail closed.
3. `_model_route` now rejects `provider_route` values that do not equal the selected model prefix.

Commands run:
- `python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_agent_task.py tests/refactoring/test_dev_status.py`
  - Result: `110 passed in 1.27s`, exit code `0`.
- `python3 -m pytest --collect-only -q -p no:cacheprovider tests/refactoring/test_agent_task.py tests/refactoring/test_dev_status.py`
  - Result: `110 tests collected in 0.09s`, exit code `0`.

No other files edited, no metadata changes, no installs/network/agents, no BLOCKED conditions.
