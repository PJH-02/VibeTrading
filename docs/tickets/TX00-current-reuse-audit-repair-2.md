# TX00 final bounded correction 2 — fail closed, publish real evidence

This is the SECOND AND LAST repair delegation. Read the base and correction1 tickets; same six writable paths, no product/test/canonical metadata changes. Seeded check.py is the previous partial implementation. Keep its useful AST/disposition logic. Finish the JSON evidence and closure.md. Do not start over or add abstractions.

Previous attempt was stopped with exit -15 after 486 seconds: the nested shell sandbox incorrectly remounted permitted evidence files read-only. Codex fixed and independently verified that infrastructure: the EXACT checker commands can now write the six permitted evidence paths; product source remains read-only and shell network remains isolated. **Do not put any read-only workaround into the checker.**

Required fixes, independently reproduced:

1. Delete `MIRROR_DIR`, mirror reads/writes, fallback/warning-success paths. Write ONLY the four declared JSON siblings. An unwritable intended destination is an error/nonzero, never a successful /tmp mirror. `--check` reads only the four intended outputs, never a mirror. The script must not falsely succeed while required output files are empty.
2. `CallCollector._resolve_expr`: a receiver which is an ast.Call has UNKNOWN returned-object type. Never invent `broker_for_name.submit_order` from `broker_for_name('toss').submit_order(...)`. Return unresolved for a Call-valued receiver; generic visiting should still collect the inner direct factory call once. Do not infer returned types from constructor spelling.
3. Wildcard imports may shadow a known imported name. If relevant lexical scope/ancestor has a wildcard, conservatively leave name resolution UNKNOWN instead of claiming a venue call. Preserve honest unresolved counts/limitations. Ambiguous same-name imports from different modules (especially alternate branches) must also remain unresolved; do not choose the last AST-walk binding as a definite target. Existing parameter/assignment/lambda shadowing and nested-once handling must remain correct.
4. On `git ls-files` failure, return errors/nonzero. Remove filesystem fallback/rglob entirely. Never scan untracked source or imply tracked-only coverage from a fallback.
5. Any missing/unreadable/unparseable tracked candidate must block successful evidence generation/check. Record diagnostic errors, return nonzero; do not silently skip it while certifying complete caller coverage.
6. Preserve both getter/setter with definition_id. Independent current AST oracle: 47 files,712 definitions;679 retained unchanged,13 retained changed,20 added,0 removed. Exactly3 changed source files, all existing ETF/Paper recovery seams. Do not hardcode these as substitutes for calculation. Current labels remain static evidence, never runtime/live proof.

`docs/refactoring/runs/20261023-v3/tx00-current/repair1-independent-review.json` gives exact synthetic cases and findings. Source faults were only synthetic AST snippets/mocked git results, no product mutation. Codex will rerun those plus independent AST/hashes and actual checker --check. No claims of passing commands until actual exit0.

After fixes execute EXACT strings (no separators/echo/ls/grep):

```
python3 docs/refactoring/vnext/reuse_audit/tx00_current/check.py --root . --write
python3 docs/refactoring/vnext/reuse_audit/tx00_current/check.py --root . --check
```

Then write closure.md using the generated current rows for line numbers. Cover matrix A1–A17 plus five normalized Perp rows with explicit environment/product/account/mode/UNKNOWN distinctions, current function references and honest live/testnet limits. Preserve all old reports and all six scoped outputs. Return exact exits, counts, files and limitations. Codex alone accepts; no other ticket starts.
