# MOV-06 — Runtime / Operations / Timefolio / image wiring

Authority: final/02 MOV-06 and final/03 ARCH-MOD/ARCH-DATA. Base HEAD
9478c02a39ff85bc527fa7fe32f2f2508a59dcc0. MOV-02–05 locally accepted.
Codex owns architecture, moves, provider selection, safety review and acceptance.
One ticket only. No MOV-07 work before full MOV-06 acceptance.

Mechanical phase: accepted flat_domain_move planner/applier, supplemental
MOV-06-path-resolution.json, runtime/operations ownership, shared/models.py pure
legacy candle types to strategies/legacy/models.py (all direct callers updated).
Existing operations/dashboard init is an empty seed; old init is only a docstring.
Archive its original bytes after AST/hash proof; never overwrite the target.
Runtime is composition only: no MM bundle, economic rules, signer, strategy or
execution-policy changes. Preserve source encodings/CRLF and original dirty repo.
Import/path-only changes and functional changes MUST have separate commits.

Functional phase: NATS-free Timefolio event-source/observation injection with
explicit fail-closed startup when unwired; reuse existing browser/order client,
submit=false, live enable + account arm, duplicate, UNKNOWN, weights, risk gates.
Do not allow a default fallback to NATS or silently enable any order service.
Runtime/MarketState adapter must consume only validated/durable market delivery;
single-consumer engine is not a broadcast bus. No unbounded queue or fake capacity.

Packaging/wiring: remove explicit vibetrading package-dir and root discovery;
where=[src], explicit domains and currently required historical compatibility
sources until MOV-07 retirement. No sys.path monkeypatch. Root legacy standalone
runner remains an explicitly historical source-only tool until CLN-02; canonical
strategy imports must not depend on root shared/services. Preserve historical
NATS Compose/dated restore snapshots byte-exact; separate reviewed NATS-free
primary config with no nats-init. Update root Dockerfiles, active overlays,
browser image, source hashing, module commands, mounts and healthchecks. Do not
erase rollback sources or silently activate collectors, accounts or strategies.

Read: selected map sources, direct importers/tests, config/runtime.yaml,
config/crypto_runtime.yaml, Dockerfile*, docker-compose.yml, deploy/*.yml,
deploy/Dockerfile*, service/cron and the two release manifest/build scripts.
Codex allowed: selected source/callers/test import rewrites, package metadata,
active image/config wiring, this ticket and supplemental map. Worker edits will
be restricted to a smaller explicit packet below. Workers never edit authority,
status, receipts, providers, protected data, deployment state, or commit/move files.

Exact independent commands (credential-free Linux namespaces, private temp data):
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_flat_domain_move.py
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/test_timefolio.py tests/refactoring/test_local_runtime.py tests/refactoring/test_local_engine.py tests/refactoring/test_local_transport.py tests/refactoring/test_local_observation.py tests/refactoring/test_shared_runtime_wiring.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_runtime_checkpoint_acceptance.py
python3 -m compileall -q src
python3 -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir /work/dist
docker compose --env-file FAKE_ENV -f SELECTED_COMPOSE config --no-env-resolution
docker build followed by outside-source container imports (BLOCKED if daemon absent).
Additional focused fake Timefolio adapter and startup tests required after design.
Inspect actual diffs/exits; compare business AST for moves, old/new Timefolio fake
oracle, wheel find_spec/__file__ from root and independent cwd, immutable04 hash.
At most two worker repair delegations, announce exact model transitions after
inspecting logs/exit/partial changes. No paid route, package installation, push,
merge, deploy, credentials, browser login, live order/cancel or new service.

Known preflight blocker: Docker client/Compose installed but /var/run/docker.sock
absent; image build/import gate cannot currently pass. Preserve this blocker in
status; do not accept MOV-06 or advance MOV-07 on static checks alone.

## Execution and bounded follow-up

Mechanical move committed separately (a6b3505); source/wheel/Compose wiring
committed separately (ebfef03). Timefolio runtime changes are a third commit.
Initial freellmapi/glm-4.5-flash received HTTP200 x8 then stream_error, worker1,
one partial source, no tests. Snapshot also predated runtime moves; this setup
fault was corrected in repair1. No GLM partial code applied.
Repair1 opencode/deepseek-v4.1-flash-free wrote runner + fake tests, ran11 tests
(1 failed due helper's None/default ambiguity,10 passed), fixed fixture, then
outer timeout124 without final worker exit. Repair2 freellmapi/gemini-3.1-flash-lite
received200 then429 x6, worker1, zero changes. Two repairs exhausted; no new worker
delegation. Private logs retain actual exits and absent exit receipts explicitly.

Codex independently compared five economic/helper function ASTs unchanged and ran
the corrected DeepSeek draft in a read-only snapshot:11 passed. Adopted worker
implementation after review; Codex changes only unused NATS import removal,
finite-duration guard/tests and existing EOL preservation. No rewrite/takeover of
the delegated implementation. Fake tests never open an actual browser or account.

Codex-owned composition adds an explicit Timefolio consumer to the existing
engine, with configured producer dependencies and LocalObservationBus. ETF/Paper
and checkpoint injections are mutually exclusive; no new fanout/queue. The CLI
requires --local-config and repeated --local-producer. The optional Compose
override replaces the single engine and fixes --no-submit; default config never
enables orders. Existing ETF engine return semantics stay unchanged.

Exact additional acceptance commands:
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/test_timefolio.py tests/refactoring/test_timefolio_local_source.py tests/refactoring/test_mov06_timefolio_wiring.py
python3 -m pytest -q -p no:cacheprovider tests/refactoring/test_mov06_source_manifest.py tests/refactoring/test_flat_domain_move.py
The private combined argv (668 tests), command exits, old/new fake browser oracle,
real spool-fsync-before-strategy/UDS/engine-to-fake-browser test, seven Compose
combinations, wheel provenance and immutable manifest hash are in the run receipt.
Compile bytecode goes to private /tmp; no pyc or data writes to product sources.

Remaining mandatory gate: Docker build exited1 due absent /var/run/docker.sock;
there is no built image or container import evidence. Reviewed production local
plan/capacity/mount inputs are not supplied; profiles are static candidates only.
BLOCKED is required, not ACCEPTED_LOCAL. No MOV-07 advancement, global release,
browser login, live order, production/server/account change, push or deployment.

## Environment repair — 2026-10-27

Docker Desktop's Linux engine was running; Ubuntu's default WSL integration was
disabled. After a private settings backup, Codex changed only
EnableIntegrationWithDefaultWslDistro false to true and stopped/started Desktop.
The standard /var/run/docker.sock now works in the existing and fresh WSL
processes, reaches the same Windows engine, and passes a scratch offline build
plus container file readback. Existing FreeLLMAPI and BuildKit were restored.

The earlier missing-daemon blocker is resolved. A new actual root Dockerfile
attempt using the existing app-reuse base with --network=none reached editable
installation, then failed because isolated setuptools build dependencies were
unavailable offline. No new packages were installed. Full app/crypto/browser
image verification remains pending; 05 holds the current blocker and repair
receipt. Historical receipts remain unchanged. No product source was modified.

## Completion packet — 2026-10-27

User explicitly requested resolving the blocker, fully accepting MOV-06, then
executing MOV-07. Reuse prior worker code and receipts; two repair delegations
remain exhausted, so no further MOV-06 worker implementation. Codex owns the
small build/path correction and independent image acceptance.
Allowed correction: Dockerfile and Dockerfile.crypto only, remove redundant
editable project installation (runtime already uses declared PYTHONPATH=/app/src,
no distribution-metadata/entrypoint consumers); remove stale root legacy source
directories inside reused images. No host dependency changes, Python economic
changes, original source/data removal, account/service activation or deployment.
Wheel metadata/build remains independently checked with existing bundled tools.
Build actual app/crypto/ledger/browser images, resolve canonical paths from root
and /tmp, compare image source hashes, test uid10001, real local-only browser
launch and fake submit=false/arm cases. Existing browser Dockerfile dependencies
are confined to the browser image; never install host packages or use accounts.
Run no-network fake startup/disabled-role matrix with synthetic local fixtures;
do not require or invent production ingress/mount capacity to claim LOCAL proof.
Production inputs and all live gates remain separate and unverified.
Accept only once all MOV-06 local criteria pass; then execute MOV-07 as a separate
read-only gate. Return any gate findings to the owning move ticket, no silent
waiver or weakening of authority requirements.

Independent full-suite follow-up found narrowly scoped move/fixture repairs:
three embedded crash-process scripts still imported old module paths; resource
and packaging tests still asserted the retired layout; historical TX12 checks
incorrectly compared frozen receipts with today's modified callers. Update those
test references to current owners, while verifying old receipt hashes against
their exact pinned completed Git batch and resolving old runbook roles through
the immutable MOV-00 map. Never rewrite historical receipts or runbooks.
The two 20261018 frozen oracles had checkout-normalized LF: restore original
CRLF bytes from the original checkout only after exact expected SHA256 and JSON
equality proof; mark only those oracle files -text in .gitattributes. Expected
test hashes and oracle JSON values remain unchanged. This is byte preservation,
not rebaselining or unrelated whitespace cleanup.

Actual separate-process startup exposed run_collector's orderly-close callback:
LocalTransportClient.close emits client_closed even after all work is confirmed.
Codex's bounded runtime-only fix ignores that reason only while publisher close
is underway, failed==0 and accepted==durable==applied. Early client shutdown and
other lease/gap failures still invalidate and fail closed. Three real-UDS tests
cover successful drain, early close and lease failure during close. No economic
function, spool durability protocol, safety gate or source dependency changed.
After the fix, rebuild all images and repeat the real no-network startup matrix.

Release config hashing also covers every top-level active deploy YAML, including
primary, Timefolio override, dashboard and LS profiles; rollback snapshots are
not active config inputs. Source hash already covers all included files.

Final parsed Compose audit also resolves the root historical topology's Telegram
bind mount to operations/telegram.py. The original root Compose snapshot remains
byte-exact in rollback/MOV-06; do not apply this source change to any server.
