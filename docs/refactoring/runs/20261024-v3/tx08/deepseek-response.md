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
​
​
​
​
​
​
​
​
​Implemented TX08 repair within the single permitted file.

**Exact execution command**
```
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_tx07a_fact_contract.py tests/refactoring/test_tx08a_account_policy.py tests/refactoring/test_tx02a_owner_fence.py tests/refactoring/test_tx02b_attribution.py tests/refactoring/test_tx02_joint_gate.py tests/refactoring/test_tx04b_continuous.py tests/refactoring/test_vn13_file_registration.py tests/refactoring/test_tx05_file_paper_e2e.py tests/refactoring/test_runtime_checkpoint_integration.py tests/refactoring/test_tx08_same_account_gate.py
```

**Actual result**
- Exit code: `0`
- Count: `97 passed in 1.66s`

**Scoped diff**
- Modified only `tests/refactoring/test_tx08_same_account_gate.py`.
- Replaced the post-restore direct `host.process_event(buy_trade)` path with a real spool append at transport sequence `4`, converted the actual append receipt cursor into the engine `RecoveryFrame`, and fed it through `engine.on_frame`.
- Added assertions for spool/engine cursor agreement, `applied_sequence == 4`, hot-path `apply_seq == 3`, `LIVE/PAPER` input phase, and unchanged ledger/matcher/callback/action/fill facts.
- Advanced final fresh trade to transport `5`, duplicate frame `5`, and stale frame `6`.
- No direct runtime dispatch call, phase assignment, product monkeypatch, or fabricated cursor remains on the repaired path.
- No production/config/old tests/Golden/metadata files changed; no installs, live/network/account/Git mutation activity occurred.

**Limits**
- Local PAPER/synthetic fixture only.
- Existing fairness, budget-policy, scarce-capital, and cross-venue claims remain out of scope exactly as stated by TX08.
