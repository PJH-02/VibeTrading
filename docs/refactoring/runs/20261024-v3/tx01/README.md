# TX01 existing Signal/OrderIntent/Paper contract — local acceptance

Source `1d1c2c8f6f0a76745fce9c04208f4e031c444cdd`; base `fad4e81bdabbc689a8757ccec43da81acf5300d4`. Existing execution contracts, action builder and Paper matcher/ledger are reused. Only PaperStrategyRuntime._validate changed:17 added guard lines for full execution/reference InstrumentId, bounded legacy IDs and causal integer NEW creation time.37 other runtime methods are AST-identical. No new API, engine, schema, arithmetic, venue/cloud wrapper or strategy plugin.

OpenCode direct `opencode/deepseek-v4.1-flash-free` wrote the product change and28 new tests; worker/delegate exits0, no repair delegation or owner product edit. Early worker no-tests exit5 is retained in worker-test-exits.json, not used as acceptance evidence. FreeLLMAPI configuration/model profiles stay unchanged; this refactor used the validated direct route and no fallback.

Before:236 existing tests passed.23 independent review cases on the old code produced238 PASS/21 FAIL, demonstrating unsafe bindings. After:287 scoped PASS (236 existing+28 worker+23 independent); full guarded product2001+isolated tooling169=2170 PASS,2 external live-data cases deselected. Counts overlap. All runs use synthetic local accounts, credential-free OS snapshots and offline guard/network isolation. A missing transitive SQLite input in the first baseline snapshot was fixed only in the disposable harness.

Invalid NEW batches stop before action claims/order admission. Existing callback claims and fault/cancel policy remain unchanged; no whole-callback atomic rollback is claimed. Original Signal/OrderIntent wire, exact Decimal values, explicit Kiwoom reference→KIS ETF Paper mapping, TOSS DATA_ONLY, C3/KP/KD Golden and Mock/Mock2 are preserved. CANCEL/REPLACE, existing tick/lot/mode checks, dedup and risk math remain authoritative in existing code. No live readiness, deployment, actual GC/deletion or subsequent ticket work is claimed.

See acceptance.json, scope-review.json, independent-final.json, original-counterexamples.json, full-regression.json and final metadata receipts. Next bounded node is TX02A; it was not started.

Original Windows/WSL checkout application PASS: all174 preexisting dirty entries (153 EOL-only plus substantive edits/deleted/untracked documents) preserved. All24 scoped paths and3 accepted product/test hashes verified, allowing only existing CRLF representation. See original-application.json.
