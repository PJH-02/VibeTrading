# CAP-02 acceptance — ACCEPTED_LOCAL

Date: 2026-10-28. Target: Ubuntu/Linux. Lead acceptance: PASS.
Base: `514f9c1f1b7ebb94ced4310bbcecedb1f6a22e01`. Accepted source: `f4f7c51c91c421cab613d20e5df2bcefd5a4da29`.
Private receipt: `/home/pjh/.local/state/codex-opencode/runs/CAP-02-20261028/CAP02.acceptance.json`.
Receipt SHA-256: `a042c3c27a67a30f35124833cfeaa25caa0d8ae527c4e6b296517cd0c8b5063f`.

## Implemented and independently verified

- A bounded account/source-bound reconciliation session authenticates, subscribes, rewinds history, checks fresh account/order/position evidence, and exposes committed factual events. Restart/gap/timeout/stale generation closes that account's PAPER projection gate while native queries remain available.
- The existing PortfolioLedger remains the sole financial writer. New metadata CAS and @private gap methods use existing tables. Financial facts and session events commit atomically; an injected outer COMMIT failure rolls both back without event/version leakage.
- Duplicate native execution IDs, private/REST overlap, lost ACK client/native binding, late fills, terminal precedence, unknown orders, page loss/cycles, missing positions, snapshot races and bounded buffers fail closed. Cancel pending survives delayed ACK and partial fills; a cancel ACK never releases reservation.
- Native Binance/Bybit adapters plus the real PAPER ledger prove fee/funding once and restart. Kiwoom native kt00009 execution IDs plus linked kt00007 remaining-cancel proof prove 2/5 → cancel pending → late1/5 → remaining cancel → restart exactly3/5. No fill is inferred from holdings or prices.
- Spot uses explicit fromId=0 with exhausted bounded native pagination, not recent-only history. Hyperliquid uses explicit origSz/sz cumulative proof; incomplete identified open-order hints require the subsequent exact native query. Foreign/conflicting order IDs block. Existing GRVT frame generation/sequence evidence is enforced.
- The account lease adds only canonical `kiwoom` to the existing allowlist. All original broker methods (65) and portfolio methods (48), their SQL schema/math, 140 other source files and all169 original tests are preserved. KIS Mock/Mock2 and protected Golden tests pass. The immutable final04 manifest hash is unchanged.

## Independent validation

| Check | Result |
| --- | --- |
| Linux full suite | 2688 passed, exit0; existing2 live-data cases deselected |
| Separate lease suite on adopted commit | 25 passed, exit0 |
| Focused reconciliation/broker/perp/ledger suite | 136 passed, exit0 |
| Offline wheel build and outside-repository imports | exit0;12 canonical modules |
| Wheel/source comparison | all145 source files byte-identical |
| Scope/AST/old-test/whitespace verification | PASS; no new diagnostics |
| Accepted status/schema/docs contracts | 78 passed, exit0; DAG projection valid |

The only full-suite warning remains the pre-existing external Babel `cgi` deprecation.
Exact commands, real exit records, frozen source hashes and private logs are in the receipt directory.
Wheel SHA-256: `96f7b157d03010e531b7b695c2fc56848844ae5d4762a0e68c03eded7c62b916`.

## Worker and review provenance

GLM4.5 returned relay HTTP502; direct OpenCode DeepSeek timed out124; Gemini failed relay inference. All three left no source edits. The user-authorized gpt-6-luna native fallback implemented isolated bounded components and adverse tests. Codex independently reviewed the actual code/diff, corrected bounded findings, promoted regressions, and executed acceptance commands. Catalog presence and worker success statements were not acceptance evidence.

## Explicit remaining boundaries

- PAPER projection freshness only. Native account identity/permissions, private WS wire auth and production activation remain EXTERNAL_ONLY and EXEC-01/CAP-03
- Kiwoom kt00009 native execution IDs/quantities and kt00007 cancellation proof are implemented, but per-execution fee is absent. fee=None/known=false stays unknown; labeled provisional PAPER fee zero never clears @private. CAP-03 cost attribution remains
- Selected bounded REST polling uses existing native clients/signers and supports already-owned driver frames. No second WS client, new economic policy or live permission
- Existing PortfolioLedger schema/math/methods, KIS Mock/Mock2, C3/KP/KD, five native Perp adapters/signers and TOSS DATA_ONLY remain preserved

Actual external account calls/orders/cancels:0. Server changes/push/deploy:0. STR-02 state and four pre-existing packets are preserved. CAP-03 and CLN-02 are ready; this receipt does not accept their work.

API evidence: [Kiwoom executions](https://openapi.kiwoom.com/m/guide/apiguide/08/kt00009), [Kiwoom order/cancel facts](https://openapi.kiwoom.com/m/guide/apiguide/08/kt00007), [Binance Spot account trades](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/account), [Hyperliquid order status](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint). Frozen evidence hashes and selected field contracts are in `CAP-02-api-evidence.json`.
