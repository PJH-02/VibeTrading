# Final refactoring status index

현재 실행 권위는 다음 다섯 문서뿐입니다.

- [최종 계획](final/01_FINAL_PLAN.md)
- [개발 티켓](final/02_DEVELOPER_TICKETS.md)
- [아키텍처 요구사항](final/03_ARCHITECTURE_REQUIREMENTS.md)
- [과거 완료 manifest — 불변](final/04_COMPLETED_MANIFEST.json)
- [현재 작업 상태와 DAG — 단일 쓰기 원천](final/05_TASK_STATUS.json)

기존 [tickets](refactoring/vnext/tickets_production.json)와
[stages](refactoring/vnext/stage_status.json)는 읽기 전용 역사 근거입니다.
OPS-01 전환 전 `scripts/dev/status.py`는 구 v3 투영이며 신규 READY 권위가 아닙니다.
현재 코드 기준선·진행·수락 Receipt는 05를 확인합니다. 실서버·실주문 승인은 없습니다.
최종 목표는 Flat `src/`이며 실제 이동·수락 여부는 MOV 티켓 근거를 따릅니다.

## Historical snapshot — 2026-10-23, read-only

아래 수치와 패키지 설명은 과거 기록이며 현재 상태나 실행 지시가 아닙니다.

- R00/R01/R02/O00/R03/R04/R05 accepted locally. O01 remains REWORK_REQUIRED for its previously recorded baseline/snapshot bug.
- Current product source `2b85c95a4e7951ac2a7590db24416e5b5f76d958`: **1961 passed** (1792 guarded product + 169 isolated tooling, including 8 prior review probes); 2 live-data cases deselected. [R05 acceptance](refactoring/runs/20261023-v3/r05/acceptance.json).
- R04: one canonical modern loader under `src/vibetrading/strategies/`; historical candle/state/fair-value APIs retained as adapters, distinct economic formulas unchanged. R05 pure C3/KP/KD decision extraction accepted; R06 not started.
- VN07 segment-identity and TX00 TOSS DATA_ONLY subunits accepted. Parent account/strategy recovery and venue/account capability work remain INCOMPLETE.
- Original VN/TX: 14 local complete / 23 incomplete / 14 not started. VN20 cutover/deletion remains external.
- No server deployment or real orders in this local ticket. NATS/rollback/data retained.

정식 패키지 루트는 `src/vibetrading/`입니다. 신규 이벤트 전략은 `vibetrading.strategies.load_strategy`를 사용합니다. 예제는 명시적 호출 때만 로드하며 자동 등록하지 않습니다. R05에서 선택된 C3/KP/KD 판단 계산을 `strategies/kr/`로 분리했고, 데이터 준비·계좌·주문 런타임은 보존했습니다.
