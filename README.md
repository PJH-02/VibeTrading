# VibeTrading

Python tooling for market data, strategy plugins, and account-scoped execution. The repository contains local code and historical deployment material; documentation or local tests do not establish that a server is running or that an account is enabled.

## Current authority

Exactly five files define product scope, work, safety requirements, completed history, and current task state:

- [Final plan](docs/final/01_FINAL_PLAN.md) — scope, priority, sequence, and release gates.
- [Developer tickets](docs/final/02_DEVELOPER_TICKETS.md) — implementation and acceptance criteria.
- [Architecture requirements](docs/final/03_ARCHITECTURE_REQUIREMENTS.md) — safety and architecture contracts.
- [Completed manifest](docs/final/04_COMPLETED_MANIFEST.json) — immutable historical completion record.
- [Task status](docs/final/05_TASK_STATUS.json) — sole current counters and task DAG.

Older plans are historical references indexed in [the archive](docs/archive/README.md). They do not define current scope or status. [Status guide](docs/status.md) links to the authority without copying counters.

## Runtime and local checks

The package uses Python 3.12+ and the dependencies declared by `pyproject.toml`; prepare an environment with those dependencies before running the CLI. No dependency installation is performed by these documentation instructions. Source packages live directly under `src/`: `core`, `feeds`, `market`, `storage`, `strategies`, `execution`, `portfolio`, `runtime`, `operations`, and `timefolio`.

```bash
PYTHONPATH=src python3 -m runtime.cli --help
python3 scripts/dev/status.py --root . --json
```

The CLI help describes available commands. The status command reads the current documentation state; it does not report remote server health. Strategy plugins own economic choices and expose action/result callbacks; execution owns order, fill, account facts, reservations, and pre-trade safety. A1 persists each source to its durable spool before delivery through UDS to market state and then strategy callbacks. Sources progress independently; a quiet or stalled source must not hold another source behind a shared barrier. Crypto and domestic market/order routes remain separately configured. C3/KP/KD and KIS Mock/Mock2 retain their established behavior. TOSS remains a market-data source in `DATA_ONLY` mode, with order mutation blocked.

KR-DATA-01 is optional and remains off unless selected and separately supported. Timefolio has an NATS-free local injection path; submission defaults to false and remains gated. No real browser login, contest submission, or order is proven by local documentation or dry-run checks.

## Deployment states

There are three distinct states:

1. **Historical deployment:** NATS was used in the documented prior deployment. The current server state is **UNKNOWN**; no current-server inspection is claimed here.
2. **Accepted local candidate:** the local NATS-free candidate and its acceptance evidence describe local work only. The candidate is not deployed.
3. **Future server:** any new server installation requires explicit user approval and the applicable release, access, and operational gates. No deployment is authorized by this README.

The local primary Compose definition is [deploy/primary-compose.yml](deploy/primary-compose.yml). The [WEB-01 access guide](deploy/WEB-01-access.md) describes a read-only HTTPS viewer design; the private ingest service and external access remain separately gated. Historical NATS configuration and rollback evidence are retained for reference and are not evidence of the current server state.

Research strategies and the archived CLN-02 oracle remain available as historical/test references. The oracle is used only when explicitly selected in pytest; retained research strategies are not rewritten by this documentation update. Consult the five authority files for actual support and acceptance status.
