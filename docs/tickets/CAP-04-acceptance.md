# CAP-04 — ACCEPTED_LOCAL

Lead independent acceptance: PASS. Ubuntu/Linux source commit `f8b515ce44cdf2a78c2d49918f5b0c9e63888f68`.
Private receipt `/home/pjh/.local/state/codex-opencode/runs/next-three-20261028/CAP04/CAP04.acceptance.json`; SHA-256 `840db11f4f144adfd7bf4fbb7f3d187e124d29a5a4182733b7225de61f5c6731`.

Optional confined process callbacks run on the existing PaperStrategyRuntime. The parent applies each market event once and retains durable callback/action claims, execution, risk checks, cancellation and reconciliation. Finite JSON IPC, bounded queues and worker capacities, exact response identity, epochs, freshness and deadlines reject stale or duplicate effects. Linux bubblewrap, clear environment, closed inherited descriptors, address-space/process limits and no-new-privileges are verified. Trusted cooperative and unconfined process modes remain explicitly labelled.

Actual LocalMarketEngine/watchdog cases prove an infinite callback does not stop another feed, healthy strategy or parent-owned cancellation. Throwing, memory-limited, fork-denied, overflow, pause, restart and checkpoint cases pass. Checkpoint hooks execute in the child and state roundtrip precedes readiness.

Independent regression: **2832 passed**, exit0; existing2 live-data deselections and Babel warning retained. Guarded lease tests: **25 passed**, exit0. Offline wheel build/import and actual confined worker launch from the installed wheel outside the repository exit0. All **149** Python sources match the wheel; all **182** original test files and **144** unaffected source files are preserved. Diff-check clean.

Two local CPU affinity experiment:200 durable events,20 PAPER actions,180 no-action evaluations,0 stale rejects. Action p50/p95/p99:9.77/12.19/12.67ms; no-action:5.95/7.84/8.88ms. Observed parent+worker RSS287.5MB, empty final queue. This measures synthetic spool+fsync→HotPath→isolated callback→parent validation→PAPER matcher/WAL. Native venue T5, production cgroup capacity and a low-millisecond release target are unverified; no such claim is made. Stale arrivals are separately counted, never reported as evaluated callbacks.

External GLM/DeepSeek/Gemini attempts and partials remain private. Per-ticket external allowance was exhausted; authorized gpt-6-luna fallback completed bounded work. The lead independently reviewed and adopted frozen hashes. No live accounts/orders/cancels, server changes, installations, fee reception, push, merge or deployment.
