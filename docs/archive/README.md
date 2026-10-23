# 보존·격리 인덱스 (docs/archive)

이 문서는 R02 문서 티켓에서 만든 보존(PRESERVE)·격리(QUARANTINE) 인덱스입니다. 인덱스일 뿐이며
아카이브 실행 기록이 아닙니다. 이 티켓에서 이동·삭제한 파일은 없습니다
(**no files moved on this docs ticket**). 폴더에는 현재 이 README만 있고,
실제 아카이브 소재는 아직 없습니다.

판정 근거는 [v3 local execution status](../status.md)와
[아키텍처 소유권 지도](../architecture.md)입니다.

## 보존 (PRESERVE) — 외부 VN20 절차 전까지

| 경로 | 보존 사유 |
| --- | --- |
| `docs/refactoring/runs/` | 티켓 수락 접수(receipts) 경로. R00·R01·O00 수락 이력의 증거 원천이며, 개별 접수 링크는 [status](../status.md)에 유지됨 |
| `docs/evidence/` | 실험·검증 증거 경로(예: 구 README가 링크했던 `mm_state_revision_20261007` 실측 보고서). 임의 정리 금지 |
| `runtime_data/` | 실제 보호 대상 운영 데이터 경로. 절대 복사·열기·임의 정리 금지. 본 인벤토리는 외부 마운트·외부 저장 위치까지 알지 못하며, 외부 장소는 UNKNOWN으로 남습니다 |
| `scripts/refactoring/deploy_p02_schema.py` | 활성 P02 롤백 스크립트(직접 열기: [링크](../../scripts/refactoring/deploy_p02_schema.py)). 외부 VN20까지 유지, 절차 경계는 [복귀 절차와 보존 경계](../refactoring/rollback.md) |
| 레거시 NATS 배치 | `Dockerfile`, `Dockerfile.crypto`, `deploy/nats.conf`, `docker-compose.yml`, `deploy/*.yml`, `deploy/*.service`, `deploy/*.cron.example` 등. 마지막 문서화 배포의 재구성 근거로 외부 VN20 전까지 유지 |

## 격리 후보 (QUARANTINE) — 수락 아님

[로컬 파일 삭제 후보 목록 — 2026-10-19](../CLEANUP_DELETE_LIST_20261019.md)은 후보를 나열한
문서입니다. 목록에 들어 있다는 것 자체는 어떤 삭제·이동·아카이브 수락도 아닙니다.

각 후보 경로는 후속 조치(이동·삭제·아카이브) 전에 **per-path** 증거 4종이 확정되어야 합니다.

1. 정적 참조(references) 조사 — 어떤 코드·문서가 참조하는지.
2. 다이내믹 임포트(dynamic import) 조사 — R00 인벤토리([repo_inventory.json](../refactoring/vnext/repo_inventory.json))가
   이미 존재·수락됨(파싱된 Python 504개, 다이내믹 임포트 지점 24개). 미해결 지점의 의미는 **UNKNOWN**이며,
   해당 지점과 겹치는 경로의 판단은 불가합니다.
3. 배포(deploy) 경계 접촉 여부 — 출시·설정 조합 원천은 [운영 전환 패키지](../refactoring/cutover.md).
4. 롤백(rollback) 경계 접촉 여부 — [복귀 절차와 보존 경계](../refactoring/rollback.md).

- blanket(대량 일괄) 아카이브 승인은 없습니다. 파일 수준 증거 없이 문서 나열만으로
  이동·삭제를 판단하는 것을 금지합니다.
- 후속 조치의 증거 기준은 [12 준비 상태·필수 증거 행렬](../refactoring/vnext/12_production_acceptance_matrix.md)을
  준용합니다.
- 조사 작업(예: 다이내믹 임포트 실측)은 이 문서가 아니라 별도 티켓이 수행해야 합니다.

## 외부 실행 경계

- VN20 실서버 전환·NATS 데이터 삭제는 외부 실행입니다. 이 리포 내부 티켓·에이전트가
  수행 대상이 아닙니다.
- 현재 서버에는 접촉 기록이 없고 실서버 상태는 UNKNOWN입니다. 판정 근거와 경계는
  [status](../status.md)와 [아키텍처 지도](../architecture.md)를 따릅니다.
- 보존 기한의 뜻: "외부 VN20까지"는 절차 시작 조건이지 문서화된 날짜가 아닙니다.

## 병행 트랙 메모

- 다음 도구 작업(VN/TX 진행)과 제품 티켓 발송 상태 같은 변하는 항목은 이 문서에 복제하지 않습니다.
  유일한 변동 요약은 [status](../status.md)와 canonical JSON
  ([tickets_production.json](../refactoring/vnext/tickets_production.json),
  [stage_status.json](../refactoring/vnext/stage_status.json))입니다.
