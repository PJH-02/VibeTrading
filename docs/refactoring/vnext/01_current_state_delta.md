# vNext 01 — 현재 상태와 기준선 차이

작성: 2026-10-20 KST. **PLAN-ONLY / 증분 설계안**. 제품 변경·배포·주문·기존 데이터 삭제·기존 gate 갱신을 수행하지 않는다. 기존 P00~P12를 대체하는 신규 프로젝트가 아니다.

## 1. 기준선과 증거의 범위

| 구분 | 이번 확인 | 해석 |
|---|---|---|
| Git | `6443eb94133bc0075b0860885829e112adc657c8` | 부모 `c80bf75`, `0022e65`. 트리는 `c80bf75`와 동일한 `f4ac499215856db8f9af1d548e0aaad804626177` |
| 작업 트리 | 분석 시작 시 추적 파일 diff 0, 미추적 `dashboard/` | dashboard는 Git 복원 가능 범위에 포함하지 않는다. 신규 문서도 아직 커밋하지 않음 |
| 현재 로컬 manifest | [source_manifest.json](source_manifest.json), 선택 파일 531개 | source-set `1b0dbadca1398b3350324443b55549d83453bc747776dd9837500ccdec7a903e`. 각 티켓 before hash의 기준. 원문 바이트 기준이며 LF 변환과 구분 |
| 최초 P00 | 기존 `baseline_manifest.json` 437개, source-set `8eabfd5f…` | 4월 HEAD와 당시 미추적 파일을 포함한 10월 18일 기준선. 현재 기준선으로 재사용하지 않음 |
| 운영 재확인 | **2026-10-20 15:30:38 KST**, [server_readonly_identity.json](server_readonly_identity.json) | Docker 14개, Python 역할 13개. 읽기 전용 inspect·소스 SHA·짧은 자원 표본. 제품 모듈 import·DB 초기화·재시작 없음 |
| 로컬↔운영 | [source_comparison.json](source_comparison.json) | `main.py`+`vibetrading/**/*.py` 131개: 원문 동일 79, 개행만 차이 14, 내용 차이 20, 로컬 전용 18. 모듈 소유 역할 override 또는 관측된 모든 variant의 동일성을 사용. 미해결 owner 0 |
| 활성 P02 | storage/crypto-storage의 image ID가 기존 적용 영수증과 동일 | `sha256:3fcaa490d47d13d1df5aca7d6321d8313ea480bc1a18dddcb5139c84a0bdde3c`. 두 서비스 healthy. 전체 리팩토링 활성화를 의미하지 않음 |
| 서버 후보 | 기존 `server-progress-v6-receipt.json`은 **INACTIVE 게시** 영수증 | `/opt/vibetrading/releases/refactoring-20261019-p02-schema/progress-v6`. 이번에는 패키지 재추출/이미지 빌드/활성화를 하지 않음 |

서버의 모든 이미지에 같은 파일명이 존재해도 사용하지 않는 오래된 사본일 수 있다. Python 소스 일치는 dependency·실제 import·bind mount·주문 프로세스 일치를 대신하지 않는다. 현재 컨테이너 Python 3.12.14 / sqlite3 3.46.1도 라이브 writer의 PRAGMA 증거는 아니다.

### 현재 설정의 변경 발견

활성 Compose 경로는 기존 `/opt/vibetrading/releases/telegram-recovery-20261014/runtime.yml`과 `dashboard.yml`이다. `runtime.yml` SHA는 기존 preflight의 `f50b9aa8…`에서 현재 `b8453deb…`로 달라졌다. dashboard SHA는 `145bae53…`로 동일하다. 변경 원인·resolved 환경·설정 의미는 **미확인**이다. 원본 환경값을 출력하지 않았으며, 과거 resolved-config hash를 현재 전환 허가로 사용하지 않는다. VN00에서 비밀을 제거한 의미 차이를 확인해야 한다.

## 2. P00~P12: 코드, 시험, 게시, 활성 상태 분리

| 단계 | 보존할 현재 구현 | 검증/증거 | 운영 적용 및 남은 작업 |
|---|---|---|---|
| P00 | source/fixture/oracle, 역할별 source 합성 | 최초 격리 baseline 및 manifest 존재 | G00 PARTIAL. 72시간 연속 측정 두 차례 중단; 계좌/호스트 주문자·writer PRAGMA·복원 미확인 |
| P01 | C01~C07, parameter/capability/ADR | scoped 계약 검토 | NATS 제거·30분 초과 독립은 확정. 생산 수치/adapter 보증 미확인; 오래된 OPEN 문구 잔존 |
| P02 | schema cache, immutable packet, bounded publisher | schema/packet 보존 시험; v5 통합에 포함 | **schema cache만 활성**. packet 및 전체 경로의 운영 효과는 미판정 |
| P03 | HotPathStore version/quality, PreparedHistory, SharedStrategyRunner | replay/shadow 시험 | generic live dispatch 없음. 현재 ETF는 별도 기존 loop. 공통 FV/feature registry가 완성됐다고 하지 않음 |
| P04 | ExecutionLedger/Owner/RuntimeControl, ETF dispatch guard seam | fake owner/permit/crash/race 회귀 | generic Owner는 **offline-only**. ETF 단일 계좌 lease 미완성, 실제 계좌 전환 미검증 |
| P05 | spool/UDS/pipeline/engine/observation | NATS-free 합성 통합 | 엔진 장애가 publisher를 invalid로 만드는 경로, 전역 engine 오류 전파가 남음 |
| P06 | C/H replay, 초기 one-use resume, ETF state-only restore | 기존 prefix 신규 주문 차단 시험 | `replay_startup`은 0부터 재생. 검증된 market checkpoint 없음 |
| P07 | Parquet/manifest/remote proof, direct spool storage/facts | hash/reader lease/commit 순서 회귀 | spool GC 금지. 새 local facts/producer spool의 지속 보관·회수 연결 미완성 |
| P08 | Telegram inbox/outbox, local facts consumer, WAL 상한 보완 | v6 관련 부분 시험 | `/halt` 등 변경 명령·범용 PnL 미연결. 현재 명령은 status/trade/help/start |
| P09 | mount/capacity 검사, 역할별 source 후보 | owner 합성·P02 제한 배포 | 새 local runtime config/image/전체 동시 예산 미확정 |
| P10 | v5 전체 + v6 부분 기록 | 아래 범위까지만 PASS | 운영 trace A/B, 실제 >30분, 24h cloud, peak/RSS/PSS/72h 미완료 |
| P11 | cutover/rollback 문서·게시 영수증 | 구체 실행 패키지의 일부 | `BLOCKED_OPERATIONAL_EVIDENCE`. 전체 NATS-free 전환 없음 |
| P12 | retire 경계 | NOT_STARTED | NATS stream/backlog·rollback 의존 보존 후 제거. 이번에 삭제 작업 없음 |

기존 `gates.json`의 `G03-G12: NOT_RUN`은 후속 개별 기록을 집계하지 않는 과거 묶음 상태다. 반대로 실행 보드의 “구현”은 전체 G03~G12 PASS가 아니다. 원본은 보존하고 [conflict_register.md](conflict_register.md)에 불일치를 기록한다.

### 시험 영수증을 현재 코드로 승격하지 않는 방법

- v5: Windows **1078 PASS / 9 SKIP / 2 DESELECT**, Linux **1087 PASS / 2 DESELECT**. 보존된 result JSON은 exit 0과 대상 hash를 제공한다. 정리로 원 stdout/JUnit/실행용 후보 디렉터리가 없는 항목은 재실행 증거로 취급하지 않는다.
- v6: Windows **46 PASS / 1 SKIP**, Linux **47 PASS**는 `test_local_events.py`, `test_local_runtime.py` 관련 부분만이다. **v6 전체 suite PASS 기록 없음**.
- 현재 파일과 receipt 원문 SHA 비교: v5 제품 30개, v6 28개가 다르다. health의 혼합 개행을 보존 baseline과 대조한 결과, v5는 **28개 개행 차이 + local_events/local_runtime의 코드 변경 2개**, v6는 **28개 개행 차이**다. health AST도 동일했다. 테스트 기록을 다시 쓰지 않으며 신규 release는 정확 candidate bytes에서 한 번 검증한다.
- 최초 `prepare_candidate.py`가 요구하는 연구 requirements 4개는 현재 삭제되어 있다. 해당 연구 자료를 복원하지 않고 현재 manifest 531개를 사용하는 새 사본/실행 경로를 [05](05_validation_and_cutover.md)에 제시한다.
- 후보 manifest의 `NOT_UPLOADED…`와 뒤의 `PUBLISHED…INACTIVE`는 시점이 다르다. 최신 게시 영수증을 우선하되 활성 증거와 합치지 않는다.

## 3. 현재 코드에서 확인한 핵심 차이

| 실제 경로/심볼 | 확인된 사실 | 필요한 증분 작업 |
|---|---|---|
| `local_pipeline.py::publish_market_packet/accept_payload/_writer` | 호출 반환은 bounded RAM 수용. group flush 후 UDS 전송 | ACCEPTED/DURABLE/APPLIED 구분, 지연·오류 원인 계측, 일시 IPC 장애와 writer 장애 분리 |
| `local_engine.py::_invalidate/_flush/event_source` | 한 producer 오류가 전역 `_source_error`·전체 거래 큐 flush로 전파 | 의존 feed별 gate·큐 무효화. healthy input의 상태 적용과 전략 전달을 별도 확인 |
| `local_runtime.py::run_engine.invalid` | producer와 무관하게 모든 ETF symbol invalidation | 현재 보수적 동작을 명시하고 dependency map으로 범위 축소 |
| `local_runtime.py::replay_startup` | C=0부터 producer별 H까지 state-only 재생 | full-state checkpoint+delta replay. 교차 feed 결정 순서를 복원했다고 주장하지 않음 |
| `market_spool.py::gc_disabled/_inventory/scan_spool` | GC 금지; segment 1 및 sequence 1부터의 연속성 전제 | base cursor 포맷·restore/archival/reader proof 없이는 삭제 0 |
| `local_snapshot.py::_document/atomic_write_snapshot` | top-depth/UI projection, 물리 내구성 보장 없음 | recovery checkpoint로 전용 금지; feed별 표시만 수정 |
| `execution_owner.py::__init__/dispatch` | fake/offline cash/spot 전용 | 기존 ETF guard/journal을 먼저 보완. crypto live owner로 임의 확장 금지 |
| `etf_journal.py::accepted/stage` | ACK가 상태 pending을 덮고, duplicate intent는 payload 대조 없이 false 반환 | fill-first 단조성 및 immutable intent/evidence 충돌 검사 |
| `telegram.py::_handle`, `runtime_control.py::resume_if_ready` | 변경 명령 없음; resume는 여러 scope를 함께 열 수 있음 | 명령 scope 계약 및 owner 연결을 먼저 확정 |

함수/줄 단위 상세 근거는 [02](02_architecture_review.md), 수정 범위는 [04](04_implementation_plan.md)에 있다.

## 4. 유지할 동작과 워크로드

Crypto는 Binance/Arcus/GRVT × BTC/ETH/SOL, native snapshot bridge/sequence, raw book, Decimal을 유지한다. Arcus USD와 USDT, 계약 승수·maker fee·funding을 동일하다고 가정하지 않는다. 새로운 MM 실거래 활성화는 범위 밖이다.

KR은 KIS/Kiwoom/TOSS의 현재 scope, C3/KP/KD, 기준 ETF↔실행 ETF mapping, Mock/Mock2, 수수료·가정 체결·테스트 제외를 보존한다. `EtfHistory.prepare`는 prior 일봉 ≥22, 최근 15거래일의 분봉을 선택하고 분봉 날짜 ≥14를 요구하며 C3 index/US ≥252를 확인한다. 이 날짜 수 검사는 모든 슬롯 완전성을 보증하지 않는다. `etf_reference.LOOKBACK=14`와 09:01 end-label·실제 가용시각 인과성을 완화하지 않는다.

보존 대상: universe metadata cache/deep-copy, Kiwoom 오래된 행 제외, KIS 계좌 모드별 token/UTC-KST 처리·유료 feed 차단, C3 당일 시가·오늘 복원, exact event run/confirmed floor, SHA readback, replacement/reader lease, root/state/data guard, ETF 누적 체결·예약·notification transaction, Telegram 기존 `/trade` 의미.

KR 누락 분봉 수리·native 재수집은 **DEFERRED_BY_USER**다. 과거 10월 19일 KP/KD 신호 공백은 당시 감사 사실이며 오늘 장중 상태로 재사용하지 않는다. 해당 결손을 정상으로 보정하지 않고 신규 구조의 history readiness에 그대로 반영한다. `etf_history_contract.py` 등 과거 계획에만 있고 현재 없는 모듈을 구현 완료로 기록하지 않는다.

## 5. 현재 자원 표본과 확인되지 않은 사실

10월 20일 15:30 KST 한 표본: 2 vCPU, RAM 3.823GiB, MemAvailable 약 1.09GiB, swap 사용 약 1.28GiB. CPU PSI some avg10 38.46%, I/O full avg10 4.40%. 순차 Docker CPU 표시 합 211.06%는 동기화된 장기 CPU 측정이 아니다. maintenance는 493.4/512MiB, CPU 92.12%, restart count 7; 원인은 미확정. monitor-bridge 182.6/192MiB. app peak ≤3GiB 및 CPU 여유 달성 증거가 아니다.

| 실제 경로의 `df` 표본 | 가용 바이트 | 제한 |
|---|---:|---|
| `/` | 3,729,887,232 | config/OS가 같은 root를 사용. tools 증가 여유 필요 |
| `/srv/vibetrading-data` | 11,065,909,248 | NATS/Docker 측 경로. bind 하위의 별도 FS는 이 숫자로 대체하지 않음 |
| `/srv/vibetrading-crypto-state` | 236,019,998,720 | 별도 큰 FS 표본. market/Parquet 하위 mountinfo를 전부 확인한 것은 아님 |

누적 BlockIO/NetIO나 1회 swap 카운터를 초당 유입률로 쓰지 않는다. 신규 λ(bytes/s, records/s), μ(검증 완료/s), fsync tail, PSS/page cache/전체 동시 peak, 실제 writer PRAGMA, 원장 복원·계좌 미체결, 오늘 ETF 판단 지속성은 **미측정/미확인**이다. 이를 해소할 Gate는 [05](05_validation_and_cutover.md)에 명시한다.

이번 분석에서 기존 문서·코드·운영 DB를 덮어쓰지 않았다. 삭제된 과거 리포트/시험 임시본을 복원해 현재 근거를 부풀리지 않는다.
