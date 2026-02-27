# 80) Risk Register

Severity scale: Critical / High / Medium / Low
Likelihood scale: High / Medium / Low

| Risk | Severity | Likelihood | Detection | Mitigation | Owner (lane) |
|---|---|---|---|---|---|
| Bar semantics mismatch (open/close meaning, interval ambiguity) | High | Medium | Validate bar schema + timestamp convention checks in adapter tests; detect non-`1m` timeframe metadata | Enforce canonical `1m` bar contract and explicit close-time semantics in shared schema and adapter normalization | Data+Core |
| Look-ahead / leakage in backtests | High | Medium | Monitor chronological processing invariant and strategy access boundary | Keep event-driven chronological loop and prevent future data access; add invariant tests for strictly non-decreasing timestamps | Core+Backtest |
| Data gaps / duplicates / out-of-order bars | High | High | Ingest validators for monotonic timestamps, duplicate key (`symbol,timestamp`) detection, gap reports | Add deterministic gap/dup/out-of-order policy in parquet/live adapters; fail-fast or canonical repair path | Data |
| Idempotency bugs (same intent submitted twice) | Critical | Medium | Replay same signal/intents and detect multiple submissions with same semantic key | Add mandatory idempotency key in core order request + state store dedupe before adapter submit | Execution+Core |
| Duplicate orders from at-least-once messaging retries | High | Medium | Correlate repeated signal IDs / repeated NATS redelivery with order count spikes | Store signal-idempotency mapping and enforce exactly-once submit behavior in order manager | Execution |
| Rate-limit lockout / API throttling | High | Medium | Track HTTP/WebSocket error rates and broker rejection codes (e.g., transport/rate errors) | Add adaptive retry/backoff and cool-down circuit; classify retryable vs non-retryable errors | Adapter |
| Timezone bugs (naive/aware mix, market-local drift) | High | Medium | Static checks + runtime assert for timezone-aware timestamps in canonical bar flow | Normalize all runtime timestamps to explicit timezone (UTC internally) and convert at adapter boundary only | Data+Core |
| Partial fills handling drift | High | Medium | Verify cumulative fill qty transitions and state update consistency | Implement explicit `PartiallyFilled` state and cumulative fill reconciliation in paper+live broker contract | Execution |
| Silent failures (errors logged but flow continues) | High | Medium | Alert on error logs without state transition/audit record | Convert critical adapter/runner failures into typed events + terminal state updates and surfaced alerts | Ops+Execution |
| Config mistakes (unsafe mode, wrong broker, missing keys) | Critical | Medium | Startup configuration audit and mandatory preflight checklist | Validate config schema at startup; block invalid live configs and add safe defaults | Apps/Config |
| Dependency drift / non-reproducible environment | Medium | High | Dependency diff checks and lockfile presence audit | Pin/lock dependencies for v2 runtime and CI; add periodic update window with compatibility tests | Build/Infra |
| Unsafe live trading defaults | Critical | Medium | Verify startup gate checks for live run path | Require `LIVE_API=1` and `CONFIRM_LIVE=YES` at live entrypoint and adapter creation (defense-in-depth) | Safety+Apps |

## Notes mapped to current repo reality
- Several mitigations above are not yet implemented in v2 and are listed as required controls from constitution-level guidance.
- Legacy v1 has partial protections (kill switch, drawdown trigger, basic order statuses) but no explicit idempotency key field and no dual live env gate.

## Evidence
- Commands run:
  - `nl -ba vibetrading_V2/core/types.py | sed -n '1,260p'`
  - `nl -ba vibetrading_V2/data/parquet_source.py | sed -n '1,320p'`
  - `nl -ba vibetrading_V2/runner/runtime.py | sed -n '1,300p'`
  - `nl -ba vibetrading_V2/strategy/bundle.py | sed -n '1,340p'`
  - `nl -ba vibetrading_V1/backtest/engine.py | sed -n '1,460p'`
  - `nl -ba vibetrading_V1/services/execution/order_manager.py | sed -n '130,520p'`
  - `nl -ba vibetrading_V1/services/execution/crypto_binance.py | sed -n '134,298p'`
  - `nl -ba vibetrading_V1/services/execution/kiwoom_broker.py | sed -n '317,520p'`
  - `nl -ba vibetrading_V1/services/risk_engine/kill_switch.py | sed -n '15,127p'`
  - `nl -ba vibetrading_V1/services/risk_engine/risk_manager.py | sed -n '149,201p'`
  - `nl -ba vibetrading_V1/shared/models.py | sed -n '57,175p'`
  - `nl -ba vibetrading_V1/shared/config.py | sed -n '58,240p'`
  - `nl -ba vibetrading_V1/services/data_feed/kiwoom_feed.py | sed -n '48,60p'`
  - `nl -ba vibetrading_V1/services/data_feed/kr_feed.py | sed -n '353,364p'`
  - `nl -ba vibetrading_V1/services/data_feed/us_feed.py | sed -n '358,369p'`
  - `nl -ba vibetrading_V1/requirements.txt | sed -n '1,120p'`
  - `find . -maxdepth 3 -name 'pyproject.toml' -o -name 'poetry.lock' -o -name 'requirements.txt' | sort`
  - `rg -n "LIVE_API|CONFIRM_LIVE" -S vibetrading_V1 vibetrading_V2 cli AGENTS.md`
- Key output excerpts:
  - dependency file probe showed only `./vibetrading_V1/requirements.txt` (no lockfile/pyproject found at repo top levels).
  - `rg` for live gates found matches only in `AGENTS.md`, not in runtime code paths.
- Concrete references:
  - `vibetrading_V2/core/types.py:21-34`
  - `vibetrading_V2/runner/runtime.py:70-77`
  - `vibetrading_V2/data/parquet_source.py:56-77`
  - `vibetrading_V2/strategy/bundle.py:10-20`
  - `vibetrading_V1/backtest/engine.py:5-9,131-137,150-154`
  - `vibetrading_V1/shared/models.py:57-65,134-169`
  - `vibetrading_V1/services/execution/order_manager.py:130-139,160-185`
  - `vibetrading_V1/services/execution/crypto_binance.py:149-156,179-184`
  - `vibetrading_V1/services/execution/kiwoom_broker.py:355-363`
  - `vibetrading_V1/services/risk_engine/kill_switch.py:19-23,54-93`
  - `vibetrading_V1/services/risk_engine/risk_manager.py:161-201`
  - `vibetrading_V1/services/data_feed/kiwoom_feed.py:56-58`
  - `vibetrading_V1/services/data_feed/kr_feed.py:362-363`
  - `vibetrading_V1/services/data_feed/us_feed.py:367-368`
  - `vibetrading_V1/shared/config.py:203-205`
  - `AGENTS.md:58-64,71-75`
