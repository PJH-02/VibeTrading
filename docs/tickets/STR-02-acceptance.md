# STR-02 — ACCEPTED_LOCAL

2026-10-28. Lead independent acceptance: PASS. Target: Ubuntu/Linux.
Base: `52ade034c3c3b2a234743911ea3a021e1b714411`. Source commit: `f9e830c67330de171647f746d4a25a4e8f70fd22`.
Receipt: `/home/pjh/.local/state/codex-opencode/runs/STR-02-resume-20261028/STR02.acceptance.json`. SHA-256: `b372c7dfa455085a31ac06b5e4320c11255baf16e4c3b8b89819092715e6e932`.

## Implemented contract

`execution.trading_contracts` supplies frozen `StrategyActionPlan`, `StrategyOrderEvent` and `StrategyOrderContext` around the existing `ExecutionAction`/`OrderIntent`/`AccountScope`. Explicit `ACTION_PROTOCOL_VERSION=1` adds synchronous `on_order_event(event, context)` on the actual source instance. Existing `on_event`/Signal jobs remain compatible; legacy runners that cannot consume plans reject opt-in sources at startup.

Portfolio feedback is opt-in, transactional and bounded in the existing receipts table. PAPER dispatch reuses existing claims, risk checks, matcher and coupled checkpoints. Each source receives its own immutable order facts, fill/fee facts, current balances/reservations and separate physical/virtual inventory, cursor and quality. Invalid ownership/mode/config, unconfirmed replacement, excess remainder and account-wide UNKNOWN new risk reject. No automatic FOK/IOC policy substitution occurs.

The explicit crypto fixture emits maker bid/ask GTC quotes, consumes partial and cancel-pending late fills, chooses cancel/reprice after terminal confirmation, restores its state/cursor, and explicitly chooses IOC after local FOK rejection. Actual matcher fills and original source traces are tested.

C3/KP/KD callbacks execute through the existing ETF loop and journal. All six source/Mock/Mock2 combinations use the actual `KisCashBroker` class with a credential-free scripted transport. Native cancellation ACK stays pending; only confirmed cancellation permits the source's current factual remainder. Ordered queued fill receipts survive restart without coalescing or duplicate actions. The five-second decision stays in source code. PAPER ETF callbacks preserve facts/cancel behavior while the original source `on_event` retains market-remainder cadence and reference-data selection.

## Independent verification

| Check | Result |
| --- | --- |
| Full Linux regression | 2756 passed, exit 0; existing 2 live-data cases deselected |
| Guarded ownership/lease suite | 25 passed, exit 0 |
| Final STR02 scenarios/adverse tests | 49 passed, exit 0 |
| Offline wheel and outside-repository imports | build/import exit 0; 12 canonical modules |
| Wheel/source equality | all 148 Python source files byte-identical |
| Preserved baseline | 172 existing test files and 134 unaffected source files byte-identical |
| Scoped AST/whitespace proof | unrelated original methods preserved; no new diagnostics |
| Status/schema/docs contracts | 78 passed, exit 0; DAG projection valid |

Source and test hashes, real exits, commands, scope proofs, worker snapshots and integration repair evidence are private in the receipt directory. Two new test files had only trailing empty lines removed; final regression ran afterward. Existing Timefolio EOF formatting was retained and recorded separately. Touched CRLF sources preserve their original line endings via exact-path attributes. The sole warning is the pre-existing external Babel `cgi` deprecation.

## Boundaries

Accepted account-config fee/reservation/PAPER/P&L policy remains unchanged. No broker fee receiver, subscription, additional fee query or fee-report reconciliation was added. Existing KIS conservative journal cost basis is labelled explicitly; missing native fee/source timestamps remain unavailable. Physical KIS inventory absent from the journal projection remains UNAVAILABLE.

This acceptance covers LOCAL/PAPER/FAKE behavior. Production common Owner routing, permissions/margin and activation remain later tickets. Python callback return-time checks are cooperative. No live account call, live order/cancel, server change, push or deployment occurred.

Implementation used the previously authorized `gpt-6-luna` fallback after STR02's external delegation allowance was exhausted. The latest user preference is recorded: check other OpenCode/FreeLLMAPI models first and use Luna when those routes cannot be used; preserve exact model IDs, attempt limits and partial-work inspection.
