# 역사 자료와 수락 증거 보존 인덱스

현재 개발 권위는 [최종 계획](../final/01_FINAL_PLAN.md),
[개발 티켓](../final/02_DEVELOPER_TICKETS.md),
[아키텍처 요구사항](../final/03_ARCHITECTURE_REQUIREMENTS.md),
[불변 완료 이력](../final/04_COMPLETED_MANIFEST.json),
[현재 상태](../final/05_TASK_STATUS.json)의 다섯 파일뿐입니다.

## CLN-01의 실제 정리

[파일별 보존 manifest](CLN-01-retention.json)는 기준 SHA, 원본 해시·바이트 수,
실제 참조·보존 이유·예외 만료 조건, 백업 아카이브의 경로와 SHA를 기록합니다.
[과거 계획·작업 패킷](CLN-01-history/README.md)은 바이트 그대로 역사 영역으로 이동했습니다.
이 파일들의 지시는 현재 실행 계획이 아닙니다. 원본 상대 링크는 표에 연결된 고정 Git
스냅샷 문맥에서 읽습니다.

반복 console/JUnit/AI 응답 중 현재 코드·직접 결과 참조가 없는 자료만 검증된 복구 사본과
원본 Git blob을 확보한 뒤 추적에서 제외했습니다. 로컬 원본 파일은 삭제하지 않았습니다.
새 원시 로그의 기본 저장소는 private run artifacts/CI이며, Git에는 compact 수락 요약을 남깁니다.

## 현재 위치를 유지한 보존 예외

- 불변04가 명시한 수락 기록 30개는 기존 경로·원본 해시를 유지합니다. 원래 receipt_path가
  없는 15개 항목은 과거 상태 근거만 있는 것으로 남기며 해시나 수락 기록을 새로 만들지 않습니다.
- Golden/test oracle, source provenance, guard JSONL, 실제 결과가 참조하는 로그와 runbook은
  현재 읽기 경로를 보존합니다. 기존 `docs/refactoring/runs` 파일이 남아 있어도 신규 작업 권위는 아닙니다.
- [P02 롤백 prepare](../../scripts/refactoring/deploy_p02_schema.py)가 문자열 경로로 읽는
  루트 refactoring_plan/context/ai.md 및 docs/refactoring/execution_board.md는 읽기 전용 역사
  입력으로 유지합니다. RET-01의 별도 승인된 롤백 대체·종료 증명 전에는 옮기지 않습니다.
- old canonical JSON/path maps/reuse inventories와 runbook은 기존 테스트·고정 receipt의 직접
  입력으로 보존합니다. 새 READY 판단은 05만 사용합니다. 파일별 Caller/만료 조건은 manifest에 있습니다.
- [MOV-05 검증 원본](MOV-05-legacy-oracles/README.md)과
  [MOV-06 롤백 목록](../../deploy/rollback/MOV-06/manifest.json)은 원본/계산 규칙을 보존합니다.

운영 기록·거래 DB·시장 원본·원격 보관물은 이 정리의 읽기/삭제 대상이 아닙니다.
