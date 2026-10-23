Implemented the six bounded corrections in `src/vibetrading/market_spool.py` only:

- Allowed distinct consumers at identical confirmed positions in canonical proofs, while retaining identity/bounds and tuple-length enforcement.
- Enforced `base <= consumer sequence <= captured durable` during capture and proof deserialization.
- Enforced v2 incremental `durable - base <= max_records` before batch delivery.
- Prevented v2 incremental durable high-water regression from `self._durable_sequence`.
- Added v2 absolute-sequence bounds for checkpoints, scan records, and iterator delivery.
- Bounded `_inventory_retained` segment metadata accumulation at `max_records + 1` and corrected its docstring.

Command run:

```bash
python3 -m pytest -p pytest_asyncio.plugin -q -p no:cacheprovider tests/refactoring/test_retained_spool.py tests/refactoring/test_retained_storage.py tests/refactoring/test_spool_recovery.py tests/refactoring/test_local_storage.py tests/refactoring/test_checkpoint_startup.py tests/refactoring/test_local_pipeline.py tests/refactoring/test_local_engine.py docs/tickets/VN08-reviewer-counterexamples.py
```

Result: **274 passed in 10.45s** (exit 0). No tests, schemas, metadata, or other source files were modified.
