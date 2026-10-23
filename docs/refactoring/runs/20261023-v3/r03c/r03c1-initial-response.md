Implemented R03C1 within the ticket scope.

Changed:
- `scripts/dev/agent_task.py`
  - Added optional schema-1 `path_map.json` loading/validation, including unsafe path, symlink, blocked component, type, ambiguity, duplicate/conflict, and overlapping root-rule rejection.
  - Added path translation for spec read/edit paths and canonical edit scopes, including directory scopes and planned `ADD` paths.
  - Added canonical forbidden-pattern translation under old prefixes while preserving original deny rules.
  - Pinned `path_map.json` in `canonical_hashes` when present and fenced map addition/removal/change during packet generation and source verification.
  - Preserved legacy no-map behavior.
- `scripts/dev/agent_run.py`
  - Changed only `_ticket_text` to include base SHA, prerequisites, model/provider/profile, contracts, forbidden paths, review severity, and bounded read paths (first five plus remaining dependency count).
- `tests/refactoring/test_agent_task.py`
  - Added fixtures/tests for exact and prefix mapping, old/new spec convergence, directory scope, planned ADD, translated forbidden paths, scope non-expansion, malformed/unsafe/conflicting/symlink maps, map hash/presence/generation drift, and legacy no-map behavior.
- `tests/refactoring/test_agent_run.py`
  - Added header fixture tests covering full fields and long dependency lists.

Command run:
```bash
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_agent_task.py tests/refactoring/test_agent_run.py
```

Result:
```text
140 passed in 3.88s
```

No commits, acceptance claims, or out-of-scope changes were made.
