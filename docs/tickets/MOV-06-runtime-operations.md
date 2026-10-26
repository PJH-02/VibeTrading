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
