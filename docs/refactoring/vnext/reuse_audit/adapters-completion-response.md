# Read-only Perp adapter completion audit

**Not an acceptance.** This reviews only the frozen excerpt and caller inventory named in the ticket. `code_complete` is `False` by construction (`Capability` defaults, lines 75-90; `capability_for`, lines 115-121), so mapping presence is not venue-runtime proof. No Bash, edits, tests, agents, or external checks were run. Test result: **none run, none claimed**.

## Shared dispatch seam and injection

All adapter network activity funnels through `_Adapter._request` (lines 217-268). It supplies the one-use mutation permit at the transport’s actual `before_send` seam, applies a timeout, and converts a post-permit failure into `OutcomeUnknown` (lines 244-257). The `Transport` protocol (lines 131-136) declares `request`; Arcus additionally calls `transport.rpc` through the `rpc_message` branch (lines 248-250; required at lines 862-863). Transport is injected into every constructor through `super().__init__` (lines 193-199) and through `make_perp_adapter` (lines 1308-1313). There is no concrete HTTP/SDK transport, connection, subscription, reconnect, or account-owner integration in this module.

## Five requested capabilities

| Venue | Metadata | Submit | Cancel | Query | Fills | Private stream | Positions | Wallet / funding | Reconcile | Environment |
|---|---|---|---|---|---|---|---|---|---|---|
| **Binance** | `instrument_metadata` L386-395: public exchangeInfo, active `PERPETUAL`, filters. | `submit_with_permit` L423-433; signed `_call` L371-384; LIMIT `/fapi/v1/order`. | `cancel_with_permit` L435-439; acknowledges only, no terminal proof. | `order_status` L441-447; 4xx becomes `UNKNOWN`. | `fills` L457-469; `/fapi/v1/userTrades`, 7-day window, `fromId` paging. | `normalize_private_event` L481-494: `ORDER_TRADE_UPDATE` normalization only. No stream transport. | `positions` L406-411; rejects hedge/isolated. | `account_state` L397-404; `funding` L471-479 via income. | Inherited `_Adapter.reconcile` L344-354. | `base` demo/live L369; no sandbox/live verification flag. |
| **Bybit** | `instrument_metadata` L541-549: public linear instruments, active check. | `submit_with_permit` L579-590; signed POST `/v5/order/create`. | `cancel_with_permit` L592-596. | `order_status` L598-604: realtime then history, else `UNKNOWN`. | `fills` L639-640 with `_history` L617-637; execution list. | `normalize_private_event` L645-653: execution/order frames only. No stream transport. | `positions` L562-568; rejects hedge index. | `account_state` L551-560; `funding` L642-643 via transaction log. | Inherited L344-354. | `base` testnet/live L504. |
| **GRVT** | `instrument_metadata` L688-695: `_USDT_Perp`, `PERPETUAL`, ORDERBOOK. | `submit_with_permit` L724-750; `GrvtSigningComposition.prepare` L181-189; signed `create_order`. | `cancel_with_permit` L752-757. | `order_status` L759-766; empty result becomes `UNKNOWN`. | `fills` L796-800 with `_history` L779-794; fill history. | `normalize_private_event` L802-809: `v1.order`/`v1.fill` only. No stream transport. | `positions` L705-708. | `account_state` L697-703; `funding` L799-800 via funding history. | Inherited L344-354. | Domains/chain IDs L667-668, L182, L732; no live verification. |
| **Arcus** | `instrument_metadata` L885-896: active USD perpetual; explicit USDG settlement and valuation flag. | `submit_with_permit` L951-981; `ArcusSigningComposition.prepare` L834-837; signed WS RPC. | `cancel_with_permit` L983-1003; composition cancel L839-853. | `order_status` L1005-1021; paginated history, `UNKNOWN` if exhausted. | `fills` L1053-1057 with `_history` L1038-1051. | `normalize_private_event` L1059-1082: order/fill/funding/account and gap frames; **normalize-only**, no socket lifecycle. | `positions` L907-917; account identity checks. | `account_state` L898-905; `funding` L1056-1057. | Inherited L344-354. | Testnet/live host L867-868; signer testnet check L829-832. |
| **Hyperliquid** | `instrument_metadata` L1129-1146: first-dex universe, delisting check, size/price mapping. | `submit_with_permit` L1210-1227; `_exchange` L1173-1208; official SDK signing. | `cancel_with_permit` L1229-1237. | `order_status` L1239-1247; identity check, else `UNKNOWN`. | `fills` L1282-1283 with `_history` L1267-1280; retention-limited. | `normalize_private_event` L1288-1305: fills/fundings/order updates and gaps; **normalize-only**. | `positions` L1157-1161. | `account_state` L1148-1155; `funding` L1285-1286. | Inherited L344-354. | Testnet/live base L1117; `is_mainnet` L1190. |

`reconcile` is not atomic: it serially reads account, positions, orders, fills, and funding (L344-354). It does not enforce a cross-endpoint snapshot boundary or consume Arcus sequence numbers, so concurrent activity can make the returned bundle internally inconsistent. It correctly says absence does not resolve `UNKNOWN`.

## Direct callers and runtime distinction

`perp_direct_callers.md` contains **25 static AST records, all from `tests/refactoring/test_perp_execution.py`**:

- `HttpResponse` at lines 40 and 197; `CapabilityBlocked` at 54; `PerpScope` at 58, 113, 116, 121, 366, 501.
- `GrvtPerpAdapter` at 107; `BinancePerpAdapter` at 134, 158, 184, 201, 212; `make_perp_adapter` at 141.
- `BybitPerpAdapter` at 232, 252, 306, 317; `GrvtSigningComposition` at 292.
- `ArcusSigningComposition` at 361; `ArcusPerpAdapter` at 367.
- `HyperliquidSdkSigningComposition` at 500; `HyperliquidPerpAdapter` at 502.

No `src/` caller is recorded. The inventory itself says “static, not deployed,” so these tests do not prove a deployed runtime, transport, or owner integration. Dynamic wiring through registries, factories, deployment entrypoints, notebooks, or imports is **UNKNOWN** from the permitted excerpt. That is not evidence that venue functionality is absent from the prototype.

The “real” adapter code is the request construction, validation, and frame normalization shown above. The tests use scripted transport/permit fakes according to the caller inventory. Existing legacy runtimes are only **composed** for signing: GRVT calls an injected adapter’s `prepare_order` (L187-189), Arcus calls an injected adapter’s `prepare_order`/`_signer` (L834-853), and Hyperliquid uses the official SDK signer (L1085-1099). None of those legacy runtimes is instantiated, connected, or exercised here.

## Duplication with TX06A-E/TX07A/B

The exact TX06A-E/TX07A/B texts are not in the allowed read set, so ticket-level duplication is **UNKNOWN**. The code-visible duplication/reuse points are:

1. **Signing/normalization reuse is already explicit** for GRVT and Arcus; Hyperliquid reuses the official SDK. These mappings should remain the reuse units.
2. **Wire-level duplication remains**: each adapter independently maps time-in-force, client-ID format, order/fill/funding fields, pagination, and environment URLs. This is the area to compare against the legacy broker tables before deduplication.
3. **Do not mandate new generic HTTP/SDK clients.** `_Adapter._request` is a safety seam, not a replacement venue client. Reuse the existing normalization/signing mappings; do not rebuild the venues.

No canonical-replacement claim is made. No code fix is proposed.

## Findings and evidence gaps

1. **Acceptance evidence is absent.** Every capability remains `code_complete=False`, `REQUEST_MAPPING_ONLY`, and `BLOCKED_UNTIL_OWNER_TRANSPORT_ACCOUNT_SIGNER_PROOF` (L85-87, L119-121). There is no sandbox/live proof, stream connectivity, owner integration, or reconciliation deployment proof.
2. **Permit one-use check has a concurrency race.** `before_send` checks `consumed` and the WeakSet, then calls `permit()`, then adds it (L232-242). Two simultaneous invocations can both pass the check; the WeakSet is also not synchronized. A buggy transport could therefore invoke the permit twice. This is not proven by a test run, but it violates the stated one-use intent under concurrency.
3. **Shared legacy signer state is mutated.** GRVT writes into `adapter.instruments` (L187); Arcus writes into `adapter.markets` (L836). Concurrent use of one legacy adapter across symbols/scopes can race or overwrite state.
4. **Binance error classification is incomplete.** `_request` converts every HTTP 4xx to `VenueRejected` (L266-267) before `_call` can inspect the Binance error code. The intended `OutcomeUnknown` codes `-1001`, `-1006`, and `-1007` (L381-382) are therefore only reachable when the transport reports a non-4xx status. If the venue reports those codes with HTTP 4xx, an indeterminate mutation would be misclassified as a rejection. Exact HTTP status evidence is not in the excerpt, so this is a concrete classification risk, not a proven venue defect.
5. **Binance post-dispatch normalization can raise the wrong error type.** `submit_with_permit` dispatches, then calls `_normalize_order` (L430-433), which requires `status`, `symbol`, `orderId`, etc. (L413-418). A successful but incomplete ACK can raise `KeyError` after submission instead of `OutcomeUnknown`.
6. **Transport interface is incomplete for Arcus.** The `Transport` protocol declares only `request` (L131-136), but `_request` supports `rpc` (L249) and Arcus requires it (L862). Any typed transport implementation must add an undeclared method.
7. **No proven secret leakage.** Credentials/session headers are retained in adapter fields (L368, L503, L666), and rejection messages do not echo signed bodies. However, `_request` re-raises raw transport exceptions with their cause (L252-255); no redaction layer exists for transports that include URLs or headers in exception text. This is a potential exposure, not observed leakage.
8. **No regression or test result can be established.** No baseline was supplied and no test was run. The caller inventory is static. Missing evidence includes transport concurrency tests, malformed-ACK tests, sandbox/live checks, private-stream reconnect/gap tests, and reconciliation consistency tests.

## GRVT `native_client_id` claim

**UNVERIFIED.** The permitted source does not contain `GrvtBrokerAdapter.native_client_id`. `GrvtPerpAdapter._client_id` requires canonical uint64 decimal text (L670-674), but that is this adapter’s own validation, not proof of a defect in the legacy adapter. No exact violated contract, failing input, or reproducer is supplied. Bit width or hashing alone is not proof. No fix should be applied on this evidence.

**Conclusion:** mapping coverage is present and function-cited; runtime, transport, stream, and owner acceptance remain unproven. Preserve existing normalization/signing reuse; do not rebuild venues or replace legacy mappings automatically.

AUDIT_COMPLETE
