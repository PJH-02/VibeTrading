# PERF-01 bounded durable-delivery heartbeat repair

Lead decision 2026-10-31 Asia/Seoul under final02 PERF-01.4/.5. This is a repair packet within PERF-01, not a new authority ticket. Lead retains independent acceptance. Native gpt-6-luna follows the previously measured external-worker cost stop; no external provider calls.

Objective: a control-lane heartbeat PONG must not be interpreted as the per-record durable delivery RECEIVED/APPLIED receipt, provoke reconnect/replay, or accumulate to the control queue bound during an otherwise idle publisher. Keep client manual heartbeat receipt API intact. Actual private failures PERF01-capacity-v3 and PERF01-combined-short-v1 have accepted/durable=1, live applied=0, recovered cursor=1; ordinary short non-aux core and aux tests pass because they finish before heartbeat interleaving.

Base checkout HEAD faa0552136f24f0a2cbbe4c38871f2b14cc0db3d; unchanged accepted product source QA-01 542dadc5250db5d6b43141aa0fe70440faa2e23e. Worker only writes isolated `/home/pjh/.local/state/codex-opencode/runs/CLN03-QA01-PERF01-20261030/perf01-heartbeat-worker`.

Allowed edits ONLY:
- src/feeds/local_pipeline.py
- src/runtime/local_transport.py
- tests/refactoring/test_perf01_heartbeat_delivery.py (new)

Read these callers and tests plus final02 PERF-01, final03 A1/FASTPATH. Do not change runtime defaults, budgets, framing, resume proofs, native adapters, owner, strategy, existing assertions, authority, benchmark files, settings, or credentials. No package installs, Docker/provider/network calls, original-data deletion, push/commit. No recursive delegation.

Implementation contract:
1. Add a small synchronous `LocalTransportClient.receive_ack_nowait()` using the same existing ACK queue and byte accounting, with conventional asyncio.QueueEmpty when empty. Preserve receive_ack/heartbeat signatures and current manually observed PONG behavior (existing test_local_transport heartbeat test must remain unchanged and pass).
2. In the existing SpoolingMarketPublisher delivery-budget path, drain ONLY idle PONG receipts with explicit finite work (at most the configured delivery batch bound per poll). Do not silently discard other phase/cursor/digest receipts. An unexpected non-PONG while idle must fail/recover explicitly. Existing simple fake transports without the optional nowait API still work for their bounded tests.
3. While awaiting live RECEIVED/APPLIED or recovery REPLAY APPLIED, ignore only PONG and use one ABSOLUTE per-record deadline across receipt waits. PONG traffic must not renew that deadline. Keep exact producer/incarnation/sequence/digest checks, strict RECEIVED then APPLIED ordering, inflight=1, fsync before delivery, durable cursor handling, state-only replay and fail-closed behavior. No ACK pipelining or new tasks/queues/service.
4. The legacy non-delivery-budget `_acks` consumer already handles PONG continuously; preserve it.

Required meaningful regressions, with before/after evidence:
- Real MarketSpool + publisher + real Linux UDS Client/Server/Engine: deterministic PONG is queued between cursor-zero handshake and the first durable payload. Before repair live applied remains0/cursor recovery occurs; after repair accepted=durable=live applied=callback=1, no reconnect/replay needed. Do not sleep 5minutes or fake applied receipts.
- PONG interleaved with live RECEIVED/APPLIED and replay APPLIED preserves exact market ACK ordering/correlation.
- Idle repeated real heartbeats beyond a small control record bound are drained without receiver overflow; next real market frame delivers exactly once.
- Continuous heartbeat-only responses cannot extend the declared ACK deadline, and wrong data phase/cursor/digest remains rejected.

Use cleared/no-net sandbox from private B/move_sandbox.py. Mount only isolated src/tests/scripts read-only at /work and use PYTHONPATH=/work/tests/refactoring:/work/src:/home/pjh/.local/lib/python3.12/site-packages:/home/pjh/.cache/vt-refactor-deps; helpers provide protected artifact /artifacts. Never invoke host Python with user environment for product runtime. Exact tests:
`python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_perf01_heartbeat_delivery.py tests/refactoring/test_local_pipeline.py tests/refactoring/test_local_transport.py tests/refactoring/test_local_runtime.py`

Record unique before/after artifact labels, actual exit codes, diff/hash and scope. Freeze files and report tests/limitations; no self-accept. Lead runs independent focused plus full Linux QA and the actual capped profile. Any functional repair requires the full QA rerun before PERF-01/REL-01 acceptance; do not waive it.
