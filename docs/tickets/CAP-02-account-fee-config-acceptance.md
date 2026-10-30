# CAP-02 amendment — account-config fee policy — ACCEPTED_LOCAL

Date:2026-10-28. Lead independent acceptance:PASS. Target:Ubuntu/Linux.
Base:`c5d51f79c2f9dc046ce6abd55daf408dbd32bedc`. Source commit:`2e642aa1fe06398e308d922ae04ec8c83217f3a2`.
Receipt:`/home/pjh/.local/state/codex-opencode/runs/CAP-02-fee-config-20261028/CAP02-fee-config.acceptance.json`. SHA-256:`27879bd4080c837db8dbb4857ed5d5750b233c839601d26d7815af4ef46fd01c`.
Original CAP02 acceptance receipts remain historical evidence; this human-directed amendment supersedes their broker-fee dependency.

## Confirmed user policy

User supplies account fee rates later in config. Configured rates drive pre-order capital reservation, PAPER trade costs and per-strategy cash/P&L attribution. Broker fee reception, subscription, new queries and broker-reported cost reconciliation are excluded. A valid configured account rate makes missing native fee fields irrelevant to configured cost readiness. Order/position/session completeness and safety gates remain required.

## Config and entry points

See `config/account_fees.example.json`. This is an illustration, not an actual account profile or default. Rate is a fractional Decimal string: example `"0.00015"` represents0.015%; use the user's own value. Exact venue/account/product/environment binds each policy; quoted `"0"` is allowed only as explicit input. Missing/malformed/duplicate/nonfinite/float rates, wrong scope and exceeded resource bounds reject. No rate lookup or config auto-discovery occurs.

Load a chosen file through `runtime.local_config.load_account_fee_policies(path)`, select `policies[ledger.scope.key]`, and pass that object as `account_fee_policy` to `PortfolioLedger.admit_order` or `PaperExecution.submit`. Both bind rates/provenance into the same immutable ProductSpec before existing reservation/fill calculations. LocalRuntimePlan's feed configuration is unchanged; do not add arbitrary account fee keys to its existing schema. Full production common-Owner/runtime activation remains its own tickets.

Reconciliation recovers policy from the admitted immutable product rules. Each Kiwoom fill uses actual native quantity/price × product multiplier × configured rate, with ACCOUNT_CONFIG basis and exact128-digit arithmetic. Incoming broker fee fields do not override this model. Costs flow through the existing single ledger and existing strategy cash attribution exactly once. No real settlement rounding or tax rules are invented; these are configured model costs, not broker bill amounts.

## Independent validation

| Check | Result |
| --- | --- |
| Full Linux regression |2707 passed,exit0; existing2 live-data cases deselected |
| Separate lease suite |25 passed,exit0 |
| Fee/ledger/PAPER/CAP02 focus |100 passed,exit0 |
| Offline wheel/outside-repository imports |build/import exit0;9 canonical modules |
| Wheel/source equality |all146 source files byte-identical |
| Scope/old-test/schema/whitespace proof |PASS;141 original source files and171 original tests unchanged |
| Accepted status/schema/docs contracts |78 passed,exit0; DAG projection valid |

Synthetic1% arithmetic:5shares×100 reserves505. Partial2shares +late1share costs3; starting cash10000 becomes9697, position3. Confirmed remaining cancellation and real close/reopen preserve this exactly once; configured recovery reaches ready without a broker fee. Two PAPER strategies are charged2 and1 respectively, with no duplicate fee subtraction. Ambient small Decimal precision, config/policy collisions and actual outer-COMMIT failure are covered. Native Kiwoom fixture reuses the existing kt00009 order-execution read, with no fee collection endpoint or subscription.

Only the pre-existing external Babel `cgi` warning remains. Worker:gpt-6-luna in an isolated clone after same-ticket external allowance exhaustion; Codex reviewed source, corrected bounded findings and independently executed acceptance. Frozen hashes, exact commands, real exits and private logs are in the receipt directory.

## Preserved boundaries

No broker fee receiver/query/subscription was added; native brokers and recovery source files are unchanged. No live orders/cancels, production account calls, server changes, pushes or deployments occurred. The actual user account rate is not set by the example. Old committed unknown-cost fills are not silently repriced/migrated. The immutable04 manifest and original STR02 packet hashes are unchanged. CAP03's other account/margin/ownership work remains unaccepted.
