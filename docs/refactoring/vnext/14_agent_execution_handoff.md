# 실행 인계 — 완료 구현 선별 커밋 (2026-10-21)

현재 판정과 다음 실행 순서는 [단계별 상태표](15_stage_status_and_next_steps.md), [machine-readable 상태](stage_status.json)를 따른다. **완성7 / 미완성24 / 미착수20**은 VN/TX의 로컬 구현 기준이다. 상위 P00~P12의 운영 완료를 뜻하지 않는다.

## 검증과 코드 경계

전체 작업 트리 `production-v12`(550 files, source-set `322bb2cb849ecb5ab8849e70e87aba53d0622f6872196ed45bdb9079ba472b8a`)는 Windows1312 PASS/14 SKIP/2 DESELECTED, Linux1326 PASS/2 DESELECTED였다. 이 결과에는 아직 미완성인 Paper/venue 등의 로컬 초안도 포함된다.

최신 사용자 지시는 완료분만 커밋하는 것이다. 따라서 Paper/portfolio/strategy/venue/group 초안6개와 테스트5개를 제외한 `completed-market-v1`(539 files)을 별도 시험하여 Windows1199 PASS/14 SKIP/2 DESELECTED, Linux1213 PASS/2 DESELECTED를 확인했다. [커밋 후보 manifest](../runs/20261020-production/completed-market-v1-source.json)와 [검증 상태](production_validation.json)에서 이 둘을 구분한다. dashboard/tmp/데이터/백업은 포함하지 않는다. 서버 코드는 로컬과 다르며 서버 반영·push·live 활성은 수행하지 않는다. 이전 커밋 보류 기록보다 최신 명시적 완료분 커밋 지시를 적용한다.

## 이번 완료 구현

- VN01: bounded fault stage/reason/cursor/age, durable-applied lag 및 소유권 회계.
- VN02/VN03: feed별 queue/generation/lease와 한국 ETF/Kiwoom 입력 의존성 격리. 관측 오류로 unrelated feed를 정지하지 않는다.
- VN04A/B: 엔진이 없어도 유한 spool에 durable 기록, digest/C/H 확인, state-only replay, 명시 readiness와 유한 재접속. broker를 기본 경로에 추가하지 않는다.
- VN05: count/byte/한 번 정한 deadline gather. 성공한 fsync 전에 전달하지 않는다.
- VN06: full native book/window/history/quality/version/ordered fingerprint/dedup codec과 실제 engine-owner 원자 capture. UI snapshot을 복원 원본으로 사용하지 않는다.
- VN07 완료 하위 기능: 독립 retained C digest 확인→고정 H까지 최대 한 producer suffix→callback-free market-only 복구. 여러 nonempty suffix는 global order 증거가 없어 차단한다. target은 unused engine이어야 하며 부분 복원 실패 시 폐기한다.
- TX06E 완료 하위 기능: 환경+signer 기준 독립 FULL-commit nonce 저장소. 전체 Hyperliquid connector 완성을 뜻하지 않는다.

`save_engine_checkpoint`는 owner loop에서 원자 capture 후 immutable bytes만 worker로 fsync한다. caller 취소 후에도 단일 write task가 소유권을 유지한다. 기존 codec과 engine wrapper는 같은 bounded temp→file fsync→replace→directory fsync→readback 경로를 사용한다. directory fsync 불가 플랫폼에서 성공을 주장하지 않는다.

## 복구 권위와 검토

`capture_checkpoint/inspect_checkpoint/restore_virgin/complete_checkpoint_recovery`는 provider/instrument ownership/budget/engine incarnation/C/digest를 묶는다. rewind·검증 전 constructor state·외부 apply·callback direct-apply로 C 벡터보다 앞선 상태를 승인하지 않는다. [독립 리뷰](../runs/20261020-production/vn07-review.json)는 trusted-owner, callback-free market 범위에서 미해결 P1/P2 0이다. 리뷰는 테스트 실행을 대신하지 않는다.

복원 상태는 initial resume/legacy bool handoff로 승격할 수 없다. 명시적 synchronous whole-vector validator가 REPLAY snapshot과 정확 receipt를 검증하고 반환한 후 cursor/generation/clock/quality/lease를 재검사한다. 한 feed의 재복구에서도 정상 LIVE peer의 현재 C와 lease는 보존한다. False/throw/awaitable/오래된 검증은 차단한다.

## 남은 작업과 바로 다음 시작점

1. **VN07 미완료 경계부터:** 여러 producer suffix의 실제 global apply order 계약/증명과 ETF/Paper account/strategy/pending-action 복구 커서 관계를 확정한다. 파일: `local_engine.py`, `local_runtime.py`, 현재 커밋 제외 상태의 `strategy_runtime.py`, `paper_execution.py`. 순서 증거 없이 config 순서로 재생하지 않는다.
2. **VN08:** retained-base cursor/first-segment reader 포맷과 v1 호환. `market_spool.py::_inventory/scan_spool/SpoolReader._locate_start`, `local_storage.py::LocalSpoolStorage.__init__`부터 수정한다. 아직 VN08 제품 변경은 없다. GC 비활성.
3. **VN00→VN10~12/TX01~03/TX07A:** 현재 서버 기준선/계좌 owner/intent/fill·ACK/공통 lifecycle 통합. 과거 서버 snapshot은 현재 identity 증거가 아니다.
4. **VN16→VN09, TX04B→TX05→TX06A-E→TX07B→TX08:** lane/GC 증명과 Paper 입력 연속성·connector/private stream·UNKNOWN 재조정·group 통합.
5. **VN14~20/TX09~12:** control/reporting/export/역할 package, 실제30분/24h/72h·자원/장중/계좌 검증, 별도 승인 전환 및 legacy 정리.

## 운영 제한

실제 NATS-free 30분 초과, 24h cloud, 15분 Parquet, 72h 대표장, 2vCPU/3.8GiB 부하, 실계좌 capability 및 실제 live-input Paper 증거는 열려 있다. [합성 VN05 측정](../runs/20261020-production/vn05-timing-summary.json)은 burst p99 개선과 Windows CPU/event 증가를 함께 남겼으며 운영 성능 PASS가 아니다. 과거 raw 측정은 로컬 참고 자료로 두고 이번 커밋에는 필요한 최신 후보/검증/리뷰와 요약만 선별한다.

단순 경계·diff 검토는 Luna, 복구·회계·GC는 독립 safety reviewer. 한 파일 한 editor, 이미 읽은 전체 파일 재독 금지, KISS/DRY/YAGNI, 원본 .env가 있는 레포에서 제품 import/pytest 금지. 다음 작업 전 source pin과 해당 선행 gate부터 확인한다.
