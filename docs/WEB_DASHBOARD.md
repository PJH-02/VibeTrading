# VibeTrading 웹 운용 대시보드

접속: https://vibetrading-monitor.mwm3870.chatgpt.site

Sites의 소유자 전용 접근 정책으로 배포했다. 브라우저에서 로그인한 후 PC·모바일에서
동일한 URL을 이용한다. 사용자가 SSH 터널이나 로컬 개발 서버를 실행할 필요가 없다.

## 화면

- 주식: 기존 수집기의 Core NATS `md.>`를 받아 거래소·종목별 최근 가격, 10단계 호가,
  최근 40건 체결을 표시한다. 1분봉만 수집하는 종목은 체결 스트림 미제공을 표시한다.
  장이 닫힌 시간에는 새 체결이 없으며, 최초 연결 전 종목은 수신 대기로 표시된다.
- 코인: Binance, Bitget, Bybit, GRVT, Arcus의 BTC/ETH/SOL 무기한 상품을 표시한다.
  조회용 공개 연결은 기존 수집기 구현을 재사용하며, 초기 전체 스냅샷을 확보한 후
  순서 검증을 거쳐 델타 호가를 복원한다. 기존 저장 소비자의 메시지를 소비하지 않는다.
  USD와 USDT를 구분하며, 호가 동기화 실패·오래된 데이터는 실시간으로 표시하지 않는다.
- 전략·계정: MM 저널의 런별 수수료 차감 실현 PnL, 전략 생존 신호, Bybit 테스트넷·실거래
  계정의 최근 7일 `closedPnl` 및 현재 미실현 PnL을 표시한다. 계정 집계는 전략 귀속으로
  간주하지 않는다. 런, 계정, 통화가 다른 PnL은 합산하지 않는다. 성과 곡선은
  대시보드 운영 시작 이후 1분 간격 관측치이며, 과거 곡선을 임의로 생성하지 않는다.
- 주문: SQLite 주문 의도, MM 저널 및 Bybit 조회 API의 주문 기록을 영구 upsert한다.
  보존된 `/srv/vibetrading-data/testnet_latency_audits/bybit_*.sqlite3` 파일럿 감사 원장은
  배포 시 네트워크 격리된 일회성 프로세스에서 표시 필드만 내보낸다.
  원장 내용·소유권·0600 권한은 바꾸지 않으며 거래소 최근 조회 기간 이전 기록도 표시한다.
  상태와 환경을 구분한다. 기존 기록에서 환경을 입증할 수 없으면 `환경 미확인`이다.
  최근 조회는 200건이고 이전 주문 불러오기를 지원한다. CSV는 조회된 필터 결과다.
- 타임폴리오: 서버 `.env`의 기본 계정과 화면에서 연결한 계정별로 로그인·조회한다.
  회전율과 실현 손익은 명시된 계정 지표만 사용한다. 주간 회전율 가이드라인은
  `현재` 열을 읽으며 `매주 5% 이상` 기준을 계정의 값으로 사용하지 않는다.
  NAV 차트 축, 평가 손익, 주문 비중으로 실현 손익을 추정하지 않는다.
  대회 종료·원본 지표 미제공·로그인 실패에는 값이 `—`로 남는다.

## 데이터 경로와 접근

`수집/저널 → 서버 조회 서비스 → HTTPS 업로드 → 개인 Sites D1 → 브라우저`

시장 상태는 약 2초 간격, 주문·로컬 저널은 10초 간격, 거래소 계정은 60초 간격,
타임폴리오는 계정을 순차 조회한 후 120초 간격으로 갱신한다. 브라우저가 숨겨지면
조회 주기가 늘어난다. 데이터 연결이 끊기면 마지막 수신 값과 지연 상태를 표시한다.
시장 틱 전체를 웹 데이터베이스에 복제하지 않는다. PnL 관측치는 30일 보관한다.

거래소 API 키, 타임폴리오 평문 비밀번호, 원본 브라우저 DOM은 Sites로 보내지 않는다.
브라우저에서 입력한 타임폴리오 계정 정보는 AES-256-GCM으로 암호화하고 AES 키를
서버의 RSA-OAEP-SHA256 공개 키로 봉인한다. D1에는 암호문과 표시 이름만 저장한다.
개인 키 `/opt/vibetrading/dashboard/monitor/private.pem`은 서버에만 있으며 0600이다.
이 키를 분실하면 등록 계정을 다시 연결해야 한다. 키 백업은 서버 비밀 관리 정책으로 한다.
서비스 업로드에는 Sites 접근 토큰과 별도 ingest 토큰이 필요하다. 브라우저 계정 변경에는
Sites 사용자 신원과 동일 출처 요청이 필요하다. 앱에는 주문 실행 API가 없다.

## 서버 운영

별도 Compose 프로젝트 `vibetrading-monitor`:

```bash
docker compose --env-file /etc/vibetrading/dashboard.env \
  -f /opt/vibetrading/dashboard/deploy/dashboard-compose.yml ps
python3 /opt/vibetrading/dashboard/scripts/check_dashboard_server.py
```

두 컨테이너는 `restart: unless-stopped`로 설정되어 Docker 시작 시 재실행된다.
bridge: 메모리 192 MiB / CPU 0.5. timefolio: 메모리 384 MiB / CPU 0.5.
기존 런타임 데이터 볼륨은 읽기 전용이고 별도 모니터 상태 디렉터리만 쓰기 가능하다.
호스트 네트워크에 새 포트를 개방하지 않는다. 기존 수집·저장 서비스를 재시작하지 않았다.
실거래와 테스트넷 모두 조회 API만 호출한다.

비밀 설정은 `/etc/vibetrading/dashboard.env` (0600)에 저장하고 Git에 넣지 않는다.
Sites `INGEST_TOKEN` 비밀과 일치해야 한다. Site의 실제 배포 URL이 변경되면 서버의
`DASHBOARD_SITE_URL`을 변경하고 bridge 컨테이너를 재생성한다.

MM 저널의 기본 호스트 경로는 `/root/vibetrading/runtime_data/liquidity_mm`이며,
실제 전략의 `state_dir` 변경 시 Compose `/pilot` 마운트도 변경해야 한다.
전략 생존 신호 추가는 로컬의 `strategy_runner.py`, `timefolio/runner.py`에 반영했다.
현재 실행 중인 수집 서비스의 코드를 바꾸지 않았으며, 다음 전략 릴리스에 포함해야 한다.
기존 전략 실행기에서 PnL을 제공하지 않으면 전략 PnL은 미확인으로 표시된다.

새 실행기는 `system.dashboard_strategy`에 아래 형식으로 10초마다 게시할 수 있다.
주문/체결의 실제 귀속을 확인한 손익만 입력한다.

```json
{"id":"job-id","name":"strategy-name","account":"account-id","section":"crypto",
 "mode":"testnet","currency":"USDT","running":true,"status":"RUNNING",
 "updated_ms":1791090000000,"realized_pnl":"1.25","unrealized_pnl":"0.10"}
```

## 소스·배포

웹 소스: `dashboard/` (별도의 Sites Git 저장소).
Python 연결부: `vibetrading/dashboard/`.
배포 설정: `deploy/dashboard-compose.yml`, `deploy/Dockerfile.dashboard-timefolio`.
`scripts/provision_dashboard.py`는 서버에서 비밀 JSON을 stdin으로 받는다.
로컬 `scripts/deploy_dashboard.mjs`는 터미널 에코를 끄고 SSH stdin으로 전달한다.
비밀을 CLI 인자나 로그에 쓰지 않는다.

Windows의 Sites Bash 패키징 래퍼는 경로 호환 문제가 있어
`scripts/package_dashboard.mjs`에서 공식 `prepare-site-build.cjs` 정규화와 동일한
아카이브 구조를 사용했다. 소스를 Sites 저장소에 push한 다음 해당 commit에서
빌드한 아카이브를 배포한다. 데스크톱의 브라우저 연결이 불가하여 화면 시각 QA는
미검증이며, 타입 검사·빌드·API 접근 검사·운영 서버 데이터 수신은 검증했다.
