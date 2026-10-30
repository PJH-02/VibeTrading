# vNext — 기존 문서/코드/증거 충돌 등록부

작성 2026-10-20. **원문 이력 보존 / 해석과 후속 승인을 위한 등록**. 오래된 설명을 삭제하거나 기존 gate를 다시 쓰지 않는다. `승인 필요`는 새 계약/기준 문서 연결 또는 운영 활성화의 의미이며 이번 문서 작성에 재확인을 요청하는 것이 아니다.

| ID | 충돌하는 자료 / 실제 근거 | 이번 해석과 필요한 조치 | 상태·승인 경계 |
|---|---|---|---|
| CR01 | plan 상단/ADR-002는 NATS 제거 확정; plan P05/C05 본문·contracts·parameters 일부는 NATS reuse/독립 여부 OPEN | 최신 사용자 확정이 우선. 최종 local 경로로 계획; 구형 NATS는 backlog/rollback 호환만 | 방향 확정. 기준 문서 정정 연결은 후속 승인 |
| CR02 | 원 contracts/implementation_decisions의 NOT_IMPLEMENTED·DRAFT와 P02~P08 실제 코드 | 코드 존재·scoped 시험·서버 게시·활성 네 상태 분리. 기존 phase를 새 개발로 반복하지 않음 | 현황 확인. C delta 채택 필요 |
| CR03 | gates의 G03-G12 NOT_RUN, G02 NOT_DEPLOYED vs 개별 scoped 기록/P02 실제 적용 | 묶음 과거 상태를 전체 PASS/전체 미구현 어느 쪽으로도 사용하지 않음. P02 schema만 활성 | 현재 receipt로 판정. 원 gate 이력 변경0 |
| CR04 | candidate-v6 manifest의 NOT_UPLOADED vs progress-v6 receipt의 INACTIVE 게시 | 후속 게시 영수증이 시간상 우선; 실행 digest 증거와 별개 | 후보 활성/최종 runtime 배포는 차단 |
| CR05 | 최초 baseline HEAD 3ec7923/미추적 설명 vs 현재 merge HEAD6443eb9 | 현재 source_manifest로 티켓 고정. HEAD tree는 c80bf75와 같아 원격 과거 코드 재도입 없음 | 현황 확인. 원본 baseline 보존 |
| CR06 | v5 전체 PASS vs local_events/local_runtime v6 변경; raw hash drift | v5 기능 변경2, v6 부분만 PASS. 현재 다수 raw SHA 차이는 CRLF/혼합개행. 현재 전체 suite는 새 candidate에서 재검증 | G10 전체/현재 source PASS 주장 금지 |
| CR07 | 10월19일 P02 preflight runtime.yml f50b9aa8… vs 현재 b8453deb… | 의미/권한 변경 원인 미확인. 이번 조사에서 설정을 덮거나 old Compose 적용하지 않음 | **VN00 BLOCKER: live package identity** |
| CR08 | local pipeline 비동기·독립 설명 vs try_send/ACK failure invalid latch | RAM admission 비동기와 Engine outage 중 수신 지속은 다른 계약. VN04A/B로 bounded persistence/delivery 분리 | C05/C08 변경 승인 후 구현 |
| CR09 | producer별 mode·quality vs global source_error/flush/all-LIVE, runtime의 전체 ETF invalid | dependent 전략은 닫되 unrelated feed delivery 유지하는 계약 필요 | C03/C05 변경, VN02/03 |
| CR10 | verified_checkpoint 이름·local_snapshot 파일 존재 vs replay C=0/대시보드 projection | 현재 full-state recovery checkpoint는 없음. 신규 narrow codec/restore 후 GC | C06/포맷 승인, VN06~09. 기존 GC 거절 유지 |
| CR11 | single-owner 목표 vs generic DB-path lease·ETF journal lease 없음 | canonical account lease+실제 legacy fencing 병행. 환경/mock2와 account scope 분리 | C04 보완, 운영 계좌 증거 전 blocked |
| CR12 | generic ExecutionOwner 구현 vs offline adapter/cash-spot 제한 | 테스트/fake를 실제 adapter로 바꾸지 않음. ETF는 기존 KIS permit/journal 사용 | generic live/derivatives 확장은 별도 범위 |
| CR13 | generic halt 모든-side 차단 vs ETF owned reduce-only SELL 허용; resume가 여러 scope 동시 open | 기존 ETF 예외 보존. pause/halt/resume scope/효과/완료 정의 먼저 확정 | 사용자 의미 결정 + VN14A/B |
| CR14 | C07의 새 명령/PnL 목표 vs 실제 _handle status/trade/help/start | inbox/outbox가 있다고 제어 명령이 구현된 것은 아님. 기존 `/trade` 조회를 주문 기능으로 변경하지 않음 | C07 연결/보고 정의 승인 |
| CR15 | generic ACK-after-fill 보존 vs ETF accepted가 pending으로 갱신 | ETF 동일 상태 불변조건은 아직 입증되지 않음. 실제 발생 장애로 단정하지 않고 fault fixture로 검증 | VN12; broker의 실제 ordering은 미확인 |
| CR16 | 기존 cutover/rollback의 운영 미변경/NATS 독립 OPEN 문구 vs P02 제한 배포/제거 확정 | 과거 전체 전환 없음 의미로 읽음. P02 적용만 별도 receipt. 현재 release 패키지로 실행 금지 | 새 VN19 package 필요 |
| CR17 | baseline_manifest/fixture/doc links가 삭제된 tar/JUnit/report/tmp source를 참조 | 보존된 JSON·oracle·baseline source·EXPERIMENT_HISTORY로 범위만 확인. 삭제본 복원/없는 링크를 PASS evidence로 쓰지 않음 | 원본 이력 보존, 새 run에 재측정 |
| CR18 | prepare_candidate의 old allowlist vs 제거된 연구 requirements4 | 기존 파일 복구 없이 현재 allowlist로 credential-free source copy. 새 run launcher/manifest 사용 | VN00, 05에 실제 명령 제공 |
| CR19 | plan의 KR 우선순위/etf_history_contract.py 등 vs 사용자 hotfix 보류·현재 파일 부재 | 자료 수리/재수집 제외. history 부족은 readiness BLOCKER로 유지, 가짜 보정/완화 없음 | 사용자 확정. 새 ADD 작업으로 몰래 포함 금지 |
| CR20 | 10월18일 CPU/disk/유입 짧은 수치를 현재 생산 용량처럼 재사용할 위험 | 10월20일 새 표본도 순간 상태만. λ/μ/72h/PSS/fsync tail은 별도 Missing Evidence | G09/G10/G-NATS-FREE 차단 유지 |

관련 자료: [01 현황](01_current_state_delta.md), [02 설계](02_architecture_review.md), [03 계약 delta](03_contract_delta.md), [04 티켓](04_implementation_plan.md), [05 검증](05_validation_and_cutover.md), [06 인계](06_agent_handoff.md).
