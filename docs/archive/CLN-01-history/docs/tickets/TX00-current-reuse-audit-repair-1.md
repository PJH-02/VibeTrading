# TX00 bounded correction 1 — preserve getter/setter definitions

Read the base ticket `docs/tickets/TX00-current-reuse-audit.md`. Its same six writable paths and exact two commands remain in force. Complete its implementation, not another probe. The initial attempt was stopped by Codex with exit -15 after 167 seconds when independent review found the ticket's unique-symbol requirement contradicted actual source: `KisCashBroker.authority_check` is both property getter (line59) and setter (line63). Only a 2560-byte temporary diagnostic checker was written, other outputs stayed empty; no implementation is being silently accepted or discarded as complete.

This correction **supersedes** only the base ticket's uniqueness/key rule:

- Baseline now contains `definition_id = qualified_symbol + '@' + per-symbol definition ordinal` (1-based in lexical source order) on ALL 692 historical rows. Compare `(path,definition_id)`. Preserve both getter and setter. Keep human `symbol` and decorators on current rows. Generate IDs with the same source-order rule. Multiple definitions are not an error; duplicate definition IDs are. Historical disposition lookup for duplicated symbol uses matching historical line; same getter/setter may have the same classification.
- Source AST hashes are explicitly Python3.12.3 `sha256(ast.dump(node, include_attributes=False).encode('utf-8'))`. Baseline includes one exact dump and hash for reproducibility. Both coordinator and sandbox use Python3.12.3. Do NOT guess hash encodings or strip AST fields. No need for any further diagnostic probe.
- New helper-only definitions belong to accepted VN07/VN08 recovery context, not new runtime/platform functionality. Existing raw reports/matrix remain historical and must be explicitly linked with current inventory translation.
- No shell exploration, `ls`, `grep`, `wc`, `true`, shell separators or `echo EXIT` are allowed/needed. Tool read/edit works. Execute ONLY these exact shell strings after writing the complete checker:

```
python3 docs/refactoring/vnext/reuse_audit/tx00_current/check.py --root . --write
python3 docs/refactoring/vnext/reuse_audit/tx00_current/check.py --root . --check
```

The tool already reports command exit. Stop after both commands and closure.md. No separate pytest suite or product changes. Keep the checker compact and conservative; unknown/dynamic/ambiguous receivers stay UNKNOWN. Correct current evidence matters more than reproducing historical caller counts.
