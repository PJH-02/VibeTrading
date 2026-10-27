# CAP-02 — bounded native recovery sources, second isolated component
Ubuntu/Linux. Read parent CAP-02-reconciliation.md and primary CAP-02-api-evidence.json.
Base HEAD514f9c1/full SHA dispatch. WRITE ONLY new src/execution/reconciliation_sources.py,
src/execution/brokers.py ONLY Kiwoom new execution_fills read method, new
 tests/refactoring/test_cap02_sources.py. No portfolio/core reconciliation edits.
No settings/env/cache/credential discovery, new signer/SDK/full client, schema, strategy,
Owner mutation, runtime/default activation, package/network/account/Git/status edits.
Use passed existing native adapter instances, existing auth/signatures/transports,
normalize_private_event/history/account methods. Do not import archived namespaces.

Pin NativePollingSource(adapter, bindings, *, identity_verifier, clock_ns,
max_pages=32,max_rows=4096,max_document_bytes=1048576,poll_interval_ns=1000000000,
max_backoff_ns=30000000000). Exact supported classes: existing five Perp adapters,
BinanceBrokerAdapter Spot, KiwoomBrokerAdapter. Bind source scope_key to adapter.scope.key
(check exact mutable identity after every await; no re-labelling). bindings explicitly
list symbol/instrument_key/native_order_id/client_order_id/day as appropriate, max count
bounded; no config import/discovery. Product/env/account/symbol/native ID validation
BEFORE I/O. identity_verifier(account_facts) is an explicit trusted Owner-supplied
synchronous checker, no truth inferred from configured account alias or caller action.
Reject missing checker/async result; callback/source identity changes fail closed.
Production account proof/permissions still CAP03; local fixtures use synthetic checker.

API async authenticate() reads actual native account_state via existing adapter (no
new private auth client); subscribe() establishes the explicit bounded POLLING mode,
not a fake WS subscribe ACK; return receipt with scope_key,success=True,mode=
BOUNDED_REST_POLL and explicit local/proof limits. These callbacks can feed
ReconciliationSession.start. This selected implementation uses validated bounded REST
polling under ARCH-VENUE-EXT private_ws OR bounded polling; no claim venue lacks WS
or native WS auth has been tested. Keep adapter.normalize_private_event path available
via ingest_frame(session,frame,generation) for an already-owned authenticated private
session (caller/driver owns socket/auth; never auto-create a second venue client).
Auth/subscription lifecycle generic core remains mandatory; real WS auth/account proof
is EXTERNAL_ONLY, new risk until complete fresh baseline0. Old connect-only ready not
accepted. No background task/client on import/constructor. No mutation method on source.

async history(start_ns,end_ns) -> {scope_key,fills,funding,complete,start_ns,end_ns}.
Call original Perp fills AND funding per exact symbol, iterate native cursor until
complete with finite byte/row/page/cycle/window/scope proof. No page skip or retention
claim from empty response, unsupported history blocks. Return native normalized rows,
kind='fill'/'funding', scope_key exact; include native event_id, symbol, order_id,
client_order_id when present, quantity/price/fee/fee_currency or amount/currency.
Map symbol only through explicit instrument keys for projection (never infer same ticker
product). Preserve existing signs/IDs/timestamps and fee/funding conventions, no new math.
Cash funding NOT_APPLICABLE, no fake margin/funding0. Spot original fills returns raw
myTrades with id/orderId/qty/price/quoteQty/commission/currency; normalize native ID,
explicit source scope, native commission and identity, no fake history complete when
API is only recent retrieval. Positive completeness requires explicit query coverage
and exhaustion, keep source original retention limits; no absent order=>reject.

Kiwoom new execution_fills(symbol,day,order_id=None) reuses _paged_post and native
_post_api only, no auth/cache or other classes changed. Source API kt00009 POST
/api/dostk/acnt (official evidence), body ord_dtYYYYMMDD,stk_bond_tp1,mrkt_tp0,
sell_tp0,qry_tp1,dmst_stex_tpKRX,optionalexactstk_cd. Table acnt_ord_cntr_prst_array.
Filter exact symbol/native order ID locally, no fr_ord_no target-as-cursor; pages
complete or return INCOMPLETE_HISTORY. Only actual execution rows: cntr_no exactly
7digits nonzero, ord_no7digits, qty/price positive exact whole units, no credit,
modification/cancel/nonexec rows misread as fills, io_tp_nm exact cash buy/sell.
Native fill identity namespaced day/order/cntr_no (NOT timestamp/hash), same ID conflicts
must fail. cntr_qty belongs to that native execution ID; dedup per record, NEVER
ka10076 cumulative quantity as perfill. Fees aren't in kt00009: preserve fee=None,
fee_known=False; ka10076 day fees remain separate vendor/day facts for CAP03, no
fabricated perfill0 or notional from last-price*cumulative. Original CAP01 .fills
semantics remain unchanged. Query whole bounded known order day; if requested window/
retention can't prove complete history, block. Native times cntr_tmHH:mm:ssKST are
provenance with1-second precision, not unique IDs. No guessing native ID from client.
Unknown native ID leaves UNKNOWN and no automated resubmit.

async account_snapshot() -> {scope_key,account,positions,orders,complete,
observed_ns,identity_verified}. Call original account/positions/open orders, and
explicit query for ALL bound unresolved orders (absence from open list NOT terminal).
Perp maps via original methods, Spot balance facts must remain asset free/locked and
native order/executedQty; do not claim actual permissions. Source clock same-process
available time not confused with exchange timestamp. Map factual positions to exact
projection instrument keys only; foreign/unexpected symbols gap, never ignore risk.
Query order facts returned kind='order' status common OPEN/PARTIAL/FILLED/
CANCEL_PENDING/CANCELED/REJECTED/EXPIRED/UNKNOWN with raw native evidence and only VERIFIED
cumulative_quantity. Terminal proof requires explicit native status+quantity evidence,
not a default native0 field where raw cumulative is missing. Late fill after cancelACK
remains fill. Kiwoom full cancel only explicit 취소확인 linked ori_ord and confirmed
remainder, combined with complete native execution history; no terminal inference from
empty rows/remaining0 alone. For unclear fields return UNKNOWN/terminal_proven=False.
Account identity checker sees detached actual facts; input attestation not native proof.
No price/holdings difference fills. Bounded freshness and recovery windows, record
backoff on failures; poll_once(session,start_ns,end_ns) enforces interval/backoff and
calls session.reconcile with this source (read-only retries bounded). stop cancels
only owned polling/driver tasks, leaves query methods callable (generic session gate).

Public tests named test_cap02_sources.py only, use existing real Perp/Broker classes
with scripted transports (all modes strings synthetic, no WAN). At least two concrete
Perp classes through actual reads/normalizer; native Spot and Kiwoom kt00009 ID rows,
complete multi-page/timeout/missing/cycle/scope/window guard, per-source independence,
unknown fee keptunknown, partial2/5 cancelACK late1/5 reconcile3/5 existing ledger via
core later integration; auth/poll not fake WS handshake, bounded rate/backoff and no
new risk mutation transport calls. Report actual exits/files/provenance and unproven
native stream/fee/permissions, no self-acceptance.

Shared component wire contract (lead clarification): event scope field is scope_key
(existing native perp shape), exactly equal to session.source_scope_key. Fill fields
kind/event_id/order_id/client_order_id(optional)/symbol/quantity/price/fee/fee_currency,
fee_known optional exact bool; fee None means unknown and blocks economic readiness.
Order fields kind/order_id/client_order_id(optional)/symbol/status/terminal_proven/
cumulative_quantity; no terminal without explicit quantity/native proof. Funding
kind/event_id/symbol/amount/currency. Bind symbol to ledger immutable instrument key,
never a guessed same-ticker product. History dict fields scope_key,fills,funding,
complete,start_ns,end_ns. Snapshot fields scope_key,account,positions (exact instrument
key→quantity),orders (normalized order list),complete,observed_ns,identity_verified.
Auth/subscribe callbacks may return exact True for explicit trusted local contract
fixtures or dict with success is True AND exact scope_key; no arbitrary truthy ACK.
Polling source returns the dict receipt with mode BOUNDED_REST_POLL; it does not fake
WS auth/subscription. Snapshot readiness only authorizes PAPER projection freshness;
never actual native account/production order permission. Bindings may have no native
ID while ACK lost; client-ID lookup only if native API supports it, otherwise UNKNOWN.

Lead verified Spot recovery refinement (2026-10-28): the official account trade list contract permits explicit symbol+fromId. Source may call the unchanged native `.fills(from_id=0)` and require page_complete=True, strictly monotonic nonnegative trade IDs, bounded row/document/page costs and exact requested-window filtering. This proves only that explicit from-origin exhausted query under the documented API; default/recent-only queries remain incomplete. Actual native query/account/permission verification remains external, and non-settlement commission is rejected by existing PortfolioLedger. No new SDK/client/signer or original broker method changes.
