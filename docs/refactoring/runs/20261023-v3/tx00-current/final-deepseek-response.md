Implemented the bounded TX00 repair and stopped after the specified checker commands and closure report.

## Commands and actual results

```
python3 docs/refactoring/vnext/reuse_audit/tx00_current/check.py --root . --write
# exit 0: files=47 functions=712 historical=692 retained_unchanged=679 retained_changed=13 added=20 removed=0 direct_calls=459 candidate_inputs=510 changed_source_files=3

python3 docs/refactoring/vnext/reuse_audit/tx00_current/check.py --root . --check
# exit 0: same counts
```

No pytest command was specified by the ticket; no product tests were run or modified.

## Changes made

`check.py`:
- Removed `MIRROR_DIR`, mirror reads/writes, `/tmp` fallback, and warning-success behavior. Only the four intended JSON siblings are read/written; write failures or empty/malformed evidence are nonzero errors.
- Call-valued receivers now return `UNKNOWN`; inner direct factory calls are still collected once.
- Wildcard imports in a relevant lexical scope/ancestor force unresolved names; conflicting same-name imports from different modules remain unresolved rather than taking the last AST-walk binding.
- `git ls-files` failure now returns errors/nonzero; filesystem fallback and `rglob` scanning were removed.
- Missing, unreadable, or unparseable tracked candidates now block successful output with diagnostics and nonzero exit.
- Preserved getter/setter `definition_id` handling.

`closure.md`:
- Covers A1-A17 and all five normalized Perp rows with current inventory/source links, environment/product/account/mode distinctions, explicit UNKNOWN/N/A labels, current changed/added function lists, and static/live limitations.

## Files written

- `docs/refactoring/vnext/reuse_audit/tx00_current/check.py`
- `docs/refactoring/vnext/reuse_audit/tx00_current/inventory.json`
- `docs/refactoring/vnext/reuse_audit/tx00_current/dispositions.json`
- `docs/refactoring/vnext/reuse_audit/tx00_current/callers.json`
- `docs/refactoring/vnext/reuse_audit/tx00_current/delta.json`
- `docs/refactoring/vnext/reuse_audit/tx00_current/closure.md`

## Counts and limitations

- 47 files, 712 current definitions, 692 historical.
- 679 unchanged, 13 retained-changed, 20 added, 0 removed.
- Exactly 3 changed source files: `etf_trading.py`, `portfolio_ledger.py`, `strategy_runtime.py`.
- 510 tracked candidate inputs; 459 direct import-qualified AST references.
- Evidence is static/lexical only: no runtime, testnet, live, deployment, or execution proof. Dynamic receivers, wildcard/ambiguous imports, reflection, and shadowed names stay unresolved. No product changes, metadata changes, or deletion authority were introduced.
