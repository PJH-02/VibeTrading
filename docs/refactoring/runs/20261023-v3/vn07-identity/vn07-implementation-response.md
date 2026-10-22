Implemented the VN07 checkpoint-specific spool identity guard and regression coverage.

## Reproduction before fix
Deterministic parameterized reproduction (oldest C vs newest unopened suffix):

```bash
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_checkpoint_startup.py::test_segment_replacement_after_c_proof_cannot_open_a_new_suffix_authority
```

Result before fix: `1 failed, 1 passed`
- `oldest-c` passed.
- `newest-unopened-suffix` failed with `DID NOT RAISE RuntimeError`.

## Fix
Changed only the authorized files:

- `vibetrading/local_runtime.py`
  - Added `_SegmentProof`, `_open_checkpoint_segment`, `_capture_segment_proofs`.
  - Added `_CheckpointSpoolReader`, which installs frozen per-segment proofs before `SpoolReader.__init__`, validates the actual opened descriptor on every `_open_segment`, preserves legitimate active growth / same-inode active→sealed rotation, and rejects sealed signature changes.
  - Reworked `replay_checkpoint_startup` to:
    - Capture proofs for the frozen scan inventory.
    - Construct one reader per producer before restore, including C=0.
    - Reuse those exact readers in both apply-order and suffix replay branches; removed post-restore fallback readers.
    - Revalidate the frozen inventory before install and again immediately after restore before applying frames.
    - Close the restored candidate on post-restore failure.

- `tests/refactoring/test_checkpoint_startup.py`
  - Replaced `iterdir()`-dependent replacement test with deterministic parameterization over oldest C and newest unopened suffix.
  - Added C=0 prefix replacement coverage.
  - Added later-segment descriptor-open replacement coverage after earlier checks passed.
  - Added sealed in-place change, missing suffix, and missing historical prefix coverage.
  - Added active-growth / same-inode seal-after-freeze coverage, proving later records remain live input.

## Regression results
Targeted post-fix test:

```bash
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_checkpoint_startup.py::test_segment_replacement_after_c_proof_cannot_open_a_new_suffix_authority
```
`2 passed`

Full checkpoint startup file:

```bash
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_checkpoint_startup.py
```
`32 passed`

Exact acceptance command:

```bash
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_checkpoint_startup.py tests/refactoring/test_checkpoint_runtime.py tests/refactoring/test_vnext_vn07.py tests/refactoring/test_local_runtime.py tests/refactoring/test_replay_handoff.py tests/refactoring/test_spool_recovery.py
```
`181 passed`, exit `0`.

Remaining boundaries: `market_spool.py`, `local_engine.py`, wire/checkpoint formats, accounts, GC, and other files were unchanged. The guard assumes the existing trusted single-writer spool model; it is not an adversarial host-filesystem sandbox. Pre-install metadata failure leaves the engine virgin/unready for caller cleanup, matching existing behavior. Parent VN07 remains incomplete.
