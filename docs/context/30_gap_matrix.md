# 30) V2 Gap Matrix (Requirements vs Observed Current State)

| Requirement | Current Status | Evidence (file/symbol) | Gap | Proposed Fix (high-level) | Priority |
|---|---|---|---|---|---|
| 1-min OHLCV only (no orderbook) | **Partially met** | `Bar` is OHLCV (`vibetrading_v2/core/types.py:10-17`), but `Timeframe` permits non-1m values (`vibetrading_v2/strategy/bundle.py:10,17`); sample strategy uses `1m` (`vibetrading_v2/strategies/my_strategy_a.py:49-53`); no orderbook symbols found (`rg -n "\borderbook\b|\bdepth\b|\bbids?\b|\basks?\b" vibetrading_v2 cli -S`). | No hard `1m`-only enforcement at schema/load boundaries. | Restrict timeframe schema to `Literal["1m"]` (or validate at load time) and document/validate OHLCV-only adapter contracts. | P0 |
| Parquet deterministic backtest harness | **Partially met** | Parquet adapter exists and sorts by timestamp (`vibetrading_v2/data/parquet_source.py:56-57`); deterministic test-style flow exists (`vibetrading_v2/tests/test_runner_flow.py:63-110`); runner currently only returns in-memory result (`vibetrading_v2/runner/runtime.py:23-30,87-94`). | Missing deterministic artifact harness (orders/positions/pnl/risk outputs + repeatable hash checks). | Add backtest app/harness that writes canonical artifacts and compares hashes (same input/config => same hash). | P0 |
| Paper broker with same order lifecycle shape as live | **Not met** | Paper/live adapters both return immediate `Fill` (`vibetrading_v2/execution/paper_adapter.py:18-25`; `vibetrading_v2/execution/live_adapter.py:22-29`); core order contract is minimal intent only (`vibetrading_v2/core/types.py:21-25`). | No shared broker lifecycle state model (Created→Submitted→Accepted/Rejected→PartiallyFilled→Filled/Cancelled/Expired). | Introduce broker-agnostic order/state entities in core and require both paper/live adapters to implement same lifecycle transitions. | P0 |
| Live/paper safety gates (`LIVE_API=1` and `CONFIRM_LIVE=YES`) | **Not met** | Live wrapper is just thin delegation (`vibetrading_v2/runner/live.py:8-14`); CLI live entrypoint has argparse but no env gate checks (`cli/live.py:30-47`); env search returns no hits (`rg -n "LIVE_API|CONFIRM_LIVE" vibetrading_v2 cli -S`). | Live path can run without dual explicit opt-in gates. | Add explicit gate check in live app/entrypoint (hard-fail or force paper when either gate missing). | P0 |
| Engine split: `RebalancingEngine` vs `SingleStrategyEngine` (Arb stub only) | **Not met** | Single shared `run_mode` orchestrates all modes (`vibetrading_v2/runner/runtime.py:53-94`); module inventory shows no rebalancing/single/arbitrage engine files (`find vibetrading_v2 -type f -name '*.py' | sort`; `rg -n "RebalancingEngine|SingleStrategyEngine|arbitrage|Arb" vibetrading_v2 -S`). | Required engine-family separation is missing; arbitrage stub interface absent. | Add explicit `RebalancingEngine` + `SingleStrategyEngine`; add arbitrage interface stub only (no live WS dependency). | P1 |
| Bar semantics: timestamp meaning/timezone/bar-close rules + missing/dup/out-of-order handling | **Partially met** | `Bar.timestamp` exists (`vibetrading_v2/core/types.py:12`); Parquet source sorts timestamps and enforces datetime (`vibetrading_v2/data/parquet_source.py:56-67`); tests verify sorting only (`vibetrading_v2/tests/test_parquet_source.py:26-55`). | No canonical statement of timestamp meaning (open vs close), timezone policy, or deterministic missing/duplicate/out-of-order policy. | Define canonical semantics doc + enforceable normalizer in data adapter pipeline; add invariants tests. | P0 |
| Order state machine + idempotency | **Not met** | `OrderIntent` has only `symbol/side/quantity` (`vibetrading_v2/core/types.py:21-25`); execution path directly submits every order produced by strategy (`vibetrading_v2/runner/runtime.py:74-77`); idempotency search finds no implementation (`rg -n "idempot" vibetrading_v2 cli -S`). | No idempotency keys, order IDs, state transitions, or dedupe checks. | Add `OrderRequest` with idempotency key + `OrderRecord` lifecycle in core; persist/check keys at broker adapter boundary. | P0 |
| Kill-switch policy | **Partially met** | Risk schema includes `kill_switch_dd` (`vibetrading_v2/policies/risk.py:15,42-46`; `vibetrading_v2/strategy/bundle.py:34`), but runtime loop has no kill-switch check path (`vibetrading_v2/runner/runtime.py:70-83`). | Kill-switch is configuration-only; no enforcement mechanism in runtime. | Add runtime risk monitor that halts/cancels order flow when kill-switch threshold triggers. | P1 |
| Adapter boundaries (core import bans) | **Partially met** | Core files currently use stdlib/internal imports only (`vibetrading_v2/core/types.py:5-7`; `vibetrading_v2/core/ports.py:5-7`); strategy sandbox deny-list includes network/DB libs (`vibetrading_v2/strategy/sandbox.py:23-42`); required `ports/adapters` directory split is absent (`find vibetrading_v2 -maxdepth 1 -type d | sort`). | Boundary rule is not encoded as required package structure and is only strategy-scoped, not full-project scoped. | Refactor to explicit `ports/**` and `adapters/**` packages + add global import-boundary tests/lint checks. | P1 |
| Backtest report outputs (`orders/positions/pnl/risk` artifacts) | **Not met** | Current `RunnerResult` has only mode/strategy/counters/fills/policies (`vibetrading_v2/runner/runtime.py:23-30`); runtime only logs summary text (`vibetrading_v2/runner/runtime.py:84-86`); backtest CLI prints summary only (`cli/backtest.py:45-49`). | Missing persisted artifact outputs for orders/positions/pnl/risk. | Add report writer layer and deterministic artifact schema for backtest runs (e.g., parquet/csv/json + hash manifest). | P0 |

## Evidence
### Commands run
```bash
find vibetrading_v2 -type f -name '*.py' | sort
rg -n "LIVE_API|CONFIRM_LIVE" vibetrading_v2 cli -S
rg -n "idempot|Created|Submitted|Accepted|Rejected|PartiallyFilled|Filled|Cancelled|Expired|order_state|state machine" vibetrading_v2 cli -S
rg -n "RebalancingEngine|SingleStrategyEngine|arbitrage|Arb" vibetrading_v2 -S
rg -n "\borderbook\b|\bdepth\b|\bbids?\b|\basks?\b" vibetrading_v2 cli -S
rg -n "kill_switch|drawdown|max_drawdown" vibetrading_v2 -S
rg -n "positions|pnl|risk|artifact|hash|write|to_parquet|to_csv|json" vibetrading_v2/runner vibetrading_v2/execution cli -S
```

### Key command excerpts
- `rg -n "LIVE_API|CONFIRM_LIVE" ...` returned no matches.
- `rg -n "idempot" ...` returned no matches.
- `rg -n "RebalancingEngine|SingleStrategyEngine|arbitrage|Arb" ...` returned no matches.
- `rg -n "kill_switch|drawdown|max_drawdown" ...` only hit policy/schema definitions, not runtime enforcement.
- `find vibetrading_v2 -type f -name '*.py'` showed no dedicated `ports/`, `adapters/`, or `apps/` modules.

### Concrete code references
- `vibetrading_v2/core/types.py:10-34`
- `vibetrading_v2/strategy/bundle.py:10-35`
- `vibetrading_v2/data/parquet_source.py:56-67`
- `vibetrading_v2/execution/paper_adapter.py:18-25`
- `vibetrading_v2/execution/live_adapter.py:22-29`
- `vibetrading_v2/runner/runtime.py:23-30,53-94`
- `vibetrading_v2/runner/live.py:8-14`
- `vibetrading_v2/policies/risk.py:11-47`
- `vibetrading_v2/strategy/sandbox.py:23-42,66-109`
- `cli/backtest.py:45-49`
