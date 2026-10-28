# EXEC-01 — BLOCKED, reviewed implementation preserved

Source commit `cecc9e7a754268cc9aa7e19b4089a1e6d350b99b`. This ticket is **not accepted**. CAP-03 and CAP-04 are accepted; the requested three-ticket batch is 2/3 complete.

Private lead receipt `/home/pjh/.local/state/codex-opencode/runs/next-three-20261028/EXEC01/EXEC01.acceptance.json`; SHA-256 `dd48ba7801a0a72742dc51514e42baec52e0a5dfa2343f398fc114eba26902b2`.

Implemented and independently verified common account-owner dispatch, actual KIS C3/KP/KD Mock/Mock2 shared-journal integration, Kiwoom REST partial/cancel/late-fill/restart, Binance/Bybit/GRVT/Hyperliquid native submit permits, dummy module/config routing and Bybit probe/Timefolio fences. Native identity, account risk and reservations, immutable strategy-attributed cumulative feedback and atomic fill/outbox use the existing account ledger. Strategy-owned economics and account-config fees are preserved; broker-fee reception/query/reconciliation was not added.

Actual isolated strategy callback checkpoint/restore is bound to source/config/manifest/account and exact durable market+feedback cut. Missing/stale/corrupt checkpoints block new input; real child restore and roundtrip precede resumed callbacks. Strict legacy lease inputs remain compatible; explicit route environment normalization shares the old physical fence. Normal KIS instances always require the common owner independently of the legacy guard flag, and canonical direct broker mutations are fenced from construction. Unknown adapters fail closed.

Independent final source verification: **2887 full tests passed**, existing2 live-data cases deselected and Babel warning retained; **25 guarded lease tests passed**. Offline wheel build/import exit0; **149** Python sources match, **13** canonical imports outside the repository. All185 original tests and139 unaffected Python sources are byte-identical. Scoped CRLF attributes preserve existing KIS/runner bytes; diff-check including new tests is clean.

Remaining required acceptance:

- **Arcus NEW:** existing product metadata uses USD quote and USDG settlement/collateral, without established native denomination/conversion and complete margin facts. CAP03 correctly rejects new exposure. The Arcus test passing proves safe rejection and historical owned query/cancel/reconcile, **not successful NEW**. The five-perpetual submit requirement remains unmet. Official target exchange/API specification is needed; no parity or zero margin is invented and this code gap is not EXTERNAL_ONLY. The official address currently recorded in source (`https://docs.arcus.xyz/llms.txt`) could not be verified. A user clarification request for exact exchange name/document URL remains pending.
- **Final Docker path smoke:** CLI29.7.2 and Windows interoperability work, but the current daemon/backend API does not respond through either Windows context within12s; the public-source-only local build timed out120s. The Linux default socket is absent. Prior CAP03 image success does not certify this final source. No existing engine/container was restarted or changed.

External initial+two repair attempts were inspected; authorized gpt-6-luna fallback performed bounded implementation. Lead independent review found and repaired actual safety/regression failures before adoption. No live accounts/orders/cancels, installations, server changes, push, merge or deployment.

Current35-ticket counts:20 accepted,1 blocked,12 not started,2 external/not authorized. CAP05 remains dependent on EXEC01 acceptance.
