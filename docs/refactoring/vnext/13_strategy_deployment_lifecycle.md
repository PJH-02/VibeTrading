# Production 13 — 전략 개발·Paper·Live 승격

1. **Research(외부):** 사용자가 알파/FV/신호/파라미터 연구. 서버는 연구/탐색/학습을 실행하지 않는다.
2. **Implementation:** 기존 StrategyModule의 on_event/Signal을 보존하고 필요한 Action 확장만 사용한다. 직접 broker/DB/network 호출 대신 공유 MarketView/History/Feature·공통 owner를 이용한다.
3. **Off-server Backtest:** 검증된 Parquet/History를 reproducible manifest로 export/download. 범위·결손/수정·timestamp/available-at·contract/source/schema/checksum·snapshot selection을 포함한다. 대규모 계산은 외부다.
4. **Registration:** strategy/version/config hash, subscribed instruments/feeds, feature/history/warmup, account/environment/product, capital/risk/queue/runtime budget, 연구 data manifest ID와 요약만 등록한다. TOSS trading account와 metadata 불명 product는 거절한다.
5. **Live-market Paper:** 실제 MarketState와 같은 판단/risk/portfolio 경로, 별도 Paper DB·adapter, 외부 mutation0. 평가기간·수익성 판단은 사용자 책임이며 고정기간 자동 승격 없음.
6. **User-approved Live:** 정확 strategy/config/version/venue/account/subaccount/product/environment·capital/risk·working/UNKNOWN/external position·margin/position mode·fee/funding·reconcile/freshness·adapter·owner·kill/rollback를 재검증한다. Paper cash/position은 실제 계좌로 복사하지 않는다.

예시 lifecycle: REGISTERED→WARMING→PAPER_READY→PAPER_RUNNING→PAUSED/HALTED. Live는 별도 permission이며 strategy 활성 상태와 다르다. 기존 C3/KP/KD의 현재 활성/Mock/Mock2는 임의 변경하지 않는다.

Testnet/Mock은 lifecycle의 의무 중간 단계가 아니다. API signing/nonce/client ID/cancel/fill/UNKNOWN·rate/reconnect·latency 검증에 사용하며 live 동작/수익성과 동일시하지 않는다.

## Export 계약

기존 `scripts/export_market_snapshot.py`, ParquetHistoryReader 및 manifest/hash/reader lease 경로를 우선 재사용한다. export 동안 reader pin으로 원본을 보호하고 정확 dataset snapshot·filter/config·schema/contract metadata·available-at/누락을 고정한다. unsupported history나 provider revision은 명시 UNVERIFIED로 남긴다. export용 신규 DB/백테스트 서비스를 서버에 설치하지 않는다.

실행 명령은 기존 CLI/side-effect 조사 후 TX10 receipt에 고정한다. 예전 research 결과 전체/raw 로그를 운영 원장에 저장하지 않는다. backtest 성과 자체가 execution readiness의 oracle는 아니다.
