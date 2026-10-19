# 복귀 절차와 보존 경계

현재 운영은 변경하지 않았다. 로컬 구현은 변경 파일별 source preimage archive와 hash를 보존했다. 작업 전부터 존재한 dirty/untracked 파일은 reset·clean·stash하지 않는다.

- packet/schema 변경은 포맷 migration이 없어 해당 코드 단위로 되돌릴 수 있다. class-level 계측과 wrapper 호환을 함께 검증한다.
- 공유 state/recovery의 모드·cursor가 다르면 메모리를 그대로 넘기지 않는다. INVALID로 닫고 검증한 snapshot/window부터 다시 준비한다. 과거 intent는 재사용하지 않는다.
- generic execution ledger와 control은 최신 원장·UNKNOWN·예약·epoch를 보존한다. candidate를 halt→미확정 전송/체결 대조→정지/자동 재시작 차단한 뒤 호환 binary를 blocked로 시작한다.
- ETF journal은 기존 fill·notification 원자성을 유지했다. Telegram은 별도 receipt를 사용하므로 이전 sender로 복귀하면 과거 알림을 재전송할 수 있다. receipt 대조·sender 단일성 검증 없이 old sender를 실행하지 않는다.
- hash mismatch 파일은 evidence와 원본 경로를 보존한다. legacy delivery prune 차단을 제거하려면 실제 confirmed-floor·정확 복구 proof가 있는 별도 migration이 필요하다. 디스크 압박을 이유로 dedup 원장을 임의 삭제하지 않는다.
- 실제 서비스 복귀는 manifest의 해당 역할 rollback image ID와 운영 Compose를 기준으로 한다. NATS stream/consumer reset, 원본/원격 객체 삭제, 과거 DB 덮어쓰기는 복귀 절차가 아니다.
