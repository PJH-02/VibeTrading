Implemented O01A within the two allowed edit paths.

## Modified symbols

`scripts/dev/agent_task.py`
- `make_packet(root, task_id, spec)`
- `check_scope(snapshot, after, allowed)`
- `verify_source(root, packet)`
- `check_tests(commands, results)`
- `classify_failure(exit_code, text)`
- `handoff(packet)`
- `write_record(state_dir, packet, results, *, decision)`
- CLI subcommands: `list-ready`, `packet`, `check-scope`, `check-tests`, `record`, `handoff`
- Supporting stdlib-only helpers for canonical loading, readiness/model qualification, safe POSIX path validation, symlink/secret/forbidden checks, hashing, receipt permissions, and package/path import compatibility.

`tests/refactoring/test_agent_task.py`
- 25 focused test functions covering ready packets, exact hashes, non-ready refusals, model qualification, canonical scope/contracts/forbidden retention, missing R/O scope, unsafe path denials, scope changes, source/canonical drift, test evidence, failure classification, handoff, private receipts, CLI behavior, malformed inputs, and package import.

## Command run and result

```text
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_agent_task.py tests/refactoring/test_dev_status.py
........................................................................ [ 81%]
................                                                         [100%]
88 passed in 1.11s
```

Exit code: `0`.

No git mutations, metadata edits, installs, subagents, model calls, or out-of-scope file changes were performed.
