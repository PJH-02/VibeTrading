Implemented the three scoped fixes.

## Changes

**`scripts/dev/agent_task.py`**
1. `make_packet`:
   - Reads and validates both canonical contract fields: `contracts` and `contract_ids`.
   - Preserves the ordered, de-duplicated union of both lists.
   - Still rejects caller `contract_ids` that drop any canonical contract.
2. `verify_source`:
   - Validates `read_paths` / `edit_paths` as string lists and rejects unsafe or within-list duplicate paths.
   - Requires `source_hashes` keys to exactly match `read_paths ∪ edit_paths`.
   - Rejects dropped pins and extra pins.
   - Continues validating hash format, symlinks, and actual file bytes at the same HEAD.
3. `write_record`:
   - Checks every existing component of the absolute `state_dir` path before `mkdir`/`chmod`.
   - Rejects symlinks in the state target or any ancestor.
   - Rejects non-directory existing target/ancestor paths.
   - Preserves victim content/permissions by failing before mutation; keeps dir `0700` / file `0600`.

**`tests/refactoring/test_agent_task.py`**
- Added synthetic TX00-style `contract_ids`-only coverage and missing-contract rejection.
- Added coexistence-union and invalid contract-field tests.
- Added exact source-pin coverage tests for empty/dropped/extra/unsafe/invalid/duplicate pins.
- Added symlink target, symlink ancestor, and non-directory target/ancestor receipt tests.

## Test evidence

Command run exactly:

```text
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_agent_task.py tests/refactoring/test_dev_status.py
```

Actual result:

```text
98 passed in 1.22s
```

Exit code: `0`.
