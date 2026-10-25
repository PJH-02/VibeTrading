Implemented TX07A within the allowed scope.

## Command run

```bash
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_execution_owner.py tests/refactoring/test_execution_nonce.py tests/refactoring/test_portfolio_ledger.py tests/refactoring/test_vnext_vn12.py tests/refactoring/test_tx02b_attribution.py tests/refactoring/test_tx07a_fact_contract.py
```

**Exit:** `0`
**Result:** `79 passed in 0.57s`

An initial run had 2 assertion failures because my new test expected the fake adapter order ID as `fake-one`; the existing adapter actually returns `fake-client-one`. I corrected only that test expectation. No production code or existing tests were changed.

## Scoped diff

Only the two ticket-authorized files were written:

- `tests/refactoring/test_tx07a_fact_contract.py` — 7 new behavior/fact-contract tests covering:
  - offline `ExecutionOwner` partial/full fill before ACK, duplicate inertness, conflicting fill ID rejection without mutation, late ACK preserving `FILLED`/reserve `0`, immutable intent/client/account/instrument mapping, and fake-response non-proof
  - `PortfolioLedger` full-fill terminal cancel facts, partial fill/cancel-unknown/reserve `60.12`, completion at cash `899.9`, duplicate inertness, stale snapshot rejection, current snapshot acceptance, unknown-intent/conflicting-identity rollback
  - `EtfJournal` cumulative facts `4/396` then `10/990`, native ACK mapping, expected cash `1603.9406`/`1009.8515`, reserve `603`/`0`, and duplicate/late/conflicting fact handling
- `docs/refactoring/vnext/reuse_audit/tx07a_fact_contract.md` — read-only mapping of real existing fields, authority/mode/state distinctions, and unavailable external facts.

Production/config changes: **0**. Existing tests, Golden/canonical metadata, and source files were not modified.

## Limits recorded

- Offline fake responses are not venue proof; live/private venue ACK and native order identity remain unavailable.
- `ExecutionLedger` has no cash column; missing cash facts are documented as unavailable, not zero.
- Paper cash/positions are simulated; paper has no invented ACK API.
- ETF broker facts remain caller-supplied cumulative/native fields; no new ETF math, normalization, or reconciliation was added.
- No universal lifecycle enum or projection runtime/API was introduced.
