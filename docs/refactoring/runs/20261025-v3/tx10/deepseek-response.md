Implemented TX10 within the allowlisted scope.

## Changed functions/files
- `src/vibetrading/strategy_runtime.py`
  - Added stdlib imports: `stat`, `pathlib.Path`.
  - Added `PaperStrategyRuntime._validate_data_manifest(receipt)`.
  - Called it from `register_configured` before `load_strategy`, durable claims, registration, or plugin creation.
- `tests/refactoring/test_tx10_manifest_registration.py`
  - Added TX10 coverage for verified export manifests, Paper order dispatch, absent optional descriptor behavior, checkpoint identity binding, manifest/file tampering, traversal/absolute/symlink paths, duplicate rows/keys, malformed rows, bounds, timestamps, bool/NaN, size/hash mismatch, and missing objects.

## Validation behavior
- Descriptor is optional; absent `research_receipt["data_manifest"]` preserves existing behavior.
- Requires the exact six descriptor fields and bounded values.
- Rejects symlinked manifest paths or referenced-object paths.
- Reads the manifest only up to `max_manifest_bytes + 1`, verifies raw SHA-256 before JSONL parsing.
- Requires unique JSON object keys, dict rows, required exporter fields, unique `file_id`/`relative_path`, relative non-traversing paths, regular files, exact declared sizes, and chunked SHA-256 verification within `max_total_bytes`.
- Rejects files that grow beyond declared size without unbounded reads.
- Descriptor remains inside the frozen `research_receipt` and therefore the runtime fingerprint/checkpoint identity.

## Limit
This is a point-in-time provenance check, not an atomic immutable filesystem snapshot. It does not claim the hash proves future immutability.

## Test execution
Exact command run:

```text
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_vn13_file_registration.py tests/refactoring/test_strategy_runtime.py tests/refactoring/test_strategy_restart_barrier.py tests/test_market_export_analysis.py tests/test_export_duplicate_reclaim.py tests/test_storage_pipeline.py tests/refactoring/test_tx10_manifest_registration.py
```

Result: **exit 0, 141 passed in 2.33s**. No Git mutations, network/account calls, installs, or changes outside the allowlisted files.
