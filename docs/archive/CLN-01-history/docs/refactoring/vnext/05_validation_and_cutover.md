# vNext 05 — 검증·용량·서버 전환 Gate

상태: **검증 계획 / 이번 시험 실행 NOT_RUN**. 이번 실제 작업은 문서·소스/영수증 대조·수동 읽기 전용 서버 표본이다. 제품 pytest/성능·장애 시험을 실행하지 않았다. 기존 PASS는 [01](01_current_state_delta.md)의 고정 소스 범위에만 유효하다.

## 1. 재현 가능한 격리 실행 경로

원 workspace `.env`에는 운영 자격 증명이 있으므로 여기서 제품 import/pytest를 하지 않는다. 기존 `prepare_candidate.py`는 최초 baseline의 제거된 연구 requirements 4개를 요구하므로 **그대로 실행하면 실패한다**. 삭제된 파일을 되살리지 않고 현재 source allowlist로 별도 사본을 만든다. VN00/후속 실행 승인 후 아래 명령을 사용한다. 작업 디렉터리는 repo root다.

```powershell
# 기존 기록을 덮지 않는 새 run과 launcher 사본. 이미 존재하면 다른 run ID로 재기준화한다.
$newRun = 'docs/refactoring/runs/20261020-vnext'
if (Test-Path -LiteralPath $newRun) { throw 'Run already exists; use a new run ID.' }
New-Item -ItemType Directory -Path $newRun | Out-Null
foreach ($file in @('offline_guard.py','run_offline_tests.py','run_linux_tests.py')) {
    Copy-Item -LiteralPath ('docs/refactoring/runs/20261018-stage-assignment/' + $file) -Destination $newRun
}
@'
import hashlib,json,shutil
from pathlib import Path
root=Path.cwd().resolve()
manifest=json.loads((root/'docs/refactoring/vnext/source_manifest.json').read_text(encoding='utf-8'))
destination=root/'tmp/refactoring/20261020-vnext/baseline'
destination.mkdir(parents=True,exist_ok=False)
for name,expected in manifest['files'].items():
    source=(root/name).resolve()
    assert source.is_relative_to(root) and not source.is_symlink()
    assert not source.name.startswith('.env')
    assert source.suffix not in {'.db','.sqlite3','.pem','.key','.pyc'}
    assert hashlib.sha256(source.read_bytes()).hexdigest()==expected, name
    target=destination/name
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target)
assert not (destination/'.env').exists()
'@ | python -

python docs/refactoring/runs/20261020-vnext/run_offline_tests.py --source tmp/refactoring/20261020-vnext/baseline --label baseline-windows --timeout 600 tests
python docs/refactoring/runs/20261020-vnext/run_linux_tests.py --source tmp/refactoring/20261020-vnext/baseline --label baseline-linux tests
```

위 코드는 **현재 기준선**의 예시다. 편집 후 candidate를 준비할 때는 root가 새 source manifest를 만들고 같은 allowlist/guard로 **새 경로**에 복사한다. baseline을 candidate로 덮어쓰지 않는다. launcher의 최초 manifest 참조는 결과에 `absent_baseline_paths` 4개를 남기는 용도로만 허용하고, 실제 시험 소스 identity는 새 manifest와 결과의 tested_source_hashes로 대조한다. 과거 source-set을 현재 PASS 키로 쓰지 않는다.

기존 Linux launcher는 로컬 WSL Ubuntu, 깨끗한 env, `/tmp` 사본에서 시험한다. WSL의 기존 dependency 위치와 Python/PyArrow/SQLite 버전부터 확인하며 없다면 운영 서버로 시험을 옮기지 않는다. guard는 협조적 socket/subprocess 차단이며 OS 보안 sandbox라고 주장하지 않는다. 이 명령은 운영 DB·실제 adapter·Telegram에 접근하지 않는 fixture 시험용이다.

관련 회귀 예시(새 source 및 고유 label 필요):

```powershell
python docs/refactoring/runs/20261020-vnext/run_offline_tests.py --source tmp/refactoring/20261020-vnext/baseline --label market-baseline --timeout 300 tests/refactoring/test_local_pipeline.py tests/refactoring/test_local_engine.py tests/refactoring/test_local_transport.py tests/refactoring/test_spool_recovery.py tests/refactoring/test_replay_handoff.py tests/test_local_snapshot.py
python docs/refactoring/runs/20261020-vnext/run_offline_tests.py --source tmp/refactoring/20261020-vnext/baseline --label execution-baseline --timeout 300 tests/refactoring/test_etf_dispatch_guards.py tests/refactoring/test_execution_owner.py tests/refactoring/test_halt_races.py tests/refactoring/test_order_crashes.py tests/test_etf_execution_update.py tests/test_etf_outage_recovery.py tests/test_dual_mock_and_assumed.py
python docs/refactoring/runs/20261020-vnext/run_offline_tests.py --source tmp/refactoring/20261020-vnext/baseline --label storage-control-baseline --timeout 300 tests/refactoring/test_local_storage.py tests/refactoring/test_local_events.py tests/refactoring/test_local_observation.py tests/refactoring/test_archive_integrity.py tests/refactoring/test_control_inbox.py tests/refactoring/test_outbox_isolation.py tests/test_trade_exclusions.py
```

신규 테스트는 tickets.json의 ADD 파일을 만든 뒤 기존 launcher의 positional `tests` 인수에 해당 경로를 지정한다. 아직 없는 `benchmark_vnext.py`의 옵션을 실행 가능한 명령처럼 제시하지 않는다. VN18에서 작은 CLI를 구현·검증하고 정확 명령을 새 run receipt에 고정한다. 위 baseline 실행 후 무관한 문서 수정마다 전체 suite를 반복하지 않는다.

## 2. fixture·독립 oracle·정확성 행렬

| 비교 / Gate | 입력과 oracle | 필수 assertion | 현재 증거와 남은 일 |
|---|---|---|---|
| 모델/형식 G02 | 기존 schema_oracle/encoding_oracle, native/raw fixture | ID/Decimal/null/배열·schema field 순서·logical set/hash 동일, overflow 거절 동일 | 기존 P02 증거. 현재 source 재실행 필요 |
| native book G03 | Binance bridge, Arcus/GRVT snapshot/delta/gap/reconnect trace | 전체 유지 레벨·native cursor·generation·BBO 동일 또는 명시 INVALID | BBO만 맞아도 PASS 금지 |
| 전략 동등 G03/G10 | 고정 config/spec/초기 state/timer/control + 기존 ETF golden | signal/intent side·price·qty·종목·계좌·fee 동일; expected safety difference만 별도 승인 | generic shadow 출력과 ETF 주문을 혼합하지 않음 |
| history G03/G06 | 15 prior sessions/14 lookback, C3 ≥252, end-label/revision/available-at | 미래 정보 사용0, 결손·상충 표시, 준비 전 신규 위험0, backfill/replay 주문0 | KR hotfix 수집/수리 없이 기존 fixture 사용; 누락 자료는 BLOCKER |
| feed 격리 G05 | 두 producer·여러 instrument interleave, 한 feed gap/queue full | unrelated delivery FIFO·cursor 유지, dependent risk 차단; 전역 state 손상은 전역 차단 | VN02/03 신규 시험 필요 |
| IPC 독립 G05/G06 | Engine/UDS stop/restart를 **격리 환경**에서 주입 | Collector durable 증가·상한, C/H continuity/digest, 중복 전략 실행0, 미복구 LIVE0 | 현재 publisher invalid 동작과 expected diff 기록 |
| producer 오류 G05 | append/EIO/fsync 불명/ENOSPC/partial tail | DURABLE 거짓 승격0, cursor/range/원인 보존, 해당 위험 차단, accepted/byte/task 누수0 | physical stalled I/O는 thread 취소와 다름; 종료 deadline 한계 보고 |
| checkpoint G06 | native full-state+rolling windows+quality+cursor vector | 동일 state checksum, bad config/hash/native sequence 거절, stale 시각 유지, replay 주문0 | UI snapshot 사용 금지; 신규 codec/restore 시험 필요 |
| 저장/GC G07 | tmp/fsync/rename/manifest/progress crash, corrupt remote, reader pin | false VERIFIED0, 유일본 삭제0, duplicate canonical set 동일, stale consumer가 있으면 GC0 | copied fixture에서만 deletion injection |
| 주문 G04 | 기존 fake HTTP/adapter, commit/permit/handoff 각 경계 | pre-COMMIT submit0, UNKNOWN blind resend0, duplicate fill 효과1회, late ACK 상태 단조 | 실제 broker의 ODNO/client ID/페이지 완전성 대체 불가 |
| owner/control G04/G08/G11 | 두 협조 프로세스·다른 DB 경로·Mock/Mock2, halt 중 await | 계좌 유효 owner≤1, closed/epoch 재검사, scoped resume가 상위 halt를 열지 않음 | legacy/다른 호스트 실제 소유권은 별도 운영 확인 |
| Telegram G08 | update 중복/TTL/allowlist/ACK 유실/429/1h 단절/1000알림 | effect≤1, durable prefix offset, bounded outbox, 주문/수집 진행 | 조회 golden 및 actual/assumed 보고 보존 |
| mount/release G09 | missing/read-only/wrong-device/changed config·image | root fallback write0, stale evidence로 check 통과0 | 새 role config를 비밀 없는 env로 렌더링 |

독립 oracle는 후보의 출력으로 만들지 않는다. 기존 검증된 provider decoder/reference 경제적 함수와 초기 baseline trace를 고정하고, proposed safety 차이는 별도 expected-diff에 명시한다. 삭제된 JUnit/raw 로그를 원본 증거로 링크하지 않는다.

## 3. 동일 조건 A/B와 latency 측정

비교군: **현재 실제 owner source에서 재구성한 운영 NATS 경로**, **v6 후보**, **VN01~05의 A1 후보**. A2/A3는 별도 승인된 실험 후보가 있을 때만 추가한다. 운영을 죽여 장애를 만들지 않고 동일 2 vCPU/RAM·디스크·dependency·provider input trace·timer/계좌 fake/config를 고정한 격리 replay로 비교한다. NATS 역할의 CPU/RAM도 운영군 비용에 포함한다.

- 각 군 cold/warm cache를 구분하고 실행 순서를 교차해 최소 3회 측정한다. 입력/초기 state/hash·기간·event count·rate/burst profile·장중/장외 scope를 남긴다. fixture 부하와 수동 운영 trace는 별도 결과다.
- 단계 timestamp: receive, normalize/encode 종료, admission, append, group fsync 종료, UDS receive/decode, engine apply, strategy decision, ledger COMMIT, permit/handoff, ACK/fill. 프로세스 간 monotonic은 같은 host boot/clock domain 확인 후만 차감한다. vendor source delay는 다른 지표다.
- CPU/event는 process CPU time/처리 이벤트 수와 전체 CPU를 함께 보고한다. worker/압축·서명·관측 포함 여부, encode/decode/copy 횟수, bytes/event를 명시한다. allocation/Arrow memory/page cache는 RSS와 구분한다.
- 전체 `receive→decision`, `signal→send`와 단계별 평균/p50/p95/p99/p99.9를 **paired event 단위**로 산출한다. component p99의 합을 E2E p99로 쓰지 않는다. histogram/표본 저장은 유한하게 하고 계측 on/off 비용을 보고한다.
- p99.9는 tail 표본 부족·burst 편중이면 미판정이다. 예를 들어 100만 표본도 tail 약 1000개일 뿐이므로 표본수·기간·분산/신뢰 범위와 함께 제시한다. 몇 건의 testnet 응답으로 tail SLO를 정하지 않는다.
- 기존 목표 유지: 같은 시나리오 p99/p99.9 > baseline 1.10이면 FAIL; CPU/event 20% 절감은 목표이며 미달 시 수치 공개·ADR 필요. 실제 E2E 개선 목표는 해당 E2E를 잴 때만 평가한다. absolute freshness/TTL은 생산 parameter 미확정이면 live gate 차단.
- Crypto의 경제 효과는 stale quote 빈도·결정 이후 가격 이동·가상/실제 fill의 adverse selection·fee/funding/통화·계약승수·queue position 자료가 있어야 비교한다. latency 감소만으로 수익 증가를 추정하지 않는다. KR은 정확한 이력/판단 시각 동등성을 먼저 통과한다.

## 4. 지속 실행·회복·자원 Acceptance

| 시험 | 절차 | 합격 조건 |
|---|---|---|
| >30분 NATS-free | broker 미기동·접속 차단인 Linux 격리 후보, **wall-clock >1800초** 수신/상태/로컬 제어/지정 fake execution 지속; Engine 재접속 별도 | 정확 cursor/hash·상한·실제 경과시간. 가상 1801초는 이 행을 PASS하지 못함 |
| Parquet 15분 중단 | worker 정지 상태로 900초 유입 후 재시작 | 남은 spool capacity 검증, backlog 1800초 내 회복, 데이터 set/hash/전략 결과 보존, false LIVE0 |
| Cloud 24h 중단 | 업로드 경로만 86400초 차단, 로컬 처리 지속 | 유일본·원격 미검증 상태 보존, 복구 86400초 내 readback 완료. 디스크 부족이면 FAIL/설계 재검토 |
| 대표 장중/crypto 동시 부하 | 72시간 coverage에 국내 개장/마감·미국 세션·crypto burst 포함; gap과 observer 생존 기록 | 중단 구간을 빼고 72h라고 하지 않음. fixture만 있으면 production Missing Evidence |
| restart/RTO | log 규모를 여러 단계로 키워 baseline full replay vs checkpoint+delta 반복 | correctness와 feed별 준비 시간 분리, 기존 3050만 index 재검증 영향 포함. RTO 상한은 실측으로 확정 전 UNVERIFIED |
| 자원 | cgroup memory.current/stat/swap·PSS/RSS, host MemAvailable·PSI·vmstat delta·IO·큐 age/inflight, 동시 maintenance/backup/monitor 포함 | 앱 전체 동시 peak 목표 ≤3GiB, host reserve/도구 공간 보존. CPU 평균 목표 2 core의70% 이하. swap 증가로 메모리 목표를 숨기지 않음 |

메모리 budget은 collector+engine/history/feature+owner/DB+Arrow/storage+archive+Telegram/dashboard+전환 중 legacy까지 포함한다. cgroup 전체 안에 이미 들어간 큐/page cache를 다시 더하지 않는다. 개별 container limit 합으로 host peak를 주장하지 않는다. 한 호스트에서 구/신 stack 전체를 동시에 실행하지 않는다.

물리 볼륨별 필요 공간은 `spool backlog + 미검증 Parquet/facts + checkpoint 두 세대와 tmp + WAL/DB/backup + reader pin으로 회수 못 하는 원본 + reserve`다. 이미 사용한 바이트를 두 번 빼지 않는다. CapacityContract의 `outage_seconds>1800`은 boot admission 최소이고 cloud 24h 목표를 만족했다는 뜻이 아니다. 정상 유입 λ, 검증 완료 μ에서 `회복시간=λT/(μ−λ)`, μ>λ가 필요하다. 기존 후보 μ/λ=1.8(Parquet),2.4(remote verified)를 유지하되 실측으로 입증한다.

## 5. 단계별 Gate와 승인 경계

| Gate / 기존 연결 | 필요한 증거 | 이번 상태 |
|---|---|---|
| VN-G0 / G00·G01 | 최신 source/image/config/mount, C 계약 delta·ticket before hashes·fixture | source/짧은 readonly 일부 확보. Compose 의미·계좌/parameter 미확인 |
| VN-G1 / G02~G08 | 각 티켓 scoped 회귀·독립 리뷰·정확 candidate 전체 Windows/Linux 회귀 | NOT_RUN; 기존 증거만 보존 |
| VN-G2 / G09·G10·G-NATS-FREE | 같은 2vCPU A/B·실제 >30m·15m/24h outage·72h·용량/전체자원·복원/GC | NOT_RUN/MISSING_EVIDENCE |
| VN-G3 / G11 | 구체적 역할별 command/digest/mount·일관 백업 복원·현재 계좌/UNKNOWN·legacy fencing | BLOCKED. 검증된 패키지 이후 서버 이동 실행 승인 |
| VN-G4 / G12 | 전환 안정화·legacy backlog 검증 보관·미참조·rollback 보존 | NOT_STARTED. 원본/stream/객체 삭제 별도 범위 |

fixture/offline gate만 충족하면 서버에서 비활성 image 게시도 자동 승인되지 않는다. 먼저 호스트에서 build/test하지 않는 immutable image·검증 결과·서비스별 config diff·예상 자원·rollback digest를 review 가능한 패키지로 완성한다. 이번 PLAN-ONLY 권한으로 새 candidate를 서버에 게시하지 않는다.

## 6. 향후 cutover / rollback 순서

1. **직전 재조회:** 현재 active Compose/config hash·image/mount·provider/strategy/account mode·OS/도구/data 여유와 모든 주문자를 대조한다. 이번 snapshot은 실행 시점 허가가 아니다. git/source/package/image의 추적 관계와 bind overlay까지 고정한다.
2. **blocked 준비:** 신규 위험을 halt하고 미체결/UNKNOWN/예약/position/실체결·가정 장부를 대조한다. 일관 백업을 별도 경로에서 복원 검증한다. NATS backlog와 기존 OPERATIONS/notifications를 별도 보존한다.
3. **단일 owner:** 기존 ETF/다른 주문 프로세스의 자동 재시작을 차단하고 종료한다. canonical lease 및 실제 계좌 owner set을 확인한다. 다른 DB path/다른 호스트/수동 주문은 lease 밖임을 점검한다.
4. **제한 후보 시작:** 승인된 role만 blocked로 실행한다. checkpoint/delta/ETF history·native snapshot/window·계좌 epoch를 검증한다. generic 전략은 shadow만, 기존 비활성 provider/전략은 유지한다.
5. **구독 중복 제약:** provider가 이중 연결을 허용하는지 확인 전 전체 이중 Collector를 기동하지 않는다. 동일 trace 오프라인 비교를 우선하고, 필요한 실시간 shadow는 허가된 제한 tap만 사용한다.
6. **승인 scope 재개:** 현재 운용 Mock/Mock2를 먼저 보존해 대조한다. 새로운 real/crypto 주문 권한은 adapter별 실제 조회/취소/체결·ID·nonce·precision/fee·위험 한도 증거와 별도 사용자 결정 전에는 0이다. 리팩토링 성공으로 활성화하지 않는다.
7. **관측/중단:** owner 충돌, UNKNOWN 증가/대조 불명, 숨긴 gap, source/mount drift, 오류 재발, budget/latency 초과 시 신규 위험을 닫는다. Collector는 가능한 기록을 계속하며 불확실한 데이터를 정상 처리하지 않는다.
8. **복귀:** candidate halt → 가능한 전송/체결 대조 → candidate/restart 차단 → **최신 원장을 읽을 수 있는** 이전 binary blocked 시작 → 계좌·시장 상태 재대조. live DB를 과거 backup으로 덮지 않는다. 호환하지 않으면 복귀보다 blocked 복구가 우선이다. Telegram sender/receipt 단일성도 확인한다.

실제 `docker compose` 전환 명령은 최신 의미 diff·exact digest가 없는 현재 단계에서 작성하지 않는다. VN19가 **실제 active Compose**와 승인된 역할만 명시한 명령·중단 조건을 완성해야 한다. NATS service/stream/consumer 삭제와 원격 객체 삭제는 전환 성공의 부수 작업이 아니다.
