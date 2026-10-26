# R03B1 — repair 2 of 2; packaging only

objective: Close the remaining packaging review defects. No other ticket work.
base_sha: 33f0e33 (current committed base; coordinator pins current bounded partial inputs by SHA256).
prerequisites: R03A pure move accepted; initial GLM timeout, MiMo worker exit0 and four tests passed; adapter later rejected unchanged baseline .env.example. Codex independently verified only allowed paths changed. Adapter code is NOT in scope.
model: opencode/deepseek-v4.1-flash-free (qualified refactoring profile). Previous models' partial code retained explicitly. Role debug.
read_paths: Dockerfile, Dockerfile.crypto, pyproject.toml, pytest.ini, tests/refactoring/test_src_packaging.py, requirements.txt, src/vibetrading/__init__.py. Additional read-only legacy Python package files are copied only so existing package-discovery tests have real directories.
edit_paths: Dockerfile, tests/refactoring/test_src_packaging.py ONLY. Existing pyproject/crypto/pytest source must not change.
forbidden_paths: all other files; product code, requirements, canonical statuses, .env/credentials, data, rollback, server/containers/accounts.
contract_ids: v3 R03; canonical vibetrading namespace; existing base reuse, UID10001/CMD preserved.

implementation_steps:
1. Confirmed counterexample: an old app base contains /app/vibetrading/__init__.py. COPY . . does not remove it. python main.py searches /app first and imports the stale flat package even though /app/src is on PYTHONPATH. Add build-only deletion of /app/vibetrading to the MAIN Dockerfile before installing editable metadata (same narrow treatment already present in Dockerfile.crypto). Never touch /data or host paths. Preserve base/dependency/build label/UID/CMD behavior, no source alias.
2. Existing focused metadata test catches every exception around Distribution.set_defaults and silently skips the check. Replace that optional swallowed validation with supported setuptools configuration expansion that really executes and asserts canonical plus all legacy namespaces. Use an appropriate public config reader or parse_config_files without nonexistent methods. Make test root/CWD explicit if needed. Do not add permissive fallbacks.
3. Add a regression assertion for cleanup in BOTH Dockerfiles. No installs/Docker calls from worker; exact pytest only. Avoid Bash exploration, python snippets, ls, or compound commands; tool permission allows only specified pytest. Use read/edit tools for source work.

acceptance: only two allowed edits; four or more focused tests exit0, no silent metadata validation skip. Codex builds wheel/images, independently checks imports/help and stale-base reuse. Parent R03 is incomplete and no follow-up work authorized.
test_commands: python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_src_packaging.py
review_severity: bounded packaging acceptance
stop_conditions: missing dependency or scope collision; no recursive delegation, provider substitution, installs, acceptance claims, deployments, or live operations.
