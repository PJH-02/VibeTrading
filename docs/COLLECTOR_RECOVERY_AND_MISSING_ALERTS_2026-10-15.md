# KIS·Kiwoom·TOSS 수집 중단 및 알림 누락 복구 — 2026-10-15

최종 검증: 2026-10-15 10:15:39 KST. 서버: VibeTrading main 49.50.141.106.

## 원인

04:33경 VS Code 서버 Stable-16507d91be0dc1ce4f94c04d4d0430210b8685d9 버전(약 671MiB)이 루트 파일시스템에 추가됐다. 보존된 설치 디렉터리 mtime은 04:33:30이고, OS 디스크 가드 기록은 04:33:28 free_bytes=1447677952에서 04:33:39 free_bytes=946470912로 감소했다.

가드의 1GiB 정지 임계값을 넘어 04:33:46 KIS/Kiwoom/TOSS 공용 컨테이너가 Docker stop으로 종료됐다. 이후 10초마다 stop을 반복했다. ExitCode=0, OOM=false였으며 restart=unless-stopped는 명시적 Docker stop을 자동 취소하지 않는다. 가드는 2GiB 이상 확보되어야 재개하도록 되어 있어 장 개장 후에도 정지 상태였다. systemd oneshot active는 내부 컨테이너의 실행 상태를 보장하지 않았다.

세 API가 각각 동시에 실패한 것이 아니다. 첫 수동 재시작도 가드가 다시 중지했다. 디스크를 복구한 뒤 KIS/TOSS는 수신을 재개했다. Kiwoom에는 별도 인증 문제가 남았다: REST 8005/return_code=3, WS 805004. 기존 코드는 미래 만료 시각만 보고 거절된 토큰을 계속 재사용했다. REST 분봉과 WS가 동시에 토큰을 발급할 수 있었고 캐시가 자격증명에 묶여 있지 않은 문제도 함께 보완했다. 최초 무효화의 정확한 원인까지 확인된 것은 아니다.

## 알림이 없었던 이유

Telegram의 독립 heartbeat 감시는 코인 수집기에만 있었다. 국내 수집기는 NATS system.collector_error 이벤트에 의존했다. OS 가드가 컨테이너 전체를 중지하면 수집기 자신이 오류 이벤트를 발행할 수 없다. 과거 KIS/Kiwoom health가 market_closed로 남은 뒤 heartbeat도 갱신되지 않았지만, 개장 후 이를 검사하는 독립 감시가 없었다.

/status는 조회 시 오래된 heartbeat를 오류로 표시할 수 있었지만 자동 경보와는 별개였다. 이번 누락은 자동 경보의 감시 범위 공백이다.

## 복구 및 수정

VS Code 서버 설치 4개 버전의 파일 14,404개, 2,730,053,376바이트를 SHA-256 비교 후 /srv/vibetrading-data/host-tools/vscode-servers로 옮겼다. /root/.vscode-server/cli/servers는 같은 위치를 가리키는 symlink로 유지했다. 사용자 작업 파일이나 시장 데이터는 삭제하지 않았다. 루트 여유는 약 0.9GiB에서 3.47GiB로 늘었다. VS Code 후속 서버 설치도 이 경로로 저장된다.

OS 디스크 가드의 1GiB 정지/2GiB 재개 기준은 유지했다. 새 가드는 이미 멈춘 컨테이너에 stop을 반복하지 않고, 현재 검증된 runtime.yml의 collectors만 --no-deps --no-build로 복구한다. 상태·여유 공간·pause 여부를 state/health/os-disk-guard.json에 기록해 독립 경보가 이유를 표시할 수 있도록 했다.

Kiwoom: 캐시에 자격증명 fingerprint를 저장하고, REST/WS 및 Linux 프로세스 간 token 발급을 직렬화한다. 8005/805004 인증 거절 시 해당 토큰만 폐기한다. 다른 프로세스가 새로 발급한 토큰은 폐기하지 않는다. 조회 API만 새 토큰으로 한 번 재조회하며 주문 POST 및 통신 결과 불명의 요청은 자동 재전송하지 않는다. WS 로그인 거절은 캐시 무효화 후 기존 reconnect 루프가 재접속한다.

Telegram: KIS/Kiwoom/TOSS를 NATS 연결 및 수집 프로세스와 독립적으로 30초마다 검사한다. 실제 거래 세션에 heartbeat 없음/120초 초과, 명시적 error·paused·retrying, 실제 이벤트 180초 이상 중단, 개장 후 연결 미완료가 지속되는 경우를 알린다. 장 종료 상태를 개장 후에도 정상으로 믿지 않는다. 정상 장 종료·시작·복구 알림은 계속 보내지 않는다. 동일 오류는 5분 간격으로 제한하며 전송에 실패하면 전송 완료로 기록하지 않는다.

수집기 내부 watchdog도 국내·TOSS에 장중 무수신 180초 reconnect를 적용했다. Kiwoom은 선물장만 열린 08:45~09:00을 주식 개장으로 취급하지 않도록 세션을 분리했다.

## 현재 상태와 주문 영향

| 수집기 | 최종 상태 | 최근 실제 수신 |
|---|---|---|
| KIS | publishing | A01612, A01703, A06612, A67612, A67703, A75610, A75611 |
| Kiwoom | publishing | 000660, 005930, 069500, 114800, 122630, 229200, 233740, 251340, 252670 |
| TOSS | publishing | QQQ, SPY |

NATS Core의 md.>를 비소비형으로 관측한 2026-10-15 10:13:28 KST~2026-10-15 10:14:03 KST의 실제 발행:

- KIS: {'order_book': 8337, 'trade': 995}. 구성은 8종목/16개 호가·체결 구독이고 짧은 관측 구간에 7종목에서 실제 이벤트가 있었다.
- Kiwoom: {'trade': 2035, 'order_book': 1199, 'etf_nav': 328, 'bar_1m': 146}. 설정 9종목 모두 실제 수신.
- TOSS: {'order_book': 431, 'trade': 14}. SPY/QQQ 실제 수신.

기존 WorkQueue를 소비하거나 ACK하지 않고 별도의 Core NATS 구독자로 검증했다. 원래 저장 소비자에 영향을 주지 않았다.

모의매매 서비스는 ready, pending_orders=0, mock/mock2 종목 라우팅 유지. 새 API 키나 계좌 설정을 변경하지 않았다. 오늘 분봉을 chart API의 backfill로 복구했고 최신 실시간 분봉도 반영됐다. **10:28 후속 시각별 검사에서 122630의 09:01·09:02 종료 분봉 두 개가 저장분에 없는 것을 확인했다. 네 ETF가 모두 완전히 복구됐다는 뜻으로 해석하면 안 된다. 무거래 또는 수집 누락인지는 별도 원본 확인이 필요하다.** 아래 수치는 10:15 당시 입력 수이며, 시각별 후속 검사는 [원인·누락 재검증 보고서](ROOT_CAUSE_AND_ETF_GAP_AUDIT_2026-10-15.md)를 따른다:

| ETF | 오늘 완료 분봉 수 | 마지막 종가 |
|---|---:|---:|
| 069500 | 75 | 87550.0 |
| 122630 | 73 | 68690.0 |
| 229200 | 75 | 12815.0 |
| 251340 | 75 | 2600.0 |

장중 미수집 기간에는 신호 평가도 중단됐다. 최종 확인 시 당일 decisions와 orders는 0건이다. 따라서 오늘 C3 개장 구간과 KP/KD 09:30·10:00 평가 기회에 공백이 있었다. 그 시점에 반드시 실제 진입 신호가 있었는지, 놓친 손익이 얼마인지는 이 기록만으로 확정하지 않는다. 과거 호가·체결 자체를 재구성하지 않았으며 지난 신호를 현재 주문으로 소급 제출하지 않았다. 복구된 과거 분봉은 상태·지표 계산에 쓰고 현재 실시간 신호부터 기존 주문 루프가 처리한다.

## 검증·배포

네트워크 격리 Linux 컨테이너에서 관련 테스트 63개, OS 가드 회귀 테스트 4개 통과(고유 67개). 기존 collector/mock 이미지별 토큰 테스트 6개씩도 통과했다. 최종 컨테이너의 실제 Telegram 모듈로 heartbeat 완전 누락 3개 provider, 중복 제한, 장 종료 무알림을 dry-run 검증했다. 이 dry-run의 전송 함수는 가로채 실제 Telegram 오류 메시지를 보내지 않았다.

실제 Telegram 서비스 enabled=true이고 실제 상태 생성기는 국내 수집기 3개를 모두 정상으로 표시했다. 이번 검증으로 실제 장애 시 Telegram 수신까지 실전 재현했다고 주장하지 않는다. 테스트용 주문이나 가짜 장애 메시지는 발송하지 않았다.

변경 서비스 collectors/telegram/etf-mock/etf-csv-sync는 각 기존 이미지를 기반으로 해당 파일만 패치했다. 코인 수집기와 저장 서비스는 재시작하지 않았다. 네 서비스의 최종 로그 오류 0건, restart_count=0, 건강검사 대상 healthy를 확인했다. systemd 재부팅 경로와 디스크 가드 재개 경로 모두 갱신된 runtime.yml을 사용한다.

배포 위치: /srv/vibetrading-data/releases/collector-recovery-20261015. 원본 이미지/compose, 주문 장부 백업은 서버에 보존했다.

## 증적

[복구 전 상태](evidence/collector_recovery_20261015/before.json), [파일 이동 검증](evidence/collector_recovery_20261015/vscode-relocation.json), [실제 NATS 관측](evidence/collector_recovery_20261015/nats-observation.json), [최종 상태·배포·테스트](evidence/collector_recovery_20261015/verification.json).
