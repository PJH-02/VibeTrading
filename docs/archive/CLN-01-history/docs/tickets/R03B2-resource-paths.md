# R03B2 — filesystem anchor compatibility only

objective: Preserve default repository config/data roots and exact source-fingerprint input coverage after src relocation, while keeping flat legacy release bundles compatible.
base_sha: de41be71468f82fea63c4a9b6f615b5b26e6a566
prerequisites: R03A and R03B1 accepted; parent R03 only authorized.
model_profile: refactoring; exact model opencode/deepseek-v4.1-flash-free; role migrate.
read_paths: src/vibetrading/settings.py, health.py, liquidity_mm/cli.py, fv_mm/runner.py, fv_pilot/runner.py. Read only these five selected source files and the exact test being added; pyproject and config inputs are read-only dependencies.
edit_paths: src/vibetrading/settings.py; src/vibetrading/health.py; src/vibetrading/liquidity_mm/cli.py; src/vibetrading/fv_mm/runner.py; src/vibetrading/fv_pilot/runner.py; tests/refactoring/test_src_resource_paths.py (ADD).
forbidden_paths: all other files, .env/credentials, data/DBs, economic/strategy/order/risk logic, metadata, historical tests/goldens, protected P02 rollback.
contract_ids: v3 R03; C3/KP/KD/Mock/Mock2/TOSS behavior unchanged; no source alias.
implementation_steps:
1. Minimal inline path-anchor compatibility in the five sites. Canonical package resides repo/src/vibetrading with repo/pyproject.toml; detect src layout only with src parent and project marker, otherwise preserve old flat repo/vibetrading behavior. No new shared-helper module that would break pinned legacy release bundles. Preserve PROJECT_ROOT, DEFAULT_DATA_ROOT, DEFAULT_CONFIG meanings in legacy and src layouts, and /app/config precedence in health.
2. FV source_hash must hash the same logical file set and filename order: root/strategy plus actual physical package/fv_mm or fv_pilot and mm_state; pilot selected fv_mm files retained. Compute physical package directory from __file__, repository root with the same layout rule. Do not change digest algorithm/name encoding, sorting, selected modules, or trading logic.
3. New focused tests use synthetic flat/src layouts, including old flat parent named src without pyproject marker, defaults and config-hash provenance, actual source-fingerprint fixture coverage. Prefer extracting relevant assignments/functions using AST with explicit dependencies rather than importing full trading runtime or instantiating runners. No fake network or credential access. Test both legacy and src paths, real files/hash mutations. Avoid silent skips/catch-all exceptions.
acceptance: only five allowed filesystem/metadata anchor regions differ in product AST; all other function bodies unchanged. Test defaults and source coverage in both layouts. Worker may not accept parent R03.
test_commands: python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_src_resource_paths.py
review_severity: normal path compatibility; Codex independent verification.
stop_conditions: missing input or scope collision; no installs, Docker, model fallback, recursive delegation, deploy, push, live calls.
