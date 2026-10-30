# MOV-06 follow-up / MOV-07 finding — historical crypto source inventory

Base 36d0285913461ebe03fc0acaf7761b4d984c2f21. Source moves/wheel/images already
verified; full Linux regression found exactly one failure: tests/test_crypto_release.py
::test_crypto_bundle_has_no_environment_database_or_server_patch calls files(),
which unconditionally requires a retired source_package(). Owning ticket MOV-06,
not a waiver in MOV-07. First bounded repair of the MOV-05/06 namespace follow-up;
previous implementation passed scope/exits; inspect its adoption and independent
43-test result. No economic implementation changes.

Worker role refactor; exact opencode/deepseek-v4.1-flash-free; isolated snapshot.
Read /work/scripts/build_crypto_release.py, /work/tests/test_crypto_release.py,
/work/tests/refactoring/test_src_release_paths.py, /work/pyproject.toml only plus
canonical source file names. Paths always /work/. No credentials/runtime data.
Allowed edits ONLY /work/scripts/build_crypto_release.py and
/work/tests/test_crypto_release.py. Never authority/status/map edits, recursive
delegation, provider/model switching or self-approval. Keep legacy-shaped fixture
behavior, old Dockerfile.r2 payload, source_package ambiguity/missing/symlink
rejection and historical Compose economics unchanged.

Implement: files(root) recognizes canonical src/runtime and inventories canonical
src Python + config + existing explicit main/requirements/builder files, without
vibetrading, archived docs or root legacy packages. Reject symlink src or nested
source path/directory rather than silently omitting it. Legacy-shaped fixtures
still enumerate source_package + config + root strategies exactly as before.
prepare(destination) must FAIL CLOSED on canonical src/runtime BEFORE writing any
bundle, with explicit message to use scripts/build_release_images.py; the old r2
Dockerfile/Compose describes a historical release and must never accidentally
build a canonical-source bundle. Historical flat/src-vibetrading fixtures retain
their original prepare() payload and assertions. Do not add a canonical r2 image
builder, activate NATS, adjust order behavior, stub imports or weaken tests.

Add meaningful tests: actual current files() enumerates all canonical Python
sources without old/archive names or forbidden data; prepare(current canonical)
raises clear historical/use-current-builder error and creates no tar; synthetic
flat-src with no legacy namespace; nested and src-root symlink negative cases.
Exact tests: python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin
tests/test_crypto_release.py tests/refactoring/test_src_release_paths.py.
Worker snapshot may lack other historical release scripts needed by latter;
report collection failures honestly; Codex runs full repository independently.
No host installs, network orders/accounts, push/merge/deploy or paid routes.
