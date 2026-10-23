Implemented R05 within the listed files.

## Changes
- Added pure shared helper `src/vibetrading/strategies/kr/_intraday.py` with the frozen `intraday_decision` body unchanged.
- Added `kp.decide(feature, book)` and `kd.decide(feature, book)` adapters.
- Added `open_decision(z, mis, sigma)` in `src/vibetrading/strategies/kr/c3.py` with the exact frozen guard/sizing/detail calculation.
- Kept `etf_trading.intraday_decision(strategy, feature, book)` as the compatibility dispatcher: KP/KD delegate to the pure adapters, other names fall back to the shared original helper. No other `etf_trading` body changed.
- `EtfHistory.c3` now imports `open_decision` and delegates only its final two statements. Historical regression/calendar/readiness logic untouched.
- Added `tests/refactoring/test_kr_strategy_extraction.py`, using `runpy.run_path` on the read-only pre-extraction oracle, with exact tuple/exception parity for KP/KD/other strategies, position and stop boundaries, entries/usability, signal values, sizing caps, missing keys, invalid volatility, and C3 thresholds/guards/runtime delegation.

## Command run
```text
python3 -m pytest -q -p no:cacheprovider -p pytest_asyncio.plugin tests/refactoring/test_kr_strategy_extraction.py tests/test_etf_mock_trading.py tests/test_etf_execution_update.py tests/test_etf_outage_recovery.py tests/test_dual_mock_and_assumed.py tests/refactoring/test_etf_dispatch_guards.py tests/refactoring/test_vnext_vn11.py tests/refactoring/test_vnext_vn12.py
```

## Result
```text
2 failed, 247 passed in 3.83s
```
Exit code: 1.

The new extraction/parity tests all passed. The two failures are unrelated environment failures in existing `tests/test_etf_mock_trading.py`:
- `test_etf_nav_is_native_typed_and_roundtrips_to_parquet`
- `test_minute_start_labels_and_unfinished_bar_rejection`

Both fail while constructing `KiwoomCollector` because `get_settings()` defaults to `/work/runtime_data` and the sandbox filesystem is read-only:
```text
OSError: [Errno 30] Read-only file system: '/work/runtime_data'
```
No source change was made for these since they are outside the ticket edit scope.
