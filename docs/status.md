# v3 local execution status

Authority: [tickets](refactoring/vnext/tickets_production.json), [stages](refactoring/vnext/stage_status.json). `python3 scripts/dev/status.py --root . --json` derives current readiness.

- R00/R01/R02/O00/R03/R04 accepted locally. O01 remains REWORK_REQUIRED for its previously recorded baseline/snapshot bug.
- Current product source `c605c19e79fc635281c6d794dd8a0ba23cb86376`: **1,822 passed**, including 8 existing independent reviewer probes; 2 live-data cases deselected. Guarded product 1,653 + OS-isolated tooling 169. [R04 acceptance](refactoring/runs/20261023-v3/r04/acceptance.json).
- R04: one canonical modern loader under `src/vibetrading/strategies/`; historical candle/state/fair-value APIs retained as adapters, distinct economic formulas unchanged. R05/R06 not started.
- VN07 segment-identity and TX00 TOSS DATA_ONLY subunits accepted. Parent account/strategy recovery and venue/account capability work remain INCOMPLETE.
- Original VN/TX: 14 local complete / 23 incomplete / 14 not started. VN20 cutover/deletion remains external.
- No server deployment or real orders in this local ticket. NATS/rollback/data retained.

정식 패키지 루트는 `src/vibetrading/`입니다. 신규 이벤트 전략은 `vibetrading.strategies.load_strategy`를 사용합니다. 예제는 명시적 호출 때만 로드하며 자동 등록하지 않습니다. C3/KP/KD 순수 계산 분리는 R05 범위입니다.
