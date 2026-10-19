# 현재 실행 인계 — 2026-10-19

최신 사용자 지시: 누락 분봉 작업을 보류하고 원 계획대로 리팩토링한다. 완료분을 서버에 반영한 뒤 다음 작업을 진행한다. 단순 리뷰는 Luna, 재독 대신 diff·기존 receipt, KISS/DRY/YAGNI를 적용한다.

현재 위치는 **P09 역할별 서버 전환 후보 구성**이다. P02 schema cache는 storage/crypto-storage에 실제 적용됐다. 두 서비스 healthy·실행 파일 SHA·저장/ACK 증가·다른 컨테이너 불변을 확인했다. 릴리스 경로는 `/opt/vibetrading/releases/refactoring-20261019-p02-schema`다. 기존 약 3,050만 crypto 이벤트 인덱스 재검증으로 최초 readiness timeout이 발생했지만 같은 컨테이너의 초기화 진행을 확인해 재시작 반복 없이 완료했다. DB 복원/마이그레이션과 주문/수집기 재시작은 없었다. 관련 receipt는 `p02-server-resume-receipt.json`, `p02-server-health.jsonl`이다.

P03~P08은 격리 구현·검증 상태이며 운영 전환은 아직 아니다. 통합 v5 전체 Windows1078 PASS/9 SKIP/2 DESELECT, Linux1087 PASS/2 DESELECT. 이후 P08 체크포인트 WAL pin/재시작 상한을 보완했고 v6 관련 Windows46 PASS/1 SKIP, Linux47 PASS와 Luna 리뷰를 통과했다. 최근 두 제품 파일만 변경됐으며 전체 v6 suite를 돌린 것으로 보고하지 않는다.

`server-active-owner-after-p02.json`은 실제 모듈 소유자 기준선에 적용된 한 파일을 반영한다. `server-active-owner-candidate-v6.json`은 그 기준에 리팩토링을 합성한 후보이며 충돌0, v6의 vibetrading 제품130파일과 바이트 동일하다. 후보는 비활성이며 full NATS-free runtime을 활성화하지 않았다.

다음 작업은 P09의 역할별 mount/자원/용량 계약과 이미지 구성, P10 실제 지속 실행·복구/부하 증거, P11 점진 전환이다. 72시간 측정은 두 번 중단돼 coverage gap이 있다. 가상1801초를 실제30분 운영으로 대체하지 않는다. 생산 peak·대표 부하·archive/GC·구형 NATS backlog 보존·전체 cutover는 아직 열려 있다. 기동 인덱스 재검증과 backlog 영향도 운영 검증에 포함한다.

누락 분봉 hotfix는 `DEFERRED_BY_USER`; 원본 native/remote 조사 결과와 후보만 보존했고 서버에 적용하지 않았다. 원본 `.env`가 있는 작업공간에서 제품 import/pytest를 금지한다. credential-free copy+clean environment/guard만 사용한다. 사용자 파일 reset/clean/stash, 실주문, DB 덮어쓰기, 원본 삭제를 하지 않는다. 최초 배정 인계는 `handoff-initial-20261018.md`에 보존했다.
