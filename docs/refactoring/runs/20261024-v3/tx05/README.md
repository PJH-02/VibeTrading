# TX05 — 기존 파일 전략/Paper 경제 E2E 수락

선택 티켓 TX05 하나만 진행했다. OpenCode `opencode/deepseek-v4.1-flash-free`가 구현/시험하고 Codex가 명세·수동 경제값·소스 리뷰·독립 실행으로 수락했다. FreeLLMAPI/Codex 인증과 모델 설정은 보존했으며 upstream 가중치 실체는 UNKNOWN이다.

실제 별도 .py 파일의 설정 등록→실제 NATS-free engine 입력→기존 Paper 체결·수수료·PnL→실제 checkpoint restore의 후속 상태와 중단 없는 실행을 대조했다. 초기 부분 체결/예약금, 중복 입력, 잔여 주문 취소, callback raise/slow-return과 정상 형제 전략을 포함한다. 제품 코드·기존 tests/Golden 변경0, 새 통합 테스트만 추가했다.

고정 source `1d39a44779ff67f39e9ba9217d9e70a9498979cd`: **2325 PASS / 2 live-data 제외**, 독립 scoped **286 PASS**. [수락](acceptance.json), [전체 회귀](full-regression.json), [입력/실제 명령](full-source-inputs.json), [최종 상태 검사](final-scope-status-validation.json)에 종료/해시/마운트를 기록한다. 이 결과는 신뢰된 cooperative 로컬 파일 전략의 기존 계약이며 실제 서버/실계좌나 callback hard preemption/process isolation을 입증하지 않는다. ETF C3/KP/KD/Mock/Mock2/TOSS DATA_ONLY 및 거래소/cloud 경로를 보존했다.

현재 **27 완료 / 12 미완료 / 12 미착수**다.

- **완료 (27):** VN00, VN01, VN02, VN03, VN04A, VN04B, VN05, VN06, VN07, VN08, VN09, VN10, VN11, VN12, VN13, VN14A, VN14B, VN16, TX00, TX01, TX02A, TX02B, TX02, TX04A, TX04B, TX04, TX05.
- **미완료 (12):** TX03A, TX03B, TX03, TX06A, TX06B, TX06C, TX06D, TX06E, TX06, TX08A, TX08B, TX08.
- **미착수 (12):** VN15, VN17, VN18, VN19, VN20, TX07A, TX07B, TX07, TX09, TX10, TX11, TX12.

기존 사용자 변경174건은 별도 보존하고, 로컬 수락/커밋만 수행한다. 원격 반영은 이번 로컬 작업에 포함하지 않았다. 다음 티켓 자동 dispatch는 중지했다.
