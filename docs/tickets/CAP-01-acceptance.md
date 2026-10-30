# CAP-01 local acceptance

PASS / ACCEPTED_LOCAL, 2026-10-27. Source commit `82128a3a00d615421eaa43d87d0283426d26cb09`.
Target Ubuntu24.04/Linux, Python3.12; no Windows/COM adaptation.

Existing five native Perp adapters/signing/nonce source remain unchanged. One generic
bounded HTTPS transport provides a synchronous one-use gate after connection waits;
Arcus composes the existing WS connection/lock/RPC instead of creating a new client.
Exact signed query bytes, origin/TLS policy, timeout/capacity/body/response bounds,
unknown-after-handoff and native broker scope at final dispatch were verified.

Existing Binance Spot and Kiwoom REST owners supply permitted mutation, exact native
identity lookup, open/fill/account facts and honest unknown/incomplete outcomes.
Kiwoom uses official kt10003 cancel (kt10002 modify), bounded continuation headers,
separate original/cancel IDs, and CANCEL_PENDING. Fees retain vendor/day provenance;
aggregate fills have no fabricated event ID/notional. Logical account_id is canonical
routing identity; alias is a label and native acctNo proof stays separate. The
identity_verified context marker is caller attestation, not authenticated proof.

| Evidence | Result |
| --- | --- |
| Full Linux suite | 2643 passed,2 existing live_data deselected |
| Guarded lease suite | 25 passed |
| Independent focused suite | 192 passed, including55 private cases |
| Positive native environment profiles | 28 cases:10 Perp,2 Spot,16 Kiwoom combinations |
| Offline wheel outside repository | 12 imports,143 source files byte-identical |
| Protected existing source | 139 files byte-identical;20 broker/RPC definitions AST-identical |
| Existing tests/Golden/auth helpers | Unchanged; KIS Mock/Mock2 preserved |
| Diff check / immutable04 | exit0 / unchanged |

One unchanged external Babel/cgi deprecation remains. Wheel SHA256
`11b64430ea787f082a89234df3ecb67c58452c220bffc18045113b1dda230aaa`. Exact private test/source manifests and actual exit logs
are included in the receipt. Initial private harness faults (certificate utility,
arrival order, incomplete synthetic ACKs and ambiguous query args) were corrected
against the published contract; original failure logs are retained. A final existing
VEN negative test exposed validation precedence; the source order was corrected and
the entire2643-case suite rerun successfully. No Golden assertion was weakened.

GLM4.5 returned relay502/no edits; direct DeepSeek timed out124 with3 scoped drafts;
MiMo repair2 timed out124/no edits. User-authorized GPT6 Luna completed broker and
transport components in separate checkouts. Codex independently reviewed/ran tests
and added the recorded small scope/code/error/TLS/format repairs. This is local
adapter-component acceptance, not all-required-route or production readiness.

CAP02 retains private session/polling/fill identity/dedup/durable reconciliation;
CAP03 retains account permissions/reservations and trusted market-reference facts;
EXEC01 retains Strategy→CommonOwner callbacks/old probe retirement. Spot market
orders requiring an unproved notional reference price fail closed; no price is
invented. Global release/live gates remain unaccepted. STR02 is preserved unfinished.
No actual account/trading API, orders/cancels, dependencies/install, runtime default
activation, server changes, push, merge or deployment occurred.

[Packet](CAP-01-reuse-integration.md) · [Verified API evidence](CAP-01-api-evidence.json)

Private receipt: `/home/pjh/.local/state/codex-opencode/runs/bypass-feed-venue-cap-20261027/CAP01.acceptance.json`.
Receipt SHA256 `2f3466ea698ac861edcbf50e92007c3d4caf8a0ac78bfc91fcc3efc5db65be44`.
