# TX12 — selected minimal local core acceptance

This matrix accepts the scoped existing local implementation. Codex owns critical
acceptance; failed partial worker inference is retained as failure evidence.
Provider limits prevented a successful worker completion. Independent executed
checks and exact source/test hashes establish local acceptance, not worker claims
or task status flags. The final batch receipt records the final source SHA,
full regression and canonical counts after this gate is accepted.

| Local contract | Existing implementation | Executed gate / receipt |
| --- | --- | --- |
| NATS-free delivery and bounded durable storage | `src/vibetrading/local_storage.py`, market spool and local transport | VN17 real UDS + VN18 storage/recovery companion suite |
| Borrowed role/Paper assembly | `src/vibetrading/local_runtime.py` | VN17 same owner, actual file and UDS, observer settings |
| Corrupt restore/readiness and transport fault closure | `src/vibetrading/runtime_checkpoint.py` | VN18 real run_engine denies listener/callback publication and retains latest reservations |
| Separate strategy files and same-account economics | `src/vibetrading/strategy_runtime.py` | TX11 real two-file registration, BUY/SELL, Decimal cash 100018, reserve 0, margin 200, virtual +1/-1 and physical 0 |
| Exact-cut recovery and native/transport duplicate suppression | existing runtime checkpoint + local engine | TX11 plugin count 1 restored before callback publication; new book count 2 without new orders |
| Optional verified export linkage | existing export manifest + register_configured | TX10 companion tests and TX11 actual owned object/manifest digests |
| Readonly reporting and durable reply queue | `src/vibetrading/dashboard/projection.py`, `src/vibetrading/telegram.py` | TX11 foreign-thread cold read and actual pending update/outbox; ledger dump unchanged; existing TX09 control gates |
| Source inventory and rollback preparation | existing release builder `build_local` | VN19 owned Git fixture and source SHA checks; `local_cutover_vn19.md`, `local_rollback_vn19.md` |
| Existing economic/operational behavior | unchanged exchange/cloud/ETF and strategy paths | unchanged Golden/Mock/Mock2/C3/KP/KD/TOSS DATA_ONLY companion and full regressions |

`local_paper_assembly.md` documents explicit programmatic ownership. This does not
introduce an automatic account/strategy loader. Positive restart is an exact-cut
direct-engine recovery before new shutdown journal facts; graceful role shutdown
can invalidate an earlier bundle and must remain blocked on proof mismatch.
Reported fees are .99/1.01 USDT; physical netting realized adjustment is 20 USDT.
No trusted mark is invented: unrealized PnL remains UNAVAILABLE, freshness UNVERIFIED.

| External operating gate | Status |
| --- | --- |
| Current server mount/device and source identity | NOT_RUN |
| Immutable candidate image and actual rollback digest | NOT_RUN |
| Current accounts, fills, reconciliation and operating authorization | NOT_RUN |
| Representative 72h production ingress/load and concurrent services | NOT_RUN |
| External Telegram and cloud service operations | NOT_RUN |

server_deployed=false; live_active=false; VN20_APPLIED=false.
Synthetic local tests and any recorded CPU affinity/RSS observations establish
no 3.8GiB cgroup or production capacity guarantee. Optional TX03/TX06/TX07/TX08B
and VN20 cleanup remain outside this selected core. No new universal trading
platform, venue wrapper, order code, source/data deletion or remote action occurs.
