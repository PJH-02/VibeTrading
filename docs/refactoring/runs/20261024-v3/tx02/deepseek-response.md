​
​
​
​
​
​
​
​
​
​
​
​Implemented `tests/refactoring/test_tx02_joint_gate.py` only. No product, config, Golden, metadata, or existing tests changed.

**Exact command run**
```
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_vnext_vn10.py tests/refactoring/test_vnext_vn12.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_paper_execution.py tests/refactoring/test_tx02a_owner_fence.py tests/refactoring/test_tx02b_attribution.py tests/refactoring/test_tx02_joint_gate.py
```

**Result**
- Exit: `0`
- Count: `103 passed`
- Duration: `4.47s`

**Coverage**
- Paper alpha/beta: reservations `201.96 + 206.04 = 408`, fills at `T+11/T+12`, physical flat, cash `100018`, remaining reservations `100.98 + 103.02 = 204`, simulated margin `200`, gross-risk enforcement, and active physical-account/product/DB-alias fence with no competitor DB created.
- Cancel/restart: wrong-strategy cancel rollback, alpha reservation retained until `T+18`, repeated cancel inert, restart to beta `UNKNOWN` with preserved leaves/reservation/attribution, duplicate-trade dedup, final beta fill at `T+20`, cash `100016.99`, physical `-1`, zero pending reserve, total release `408`.
- ETF journal: `6000` initialized into `C3/KP/KD` at `2000`, C3 cash `1603.9406`/position `4`/reserve `603`, KP cash `1699.955`/position `3`/reserve `703.5`, C3-only cancel release, journal reopen, and duplicate-status invariance for books, notifications, orders.
- Existing VN10, VN12, portfolio-ledger, paper-execution, TX02A, and TX02B regressions all pass.

**Limitations**
- Synthetic local paper/ETF data only; no live holdings, venue capability, credentials, or deployment.
- The owner fence remains a local/shared-volume lease, not a cross-host authority.
- VN10 Mock/Mock2 fake-broker tests remain authoritative for ETF ownership; the ETF journal exercise is accounting-only.

**Repairs**
- Test-local only. The first failed run exposed that `strategy_cash.fees` stores the positive fee charged rather than a signed value; expectations were corrected without changing product code. Remaining failures were test-code issues (`Decimal` alias usage and exact-zero string formatting), also corrected in the new test only.
