## vibetrading_v2_production_readiness_plan - 2026-02-21
- [ ] Should `vibetrading_V2/docs/vibetrading_refactor_spec.md` supersede all references to `docs/vibetrading_refactor_spec.md`? — Needed to avoid conflicting requirements.
- [ ] Must quality gates run only on `vibetrading_V2/` and `cli/`, excluding legacy `strategies/` and V1 modules? — Prevents false failures and scope creep.
- [ ] What exact CI matrix (Python versions/OS) is required for production-readiness? — Defines compatibility target.
- [ ] Are gate commands fixed, and are zero errors required for lint/type/tests/strategy validation? — Makes “ready” objectively testable.
- [ ] What determinism standard is required for backtest outputs (exact equality vs tolerance fields)? — Enables reproducibility verification.
- [ ] Should backtest clock behavior be fixed/replayable by requirement, distinct from paper/live? — Needed to formalize deterministic expectations.
- [ ] What release artifact(s) are required (tag only vs package/container) for this scaffold? — Defines release hygiene scope.
- [ ] What mandatory release metadata is required (versioning policy, changelog, provenance)? — Needed for auditability.
- [ ] What is the required CLI error/exit-code contract for production automation? — Needed for reliable orchestration and monitoring.

## vibetrading_v2_multistrategy_execution_plan - 2026-02-21
- [ ] Should strategy type be stored in `StrategyMeta` or a separate `StrategyBundle` field? — Determines loader/router contract shape.
- [ ] Is strategy-declared type authoritative, or may CLI override it? — Prevents ambiguous routing behavior.
- [ ] What exact per-type strategy outputs are required (orders vs weights vs multi-leg intents)? — Needed for engine interfaces and tests.
- [ ] For arbitrage, what timestamp alignment policy is required (exact match vs tolerance; if tolerance, what value)? — Defines correctness and reject conditions.
- [ ] For arbitrage, what is leg failure policy (stop/cancel/unwind)? — Critical for execution safety.
- [ ] What phase-1 support level is required for KIS and Bybit (full adapter vs explicit scaffold)? — Prevents scope explosion.
- [ ] Which broker+mode combinations are mandatory in this milestone? — Needed for concrete acceptance criteria.
- [ ] May V2 execution contract be extended for ordered/batch leg execution? — Impacts feasibility of arbitrage guarantees.
