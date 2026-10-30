# VN08Q — Additional storage recovery tests through FreeLLMAPI

Parent VN08 only. OpenCode build/implement worker, exact freellmapi/glm-4.5-flash profile. Do not substitute providers/models. Codex accepts independently.

## Write scope
ADD ONLY tests/refactoring/test_retained_acceptance.py (max250 lines). No product edits, old-test edits, metadata edits, commit/push, packages, network/accounts or recursive delegation. Read these EXACT absolute paths using Read (do not guess or search):
- /work/src/vibetrading/market_spool.py
- /work/src/vibetrading/local_storage.py
- /work/tests/refactoring/test_local_storage.py
Write ONLY /work/tests/refactoring/test_retained_acceptance.py. No /work/vibetrading path exists. Avoid ls/find/grep/python-c: command filter denies them. Read the named files, write the test file, run the exact pytest command. All other files are read-only import dependencies.

## Objective
Add meaningful independent integration tests for the actual existing storage journal/manifest and retained-prefix seam, rather than copying implementation assertions. Keep original sources intact and construct pruned TEST COPIES by selecting copied files. No unlink/production data deletion.
Use existing test_local_storage context fixture (legacy/compact), budgets, item, trade, wire, observation, SPOOL/READ and original MarketSpool. Use a source with3 records, each explicitly sealed into its own segment. First LocalSpoolStorage with batch_records=1 commits exactly sequence1 and closes. Capture prefix proof before omission: first_retained_segment=2, generation='vn08q', consumer_cursors=(SpoolCursor(source.producer, source.incarnation,1),), **READ. Copy checkpoint/segments>=2 into a NEW test path, update the in-memory source.spool_root only, reopen with retained_prefixes mapping. Do not manufacture progress/checkpoint rows.
Required cases (about4-5 functions; legacy/compact parameterization yields8-10 cases):
1. Actual market progress exactly at base1 resumes2/3, progress3, original source bytes unchanged, existing Parquet event IDs/economic Decimal values unchanged and no duplicate output. Reopen again above base and verify no extra output. Use actual store/manifests, not a dummy counter.
2. Actual observation/fact progress exactly at base resumes2/3; inspect existing local_fact rows and raw payload bytes. Same progress survives repeated restart.
3. After real fact progress1, deliberately alter only that temporary local_fact.raw_payload (leave local_progress hash unchanged). Retained startup must reject before delivering/progress advancement; retained source and existing progress preserved. Synthetic temporary DB only.
4. For real market progress1, make existing RuntimeStore.delivery_committed returnFalse (monkeypatch, no DB deletion) before reopening. Must reject retained startup, no risk of trusting proof hash alone. Preserve progress/source.
5. Unknown producer mapping must be rejected before shared store.prepare_event_index/writer work. Use a sentinel monkeypatch to detect ordering. No need to test unrelated errors.
Use real APIs/fixture assertions, don't weaken production checks to satisfy tests. If fixture setup needs correction, fix only the new file. Run all new tests and original local_storage tests once. No sleeps/property stress or huge rewrites.

## Exact command
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_retained_acceptance.py tests/refactoring/test_local_storage.py

Return actual command and exit, any unresolved gap, changed path only. Your success statement is not acceptance. No metadata self-approval.
