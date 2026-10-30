# Architecture and ownership map

This guide summarizes current source boundaries. The five files under [docs/final](final/01_FINAL_PLAN.md) are the only product and execution authority. Earlier v3, vnext, Minimal Reuse, VN/TX/R/O plans are historical material; see the [archive index](archive/README.md). The current server state is **UNKNOWN**.

## Three deployment states

- **Historical deployed system:** the documented earlier deployment used NATS. That is historical configuration, not evidence of present server operation.
- **Accepted local candidate:** the current candidate uses a local NATS-free market-data path. Its local acceptance does not mean it has been installed on a server.
- **Future server:** installation requires explicit user approval and the release and operational gates. This document does not authorize deployment.

The local topology is described in [primary-compose.yml](../deploy/primary-compose.yml); old NATS deployment and rollback files remain historical evidence. Current counters and the task DAG are maintained only in [05_TASK_STATUS.json](final/05_TASK_STATUS.json).

## Source and data flow

Install the dependencies declared in the project metadata in a suitable Python 3.12+ environment before invoking runtime commands; this guide does not install packages. The source tree is organized directly under `src/` into `core`, `feeds`, `market`, `storage`, `strategies`, `execution`, `portfolio`, `runtime`, `operations`, and `timefolio`. The supported local discovery commands are:

```bash
PYTHONPATH=src python3 -m runtime.cli --help
python3 scripts/dev/status.py --root . --json
```

The first prints CLI help. The second reports repository task status from the final documents, not server health. Do not use either as evidence of a live feed, browser session, order, or deployment.

Each feed source has an independent path: source event → A1 durable spool → UDS transport → market state → strategy callback. A stalled source must not impose a global barrier on other sources. Strategy files own economic policy and action/result callbacks. Common execution owns factual order and fill state, account and position facts, reservations, and pre-trade safety; it does not choose strategy economics. Crypto and domestic execution routes are configured separately from data-source selection.

TOSS is retained as `DATA_ONLY`; it cannot mutate orders. C3/KP/KD strategies and KIS Mock/Mock2 paths retain their existing calculation and operating contracts. KR-DATA-01 is opt-in, conditional work and remains disabled unless explicitly selected. Timefolio supports local NATS-free event injection; `submit=false` is the default and further gates apply. This does not establish real browser login, contest submission, or order behavior.

## Viewer and private services

The [WEB-01 access guide](../deploy/WEB-01-access.md) describes the read-only HTTPS viewer. The private ingest service is not part of the public viewer surface, and external access remains gated. A documented design or local check is not production external-access evidence.

## Historical strategies and oracle

The retired CLN-02 oracle is archived and available only when explicitly selected in pytest. Research strategies are retained as research/history and are not silently migrated or replaced. Historical source snapshots and old plans are not current implementation maps.

## Canonical owner paths

The current source map replaces the historical R07 map; these paths are the actual import owners, not a new execution layer.

| Responsibility | Canonical source |
| --- | --- |
| Models / settings | [`core/models.py`](../src/core/models.py), [`runtime/settings.py`](../src/runtime/settings.py) |
| Provider ingress / bounded durable delivery | [`feeds/providers/`](../src/feeds/providers/), [`feeds/local_pipeline.py`](../src/feeds/local_pipeline.py) |
| Spool / archive consumers | [`storage/market_spool.py`](../src/storage/market_spool.py), [`storage/storage_worker.py`](../src/storage/storage_worker.py) |
| Local transport / composition | [`runtime/local_transport.py`](../src/runtime/local_transport.py), [`runtime/local_runtime.py`](../src/runtime/local_runtime.py) |
| Market state / checkpoint | [`market/local_engine.py`](../src/market/local_engine.py), [`runtime/runtime_checkpoint.py`](../src/runtime/runtime_checkpoint.py) |
| File strategy contracts / loading | [`strategies/contract.py`](../src/strategies/contract.py), [`runtime/strategy_runtime.py`](../src/runtime/strategy_runtime.py) |
| Order authority / native routing | [`execution/execution_owner.py`](../src/execution/execution_owner.py), [`runtime/selected_execution.py`](../src/runtime/selected_execution.py), [`execution/venues/registry.py`](../src/execution/venues/registry.py) |
| Account facts / margin | [`portfolio/portfolio_ledger.py`](../src/portfolio/portfolio_ledger.py) |
| Read-only PnL / telemetry bridge | [`operations/dashboard/`](../src/operations/dashboard/), [`dashboard/`](../dashboard/), [`deploy/WEB-01-access.md`](../deploy/WEB-01-access.md) |
| Contest browser / local consumer | [`timefolio/client.py`](../src/timefolio/client.py), [`timefolio/runner.py`](../src/timefolio/runner.py) |

File plugins implement `create_strategy`, `on_event`, and `on_order_event`; actions return through the registered common execution owner. Loading is explicit; a module's presence does not activate a strategy or account. Initial checkpoint restoration and owner/account readiness retain their recovery fences. `event_source(required_producers=())` permits per-strategy dependency checks in the shared runtime; it does not waive those restoration proofs.

No production `vibetrading.*` compatibility package is shipped. The byte-preserved [MOV-05 oracle](archive/MOV-05-legacy-oracles/README.md) and [CLN-02 oracle](archive/CLN-02-legacy-oracles/fair_value) remain test/history evidence. Pytest's explicit test harness can resolve these oracles; ordinary runtime, wheel, and image imports cannot.
