# PERF-01 independent local acceptance — 2026-10-31 KST

PERF-01 is **ACCEPTED_LOCAL** for reproducible profiling and structural safety. The numeric 2/5/10ms references are exceeded and remain nonblocking under final02. This is a bounded Linux test result, not production capacity or live activation approval. The machine-readable [acceptance receipt](PERF-01-local-acceptance.json) pins code, images, configuration, commands, actual exits and raw evidence. Historical QA-01 acceptance remains unchanged.

Runtime source: `2fe70d2fb2a5caa0d6e99fbb3d2a05f08813ef61`; all 149 Python source files match the tested snapshot and measured image, aggregate `acc56cd077bd3156a3d2f10aab7ff0d4c3af08381886d99cfdd8f0c7c4c90451`. Benchmark implementation: `8c08f99` (full SHA in receipt). Production changes are confined to `feeds/local_pipeline.py`, `runtime/local_transport.py`, and `runtime/selected_execution.py`; benchmark, fixtures and new regression tests are separate from production wiring.

## Measured workload and limits

The accepted run used Linux Python 3.12.15, actual cgroup `cpu.max=200000 100000`, affinity CPUs 0/1, `memory.max=4080218112`, no network, read-only root, UID 10001, dropped capabilities and a new private persistent ext4 Docker volume. No packages or images were pulled. Docker Desktop supplied the Linux test host; Windows paths occur only in private host orchestration, not product implementation. Existing QA browser dependencies were inherited.

The real chain is fake-wire receive → actual Binance collector normalization → bounded publisher queue → append/flush/fsync → Linux UDS ordered RECEIVED/APPLIED → HotPathStore/LocalMarketEngine → imported file strategy → selected dispatcher/real Owner/SQLite FULL WAL → two concrete Binance/Bybit adapter code paths with scripted native transports. T5 is transport request invocation; durable handoff/before-send and T6 scripted response are separate points. No WAN time before T0 is included.

The main source accepted, durably persisted, LIVE-applied and strategy-evaluated **1,202 events**: one viewer bootstrap, 100 first events at a real one-second cadence, 1,000 burst events, 100 variable-ACK events, and one slow-fsync event. There were **800 successful scripted T5 calls / 400 two-leg ACK groups**, with 400 persisted unique SUBMITTED intents per owner and exact final native working-inventory reconciliation. This is a scripted two-leg workload, without price-discovery or economic-neutrality claims.

The 1,000-event burst was offered in **16-record pulses with drain between pulses**. It is not an instantaneous 1,000-event burst or 1,000 events/s claim. The separately preserved 64-record-pulse attempt (`PERF01-capacity-v6`) reached 933 admitted/durable and 920 applied at failure capture, with 612 scripted T5 calls, then safely stopped at the unchanged 5s source-group deadline. Record this as **OBSERVED_CAPACITY_LIMIT**; do not increase deadlines or claim that offered load is supported. The explicit pulse parameter participates in the configuration hash.

Six auxiliary venue streams used typed native-equivalent inputs: Binance ETH, Arcus ETH, GRVT ETH, KIS ETF and Kiwoom ETF each 101 records; TOSS QQQ two distinct closed minute bars. All **507** were durably applied and later checkpointed by actual LocalSpoolStorage/Parquet. Bybit/Bitget market publication remains filtered by existing policy; no new collection route or KR350 activation was introduced. A second actual PAPER file strategy received 101 market callbacks and produced four PAPER actions.

Actual Parquet flush, local-backend ArchiveMaintenance upload and TelegramController with a fake session stayed pending for at least **60 real seconds**; all six publishers progressed before release. Archive evidence is local, not a cloud-provider/WAN benchmark. Two authenticated HTTPS API polling clients ran every two seconds against actual dashboard gateway/TypeScript API/SQLite code and recovered after a 60s polling outage while private scalar telemetry ingest continued. SQLite was **in-memory**, and there was no frontend SSR/full-page rendering. A real Timefolio Chromium blank page was ready before ingress; no contest page, login or submission was opened.

## Latency and structural findings

Milliseconds, nearest-rank quantiles from whole-event causal spans in one Python PID/clock domain. T0→T3 includes no-action events; T0→T5 selects action events and uses the later of the two leg invocations. Stage p99 values are never added. Bootstrap is separate. No confidence interval or repeated-host statistical improvement is claimed.

| Phase / span | N events | p50 | p95 | p99 | p99.9 |
|---|---:|---:|---:|---:|---:|
| First 100, T0→T3 | 100 | 19.741 | 37.439 | 54.726 | 58.683 |
| First 100, T0→T5 | 33 | 52.386 | 80.386 | 85.307 | 85.307 |
| Burst, 16-record pulses, T0→T3 | 1,000 | 326.825 | 1,591.220 | 2,497.225 | 3,710.235 |
| Burst, 16-record pulses, T0→T5 | 334 | 347.853 | 1,680.161 | 2,419.955 | 3,006.696 |
| Variable ACK, T0→T3 | 100 | 23.787 | 46.978 | 104.853 | 107.533 |
| Variable ACK, T0→T5 | 33 | 110.660 | 195.667 | 202.351 | 202.351 |

Variable ACK injects 0/1/5/20ms equally (25 each) as a blocking server `on_frame` stall; this is not an isolated wire-delay experiment. The slow-fsync case adds 20ms in the real flush path and evaluates once at 42.821ms, with no action.

| Required seam / direct callers | Actual ownership and measured evidence | Decision |
|---|---|---|
| `SpoolingMarketPublisher.publish_market_event → accept_payload` | Per-publisher bounded `put_nowait`, no suspension or other-source readiness check during admission. Main ingress depth peaked at 16 / 15,672 bytes. Source A continued while registered B was silent. | No all-venue admission barrier; preserve finite record/byte/age budgets. |
| `SpoolingMarketPublisher._writer → _persist → MarketSpool.flush` | One gather deadline per bounded flush batch, then single executor persistence. Configured 2ms group wait; first-event median 2.620ms, p99 8.330ms; flush median 6.097ms, p99 15.872ms. All 273 main batches have measured records/bytes. | Batch-bound fsync cost, not all-venue waiting. Preserve A1 and group-sync ordering; scheduler delay explains overshoot beyond configured wait. |
| `_delivery_loop → _deliver_live → LocalTransportClient` | One record in flight, exact cursor/incarnation/digest, RECEIVED→APPLIED before advancing. Durable/apply backlog peaked at 16; oldest backlog 3.912s. Burst RECEIVED-wait p99 495.845ms includes scheduling/engine contention; APPLIED-wait p99 0.008ms after RECEIVED. | Ordered serialization exposes sustained backlog. Repair only PONG interleaving; no ACK pipeline, relaxed deadline, reordered apply or durability shortcut. |
| `LocalTransportServer → LocalMarketEngine.on_frame → event_source → RealRunner/selected dispatcher/Owner` | Exclusive one-writer market apply/cursor commit; bounded strategy queue; actual SQLite FULL work in Owner/source claims/handoff. Burst ingress-dequeue p99 489.915ms; engine apply p99 3.216ms, callback-body p99 0.624ms, authorize p99 4.969ms, handoff p99 4.077ms. Actual independent storage/archive/Telegram gates did not stop publisher or strategy progress. | Durable work and sequential scheduling are material costs; measurements do not establish a safe batching optimization or prove a production throughput ceiling. Retain safety, record offered-load limit. |

`LocalMarketEngine.event_source(required_producers=None)` intentionally selects all registered feeds. The ETF/shared runtime callers in `runtime/local_runtime.py` explicitly pass `required_producers=()`, while each strategy checks its own declared input freshness. `_queue_admissible(producer)` checks that source. Normal A ingress/apply continues with B silent or invalid; strategies requiring B remain blocked in the executed QA source-gap/dependency tests. The measured 300s silence is **virtual**, and the profiler's lease is 600s: timed B lease expiry is not established by that run alone.

`_initial_proofs_ready()` still requires every registered initial C/H proof. `runtime_checkpoint.restore` and `complete_checkpoint_recovery` retain the coupled root/account recovery fence. With B's proof absent, cold root readiness remains blocked; this is an intentional consistency dependency, separately tested from normal source isolation. No `all→any` substitution or bypass was made. This closes PERF's review of historical `DATA-BARRIER-FINDING`, preserving its original receipt.

## Minimal repairs and independent verification

Commit `992bb75` fixes ordinary heartbeat PONG interleaving in the delivery ACK queue. A bounded idle drain discards only PONG; active waits skip PONG under the same absolute record deadline. Unexpected receipt phases still fail, and the existing manual heartbeat/ACK API remains available. A paired diagnostic with a real queued heartbeat reproduced durable=1, LIVE-applied/strategy=0 before repair (expected exit 1), then passed after repair (exit 0, four LIVE events/two T5). CPU/RSS and raw traces for both runs are retained; failed-baseline latency is not a valid optimization denominator. The private paired receipt records one historical artifact-label collision and points to the uniquely rerun lead baseline.

Commit `2fe70d2` refreshes exact native promotion proofs before durable dispatch work when remaining validity is at most the minimum of 1s, one quarter of the original proof window and existing prepare timeout. A FULL source-claim commit had crossed the old expiry before the required actual-use check; the old rejection was correctly fail-closed. The repair changes refresh timing only: actual-use TTL, account scope, ledger version, epochs, input freshness and hard deadlines remain enforced. Controlled-clock tests prove both successful early refresh and zero callback/order when the refreshed proof expires. There is no retry/resubmit policy change.

The new benchmark working-inventory helper is test-only: successful scripted POST payloads/native ACKs populate bounded independent native inventory, returned through the real native GET parser. It does not fabricate a production reconciliation result from the local ledger or disable pending-order equality checks. Fixture bounds are explicit: 1,024 ledger intents, 512 pending orders per owner, 4,096 feedback/claim records and 8MiB, dispatch 128 records/16MiB/30s, group 512 groups/16,384 facts/32MiB. Actual guard checks remain active.

Lead-independent Linux verification passed **3,171 full + 25 guarded lease = 3,196 required Python tests**, no required skips/deselections. Two unchanged external live-data tests remain deselected and two inherited deprecation warnings remain. A separate constrained Linux run passed **225 focused checks**. Additional independent focused sets passed heartbeat 132, native proof 30, inventory 32 and auxiliary 1; these overlap the full run and are not added to its distinct count. Original QA wheel/image/frontend receipts remain historical; the repaired source is explicitly revalidated by PERF's full suite and source-overlaid Linux image, not relabeled as the older wheel build.

The full trace has **19,763 rows**, zero omitted, sample_count=1. Independent code recomputed all **96** raw-stage quantile groups and **1,602** causal event spans, checked exact per-event stage cardinality, durability before callback, per-intent authorization/handoff/send/response order, all successful responses, and actual copied persisted SQLite owner/group facts. All **15** workload/safety checks pass. ENOSPC/EIO before durability produced zero engine/strategy callbacks; retained-prefix restart replay was state-only and duplicates suppressed. An intentionally corrupted temporary sealed tail invalidated its source; exact corrupted-tail loss is **UNKNOWN**, not zero. Real account-ledger cuts were not rewound.

Aggregate cgroup memory peaked at **472,137,728 bytes** (450.27MiB), OOM=0; CPU usage delta **147,629,399µs** over 196.156s, throttled time=0; main Python fd sample peak 88; 1,771 resource samples, none omitted. Persistent device writes were **230,395,904 bytes**, 29,076 write operations; independent process write-byte delta **230,350,848**. Resource costs include Node/Chromium and workload overlap; these are not baseline-only CPU figures.

## Raw-report correction and reproducibility

Private evidence root: `/home/pjh/.local/state/codex-opencode/runs/CLN03-QA01-PERF01-20261030` (access restricted). Image `codex-local/vibetrading-perf01:e0e01df5c6a4`, ID `sha256:ed45038d4e48b44e2d528dad560ff77b07afe8841223f2c923099262190e1ccf`. Runtime source, host hash and actual workload configuration are pinned in the receipt and every trace row. The private launch receipt includes the complete actual Docker flags/command and exit 0.

The original v7 report was `MEASURED_PARTIAL_CAPACITY` because a derived I/O-delta type filter omitted a newly observed device when initial `io.stat` was empty. All actual counters and other workload checks were already present. The final reporter defaults the missing initial field to zero, and returns exit 2 for a genuinely incomplete capacity report. An offline independent recomputation verified existing/new-device delta cases and unchanged hot-path AST. **There was no new runtime measurement after this reporting-only correction.** The measured script (`2b3d3a12…`) and final reporter (`9df5949d…`) therefore have distinct recorded hashes. Raw report, raw trace and all failed attempts remain unchanged. The [kernel cgroup documentation](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#io-interface-files) defines the device counters and `wbytes` units; the zero-baseline handling here is explicit analysis of captured counters.

- Raw report SHA256: `d592456037d56c7d0a466b9cee674300cc6b23cb7a29a7a9b008c8f068322050`.
- Raw trace SHA256: `a29a71543d5a687bcf8796b35c2a3567c758aca630e093cb675873ed810253ca`.
- Corrected derived report SHA256: `6a1eafd79c0b73b65a5a008883981fdd3154bd5153d4d2b49c5e37662a2a1de4`.
- Recompute/acceptance verifier: `python3 "$PERF01_ARTIFACTS/verify_perf01_report.py" PERF01-capacity-v7` (exit 0, preserved independent JSON).

For an already prepared equivalent isolated Linux image, the benchmark command is:

```bash
PYTHONPATH=src:tests/refactoring python scripts/benchmark_perf01.py \
  --source-sha 2fe70d2fb2a5caa0d6e99fbb3d2a05f08813ef61 \
  --scenario all --samples 100 --burst-chunk-records 16 --capacity \
  --viewer-node /usr/local/lib/python3.12/site-packages/playwright/driver/node \
  --viewer-typescript /opt/typescript/lib/typescript.js \
  --viewer-cert /tmp/perf01-tls/server.crt --viewer-key /tmp/perf01-tls/server.key \
  --viewer-script /checks/project/scripts/perf01_viewers.cjs
```

Use the pinned private image manifest/launcher for the local-test certificate, existing TypeScript dependencies, copied dashboard source, cgroup limits and persistent volume preparation. Do not treat this invocation alone on an unconstrained host as capacity acceptance. Credential-free pytest commands and actual per-run exits are recorded in the receipt.

No external worker calls were made in this batch: the prior measured cache-inclusive cost stop was carried forward; native `gpt-6-luna` implemented bounded isolated packets and the unchanged sol lead reviewed and accepted. The tiny historical native comparison is not a full-ticket counterfactual or an exact savings estimate. No live account/order, broker-fee reception, production server/TLS/DNS change, public dashboard, original-data deletion, push, merge or deployment occurred. Required offline work remaining after this three-ticket batch is REL-01 then DEP-01; optional KR-DATA-01 remains disabled, CUT-01/RET-01 require external authorization. Global release/production gates are unchanged.
