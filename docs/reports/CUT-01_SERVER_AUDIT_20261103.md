# CUT-01 서버 작업 감사 및 로컬 동기화 보고서

기준일: 2026-11-03, 한국시간(KST). 최초 서버 작업은 11월 2일에 시작했다.

## 1. 결론과 계획 준수 여부

**CUT-01은 완료되지 않았다. RET-01은 시작하지 않았다.** 최신 V7 시험도 1시간을 채우지 못하고 실패했다. 이 문서는 작업 내역과 증거를 정리한 보고서이며, 새로운 제품 계획이나 완료 권위가 아니다. 상태 권위는 [05_TASK_STATUS.json](../final/05_TASK_STATUS.json)이다.

사용자가 의도한 순서는 완성된 로컬 코드를 서버에 한 번 올려 전체 경로를 점검하고, 발견된 오류의 원인을 확인해 필요한 부분만 수정하는 것이었다. [최종 계획](../final/01_FINAL_PLAN.md)의 원칙도 단일 최종 후보 설치와 순차 안전 검증이다. [배포 요구사항](../final/03_ARCHITECTURE_REQUIREMENTS.md)의 `ONE INSTALL` 역시 반복적인 개별 단계 릴리스를 기본 절차로 삼지 않는다.

실제 작업에서는 아래의 두 가지를 구분해야 한다.

- **지킨 계약:** 로컬 수정 → 제한된 수정 티켓 → 독립 검증 → 고정 이미지 → Ubuntu 검사 순서, 원본 데이터 보존, 기존 14개 서비스 보존, 공개 시세와 경제적 실행 분리, 실주문 미활성화는 유지했다. 교차 호가를 정상으로 인정하거나 durable 이전 소비를 허용하는 식으로 안전 기준을 낮추지 않았다.
- **계획과 달라진 진행:** 서버 통합 점검이 공개 시세의 수집·전달·복구 앞단에 집중됐다. V1~V7 시험 그룹과 여러 중간 이미지 반입이 이어졌고, Telegram·대시보드·계좌 소유권·Cloud·롤백 등 전체 점검은 끝나지 않았다. `DATA_ONLY` 실행 조립 코드도 추가됐다. 수정 티켓이 각각 존재한다는 사실만으로 이 반복 방식이 원래의 단일 후보 전체 점검 계획과 같다고 말할 수 없다.

따라서 **리팩토링의 핵심 안전 계약은 유지했지만, CUT-01 실행 관리와 범위 추적은 원래 의도에 충분히 맞지 않았다.** 부분 수정의 검증 통과를 전체 시스템 완성처럼 받아들이게 만든 진행 보고 역시 불충분했다. 특히 메모리 최적화와 특정 Gap 버그 해결은 확인됐어도, 실서버 지속 처리 성능까지 해결됐다고 판단할 수 없었다.

이번 감사에서는 새 제품 코드 수정이나 새 장시간 시험을 시작하지 않았다. 실패 원인·수정 근거·미해결 사항을 정리하고, 실제 배포 후보의 소스 대조 및 서버 전용 실행 파일의 로컬 보존을 수행한다.

## 2. 실제 배포 상태와 코드 기준

| 구분 | 실제 상태 |
|---|---|
| 현재 작업 저장소 | `/home/pjh/.codex/worktrees/move-core-feeds/vibetrading` |
| 브랜치 | `codex/move-core-feeds` |
| CUT 시작 기준 커밋 | `972fb679583549e6c3ca8c49dd3795298673e62c` |
| 최신 제품 소스 커밋 | `111fa0a9ec4e71b311ff1c3e38f790f8611e82ae` |
| 최신 릴리스 증거 명칭 | `V8-frozen-release.json` — **시험 V7과 이미지 세대 V8은 서로 다른 번호** |
| 최신 릴리스 manifest SHA-256 | `012e37794d189c74710a0eba4c78dfc4bb66a705b30385c784273995f7a26cf2` |
| Ubuntu 후보 제품 소스 | `src/` Python 파일 150개, 기본 설정 10개. 로컬과 SHA-256 대조 일치 |
| 기존 운영 서비스 | 기존 세대 14개 유지. 새 flat `src/` 후보로 전체 전환하지 않음 |
| 새 후보 시험 서비스 | 공개 시세 수집기 3개 + 엔진 + 저장 작업. V7 실패 후 5개 모두 종료 |
| 계좌·주문·공개 웹 | 실주문 미활성화, 기존 env 키 교체 없음, 새 공개 대시보드 활성화 없음 |

최신 Ubuntu 이미지 식별자:

| 역할 | 이미지 ID |
|---|---|
| app | `sha256:0876cf2f41bcd75e953a5bff41bc49d01ffb5897681bbc295c62ffd3b5ad37ab` |
| browser | `sha256:c5f1ba6ce3d07ac8c1adc1090a96c49d1b5f0238e17eac64e719b40a6b957964` |
| crypto | `sha256:19e0cb260898281fc47b887d8a2c4cb8d6961b58c8e4493c79dc7f4b849afda4` |
| ledger | `sha256:cb89cdcc27be29b1b76f267f453034ac05b827461ddfd28c0e988862d43eda51` |

**이미지 반입·소스 설치·검증용 실행·운영 전체 전환은 서로 다른 상태다.** 최신 코드를 서버에 설치했다는 사실이 기존 14개 역할의 운영 전환 완료를 뜻하지 않는다. 기존 운영본은 이전 패키지 구조를 사용하는 별도 배포 세대다. 그 오래된 소스를 새 로컬 코드 위에 덮어써 리팩토링을 되돌리지 않는다.

## 3. 시험 이력

시간은 각 시작 영수증을 KST로 변환했다. 시작 성공과 시험 완료를 구분한다.

| 시험 | 시작 시각 | 소스/상태 및 결과 |
|---|---|---|
| V1 | 11-02 22:58경 | 최초 공개 시세 통합 시험. Arcus ingress 허용 한도 소진으로 중단. 정확한 최초 거부 조건은 당시 진단에 충분히 남지 않음 |
| V2 | 11-02 23:13경 | 저장 작업이 checkpoint 임시 파일의 정상적인 원자적 교체를 손상으로 오인. 새 그룹 중단 |
| V3 | 11-03 11:12경 | 재생 후 공개 시세 복구 준비 증명 부족으로 WARMING 유지. freshness 가드 중단 |
| V4 | 11-03 11:29경 | `aa12a22` 후보. 보존된 Arcus 원시 데이터의 실제 교차 호가 재생이 엄격한 검사를 통과하지 못함 |
| V5 | 11-03 17:00:55 | `d2d6dc7`. 약 275초 관찰 후 freshness 실패. 정리 포함 약 305초. 계획한 엔진 재시작까지 도달하지 못함 |
| V6 | 11-03 21:27:23 | `993f700`. 정상 9종목 초기 확인 실패. 약 150초에 ingress 가드 실패. 반복 전달 복구의 원인을 별도 진단 |
| 별도 60초 진단 | V6 이후 | 동일 제품 이미지에 제한된 오류 이유 기록을 추가한 실행기 사용. 원시 데이터 보존. 1시간 시험·복구 통과 증거로 취급하지 않음 |
| V7 | 11-03 21:51:40 | `111fa0a`, 이미지 세대 V8. 초기 5개 역할/9종목 정상 확인 후 **실패**. 약 128초 시점 가드 발생, 정리 포함 153.23초 관찰 기록. 유닛 21:54:14 종료, StopPost 21:54:17 완료 |

V7의 당초 종료 예정 시각은 22:51:40이었다. 실제로는 이미 조기 종료됐으므로, 현재 기다려야 할 정상 진행 중인 1시간 시험은 없다. V7에서도 600초 시점의 엔진 재시작 검증은 실행되지 않았다.

## 4. 오류·원인·해결방안·해결 여부

`수정 검증 완료`는 해당 결함의 제한된 증거가 확보됐다는 뜻이다. CUT 전체 운영 통과를 뜻하지 않는다. `가설`은 확정 원인과 구분한다.

| ID | 오류/관찰 내용 | 원인 또는 현재 근거 | 조치/해결방안 | 판정 및 남은 이유 |
|---|---|---|---|---|
| I-01 | 실제 Binance 등의 프레임이 초기 예상 허용 크기보다 큼 | 실제 인코딩 프레임을 계측해 예상치 대신 관측값 필요 | 공개 소스별 1,800초 이상 계측, payload 512KiB/spool 1MiB 및 정적 예산 검증 | **용량 증거 확보.** 초기 V1의 정확한 거부 조건 전체가 입증된 것은 아님. 실시간 지속 처리 미완료 |
| I-02 | 초기 ingress admission 한도 소진 | 당시 특정 한도 조건을 충분히 기록하지 못함. 작은 소유 버퍼·batch 설정 등을 의심했으나 단일 원인으로 확정 불가 | 인코딩 크기/소유 버퍼를 분리 계측하고 제한된 FAKE 부하 검사. 실제 이벤트를 버리거나 큐를 무제한화하지 않음 | **최초 원인 미확정/처리 성능 미해결.** 뒤의 V7 지연과 같은 원인이라고 단정하지 않음 |
| I-03 | `checkpoint.json.tmp`가 사라져 storage 오류 | 디렉터리 열거와 stat 사이 정상 atomic rename이 일어나는 경쟁 조건 | incremental reader만 정확한 내부 임시 파일의 소멸을 허용. 시작 검사·세그먼트·checksum 검사는 유지 | **수정 검증 완료**, `aa12a22`. 다른 임시 파일이나 손상까지 무시하지 않음 |
| I-04 | durable 이후 한 건씩 ACK를 기다리는 전달 병목 | 기존 live/replay가 배치를 읽어도 사실상 stop-and-wait | opt-in 최대 32건 창. RECEIVED와 APPLIED를 구분하고 정확한 연속 cursor/digest에만 반영. 기본 창 1 유지 | **구조 수정·회귀 통과**, `aa12a22`. 이것이 모든 병목의 단일 원인이라는 증거는 없음. V7 지속 지연 남음 |
| I-05 | 재생 후 WARMING에서 공개 시세 전달 시작 불가 | 실제 상태/cursor/digest/고정 H를 검증한 공개 관찰 전용 준비 증명이 실행 경로에 연결되지 않음 | 엄격한 공개 소스 범위, 경제 콜백/큐 0, 실제 published checkpoint를 검증하는 명시적 `DATA_ONLY` 조립 경로 추가 | **제한된 관찰 경로 구현/검증 완료.** 경제 모드 준비 증명이나 운영 전략 조립 완료를 대신하지 않음 |
| I-06 | Arcus 원시 delta가 교차 호가를 만듦 | 실제 보존 ETH native sequence `506131406→506131407`. 정상 정규화 결과 자체가 교차. 단순 세대 초기화만으로 설명되지 않음 | venue 내부 유효성 검증. 원문은 `fast=False`로 보존하고 DataGap 기록 후 새 WebSocket snapshot 요청 | **실제 문제 구간 재현 및 수정 검증 완료**, `c3f6f9f`/`9a67ed7`. 외부 데이터가 항상 유효해진다는 보장은 아님 |
| I-07 | 공개 관찰 실행 경계의 환경/증명 파일/호환성 결함 | 첫 통합 검사에서 공개 CLI의 환경 경계 및 Arcus 기존 raw 호환성 등이 실패 | env 읽기 allowlist, 명시적 mount 검증, 실패한 증명 파일의 소유 inode 정리, 원시 kind/metadata 보존. 비경제적 경계 유지 | **수정·독립 검사 완료**, `9a67ed7`. 실패한 중간 검사 기록도 보존 |
| I-08 | 공개 모드의 cold replay가 과거 DataGap에서 거부됨 | 일반 경제 모드의 엄격한 Gap 재생 정책을 관찰 전용 경로에도 적용 | 콜백 없는 명시적 public observer만 Gap을 무효 상태로 재생. 일반 경제 모드는 기존 거부 유지 | **수정 검증 완료**, `7423331`. 유효 호가/거래 준비를 만들어낸 것이 아님 |
| I-09 | `python -m runtime.data_only_runtime` 실제 진입 실패 | `__main__`과 canonical module의 class identity가 달라 정확한 readiness 타입 검사 실패 | canonical `main` 호출로 통일. import/help 외에 실제 UDS·snapshot 생성과 종료 검사 추가 | **실제 Linux CLI 재현·수정 확인**, `d2d6dc7`. 이 CLI smoke의 용량 입력은 FAKE이며 외부 시세 운영 증거가 아님 |
| I-10 | V5 엔진 메모리/CPU 압력, 저장 작업 메모리 상한 접촉 | 엔진 중복 과거 호가 projection 보유. 당시 엔진 약 338MiB, storage 384MiB 상한 이벤트 관측 | 관찰 모드만 book 1/trade 40/index 1 유지. `Mapping` runtime import 단순화. 엔진 CPU cap .4→.6, storage 메모리 384→512MiB | **성능 개선 확인**, `993f700`. 실제 보존 14,000건에서 동일 최신 상태/지문, profile CPU 약 15.67→11.63초, RSS 358,824→96,872KiB. 프로파일 수치이며 전체 전달 성능 해결 증거는 아님 |
| I-11 | V6에서 반복 복구 및 지연, ACK mismatch는 2차 증상 | 실제 5930번 DataGap이 cursor를 commit하면서 feed를 INVALID로 변경. 5931번 연속 Gap은 `live_input_before_verified_handoff`로 거부. 재연결/전체 prefix 검증 반복 | public observer의 live Gap만 canonical 무효 상태/commit을 유지하고 전달 LIVE·수신 lease 유지. 일반 경제 모드 동작 유지 | **정확한 원인 재현·수정 검증 완료**, `111fa0a`. 보존 7,757건/Gap 9개 전부 처리, generation 2 유지, cold prefix와 상태/지문 일치. V7에서도 정지 전 반복 재연결은 관찰되지 않음 |
| I-12 | Arcus 새 계측에서 정상 호가 0처럼 잘못 집계 | `fast=None`을 raw-only로 취급한 **계측기 오류**. 정상 판정은 `fast is not False` | 잘못된 계측은 보존하고 corrected 계측 재수행 | **계측 수정 완료.** 1,875초/481,986건 실측. 제품 소스 변경과 혼동하지 않음 |
| I-13 | 대형 SQLite 검증 시간 초과/중단, systemd property 변경 일부 실패 | 16GB 전체 무결성 검사/복원 I/O가 제한된 자원·시간 안에 끝나지 않음. RuntimeMax 동적 변경도 최초 거부됨 | 새 백업 작업의 한도/실행시간만 조정, 원본 SQLite backup API, 독립 복사/두 번째 복원·hash/integrity 재검증 | **보존된 4개 DB 복사/복원 검사 완료.** 최초 국내 DB cut-facts 영수증은 복구되지 않았고 최종 소유권 정지 시점 최신 cut 및 외부 index 파일 포괄 증명은 미완료 |
| I-14 | 모의 계좌 조회 설정 해석 불일치 | 기존 Compose env 해석과 단순 env-file 전달에서 inline comment 처리 차이 | 원본 env를 고치지 않고 기존 Compose 해석으로 cached-token GET만 수행 | **해당 조회 검사 통과.** MOCK/MOCK2 잔고·당일 미체결 대조는 그 읽기 시점에 한정. owner fencing·최종 전환 직전 조회는 미수행 |
| I-15 | Cloud 확인의 범위가 새 파이프라인까지 이어지지 않음 | 기존 maintenance 전용 설정/RO secret mount로 과거 객체 한 건 HEAD/GET은 성공. 새 storage 실행은 Cloud worker를 자동 입증하지 않음 | 기존 객체 145,496 bytes의 전체 GET/hash 일치 기록. 새 파이프라인 upload→readback→retained prefix를 별도 검증해야 함 | **기존 객체 읽기만 완료. 새 Cloud/retained prefix 미완료.** 과거 객체 성공을 새 파이프라인 성공으로 대체하지 않음 |
| I-16 | V7 Arcus 호가 신선도 가드 재실패 | 전달 재연결 오류 없이 durable→APPLIED 지연 누적. 엔진 메모리 약 49MB, CPU cap 여유. 단순 메모리/Gap 버그로는 설명 부족 | 아래 V7 관측값으로 병목 구간을 고정. collector CPU throttling, 공유 spool executor의 읽기/쓰기 대기, ACK 창 대기를 각각 시간 분해해야 함 | **미해결.** 위 후보들은 가설이다. 현재 기록만으로 어느 하나를 확정 원인으로 삼아 새 수정을 승인하지 않음 |
| I-17 | V7 정리 중 GRVT가 exit 1, `STOP_ERROR` 기록 | 기록된 traceback은 WebSocket `ConnectionClosedError`/peer reset. Arcus freshness 주 실패 이후 정리 구간과 겹침 | 주 실패와 종료 오류를 분리해 기록. StopPost는 소유권 확인 후 모든 새 역할 종료 확인 | **종료는 확인, 원인 분류 미완료.** 독립 GRVT 오류인지 종료 경합인지 정확한 시점 대조 필요. 정상 종료 통과라고 쓰지 않음 |
| I-18 | 시험 종료 실행기의 정적 감사에서 오류 분류 부족 발견 | `docker inspect`의 모든 실패를 `not_created`로 취급. 실제 미생성뿐 아니라 daemon/권한 오류도 이 분기로 처리 가능 | 향후 재사용 전에 존재하지 않음과 조회 오류를 구분하고 조회 실패는 미확인으로 보고하도록 보완 필요 | **정적 결함 확인/미수정.** V7의 5개 컨테이너는 이번 별도 조회로 exited를 확인했으므로 현재 종료 사실은 이 분기에 의존하지 않음 |
| I-19 | 이번 소스 패키지 설치의 첫 검증 시도 실패 | 전송 도구가 먼저 읽은 tar manifest를 일반 반복에서 다시 열거해 파일 목록 검증 assertion 발생 | 로컬에서 중복 열거를 재현하고 `t.next` 기반 순회로 수정. 실제 849개 항목 dry-read 후 재전송. 서버의 첫 대상 디렉터리가 비어 있음을 확인한 뒤에만 재사용 | **전송 도구 수정·재검증.** 최초 시도는 exit 1, 소스 파일 기록 0. 최종 설치 결과는 7절 영수증으로 별도 판정 |

## 5. 최신 V7 실패의 확인된 사실과 미확정 부분

서버 감사 시각: **2026-11-03 21:55:46 KST**. 이미 시험은 종료돼 있었다. 다음 표는 종료 전 샘플의 producer progress 안에 함께 기록된 durable와 applied를 사용했다. dashboard snapshot과 progress는 별도 주기로 기록되므로 두 파일의 cursor를 원자적인 동시 cut으로 취급하지 않았다.

| 관찰 경과(초) | Arcus durable | Arcus applied | 차이(건) | 호가 이벤트 나이(초, snapshot) | 전달 오류 기록 |
|---:|---:|---:|---:|---:|---|
| 74.14 | 11,802 | 11,702 | 100 | 0.27 | 없음 |
| 84.91 | 15,445 | 15,439 | 6 | 0.31 | 없음 |
| 95.60 | 20,342 | 18,154 | 2,188 | 5.07 | 없음 |
| 106.09 | 23,494 | 22,408 | 1,086 | 2.08 | 없음 |
| 116.94 | 29,603 | 25,393 | 4,210 | 8.43 | 없음 |
| 128.06 | 33,767 | 30,129 | 3,638 | 11.46 | 없음 |

이 구간 Arcus 전달은 LIVE, generation 2를 유지했고 `last_delivery_error_type`은 비어 있었다. 즉 I-11의 반복 재연결이 그대로 재발한 것으로 볼 근거는 없다. 그럼에도 최신 호가 반영이 밀렸다. `book_valid`는 내부 1초 신선도 기준의 영향을 받으며, 시험 가드는 `book_valid`와 이벤트 나이 조건이 30초 이상 계속 충족되지 않으면 중단한다. 이벤트 나이가 15초 미만이었다는 이유만으로 정상이라고 판단할 수 없다.

`PASS_OWNED_STOP_POST`는 종료 확인에 한정된다. 실행기는 exit 1이나 OOM 표시가 있어도 종료 상태 자체에 PASS를 줄 수 있다. 정상 종료·OOM 여부·기존 14개 보존은 별도의 컨테이너 조회와 자원 기록으로 판단했다. V7에서는 GRVT exit 1을 별도 실패로 기록한다.

확정된 실패는 `SOURCE_FRESHNESS_GUARD:arcus:crypto_perp:BTCUSDT`다. 정리 후 모든 feed가 INVALID인 최종 snapshot을 최초 원인으로 쓰지 않았다. CPU/메모리/OOM 기록만으로 상세 병목을 확정할 수 없으며, API 자체 장애라고도 단정하지 않는다. 가드를 느슨하게 하거나 오래된 호가를 정상으로 표시하는 것은 해결방안이 아니다.

## 6. 완료한 검증과 아직 하지 못한 검증

| 영역 | 상태 | 증거의 범위/남은 이유 |
|---|---|---|
| 최신 소스 오프라인 회귀 | PASS | 전체 3,360개 + 별도 lease 25개, 실제 exit 0. 별도 집중 215개는 중복이므로 총계에 더하지 않음 |
| 재현 가능한 wheel/이미지 | PASS | wheel 두 번 byte 동일, source 150개/RECORD 검증, 이미지 4종 local 및 Ubuntu 검사, Compose 7개 조합 |
| source-independent 공개 시세 경로 | 부분 검증/FAIL | 실제 공급자 수집·spool·UDS·엔진·저장 시작은 확인. 지속 신선도 V7 실패 |
| 엔진만 재시작하면서 수집 지속/복구 | NOT_RUN | V5~V7이 예정 600초까지 도달하지 못함. 로컬 복구 테스트는 이 실서버 시험을 대신하지 않음 |
| 원시 데이터/저장 무손실의 전체 범위 | 부분 검증 | V1/V2 일부 prefix·Parquet 대조 통과. 모든 후속 시도·최종 retained/remote prefix를 전부 증명한 것은 아님 |
| DB 복사와 복원 | 부분 완료 | 국내/crypto/ETF journal/gaps의 보존 복사/무결성/복원은 확인. 국내 최초 cut-facts 누락, 최종 fenced cut/외부 retained index 포괄은 별도 |
| MOCK/MOCK2 계좌 사실 대조 | 읽기 시점 PASS | cached-token GET만 수행. 소유권 정지 및 최신 cut 적용·전환은 미완료 |
| 새 Cloud 경로 | NOT_VERIFIED | 기존 객체 읽기 성공만 있음. 새 후보의 upload/readback/retention 경로 미검증 |
| C3/KP/KD·PAPER 전체 서버 조립 | NOT_VERIFIED | 새 후보에 경제적 실행 주입/owner 준비가 연결된 전체 실서버 증거 없음 |
| Telegram singleton·대시보드 TLS/auth·Timefolio 전체 전환 | NOT_VERIFIED | 기존 서비스는 유지. 새 후보의 전체 역할 전환/외부 검증 미완료 |
| 최신 계좌 기준 binary rollback | NOT_VERIFIED | 이미지와 DB 보존은 실제 롤백 실행 검증을 대신하지 않음 |
| 대표 장중/24x7 운영 | NOT_VERIFIED | 짧은 시험·실측을 장시간 운영으로 환산하지 않음 |
| RET-01 | NOT_STARTED | CUT 운영 통과, caller 0, rollback 0, unique data 0, retained/remote readback·retention 종료 조건 미충족 |

## 7. 서버 코드와 로컬 코드의 동기화

제품 수정은 서버 파일을 직접 고쳐서 별도 버전을 만드는 방식이 아니라, 로컬 소스를 수정·검증·고정한 뒤 이미지로 반입하는 방식이었다. 이번 감사에서는 다음을 구분해 동기화했다.

1. **제품 소스:** 최신 서버 이미지 4종 각각의 `/app/src` 150개와 `/app/config` 10개를 로컬 릴리스 manifest와 파일별 SHA-256으로 재대조했다. 전부 일치했다. 종료된 V7 컨테이너 5개의 실제 파일도 `docker cp` 읽기 스트림으로 별도 대조해 각각 소스 150개/설정 10개의 일치를 확인했다. 컨테이너 파일은 변경하지 않았다. 별도 서버 제품 패치를 로컬에 역반영할 차이는 발견되지 않았다.
2. **서버 전용 실행 파일:** 그동안 저장소 밖에 있던 현재 V7 시험 실행기 3개와 실제 로컬 plan/mount/capacity 설정 5개를 서버에서 읽어 [배포 snapshot](../../deploy/CUT-01/server-snapshot-20261103-v7/manifest.json)에 원본 bytes와 SHA-256 그대로 보존했다. 이는 실행 당시의 기록용 snapshot이며 그대로 재사용하도록 승인한 실행기가 아니다. 고정된 시험 경로와 I-18의 결함을 포함하므로 신규 실행 전 별도 검토가 필요하다. 비밀키·env 실값·계좌 데이터·DB·원시 시세는 포함하지 않았다.
3. **소스 패키지 설치:** 로컬의 현재 Git 추적 파일 중 `docs/`를 제외한 소스·설정·배포 입력과 위 snapshot을 하나의 내용 고정 패키지로 서버의 새 디렉터리에 설치했다. 기존 데이터나 기존 운영 소스 디렉터리를 덮어쓰지 않는다. 문서인 이 보고서는 별도 포함한다. 이 설치는 운영 전체 전환이나 새 시험 실행을 뜻하지 않는다.

소스 패키지의 고정 정보:

| 항목 | 값 |
|---|---|
| 제품 코드 기준 | `111fa0a9ec4e71b311ff1c3e38f790f8611e82ae` |
| 소스/설정/배포 입력 파일 수 | 849개, 9,974,205 bytes — 테스트·기존 추적 파일도 보존, 파일 정리 미수행 |
| runtime tree SHA-256 | `1785fa60953ddb7111d1c7cdbe7cd9e82310f3334067a77ed39e88ae6b672769` |
| 서버 설치 디렉터리 | `/srv/vibetrading-crypto-state/cut01-20261102/releases/source-sync-20261103-1785fa60953d` |
| 패키지 manifest SHA-256 | `4dc05c6d385d341e9772b30a9e3d003f375e9f72e8833ae976c013b34086304e` |
| 서버 설치 영수증 | `/srv/vibetrading-crypto-state/cut01-20261102/evidence/source-sync-20261103.json` |
| 설치 판정 | **PASS: 11-03 22:10:21 KST 설치 후 849개 파일 전체 재읽기 SHA-256 일치** |

이 패키지는 이번 소스 대조·인계를 위한 설치다. 현재 자동 실행되는 기존 운영 세대와 동일하다고 주장하지 않는다. 운영 전체 전환을 진행하려면 6절의 남은 CUT gate가 필요하다.

## 8. 남은 증거와 진행 권고

이 절은 새 실행 권위가 아니다. 실제 작업 순서와 허용 범위는 최종 5개 문서, `05`의 현재 상태 및 승인된 수정 티켓에 따른다.

1. I-16의 전달 병목과 I-17의 종료 오류를 별도 재현 항목으로 고정한다. 보존된 V7 원시 prefix, 실제 자원 한도, 소스 SHA를 재사용하고 시간 분해 증거로 수정 대상 파일/설정을 특정한다. 원인을 특정하기 전 새 기능 추가나 자원 변경을 결론으로 삼지 않는다.
2. 후보 전체 역할을 하나의 gate 표로 관리한다. 공개 시세 앞단의 부분 통과로 다른 기능이 검증된 것으로 처리하지 않는다. 실제 결함의 수정은 이 표에 오류·원인·패치·회귀 결과를 연결한다.
3. 필요한 수정과 전체 회귀가 끝난 한 후보를 고정해 서버 통합 점검한다. 원시 데이터 삭제·실행 모드 우회·신선도 가드 완화로 통과시키지 않는다.
4. 장시간 시험에 도달하면 사용자 요청대로 서버에서 기록·종료하도록 시작만 확인하고 중단한다. 사용자의 재개 명령 후 결과를 조회한다. 시작 확인을 1시간 성공으로 기록하지 않는다.
5. 새 Cloud/retained prefix, owner-fenced 최신 사실, 롤백, 전체 역할, 대표 운영 구간이 실제 통과한 뒤에만 CUT-01을 완료한다. 그 다음 조건을 만족한 경로만 RET-01 대상으로 삼는다.

## 9. 증거와 변경 추적

민감한 원시 자료는 저장소에 복사하지 않았다. 아래 파일명은 보호된 로컬 실행 기록 디렉터리 `/home/pjh/.local/state/codex-opencode/runs/CUT01-RET01-20261102/` 아래의 영수증을 가리킨다.

| 대상 | 주요 증거 |
|---|---|
| 최초 단계/진단 | `LEAD-NOTES.md`, `shadow-v2-failure-diagnostic.stdout`, `shadow-v2-prefix-verification.stdout`, `aa12a22-replay-rejection-pin.stdout` |
| V5/V6 실패·자원 개선 | `v5-trial-final-results.stdout`, `v5-sample-diagnosis.stdout`, `v5-profile-default-fingerprint-v3.stdout`, `v5-profile-latest-abc-v3.stdout`, `v6-unconfirmed-owned-stop.stdout` |
| Gap 버그 실제 재현·수정 대조 | `diag-v7-gap-frames.stdout`, `diag-v7-old-bug-reproduction.stdout`, `v8-retained-live-gap-proof.stdout` |
| 최신 회귀/릴리스 | `V8-source-local-acceptance.json`, `v8-test-artifacts/V8-full.result.json`, `v8-test-artifacts/V8-leases.result.json`, `v8-server-four-image-proof.stdout` |
| 이번 현재 상태·소스 대조 | `audit-server-local-20261103.stdout`, `audit-stopped-sources-20261103.stdout`, `v7-failure-audit-20261103.stdout` |
| 소스 설치 검증 | `source-sync-deploy-20261103.result.json`(최초 실패), `source-sync-empty-stage-check.stdout`, `source-sync-deploy-20261103-v2.stdout`(최종 영수증) |
| 계좌/백업/기존 Cloud | `n9-current-mock-orders-get-only.stdout`, `snapshot-local-verification-v3.progress.json`, `n9-crypto-backup-status-1.stdout`, `current-account-backup-nov3.stdout`, `existing-cloud-runtime-readback.stdout` |

제품 변경은 7개 소스 파일에 810줄 추가/81줄 삭제이며, 기준은 `972fb679..111fa0a`다. 테스트 10개 파일과 수정 티켓을 포함한다. 주요 커밋:

- `aa12a22`: checkpoint 경쟁 조건 및 제한된 전달 창.
- `c3f6f9f`, `9a67ed7`: Arcus 원시 무효 호가 보존·재연결, 명시적 공개 관찰 조립과 경계 수정.
- `7423331`: 공개 관찰의 사실 Gap 재생.
- `d2d6dc7`: 실제 module CLI class identity 수정.
- `993f700`: 공개 관찰 projection 예산과 Mapping runtime import.
- `111fa0a`: 공개 관찰 live Gap 전달 연속성.

이번 보고/소스 보존 커밋은 위 제품 소스를 다시 변경하지 않는다. `04_COMPLETED_MANIFEST.json`의 기존 완료 기록은 수정하지 않는다.
