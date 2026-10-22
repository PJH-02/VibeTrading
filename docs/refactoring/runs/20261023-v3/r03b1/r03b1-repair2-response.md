I'll reproduce the current failure first with the exact pytest command.
The existing suite exits 0 only because the metadata block swallows the exception and skips validation. I'll fix both the Dockerfile and that test.
Implemented the two scoped packaging fixes.

Changes:
- `Dockerfile`: added `RUN rm -rf /app/vibetrading` after `COPY . .` and before the editable install, so a stale flat package from a reused app base cannot shadow `src/vibetrading` via `/app`.
- `tests/refactoring/test_src_packaging.py`:
  - Replaced the exception-swallowing metadata block with setuptools’ supported `setuptools.config.pyprojecttoml.apply_configuration`, which executes real package discovery and asserts both canonical and legacy namespaces.
  - Pinned test CWD/root via `monkeypatch.chdir(ROOT)`.
  - Added a main-Dockerfile cleanup regression assertion and ordering check against the editable install, complementing the existing crypto assertion.

Commands and results:
- Initial baseline: `python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_src_packaging.py` → `4 passed` (but metadata validation was silently skipped).
- After test rewrite, first run exposed the unsupported `expand` kwarg: `1 failed, 3 passed` (`TypeError: apply_configuration() got an unexpected keyword argument 'expand'`).
- Corrected call, final run: `python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_src_packaging.py` → `4 passed, 4 warnings in 1.29s`.

No Docker builds, installs, or other files touched. Acceptance remains with Codex/orchestrator.
