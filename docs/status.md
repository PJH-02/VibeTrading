# v3 local execution status

2026-10-23 현재 단계: [최소 재사용 계획](refactoring/vnext/VibeTrading_Minimal_Reuse_Plan_20261023.md) 확정. 기존 거래소/주문/클라우드 코드는 변경·삭제하지 않았습니다. 47개 파일·692개 함수의 정적 인벤토리, 37개 미래 VN/TX 범위 축소, 14개 완료 VN 근거 보존. 아래 R05 결과는 제품 기준선이며, 이번 단계는 STATIC/DOC입니다. 신규 구현은 착수하지 않습니다.

Authority: [tickets](refactoring/vnext/tickets_production.json), [stages](refactoring/vnext/stage_status.json). `python3 scripts/dev/status.py --root . --json` derives current readiness.

- R00/R01/R02/O00/R03/R04/R05 accepted locally. O01 remains REWORK_REQUIRED for its previously recorded baseline/snapshot bug.
- Current product source `2b85c95a4e7951ac2a7590db24416e5b5f76d958`: **1961 passed** (1792 guarded product + 169 isolated tooling, including 8 prior review probes); 2 live-data cases deselected. [R05 acceptance](refactoring/runs/20261023-v3/r05/acceptance.json).
- R04: one canonical modern loader under `src/vibetrading/strategies/`; historical candle/state/fair-value APIs retained as adapters, distinct economic formulas unchanged. R05 pure C3/KP/KD decision extraction accepted; R06 not started.
- VN07 segment-identity and TX00 TOSS DATA_ONLY subunits accepted. Parent account/strategy recovery and venue/account capability work remain INCOMPLETE.
- Original VN/TX: 14 local complete / 23 incomplete / 14 not started. VN20 cutover/deletion remains external.
- No server deployment or real orders in this local ticket. NATS/rollback/data retained.

정식 패키지 루트는 `src/vibetrading/`입니다. 신규 이벤트 전략은 `vibetrading.strategies.load_strategy`를 사용합니다. 예제는 명시적 호출 때만 로드하며 자동 등록하지 않습니다. R05에서 선택된 C3/KP/KD 판단 계산을 `strategies/kr/`로 분리했고, 데이터 준비·계좌·주문 런타임은 보존했습니다.
