Implemented REPAIR 1 of R03C1.

### Changes
- `scripts/dev/agent_task.py`
  - Reworked `_load_path_map` to read the map file exactly once into a byte buffer, decode that same buffer, and compute its SHA-256 from those same bytes.
  - Closed the decode/hash generation split where a swapped map could be decoded as A while pinning digest B.
  - Preserved pre-read symlink rejection and added `O_NOFOLLOW` plus regular-file verification at open time.
  - Left all other functions unchanged, including `_ticket_text` in `agent_run.py`.

- `tests/refactoring/test_agent_task.py`
  - Added `test_path_map_digest_comes_from_decoded_generation`, a deterministic regression that changes the file during validation and asserts the returned digest belongs to the decoded generation rather than the replacement bytes.

### Verification
Command run exactly as specified:

```text
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_agent_task.py tests/refactoring/test_agent_run.py docs/tickets/R03C1-reviewer-oracle.py
```

Result: **142 passed in 4.23s**

No commits, metadata edits, or acceptance claims were made.
