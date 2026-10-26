# MOV-05 / MOV-06 follow-up — MOV-07 flat namespace findings

Base: b096288 (MOV-06 accepted; MOV-07 gate in progress). Codex owns ownership,
mechanical moves, review and acceptance. This packet returns MOV-07 findings to
MOV-05 source ownership and MOV-06 packaging/release inventory ownership. It does
not reopen or overwrite any historical acceptance, immutable map or disposition.

Objective: no legacy package ships; preserve all inactive legacy implementations
and all economic math, native/private state, Golden and rollback inputs exactly.
Move only the dependency closure used by the eight already canonical research/
legacy strategy modules: fv_mm/types -> strategies/legacy/fair_value/contracts;
mm_state/features and policy -> strategies/research/state/{features,policy}.
These are strategy-private calculations producing intents, not the canonical
market engine, execution signer or active account owner. Keep every function/
class AST identical except dependency import spelling; no registrations or new
strategies. Four inactive mixed-owner bundles remain DEFER for CAP/STR/CLN.

Preserve the other 42 src/vibetrading Python files byte-for-byte under
`docs/archive/MOV-05-legacy-oracles/vibetrading`. Add three narrow whole-module
identity aliases there for the moved helpers. This archive is historical test/
rollback-only, excluded from source packages and images. pytest explicitly adds
this archive for existing Golden/oracle tests only. Ordinary source, wheel,
editable and image processes must not resolve vibetrading. Preserve all old
receipts and the immutable MOV-05-legacy-disposition.json; append a supplemental
SHA/blob/AST resolution map for all 45 files and pin the source commit.

Codex allowed: exactly these mechanical moves, canonical imports, supplemental
map, pyproject include removal, pytest test-only archive path, stale loader
docstrings, direct test resource constants and embedded plugin imports.
Worker allowed in isolated snapshot: scripts/refactoring/build_release_manifest.py,
tests/refactoring/test_src_packaging.py, tests/refactoring/test_mov07_flat_gate.py.
No concurrent writers in any repository; no worker authority/status/map edits.

Worker task: (1) release inventory build() must select canonical_source_hashes
when flat src/runtime exists, without requiring a legacy package. Legacy-shaped
fixtures keep their existing source_package()/vibetrading manifest behavior.
Do not alter build_local security checks or historical release scripts.
(2) strengthen existing package tests: old namespace excluded in package discovery,
pytest path explicitly src + historical archive; fresh subprocess with ONLY src
must find no vibetrading, all ten domains resolve to src from root/outside cwd.
(3) add bounded regression tests for archive helper module/class/monkeypatch
identity, fresh source isolation, and flat release inventory missing old package.
No pytest skip, stub product module, assertion removal or host installation.
Read only the allowed files, relevant direct callers, pyproject, pytest.ini,
three moved helpers and archive identity shims. No credentials/runtime data.
Use /work/ absolute paths. Do not run external network/production actions.
Worker never changes model/provider, recursively delegates or self-approves.

Exact acceptance: python3 -m compileall -q src; offline pip wheel --no-deps
--no-build-isolation --no-index in private snapshot; editable build in private
snapshot using existing build dependencies; python3 -m pytest -q -p no:cacheprovider
-p pytest_asyncio.plugin tests/refactoring/test_src_packaging.py
tests/refactoring/test_src_release_paths.py tests/refactoring/test_mov07_flat_gate.py;
full offline Linux regression incl strict fork-lease suite separately; source
AST/hash resolution, old trusted artifact readback/forward conversion; 4 actual
image builds and 8 imports; Compose7, network-none fake subprocess startup and
local blank Chromium; historical rollback SHA drill. Codex independently checks
real worker exits/diff/scope and runs all acceptance gates. LOCAL/FAKE only;
no live orders/accounts, deployment/push/merge, host installs or paid routes.
