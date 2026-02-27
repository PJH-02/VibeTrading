# 50) Bar Semantics

## Decision: Canonical Timestamp Convention
**Proposed canonical convention:** `Bar.ts` is the **bar CLOSE time** in timezone-aware UTC.

### Why this is the safest canonical choice
- **Observed V2 ingestion already expects sortable bar timestamps** and builds bars from `timestamp` after sorting (`vibetrading_V2/data/parquet_source.py:56-77`).
- **Observed V2 tests use UTC-aware timestamps** in both data-source and runner tests (`vibetrading_V2/tests/test_parquet_source.py:29-38`, `vibetrading_V2/tests/test_runner_flow.py:16-26`).
- **Observed V1 live feeds often emit non-final candles (`is_closed=False`)** and use `datetime.now()` (ingest-time approximation), which is unsuitable as deterministic backtest canonical timestamps (`vibetrading_V1/services/data_feed/kr_feed.py:161-173`, `vibetrading_V1/services/data_feed/us_feed.py:159-171`).
- **Observed V1 crypto feed explicitly distinguishes final bars** via `is_closed` and only persists closed bars (`vibetrading_V1/services/data_feed/crypto_feed.py:204-217`, `:303-307`).

## Observed Current Behavior (Repository Reality)

### Observed in V2
1. `ParquetDataSource` requires OHLCV + `timestamp`, sorts by `timestamp`, and converts each row to `Bar` (`vibetrading_V2/data/parquet_source.py:17-77`).
2. V2 currently does **not** define:
   - timestamp semantic (open vs close),
   - timezone policy enforcement,
   - gap/duplicate/out-of-order resolution policy.
   (No such checks exist in `ParquetDataSource` beyond sort/type checks.)
3. V2 strategy metadata still allows multiple timeframes (`"1m", "5m", "15m", "1h", "1d"`) (`vibetrading_V2/strategy/bundle.py:10-18`).

### Observed in V1
1. `Candle` includes `interval` and `is_closed` flags (`vibetrading_V1/shared/models.py:92-104`).
2. KR/US realtime feed converts tick-like payloads into approximate 1m candles with `is_closed=False` and `datetime.now()` timestamps (`vibetrading_V1/services/data_feed/kr_feed.py:161-173`, `vibetrading_V1/services/data_feed/us_feed.py:159-171`).
3. Kiwoom historical can fetch true minute bars and sorts ascending before return (`vibetrading_V1/services/data_feed/kiwoom_feed.py:320-323`, `:382-385`, `:440-452`).
4. KIS KR/US historical paths map `"1m"` to daily codes (limitation for minute semantics) (`vibetrading_V1/services/data_feed/kr_feed.py:354-364`, `vibetrading_V1/services/data_feed/us_feed.py:358-369`).
5. Backtest loader and engine both depend on chronological order for look-ahead safety (`vibetrading_V1/backtest/data_loader.py:45-47`, `:82-89`; `vibetrading_V1/backtest/engine.py:135-137`, `:150-154`).

## Timezone / Session / Calendar Policy

### Observed
- Mixed timestamp behavior exists in V1 (`datetime.now()`, `datetime.utcnow()`, and exchange UTC timestamps) (`vibetrading_V1/services/data_feed/kr_feed.py:165`, `vibetrading_V1/services/data_feed/crypto_feed.py:207`, `vibetrading_V1/services/data_feed/crypto_feed.py:382`).
- Sample curated Parquet in this repo stores `timestamp` as `datetime64[ns, UTC]` with 1-minute progression (command output in Evidence).
- No explicit holiday/weekend/calendar policy was found via code search (`rg -n "holiday|weekend|calendar|market_hours|trading_hours|session_boundary|trading_session" vibetrading_V1 vibetrading_V2` returned no matches).

### Proposed
1. Normalize all bars to timezone-aware UTC at adapter boundaries.
2. Define `ts` as **close time** for both live and historical adapters.
3. Treat any adapter-local timezone/session logic as pre-normalization concerns; core consumes only normalized UTC close-time bars.

## Deterministic Data Quality Rules (Proposed)

### Missing bars
- **Rule:** do not forward-fill OHLCV in core ingestion. Keep observed bars only, and emit explicit gap metadata (`gap_count`, `prev_ts`, `next_ts`) for reporting.
- **Reason:** preserves auditability and avoids synthetic price leakage.

### Duplicate bars
- **Dedup key:** `(symbol, ts, timeframe)`.
- **Winner rule:** keep the **last** record after stable sort by ingestion order (or source sequence if provided), and log overwrite count.

### Out-of-order bars
- **Rule:** sort by `ts` before strategy loop; reject bars older than the latest committed `ts` in streaming mode unless within a bounded reorder window.
- **Reason:** deterministic runner behavior + stable replay semantics.

## Testable Invariants (Proposed)
1. `ts` is strictly increasing within a symbol stream after normalization.
2. Adjacent bars are exactly 60 seconds apart for contiguous segments.
3. No duplicate `(symbol, ts, timeframe)` after dedup.
4. `open/high/low/close/volume` are present and numeric for every emitted bar.
5. Only `is_closed=True` bars reach deterministic backtest execution.

## Parquet Storage Spec (Proposed, aligned to current V2 adapter)
Required columns:
- `timestamp` (UTC datetime, canonical close time)
- `open`, `high`, `low`, `close`, `volume` (float-like)

Recommended additional columns:
- `symbol` (if multi-symbol files are used later)
- `timeframe` (fixed `"1m"`)
- `is_closed` (`True` for canonical backtest bars)
- `source`, `ingested_at` (lineage/debugging)

This remains compatible with current V2 `ParquetDataSource` minimum required columns (`vibetrading_V2/data/parquet_source.py:17`, `:44-55`).

## Evidence
- `nl -ba vibetrading_V2/data/parquet_source.py | sed -n '1,120p'`  
  - Shows required columns, timestamp checks, sort-by-timestamp, and Bar construction (`:17`, `:44-57`, `:62-77`).
- `nl -ba vibetrading_V2/tests/test_parquet_source.py | sed -n '1,90p'`  
  - UTC-aware timestamps + sorted-order assertion (`:29-38`, `:53`).
- `nl -ba vibetrading_V2/tests/test_runner_flow.py | sed -n '1,120p'`  
  - UTC-aware bars used in runner flow test (`:16-26`, `:46-52`).
- `nl -ba vibetrading_V2/strategy/bundle.py | sed -n '1,40p'`  
  - Timeframe type currently allows non-1m values (`:10-18`).
- `nl -ba vibetrading_V1/shared/models.py | sed -n '92,110p'`  
  - `Candle` fields include `interval` and `is_closed`.
- `nl -ba vibetrading_V1/services/data_feed/kr_feed.py | sed -n '151,176p;353,365p'`  
  - Realtime uses `datetime.now()` and `is_closed=False`; historical `1m -> D` mapping.
- `nl -ba vibetrading_V1/services/data_feed/us_feed.py | sed -n '149,174p;358,370p'`  
  - Same realtime approximation and historical `1m` daily mapping in US feed.
- `nl -ba vibetrading_V1/services/data_feed/crypto_feed.py | sed -n '199,218p;303,307p;366,413p'`  
  - Exchange UTC timestamps, `is_closed` semantics, closed-only persistence, and historical query order.
- `nl -ba vibetrading_V1/services/data_feed/kiwoom_feed.py | sed -n '320,385p;440,452p'`  
  - Minute TR support and sorted return path.
- `nl -ba vibetrading_V1/backtest/data_loader.py | sed -n '45,48p;82,89p'` and `nl -ba vibetrading_V1/backtest/engine.py | sed -n '135,154p'`  
  - Explicit chronological-order requirements for look-ahead safety.
- `python - <<'PY' ... pd.read_parquet('data/curated/BTC-USD.parquet') ... PY`  
  - Output showed columns `['timestamp','open','high','low','close','volume']`, dtype `timestamp: datetime64[ns, UTC]`, and 1-minute sample rows (`2026-01-01 00:00:00+00:00`, `00:01:00+00:00`).
- `rg -n "holiday|weekend|calendar|market_hours|trading_hours|session_boundary|trading_session" vibetrading_V1 vibetrading_V2`  
  - No matches (no explicit holiday/weekend/session-boundary policy found in code).
