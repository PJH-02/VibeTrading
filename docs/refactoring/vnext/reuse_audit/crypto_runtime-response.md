I'll read the ticket-referenced inventory and contract, then the source files in bounded batches.
# Reuse Audit — `crypto_runtime`

**Scope:** read-only, static audit of HEAD `4977ab509490173e312183f32d47f862d94ee0bf` as claimed by the ticket. No code was changed. This is **not** an acceptance receipt and does not prove live behavior.

**Read:** ticket, inventory, the eight assigned source files, plus limited caller/import evidence from `liquidity_mm/cli.py`, `liquidity_mm/__main__.py`, `fv_mm/market.py`, `fv_mm/types.py`, `strategies/contract.py`, relevant launch/probe scripts, and existing tests.

**Commands / tests:** No shell commands were executed; one `python3` inventory-parse attempt was blocked by policy before execution. No `pytest`, network, exchange, account, or mutation command was run. No test result is claimed.

**Inventory limitation:** `function_inventory_crypto_runtime.json` is a single line and was truncated by the Read tool at 2,000 characters. Function names and line references below are taken directly from the assigned source files, not from a complete inventory parse.

---

## 1. Execution path chains

| Chain | Entry | Exact call chain | Caller class | Evidence |
|---|---|---|---|---|
| C1 — liquidity Bybit CLI | `python -m vibetrading.liquidity_mm` | `liquidity_mm/__main__.py:3 main` → `liquidity_mm/cli.py:67 main` → `cli.py:93 asyncio.run(execute(...))` → `liquidity_mm/runner.py:720 execute` → `runner.py:725 TestnetClient` → `runner.py:751 Runner` → `runner.py:662 Runner.run` | direct CLI | `__main__.py:1-3`, `cli.py:67-93`, `runner.py:720-752` |
| C2 — liquidity public/private transport | C1 | `runner.py:673 client.connect_private()` → `liquidity_mm/exchange.py:146 TestnetClient.connect_private`; `runner.py:674 connect_public` → `exchange.py:164` | direct runtime | `runner.py:672-677`, `exchange.py:146-168` |
| C3 — liquidity submit | C1 | `runner.py:299 Runner.submit` → `runner.py:334 client.create` → `exchange.py:131 TestnetClient.create` → `exchange.py:37 request` → `bybit_execution.py:220 BybitOrderClient.request` | direct runtime | `runner.py:299-344`, `exchange.py:37-45,131-140` |
| C4 — liquidity preflight/check | `python -m vibetrading.liquidity_mm check` | `cli.py:28 check` → `exchange.py:30 TestnetClient` → `exchange.py:80 preflight`; `cli.py:32 connect_private` | direct CLI | `cli.py:28-36` |
| C5 — fv_mm launch | `scripts/launch_fv_run.py` | `launch_fv_run.py:20` subprocess `scripts/run_fv_testnets.py` → `run_fv_testnets.py:10 from vibetrading.fv_mm.runner import Runner` → `:25 Runner(args)` → `:29 Runner.run` | direct script | `launch_fv_run.py:20-23`, `run_fv_testnets.py:10,25-29` |
| C6 — fv_mm venue factory | C5 | `fv_mm/runner.py:351 self.clients={name:cls(...)}` using `venues.Bybit` and `venues.Grvt`; `:354 client.connect`; `:365` private task factory; `:367 asyncio.create_task` | direct runtime | `fv_mm/runner.py:351-367` |
| C7 — fv_mm Bybit private | C6 | `fv_mm/private.py:9 bybit_private` → `fv_mm/venues.py:41 Bybit` → `private.py:13 client.client.connect_private()` → `liquidity_mm/exchange.py:146` | direct runtime | `private.py:9-32` |
| C8 — fv_mm GRVT private | C6 | `fv_mm/private.py:35 grvt_private` → `fv_mm/venues.py:122 Grvt.login` → `private.py:39 websockets.connect("wss://trades.testnet.grvt.io/ws/full")` | direct runtime | `private.py:35-58` |
| C9 — GRVT operator cancel | `scripts/cancel_fv_owned_order.py` | `:8 import Grvt` → `:12 Grvt(session, trading=True)` → `:12 login` → `:14 rpc("cancel_order", mutation=True)` → `:17 order` | direct diagnostic | `cancel_fv_owned_order.py:8-18` |
| C10 — Bybit probe | tests / external script | `tests/test_crypto_perp_pipeline.py:8 import BybitExecutionProbe`; `:112 object.__new__(BybitExecutionProbe)`; `:117 _listen`; no in-repo `BybitExecutionProbe.run` caller found | fixture-only; `run` dynamic UNKNOWN | `tests/test_crypto_perp_pipeline.py:8,106-122`; `scripts/run_crypto_e2e_audit.py:58` mounts `/tmp/measure_crypto_e2e.py` (not in repo) |
| C11 — Bybit status | `scripts/bybit_testnet_status.py` | `:9 import BybitCredentials, BybitOrderClient` → `:14 from_settings(testnet=True)` → `:16 sync_clock` → `request` | direct diagnostic | `bybit_testnet_status.py:9-20` |
| C12 — perp_execution adapters | tests only | `tests/refactoring/test_perp_execution.py:13-18` imports `BinancePerpAdapter`, `ArcusPerpAdapter`, `BybitPerpAdapter`, `GrvtPerpAdapter`, `HyperliquidPerpAdapter`; `ScriptedTransport` at `:21`; no live HTTP | fixture-only / injected transport | `tests/refactoring/test_perp_execution.py:13-43,137-151` |
| C13 — Testnet read-only probe | `scripts/probe_fv_testnets.py` | `:14 import TestnetClient` → `:20 TestnetClient` → `:21 sync_clock` → `:23 wallet`, `:24 positions`, `:25 open_orders`; GRVT uses `GrvtBrokerAdapter`, not assigned `venues.Grvt` | direct diagnostic | `probe_fv_testnets.py:14-47` |

### Venue presence in assigned files

| Venue | Present in assigned execution source? | Product / environment | Classification |
|---|---|---|---|
| Bybit | yes: `liquidity_mm/exchange.py`, `liquidity_mm/runner.py`, `fv_mm/venues.py`, `bybit_execution.py` | linear USDT perpetual, fixed Bybit testnet | direct live-HTTP testnet |
| GRVT | yes: `fv_mm/venues.py`, `fv_mm/private.py`, `fv_mm/runner.py` | `*_USDT_Perp`, fixed GRVT testnet, chain ID 326 | direct live-HTTP testnet |
| Binance | not in assigned execution files; only fv reference asset handling in `fv_mm/market.py:81` | no perp adapter here | imported/test-only via `perp_execution` tests |
| Arcus | not in assigned files | none | imported/test-only |
| Hyperliquid | not in assigned files | none | imported/test-only |
| TOSS | not in assigned files | data-only elsewhere | out of assigned scope |

No inverse, coin-M, spot, cross-margin, hedge-mode, or live mainnet execution path exists in the assigned files. Do not infer that these venues are permanently unsupported solely because this excerpt lacks an adapter.

---

## 2. Per-venue / per-path capability comparison

| Path | Submit | Cancel | Status | Fills | Private stream | Positions | Reconciliation | Account/wallet/funding | Env gate | Product | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `liquidity_mm` Bybit | `exchange.py:131 TestnetClient.create`; `runner.py:299 submit` | `exchange.py:142 cancel`; `runner.py:346 cancel` | REST realtime/history via `runner.py:389-401`; private `order_event` `:161` | private `execution_event` `:184`; REST `rows("/v5/execution/list")` `:371` | `exchange.py:146 connect_private` is transport/auth/subscribe; normalization in `runner.py:122 process` | `exchange.py:66 positions`; applied only by fills; REST mismatch raises `runner.py:425-426` | query + real-state check; does not overwrite ledger from exchange | `wallet` `exchange.py:69`; funding via ticker `runner.py:150-151`; fees via `execution_event` | `TestnetClient.__init__` fixed testnet; `execute` `trading=mode=="testnet"`; shadow mode read-only | linear perpetual | **KEEP / REUSE** |
| `fv_mm` Bybit | `venues.py:76 Bybit.create`; `runner.py:228 create` | `venues.py:85 cancel`; `runner.py:215 cancel` | `venues.py:88 order` realtime→history; private `runner.py:83 on_order` | `venues.py:98 fills`; `runner.py:113 on_fill` | `private.py:9 bybit_private` transport; normalization in `runner.py:83,113` | `venues.py:67 positions`; `runner.py:151 reconcile` applies inventory | query + fill generation guard + positions apply; no halt on mismatch | `account` `venues.py:63`; `account_update` `runner.py:193`; funding only at end `runner.py:394` | `Runner.__init__` rejects non-testnet config; explicit `--trade-testnet`; no live path | linear perpetual, 4 assets | **REUSE / ADAPT** |
| `fv_mm` GRVT | `venues.py:195 Grvt.create` → `signed_order` `:182` | `venues.py:198 cancel` | `venues.py:201 order`; private `runner.py:83 on_order` | `venues.py:224 fills` → `history`; `runner.py:113 on_fill` | `private.py:35 grvt_private`; normalization in `runner.py:83,113` | `venues.py:175 positions`; `runner.py:151 reconcile` | query + pagination; no private cursor/seq proof | `account` `:169`; funding `:227`; no wallet transfer | fixed testnet; `rpc` mutation allowlist; chain 326 | `*_USDT_Perp` | **REUSE / ADAPT** |
| `bybit_execution` probe | `BybitOrderClient.request` `:220`; probe `_run_market_trigger_order` `:344` | probe `:406-407`, `:510`; audit `transition` | `_reconcile` `:305` | `_listen` `:292-303`; `BybitAudit.fill` `:130` | `_listen` combines transport + normalization + audit | query only `:438`, `:460` | query/cancel/unknown; no position apply | none | credentials allow live; `run` blocks live `:422`; **kill switch bypass** | linear perpetual | **KEEP; REPLACE_DEFECT on live kill path** |
| `perp_execution` adapters | test-visible `submit_with_permit` | `cancel_with_permit` | `order_status` | `fills` | `normalize_private_event` normalize-only | `positions` / `account_state` | `reconcile` seam referenced in TX07B; injected transport | `account_state`; funding normalize tested | `PerpScope` sandbox/live; tests use synthetic | linear perp; explicit capability blockers | **REUSE composition; do not promote test transport** |

**Composition vs live HTTP:** `perp_execution.*PerpAdapter` tests use injected `ScriptedTransport` and explicit permits; those are composition/request-mapping prototypes, not live HTTP. `liquidity_mm.TestnetClient`, `fv_mm.venues.Bybit`, `fv_mm.venues.Grvt`, and `bybit_execution.BybitOrderClient` are live HTTP clients (testnet by gate). `BybitExecutionProbe` is a live-HTTP testnet probe and must not become the sole live authority.

---

## 3. Function-level reuse matrix

### 3.1 `src/vibetrading/bybit_execution.py`

| Function | Evidence | Verdict |
|---|---|---|
| `_market_trigger_price` `:23` | quote validation, maker/taker price selection | **KEEP** |
| `BybitCredentials.from_settings` `:38` | testnet prefix vs live prefix; live requires `CRYPTO_LIVE_ENABLED=true` | **KEEP** |
| `BybitAudit.__init__` `:50` | separate WAL audit DB; orders/fills/transitions/triggers | **KEEP** |
| `BybitAudit.close` `:83` | close DB | **KEEP** |
| `BybitAudit.kill_active` `:86` | persisted kill switch | **KEEP** |
| `BybitAudit.set_kill` `:90` | set kill switch | **KEEP** |
| `BybitAudit.create` `:93` | persist intent before submit | **KEEP** |
| `BybitAudit.record_trigger` `:97` | market trigger provenance | **KEEP** |
| `BybitAudit.record_order_headers_sent` `:106` | send timestamp provenance | **KEEP** |
| `BybitAudit.transition` `:110` | terminal-state guard at `:121` only blocks a subset; `UNKNOWN/TIMEOUT/CANCEL_REQUESTED` can overwrite `FILLED` | **REPLACE_PROVEN_DEFECT (local guard)** |
| `BybitAudit.fill` `:130` | `INSERT OR IGNORE` by `exec_id` | **KEEP** |
| `BybitAudit.report` `:137` | per-order latency/fill report | **KEEP** |
| `BybitAudit.summary` `:175` | percentile summary | **KEEP** |
| `BybitOrderClient.__init__` `:195` | testnet/live base selection | **KEEP** |
| `BybitOrderClient.sync_clock` `:202` | clock offset/RTT, 60s fail-closed | **KEEP** |
| `BybitOrderClient.now_ms` `:217` | adjusted clock | **KEEP** |
| `BybitOrderClient.request` `:220` | HMAC signing, no secret logging | **KEEP** |
| `BybitExecutionProbe.__init__` `:240` | selects audit DB by testnet/live | **KEEP** |
| `BybitExecutionProbe._market_trigger` `:247` | public book trigger; `Any` annotation is unimported but postponed by `from __future__ import annotations` | **KEEP (low-risk annotation issue)** |
| `BybitExecutionProbe._listen` `:273` | order/execution normalization; maker/posted enforcement; fill dedup | **KEEP** |
| `BybitExecutionProbe._reconcile` `:305` | resolves unresolved orders; cancels open; raises on ambiguity | **REUSE** |
| `BybitExecutionProbe.activate_kill_switch` `:329` | `cancel-all` via real `BybitOrderClient`; does not check `self.testnet` | **REPLACE_PROVEN_DEFECT (live gate)** |
| `BybitExecutionProbe._run_market_trigger_order` `:344` | hardcodes testnet private/public WS; timeout cancel | **KEEP (testnet only)** |
| `BybitExecutionProbe.run` `:419` | rejects live at `:422`; preflight/reconcile/position guard | **KEEP** |

### 3.2 `src/vibetrading/liquidity_mm/exchange.py`

| Function | Evidence | Verdict |
|---|---|---|
| `ExchangeError` `:24` | typed exchange failure | **KEEP** |
| `TestnetClient.__init__` `:32` | forces `BybitCredentials.from_settings(..., testnet=True)` | **REUSE** |
| `TestnetClient.request` `:37` | rejects non-testnet base; allows POST only create/cancel when trading | **KEEP** |
| `TestnetClient.rows` `:47` | bounded pagination, fails on no progress | **KEEP** |
| `TestnetClient.open_orders` `:63` | linear/USDT open orders | **REUSE** |
| `TestnetClient.positions` `:66` | linear positions | **REUSE** |
| `TestnetClient.wallet` `:69` | requires equity/available/IM/MM fields; no invented zeroes | **KEEP** |
| `TestnetClient.preflight` `:80` | product, fees, ticker, positions, orders, wallet, permission, unit/notional | **REUSE** |
| `TestnetClient.create` `:131` | limit order, IOC/PostOnly, positionIdx=0, reduceOnly for non-entry, optional SL | **REUSE** |
| `TestnetClient.cancel` `:142` | cancel by client ID | **REUSE** |
| `TestnetClient.connect_private` `:146` | HMAC auth, subscribe order/execution/position | **REUSE** |
| `TestnetClient.connect_public` `:164` | orderbook/trade/ticker subscription | **REUSE** |

### 3.3 `src/vibetrading/liquidity_mm/runner.py`

| Function | Evidence | Verdict |
|---|---|---|
| `Runner.__init__` `:32` | run journal/risk ledger/market/strategy/wallet state | **KEEP** |
| `Runner.reader` `:82` | queue overflow/disconnect → halt | **KEEP** |
| `Runner.heartbeat` `:102` | WS ping failure → halt | **KEEP** |
| `Runner.own_depth` `:113` | active leaves by side/price | **KEEP** |
| `Runner.process` `:122` | raw event journal, subscription rejection, book/trade/ticker/order/execution dispatch | **KEEP** |
| `Runner.order_event` `:161` | owned/unowned/native stop classification; monotonic reported fill | **KEEP** |
| `Runner.execution_event` `:184` | execution dedup, overfill check, inventory fill, maker contract | **KEEP** |
| `Runner.fresh` `:226` | book/private/mark/backlog freshness | **KEEP** |
| `Runner.risk` `:243` | daily/drawdown/absolute loss/margin/worst inventory/holding/stop/liquidation | **KEEP** |
| `Runner.submit` `:299` | revalidates decision, durably journals order before create, UNKNOWN on exception | **KEEP / REUSE** |
| `Runner.cancel` `:346` | PENDING_CANCEL, ACK is not terminal, UNKNOWN on exception | **KEEP** |
| `Runner.cancel_entries` `:359` | cancel all entry leaves | **KEEP** |
| `Runner.drain_queue` `:364` | deterministic processing of queued private events | **KEEP** |
| `Runner.reconcile` `:369` | open orders, executions, missing order query, positions, wallet, foreign order checks, mismatch/liquidation guards | **KEEP / REUSE** |
| `Runner.maintain_exit` `:435` | reduce-only IOC at executable target; soft drain | **KEEP** |
| `Runner.tick` `:462` | stop file, clock, freshness, timeout cancels, risk, strategy evaluation, exit maintenance | **KEEP** |
| `Runner.status` `:567` | operator-visible state, PnL, worst-case, halt reason | **KEEP** |
| `Runner.cleanup` `:586` | shadow read-only verification or testnet reduce/cancel loop | **KEEP** |
| `Runner.bounded_cleanup` `:642` | 60s cleanup timeout | **KEEP** |
| `Runner.reduce_cycle` `:650` | flat verification then cooldown or halt | **KEEP** |
| `Runner.run` `:662` | private/public tasks, signal handling, queue, status, storage budget, cleanup | **KEEP** |
| `execute` `:720` | account UID hash lock, preflight, STOP handling, risk ledger, Runner | **KEEP / REUSE** |

### 3.4 `src/vibetrading/liquidity_mm/journal.py`

| Function | Evidence | Verdict |
|---|---|---|
| `encode` `:11` | `allow_nan=False` JSON | **KEEP** |
| `write_json` `:15` | temp file + `os.replace` | **KEEP** |
| `RunLock.__init__` `:25` | account lock path | **REUSE** |
| `RunLock.__enter__` `:29` | OS non-blocking exclusive lock; stale process safe | **KEEP** |
| `RunLock.__exit__` `:49` | releases on close | **KEEP** |
| `Journal.__init__` `:55` | WAL, FULL sync, events/decisions/orders/executions/runs | **KEEP** |
| `Journal.event` `:70` | raw event append | **KEEP** |
| `Journal.decision` `:75` | decision record | **KEEP** |
| `Journal.order` `:78` | durable order upsert **before** exchange mutation | **KEEP** |
| `Journal.execution` `:84` | `exec_id` dedup | **KEEP** |
| `Journal.commit/close` `:88-91` | transaction boundaries | **KEEP** |
| `RiskLedger.__init__` `:99` | daily start, high-watermark, halt controls | **REUSE** |
| `RiskLedger.observe` `:106` | restart-safe risk state | **KEEP** |
| `RiskLedger.halt` `:118` | persisted halt reason | **KEEP** |

### 3.5 `src/vibetrading/fv_mm/venues.py`

| Function | Evidence | Verdict |
|---|---|---|
| `UncertainMutation` `:21` | subtype of `Exception`, not `RuntimeError`; distinguishes unknown mutation | **KEEP** |
| `Instrument.floor` `:34` | lot floor | **KEEP** |
| `Instrument.valid` `:37` | positive/minimum/min-notional/reduce exemption | **KEEP** |
| `Bybit.__init__` `:41` | `TestnetClient`; trading flag | **REUSE** |
| `Bybit.connect` `:49` | public instrument metadata, fee, API permission; only `LinearPerpetual` | **REUSE** |
| `Bybit.account` `:63` | equity/available/margin rate | **REUSE** |
| `Bybit.positions` `:67` | one-way only; signed size | **REUSE** |
| `Bybit.open_orders` `:73` | delegated pagination | **REUSE** |
| `Bybit.create` `:76` | limit/post-only/IOC, reduceOnly, positionIdx=0; only retCodes `10000/10016` → `UncertainMutation` | **ADAPT** |
| `Bybit.cancel` `:85` | cancel by `orderLinkId` | **REUSE** |
| `Bybit.order` `:88` | realtime then history | **REUSE** |
| `Bybit.fills` `:98` | execution list, ns→ms | **REUSE** |
| `Bybit.funding` `:101` | transaction-log settlement, ns→ms | **REUSE** |
| `Grvt.__init__` `:111` | env credentials/signer/subaccount/address; auth lock | **KEEP** |
| `Grvt.login` `:122` | 240s cookie cache; signer address, subaccount, cookie checks | **KEEP** |
| `Grvt.rpc` `:143` | mutation allowlist only create/cancel; 5xx/200 indeterminate → `UncertainMutation`; no response payload logging | **KEEP** |
| `Grvt.connect` `:156` | fixed testnet REST/market URLs; `*_USDT_Perp`; `min_size` as quantum | **REUSE** |
| `Grvt.account` `:169` | equity/available/margin, no invented values | **REUSE** |
| `Grvt.positions` `:175` | signed base size | **REUSE** |
| `Grvt.open_orders` `:179` | delegated | **REUSE** |
| `Grvt.signed_order` `:182` | validates precision/minimum; EIP-712 chain 326; uint32 nonce; expiry; client metadata | **REUSE (signer composition candidate)** |
| `Grvt.create` `:195` | signed order → `create_order` mutation | **REUSE** |
| `Grvt.cancel` `:198` | `cancel_order`, TTL string | **REUSE** |
| `Grvt.order` `:201` | status; terminal set includes `CANCELED/EXPIRED` | **REUSE** |
| `Grvt.history` `:211` | bounded cursor pagination; same-cursor failure | **REUSE** |
| `Grvt.fills` `:224` | fill history | **REUSE** |
| `Grvt.funding` `:227` | funding payment history | **REUSE** |

### 3.6 `src/vibetrading/fv_mm/private.py`

| Function | Evidence | Verdict |
|---|---|---|
| `bybit_private` `:9` | connect/auth; order/execution dispatch; gap journal; reconnect | **REUSE** |
| `grvt_private` `:35` | login; subscribe `v1.order`/`v1.fill`; ready only after both acks; gap journal | **REUSE** |

### 3.7 `src/vibetrading/fv_mm/runner.py`

| Function | Evidence | Verdict |
|---|---|---|
| `Working` `:28` | client ID, intent, leaves, live/done, timestamps | **KEEP** |
| `Runner.__init__` `:43` | rejects existing output; requires `environment=="testnet"`; loads models; routes | **KEEP** |
| `Runner.on_order` `:83` | Bybit and GRVT order normalization; terminal guard `:101`; DB order update | **REPLACE_PROVEN_DEFECT (GRVT terminal set)** |
| `Runner.on_fill` `:113` | `INSERT OR IGNORE` fills; inventory/PnL context; maker markout | **KEEP** |
| `Runner.reconcile` `:151` | per-working order query; fills over last 120s; generation guard; positions apply; unexpected-position halt | **ADAPT** |
| `Runner.account_update` `:193` | equity/peak/drawdown/margin halt; equity table | **KEEP** |
| `Runner.gross_reserved` `:203` | inventory + non-reduce pending leaves at mid | **KEEP** |
| `Runner.cancel` `:215` | 2s throttle; cancel ACK not terminal | **KEEP** |
| `Runner.create` `:228` | marketable maker check; API interval; durable intent; shadow mode; ACK timeout; reject vs UNKNOWN | **ADAPT** |
| `Runner.decide` `:268` | risk/halt/working-order/target selection; emergency taker only if config | **KEEP** |
| `Runner.worker` `:320` | 2s reconcile, 10s account, dirty decision loop; error halt/cancel | **KEEP** |
| `Runner.status` `:343` | operator status | **KEEP** |
| `Runner.run` `:346` | venue factory, preflight empty account, feeds, private streams, warmup, bounded run, cleanup, funding, manifest | **KEEP** |
| `Runner.source_hash` `:430` | strategy/fv_mm/mm_state source hash | **KEEP** |

### 3.8 `src/vibetrading/fv_mm/journal.py`

| Function | Evidence | Verdict |
|---|---|---|
| `Journal.__init__` `:9` | WAL/FULL sync; samples/quotes/trades/events/orders/fills/equity | **KEEP** |
| `Journal.event` `:28` | append event | **KEEP** |
| `Journal.save` `:31` | temp+replace JSON | **KEEP** |
| `Journal.commit` `:37` | commit | **KEEP** |
| `Journal.close` `:40` | commit/close | **KEEP** |

---

## 4. TX00–TX12 duplication map

| TX | Ticket instruction (short quote) | Existing method(s) with same job | Reuse recommendation | Gap / verdict |
|---|---|---|---|---|
| TX00 | `(venue, product family, environment, account domain, position mode, margin mode)` capability registry; `TOSS=DATA_ONLY` | `TestnetClient.preflight` `exchange.py:80`; `Bybit.connect` `venues.py:49`; `Grvt.connect` `venues.py:156`; `BybitCredentials.from_settings` `bybit_execution.py:38` | Extract/annotate facts from these; do not build a new universal platform | No canonical registry. **ADAPT (facts)** |
| TX01 | immutable `OrderIntent`, `CancelIntent`, identifiers/version/epoch | `fv_mm.types.Intent` `:51` frozen; `fv_mm.runner.Working` `:28`; `liquidity_mm` `Order`/`Decision` (outside assigned) | Reuse `Intent`; add only required ID/epoch fields at the local boundary | No canonical contract here. **ADAPT, not a new order engine** |
| TX02A | account scope/owner lease/stale epoch | `liquidity_mm.journal.RunLock` `:22`; `liquidity_mm.runner.execute` `:733-734` | Reuse `RunLock` pattern for fv account scope | fv_mm has **no account lock**; two fv processes can hit same testnet account. **COMPLETE_MISSING (narrow local fence)** |
| TX02B | Fill → physical aggregate + strategy virtual allocation | `fv_mm.runner.on_fill` `:113`; `inventory` shared by S1–S4; `gross_reserved` `:203` | Keep shared physical inventory; add strategy attribution only if same-account strategies become non-disjoint | Current `ROUTES` are asset-disjoint (`market.py:8`), so no immediate collision. **KEEP** |
| TX03A | Linear/Inverse product spec, collateral/settlement/funding separated | `venues.Instrument` `:25`; `TestnetClient.preflight` `:80` | Keep explicit isolated-linear assumptions; keep inverse/coin-M blocked unless separately proven | No inverse model. **KEEP for current scope; COMPLETE_MISSING only for explicit new product** |
| TX03B | fee/funding/equity/margin single identity, no invented values | `fv_mm.runner.on_fill` fee; `account_update` `:193`; `Bybit.funding` `:101`; `Grvt.funding` `:227`; `liquidity_mm.RiskLedger` `:96`; `wallet` `exchange.py:69` | Reuse facts/ledger seams; do not create a second portfolio ledger | Funding is end-of-run/analytics, not continuous settlement accounting. **ADAPT** |
| TX04A/B | durable local paper matcher, event ordering, conservative fills | No paper matcher in assigned files; `liquidity_mm` FIFO replay is research/testnet, not paper account ledger | Use existing `paper_execution`/`portfolio_ledger`/runtime named by ticket; do not build a second matcher here | **Outside assigned excerpt; do not duplicate. UNKNOWN** |
| TX05 | shared strategy runtime, bounded callbacks, single state apply | `fv_mm` loops four `ROUTES` per venue in-process; `strategies/contract.py:15 StrategyInstance`, `:112 load_strategy` is the real plug-in contract | Reuse `strategies/contract.py`; do not create a second runtime in `fv_mm` | `fv_mm` is a research runner, not the shared runtime. **REUSE contract; no new runtime** |
| TX06A | Binance perp metadata/submit/cancel/status/fills/positions/funding/private | `tests/refactoring/test_perp_execution.py` imports `BinancePerpAdapter`; `ScriptedTransport` injected | Reuse mapping/composition; do not infer Binance unsupported from one wrapper | No assigned live HTTP implementation. **REUSE composition / UNKNOWN live** |
| TX06B | Arcus signer/nonce/chain-specific helper | `test_perp_execution.py` `ArcusSigningComposition`; `BybitExecutionProbe` is unrelated | Reuse existing ED25519 composition; no new signer | No assigned Arcus source. **REUSE composition / UNKNOWN live** |
| TX06C | GRVT typed data/nonce/cancel/status/fills/funding/private gap | `venues.Grvt.signed_order` `:182`, `rpc` `:143`, `history` `:211`; `test_perp_execution.py` `GrvtSigningComposition`/`GrvtPerpAdapter` | Choose one signer composition; keep `Grvt.signed_order` as testnet probe or delegate to shared signer | Duplicate GRVT signing paths exist (`venues.Grvt` and `perp_execution.GrvtSigningComposition`). **ADAPT / consolidate later** |
| TX06D | Bybit linear/inverse, positionIdx, create/amend/cancel/get/execution/position/wallet, private stream | `TestnetClient` `exchange.py`; `venues.Bybit` `:41`; `BybitOrderClient` `bybit_execution.py:194`; `BybitExecutionProbe` `:239`; `BybitPerpAdapter` tests | Reuse `BybitOrderClient` transport and one normalizer; do not add another REST/private wrapper | Four Bybit layers; probe is testnet, adapter is injected transport. **REUSE / ADAPT; no new wrapper** |
| TX06E | Hyperliquid native action, subaccount/vault/nonce, metadata, fills/funding | `tests/refactoring/test_perp_execution.py` `HyperliquidPerpAdapter`, `HyperliquidSdkSigningComposition`, `SignerNonceStore` | Reuse SDK composition and nonce store | No assigned live source. **REUSE composition / UNKNOWN live** |
| TX07A | canonical `OrderSubmitted…Funding/AccountChange`, ACK/fill order independence, terminal monotonicity | `fv_mm.runner.on_order/on_fill`; `liquidity_mm.runner.order_event/execution_event`; `BybitAudit.transition/fill` | Reuse as venue fact normalizers; add one local event bridge if needed | `BybitAudit.transition` can overwrite terminal (`:121`). **REPLACE_DEFECT (local predicate)** |
| TX07B | private cursor/seq/generation, REST pagination completeness, absence ≠ never submitted | `fv_mm.runner.reconcile` `:151`; `liquidity_mm.runner.reconcile` `:369`; `BybitExecutionProbe._reconcile` `:305` | Reuse reconcilers; add missing-window evidence only where needed | `fv_mm` only looks back 120s (`:167`); no cursor/generation. **ADAPT** |
| TX08A/B | multi-strategy account risk, cross-venue execution group | No `execution_groups` in assigned files; `fv_mm` account-level risk only | Keep existing account inventory/reservation owner; do not add universal group state machine here | **Outside assigned scope; UNKNOWN** |
| TX09 | Telegram/PnL/portfolio projection | Not in assigned files | Do not touch; existing projection owner elsewhere | **Outside assigned scope** |
| TX10 | Parquet/manifest export, promotion metadata | Not in assigned files | Do not build an uploader/exporter here; preserve `storage_backend` | **Outside assigned scope; UNKNOWN** |
| TX11 | multi-strategy local E2E, CPU/RSS/latency | `fv_mm.Runner.run` `:346`; `liquidity_mm.Runner.run` `:662`; `source_hash` `:430` | Reuse bounded testnet harness as a reference, not as proof of paper E2E | Existing runners are testnet/research, not multi-strategy paper. **REUSE harness / gap remains** |
| TX12 | release candidate matrix, Linux full test, live gates | `fv_mm.runner.source_hash` `:430`; runtime manifests `:374`; `liquidity_mm.cli` | Keep; do not invent release acceptance from this static audit | No full release evidence here. **UNKNOWN** |

---

## 5. Concrete findings: defects, concurrency, interface, secrets, acceptance gaps

| # | Severity | Finding | Evidence | Recommendation |
|---|---|---|---|---|
| F1 | High | `BybitAudit.transition` can overwrite a terminal status with `UNKNOWN`, `TIMEOUT`, or `CANCEL_REQUESTED` because its guard only blocks a subset of non-terminal statuses. This violates TX07A terminal monotonicity. | `bybit_execution.py:121-126` | Fix local predicate only; preserve audit schema. **REPLACE_PROVEN_DEFECT** |
| F2 | High | `BybitExecutionProbe.activate_kill_switch` calls `cancel-all` via `BybitOrderClient` without checking `self.testnet`. A live-credential probe object can issue live cancel-all even though `run()` blocks live. | `bybit_execution.py:329-342`, `:419-423` | Add explicit testnet/live guard or separate testnet-only kill method. **REPLACE_PROVEN_DEFECT** |
| F3 | Medium | `fv_mm.Runner.on_order` treats GRVT `EXPIRED`/`CANCELED` as non-terminal, while `Grvt.order` treats them as terminal. A private expired/canceled event can leave a working reservation stuck. | `fv_mm/runner.py:96`, `:101-105`; `venues.py:209` | Align terminal status predicate. **REPLACE_DEFECT (predicate)** |
| F4 | Medium | `fv_mm.Runner.create` classifies `RuntimeError` as definite rejection, including vendor rate-limit/auth/timeout errors that may be indeterminate. `Bybit.create` only maps retCodes `10000/10016` to `UncertainMutation`. | `fv_mm/runner.py:255-261`; `venues.py:80-83` | Classify uncertain codes/transport as UNKNOWN; do not release reservation on ambiguity. **ADAPT** |
| F5 | Medium | `fv_mm.Runner.reconcile` only requests fills for the last 120 seconds. A private-stream gap or late fill older than that can be missed; positions are then applied without booking the missed fill/fee. | `fv_mm/runner.py:167-181` | Add bounded, cursor/window-complete recovery; keep reservations until proven. **ADAPT** |
| F6 | Medium | `fv_mm.Runner.reconcile` applies exchange positions to `inventory` and only journals `inventory_reconciliation`; it does not halt on mismatch. This can mask a real ledger divergence. | `fv_mm/runner.py:178-181` | Treat material mismatch as fail-visible/halt, or reconcile only through confirmed fills. **ADAPT** |
| F7 | Medium | `fv_mm.Runner.reconcile` treats a `None` order result as no-op; a permanently absent order remains in `working` and blocks new orders on that venue/asset. | `fv_mm/runner.py:159-166` | Mark `UNKNOWN`/halt after bounded absence, or query history explicitly. **ADAPT** |
| F8 | Medium | `fv_mm` has no account-level process lock. `liquidity_mm` has `RunLock`, but `fv_mm.Runner.run` only prevents reuse of its output directory. Two fv processes can use the same testnet account. | `fv_mm/runner.py:45-46`, `:351`; `liquidity_mm/journal.py:22` | Reuse the existing `RunLock` pattern; do not build a new owner service. **COMPLETE_MISSING (local fence)** |
| F9 | Low | `BybitExecutionProbe._market_trigger` annotates `ws: Any` but `Any` is not imported; safe today only because annotations are postponed. Any `typing.get_type_hints` use would fail. | `bybit_execution.py:1-20`, `:247` | Import `Any` or remove annotation. Cosmetic. |
| F10 | Low | `BybitAudit.report` calls `fetchone()` and immediately `dict(None)` for an unknown client ID. | `bybit_execution.py:137-138` | Raise a typed `KeyError`/return no report. |
| F11 | Info | No secret exposure found in assigned source: GRVT avoids logging response payloads; Bybit/Testnet clients sign in memory; journals store order/fill/fact data, not API keys. `liquidity_mm.Journal` stores raw private messages, which should be reviewed for account metadata before broad export. | `venues.py:152-153`, `bybit_execution.py:229-230`, `liquidity_mm/journal.py:70-73` | Keep as-is; redact/select fields before any external export. |
| F12 | Info | Interface duplication: `fv_mm` and `liquidity_mm` each define their own `Runner`, `Journal`, `Working/Order`, and Bybit access path. No shared canonical `Order`/`Fill` contract. | `fv_mm/runner.py:42`, `liquidity_mm/runner.py:31` | Do not merge blindly; one normalized local fact boundary is enough. |
| F13 | Info | `liquidity_mm.Runner.reconcile` is the stronger pattern for real-state apply: it queries and raises on inventory mismatch rather than silently overwriting. | `liquidity_mm/runner.py:403-426` | Reuse this behavior as the reference for any local bridge. |
| F14 | Info | `fv_mm.Runner.run` and `liquidity_mm.Runner.run` are bounded, explicit-duration testnet runs with cleanup; neither is a scheduler or live deployment. | `fv_mm/runner.py:377-425`; `liquidity_mm/runner.py:679-717` | Preserve bounded semantics; do not auto-promote. |
| F15 | Info | Cloud/storage transport is not in the assigned files. No cloud uploader was reviewed or should be added by default. | ticket read paths; no `storage_backend` source in assigned set | Preserve existing storage backend; audit separately if in scope. **UNKNOWN** |

---

## 6. Minimal-diff recommendation

1. **Keep the authoritative state owners as they are:** `liquidity_mm.Runner`/`Journal`/`RiskLedger` for the Bybit testnet path; `fv_mm.Runner`/`Journal` for the four-route testnet path; `BybitOrderClient` for signed Bybit transport; `Grvt.rpc`/`signed_order` for GRVT testnet. Do not introduce a universal state machine or second order engine.
2. **Reuse `liquidity_mm.journal.RunLock`** as the only local account fence for `fv_mm`; this is a narrow adaptation, not a new ownership platform.
3. **Add at most one normalized local event boundary** between venue facts and any new local consumer. The existing normalizers are `fv_mm.Runner.on_order/on_fill`, `liquidity_mm.Runner.order_event/execution_event`, and `BybitAudit.transition/fill`. Reuse their identity/dedup semantics rather than creating another event bus.
4. **Fix only proven local defects first:** terminal-state monotonicity in `BybitAudit.transition`, GRVT terminal set in `fv_mm.Runner.on_order`, live kill-switch guard in `BybitExecutionProbe.activate_kill_switch`, and uncertain-vs-reject classification in `fv_mm.Runner.create`/`Bybit.create`.
5. **Use the existing paper matcher/ledger/runtime** named by TX04/TX05 (`paper_execution`, `portfolio_ledger`, `strategy_runtime`) rather than writing another matcher in `fv_mm` or `liquidity_mm`. The assigned files do not contain that paper owner.
6. **Reuse `src/vibetrading/strategies/contract.py`** (`StrategyInstance:15`, `load_strategy:112`, required `STRATEGY_ID/SUBSCRIPTIONS/WARMUP/create_strategy:115`) for plugin work. R05 KR functions are calculators, not completed pluggable strategy files; do not create a parallel plugin contract.
7. **Do not promote testnet probe classes:** `BybitExecutionProbe` and `TestnetClient` must remain testnet-only execution probes. `perp_execution.*PerpAdapter` with injected `ScriptedTransport` is composition, not live HTTP. Do not label every venue unsupported because one wrapper lacks a method.
8. **Do not add a cloud uploader by default.** `storage_backend` is outside this assigned excerpt; preserve it and audit separately.
9. **Retirement:** none recommended now. `RETIRE` requires caller/rollback/golden-proof and is plan-only. Duplicate wrappers to study later: `fv_mm.venues.Bybit` vs `liquidity_mm.TestnetClient` vs `bybit_execution.BybitOrderClient` vs `BybitExecutionProbe`; and `fv_mm.venues.Grvt.signed_order` vs `perp_execution.GrvtSigningComposition`.

---

## 7. Highest-value existing tests (static evidence only; not executed)

| # | Test path::function | Why it matters |
|---|---|---|
| T1 | `tests/test_fv_mm.py::test_transport_loss_keeps_unknown_order_and_halts_new_exposure` `:151` | Proves timeout becomes UNKNOWN with reservation held and new exposure halted. |
| T2 | `tests/test_fv_mm.py::test_duplicate_fill_is_not_double_booked` `:179` | Proves `fills` primary-key dedup and inventory single application. |
| T3 | `tests/test_fv_mm.py::test_cancel_ack_does_not_release_reserved_quantity` `:138` | Proves cancel ACK is not terminal and leaves remain reserved. |
| T4 | `tests/test_fv_mm.py::test_pending_order_is_not_netted_against_opposite_inventory` `:169` | Proves worst-case reservation includes pending order rather than netting it. |
| T5 | `tests/test_fv_mm.py::test_grvt_server_error_is_unknown_mutation_not_definite_rejection` `:204` | Proves GRVT 5xx mutation becomes `UncertainMutation`, not a definite rejection. |
| T6 | `tests/test_fv_mm.py::test_grvt_xrp_signing_has_exact_integer_size_testnet_chain_no_builder_fee` `:120` | Proves exact GRVT EIP-712 size/chain/builder-fee behavior. |
| T7 | `tests/test_liquidity_mm.py::test_testnet_client_blocks_mainnet_and_readonly_mutation` `:302` | Proves fixed testnet endpoint and read-only mutation block. |
| T8 | `tests/test_liquidity_mm.py::test_submission_timeout_unknown_no_second_order` `:352` | Proves UNKNOWN submission does not create a second order. |
| T9 | `tests/test_liquidity_mm.py::test_cleanup_reconciles_partial_iocs_until_verified_flat` `:454` | Proves partial IOC cleanup/reconcile loop. |
| T10 | `tests/test_liquidity_mm.py::test_run_lock_blocks_concurrent_process` `:282` | Proves OS account lock prevents concurrent process. |
| T11 | `tests/test_crypto_perp_pipeline.py::test_bybit_audit_only_counts_verified_fills` `:78` | Proves audit fill dedup and terminal status not overwritten by late ACK. |
| T12 | `tests/refactoring/test_perp_execution.py::test_possible_handoff_never_retries_and_keeps_unknown` `:197` | Proves injected-transport handoff failure does not retry. |
| T13 | `tests/refactoring/test_perp_execution.py::test_bybit_pagination_cycle_fails_closed_and_empty_history_is_unknown` `:248` | Proves pagination cycle/empty history fail closed. |
| T14 | `tests/refactoring/test_tx00_toss_data_only.py::test_auth_client_request_rejects_mutating_methods_before_authentication` `:142` | Proves TOSS DATA_ONLY rejects mutation before auth. |
| T15 | `tests/refactoring/test_tx00_toss_data_only.py::test_data_only_dispatch_cannot_reinterpret_a_validated_method` `:357` | Proves a mutated string cannot reinterpret a validated GET as POST. |

---

## 8. Verified source findings vs unresolved questions

### Verified from source

- Bybit execution in the assigned files is linear-USDT-perpetual and testnet-gated; no inverse/coin-M path exists.
- GRVT execution in `fv_mm` is fixed to `*_USDT_Perp` testnet endpoints with chain ID 326; signing is local and mutation endpoints are allowlisted.
- Both `fv_mm` and `liquidity_mm` durably persist an order/intent before exchange mutation.
- Both have fill dedup; `fv_mm` uses `(venue, exec_id)` and `liquidity_mm` uses `exec_id`.
- Both treat cancel ACK as non-terminal and retain reservations.
- `liquidity_mm.Runner.reconcile` raises on exchange/ledger inventory mismatch; `fv_mm.Runner.reconcile` instead applies positions and only journals a mismatch.
- `bybit_execution.BybitAudit.transition` has a terminal-monotonicity defect.
- `bybit_execution.BybitExecutionProbe.activate_kill_switch` lacks a testnet guard.
- `fv_mm` has no account-level process lock; `liquidity_mm` does.
- `strategies/contract.py` is the real event-strategy contract; `fv_mm` ROUTES are research modules, not that contract.

### Unresolved / not proven by this excerpt

- Whether HEAD is actually `4977ab509490173e312183f32d47f862d94ee0bf` (no git command run).
- Full inventory contents (Read truncated the single-line JSON).
- Live/testnet verification for any venue; this is static-only.
- Exact GRVT private payload shape: whether `client_order_id`/metadata is present at the root or only under `metadata` in `Grvt.order` results.
- Vendor terminal status semantics for GRVT `PARTIALLY_FILLED_CANCELED` and Bybit deactivated/partially-filled-canceled variants.
- Whether `BybitExecutionProbe.run` has an in-repo caller; `scripts/run_crypto_e2e_audit.py:58` references an external `/tmp/measure_crypto_e2e.py`.
- Whether `BybitExecutionProbe.activate_kill_switch` is reachable from any deployed entrypoint; the live-risk path is still present.
- The paper matcher/ledger/runtime, `execution_groups`, `strategy_runtime`, `portfolio_ledger`, `storage_backend`, and Telegram/export paths are outside the assigned excerpt. Do not claim they are missing or complete from this audit.
- Working user-reported production behavior is assumed preserved because no changes were made; this audit cannot prove live production correctness.

No files were edited. No acceptance is asserted.
