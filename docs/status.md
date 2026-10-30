# Current task status

[05_TASK_STATUS.json](final/05_TASK_STATUS.json) is the sole mutable source for current counters, dependencies, and the task DAG. This page intentionally duplicates no counters.

- Scope and sequence: [final plan](final/01_FINAL_PLAN.md)
- Implementation and acceptance: [developer tickets](final/02_DEVELOPER_TICKETS.md)
- Safety and architecture contracts: [architecture requirements](final/03_ARCHITECTURE_REQUIREMENTS.md)
- Immutable historical completion: [completed manifest](final/04_COMPLETED_MANIFEST.json)
- Current source overview: [architecture guide](architecture.md)

The documented prior deployment used NATS; the current server state is **UNKNOWN**. The accepted local candidate is NATS-free and is not deployed. A future server requires explicit user approval and release gates. Historical plans and raw evidence are indexed in [the archive](archive/README.md); they do not set current scope or status.
