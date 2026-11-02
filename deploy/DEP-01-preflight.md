# DEP-01 local release session

This tool assembles a sealed, offline preflight record. It does not contact a
host, start services, submit orders, restore a database, or authorize
activation. Production facts remain `EXTERNAL_ONLY` until a separately approved
and observed CUT-01 process supplies them.

## Plan and inspect

Run from the repository root with the exact frozen REL manifest digest and the
wheel digest supplied by the release builder:

```sh
python -m scripts.refactoring.deployment_session plan \
  --manifest /path/to/REL01-frozen-release.json \
  --manifest-sha256 FROZEN_MANIFEST_SHA256 \
  --release-root /path/to/clean/frozen/source \
  --wheel /path/to/vibetrading.whl --wheel-sha256 VERIFIED_WHEEL_SHA256 \
  --output /private/evidence/release_verification.json
python -m scripts.refactoring.deployment_session preflight \
  --session /private/evidence/release_verification.json \
  --output /private/evidence/preflight.json
```

Plan creation reuses `release_candidate.verify`, including the manifest trust
anchor, current source tree, actual image evidence, wheel archive hash and
wheel RECORD/source checks. Keep the source checkout immutable after freeze.
The resulting report includes role/old-image classification and input lineage;
historical roles remain distinct from present-host observations.

The six modes are `plan`, `preflight`, `install-blocked`, `verify`, `activate`,
and `rollback`. Plan/preflight/verify/activate/rollback are read-only. Activate
always reports blocked pending external CUT-01 authority. Rollback is a binary
plan only; it never rewinds SQLite or copies an old database over current
state. Any P0 session latch blocks subsequent staging. Sealed session JSON
rejects accidental edits and phase/session mixing; it is a consistency seal,
not an authorization signature.

## Private sandbox only

The only write mode is `install-blocked`, which requires all three controls:
an existing dedicated private volume, its `.DEP01-LOCAL-FAKE` marker containing
the exact string below, and an explicit CLI marker argument:

```text
LOCAL_FAKE: DEP-01 PRIVATE SANDBOX ONLY
```

It creates one new versioned directory, copies only frozen allowlisted files,
rehashes every copied byte, and atomically writes `release_verification.json`
with `INSTALLED_BLOCKED`. It does not start a process or alter data/account
state. Existing destinations, links, path traversal, unmarked volumes, and
changed source bytes fail before staging. Use the same sealed session for
`verify`; it reopens and hashes the staged bytes.

## Local evidence and limits

`--host-facts` is a bounded LOCAL_FAKE envelope bound to the frozen source,
manifest and evidence timestamp. It references exactly nine JSON proof
artifacts (`mounts`, `owner`, `sqlite`, `latest_truth`, `disk`, `spool`,
`health`, `paper_data`, `kill_switch`), each by absolute path and SHA-256. Each
artifact must reopen with matching `evidence_class`, `observed_ns`, source and
manifest hashes, deterministic session ID, group, and `payload`. The payloads
are checked against the actual mount contract, `/proc/self/mountinfo`, OS disk
reserve, cooperative lease file, SQLite live and backup rows, latest account
facts, bounded spool records, execution control rows, and referenced health /
PAPER artifacts. The PAPER proof is cross-checked against durable
`portfolio_receipts` and order rows. Missing or stale evidence creates a
persistent P0 read-only latch and blocks staging. Production facts still remain
external, and local proof never authorizes production activation. Noncooperating
owners on other hosts cannot be fenced by a local process lock.

Payload contracts are strict: `mounts` has `contract_path` and
`settings_paths` (`data_root`, `state_dir`, `parquet_root`); `disk` has `path`,
`reserve_bytes`, `reserve_inodes`; `owner` has `authority_root`, `venue`,
`physical_account`, `environment`, `lease_path`; `sqlite` has `source_path`,
`backup_path`, `backup_sha256`, `scope`, `schema_version`, `account_version`;
`latest_truth` has `source_path`, `scope`, `account_version`, `unknown_count`,
and hashes for orders/fills/positions; `spool` has root, producer/incarnation,
four explicit scan limits, durable sequence, record digest and cloud
readback classification; `kill_switch` has source path, scope, strategy ID and
expected epochs; `health` and `paper_data` point to separate hashed proof JSON
files. These structures provide inputs to validators; values alone never pass
a gate.

`snapshot_sqlite` opens the source URI read-only/query-only, uses SQLite's
online backup API (including WAL state), checks scope/schema/account version
and exact bounded row hashes, then reopens the backup and a second new private
restore copy read-only for integrity and metadata checks. It never overwrites
the source or an existing destination and verifies the source file hash stayed
unchanged.
No credential, environment value, or original database content is placed in
diagnostics.

## Sandbox evidence template

`dep01-sandbox.example.json` is a placeholder only. Replace paths and hashes
with measured LOCAL_FAKE evidence; do not use it to claim production readiness.

## Reproduce the independent Linux sandbox probe

Use a clean frozen candidate and actual empty private bind-mounted data/stage directories in a network-isolated test namespace. No user environment or production account is needed. With the existing offline test dependencies and test-only historical adapters on `PYTHONPATH`, run:

```sh
python tests/refactoring/dep01_sandbox_probe.py \
  --release-root /work --data-root /sandbox-data --stage-root /sandbox-stage \
  --manifest /private/frozen-release.json --wheel /private/vibetrading.whl
```

Use new empty mounts for a second run with `--tamper-stage`; it confirms a changed staged source latches P0 and blocks rollback/install. The regular run proves thirteen pre-mutation faults (including a separate-process owner contender), one actual blocked install, current-cut rollback planning, and rejection after a newly durable fill. The ignored-root namespace shadow is independently exercised by `test_ignored_root_strategies_shadow_is_rejected`. These LOCAL_FAKE probes never promote the production host or cloud observations.
