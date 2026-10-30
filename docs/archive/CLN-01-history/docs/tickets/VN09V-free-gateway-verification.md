# VN09V — Read-only verification through FreeLLMAPI

Role:test. Exact model freellmapi/glm-4.5-flash, no substitute or fallback. Codex accepts independently.

There is NO coding request for this worker. All source/tests are READ ONLY. Do not inspect/search/edit files, ask questions, run ls/find/grep or invent /work/vibetrading paths. Source layout is /work/src/vibetrading. No agents, packages, network calls, accounts, metadata changes, git or source deletion.

Execute the following EXACT command immediately using one Bash tool call, then return actual exit and pytest count. Use the exact command; do not prefix environment variables. The credential-free test sandbox already configures paths/dependencies and blocks external services. Fixtures use owned temporary LocalStorageBackend data only.

python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_spool_gc.py tests/refactoring/test_spool_gc_storage.py tests/refactoring/test_retained_storage.py tests/refactoring/test_archive_integrity.py

Stop after the command and concise result. Do not claim production acceptance. If inference/quota fails, report it; no provider switch and no repeated model guessing.
