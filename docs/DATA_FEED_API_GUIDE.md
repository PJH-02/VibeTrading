# Data Feed API Integration Guide

이 문서는 VibeTrading 시스템에서 사용하는 각 시장별 데이터 API 통합 방법을 설명합니다.

## 📋 목차

1. [개요](#개요)
2. [한국 시장 (KR Market)](#한국-시장-kr-market)
3. [미국 시장 (US Market)](#미국-시장-us-market)
4. [암호화폐 시장 (Crypto Market)](#암호화폐-시장-crypto-market)
5. [환경 설정](#환경-설정)
6. [사용 예제](#사용-예제)

---

## 개요

VibeTrading 시스템은 다음 3개 시장을 지원합니다:

- **한국 주식 시장 (KR)**: 한국투자증권 KIS API 사용
- **미국 주식 시장 (US)**: 한국투자증권 KIS API 사용 (해외주식 API)
- **암호화폐 시장 (CRYPTO)**: Binance/Bybit Public WebSocket + Binance REST 사용

각 시장별로 실시간 데이터 스트리밍과 과거 데이터 조회를 지원합니다.

---

## 한국 시장 (KR Market)

### 사용 API

**한국투자증권 KIS API**
- 공식 문서: https://apiportal.koreainvestment.com
- 국내주식 시세 조회
- 실시간 시세 WebSocket 스트리밍

### API 키 발급 방법

1. 한국투자증권 홈페이지 접속
2. API 포털 (https://apiportal.koreainvestment.com) 회원가입
3. 앱 등록하여 AppKey, AppSecret 발급
4. 모의투자 또는 실전투자 계좌 선택

### 주요 기능

#### 1. 실시간 시세 스트리밍
- WebSocket을 통한 실시간 체결가 수신
- 1분봉 데이터 생성
- 거래량, 체결가 정보 제공

#### 2. 과거 데이터 조회
- REST API를 통한 일별/주별/월별 OHLCV 데이터 조회
- 최대 1000개 캔들 조회 가능
- 수정주가 옵션 지원

### 지원하는 데이터 형식

```python
Candle(
    market=Market.KR,
    symbol="005930",  # 삼성전자 (6자리 종목코드)
    timestamp=datetime(2024, 1, 1),
    open=Decimal("70000"),
    high=Decimal("71000"),
    low=Decimal("69500"),
    close=Decimal("70500"),
    volume=Decimal("10000000"),
    interval="1d"
)
```

### 종목코드 형식
- 6자리 숫자로 구성 (예: `005930` - 삼성전자)
- `.KS`, `.KQ` 등의 접미사는 자동으로 제거됨

---

## 미국 시장 (US Market)

### 사용 API

**한국투자증권 KIS API (해외주식)**
- 공식 문서: https://apiportal.koreainvestment.com
- 해외주식 시세 조회
- 실시간 해외주식 WebSocket 스트리밍

### API 키 발급 방법

한국 시장과 동일한 KIS API 사용 (동일한 AppKey, AppSecret 사용)

### 주요 기능

#### 1. 실시간 시세 스트리밍
- WebSocket을 통한 미국 주식 실시간 체결가 수신
- NASDAQ, NYSE, AMEX 지원
- 실시간 가격 및 거래량 정보

#### 2. 과거 데이터 조회
- REST API를 통한 일별/주별/월별 OHLCV 데이터 조회
- 미국 주요 거래소 종목 지원
- 수정주가 옵션 지원

### 지원하는 데이터 형식

```python
Candle(
    market=Market.US,
    symbol="AAPL",  # 애플 (티커 심볼)
    timestamp=datetime(2024, 1, 1),
    open=Decimal("180.50"),
    high=Decimal("182.00"),
    low=Decimal("179.80"),
    close=Decimal("181.20"),
    volume=Decimal("50000000"),
    interval="1d"
)
```

### 종목코드 형식
- 티커 심볼 사용 (예: `AAPL`, `MSFT`, `TSLA`)
- `.US`, `.NASDAQ` 등의 접미사는 자동으로 제거됨

### 지원 거래소
- NASDAQ (`NAS`)
- NYSE (`NYS`)
- AMEX (`AMS`)

---

## 암호화폐 시장 (Crypto Market)

### 사용 API

**Binance + Bybit**
- Binance 문서: https://binance-docs.github.io/apidocs/spot/en/
- Bybit 문서: https://bybit-exchange.github.io/docs/v5/ws/connect
- 공용 WebSocket 스트리밍 (API 키 없이 가능)
- Binance WS (mainnet): `wss://stream.binance.com:9443/stream`
- Binance WS (testnet): `wss://testnet.binance.vision/stream`
- Bybit WS (mainnet): `wss://stream.bybit.com/v5/public/spot`
- Bybit WS (testnet): `wss://stream-testnet.bybit.com/v5/public/spot`

### API 키 발급 방법

1. Binance 회원가입 (https://www.binance.com)
2. API Management 페이지에서 API Key 생성
3. Testnet 사용 시: https://testnet.binance.vision

### 주요 기능

#### 1. 실시간 시세 스트리밍
- WebSocket을 통한 실시간 캔들 데이터
- 다양한 시간 간격 지원 (1m, 5m, 15m, 1h, 4h, 1d)

#### 2. 과거 데이터 조회
- REST API를 통한 과거 캔들 조회
- 최대 1000개 캔들 조회 가능

### 지원하는 데이터 형식

```python
Candle(
    market=Market.CRYPTO,
    symbol="BTCUSDT",
    timestamp=datetime(2024, 1, 1),
    open=Decimal("45000.50"),
    high=Decimal("45500.00"),
    low=Decimal("44800.00"),
    close=Decimal("45200.00"),
    volume=Decimal("150.5"),
    interval="1m"
)
```

### 종목코드 형식
- 기본적으로 USDT 페어 사용
- `BTC` → `BTCUSDT`로 자동 변환

---

## 환경 설정

### 1. 환경 변수 파일 생성

`.env.example` 파일을 `.env`로 복사하고 실제 값을 입력합니다:

```bash
cp .env.example .env
```

### 2. API 키 설정

#### 한국투자증권 KIS API (한국 + 미국 시장)

```env
KIS_APP_KEY=your_app_key_here
KIS_APP_SECRET=your_app_secret_here
KIS_ACCOUNT_NUMBER=12345678-01
KIS_ACCOUNT_PRODUCT_CODE=01
KIS_USE_MOCK=true  # 모의투자: true, 실전투자: false
```

#### Crypto API / Public WebSocket (암호화폐 시장)

```env
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_api_secret
BINANCE_TESTNET=true  # 테스트넷: true, 메인넷: false
CRYPTO_EXCHANGE=binance  # binance | bybit
CRYPTO_WS_URL=  # optional: public websocket URL override
BYBIT_TESTNET=true
```

### 3. 의존성 설치

```bash
pip install -r requirements.txt
```

주요 의존성:
- `aiohttp`: KIS API HTTP 요청
- `websockets`: 실시간 데이터 스트리밍
- `python-binance`: Binance API 클라이언트

---

## 사용 예제

### 한국 시장 데이터 가져오기

```python
import asyncio
from datetime import datetime, timedelta
from services.data_feed.kr_feed import KRDataFeed

async def main():
    feed = KRDataFeed()
    
    # 연결
    await feed.connect()
    
    # 과거 데이터 조회 (삼성전자, 최근 30일)
    candles = await feed.get_historical_candles(
        symbol="005930",
        interval="1d",
        start_time=datetime.now() - timedelta(days=30),
        limit=30
    )
    
    print(f"조회된 캔들 수: {len(candles)}")
    for candle in candles[:5]:
        print(f"{candle.timestamp}: {candle.close} (거래량: {candle.volume})")
    
    # 실시간 스트리밍
    await feed.subscribe_candles(["005930", "000660"], interval="1m")
    
    async for candle in feed.stream_candles():
        print(f"실시간: {candle.symbol} - {candle.close}")
        # 10개만 받고 종료
        if candle.timestamp:
            break
    
    await feed.disconnect()

asyncio.run(main())
```

### 미국 시장 데이터 가져오기

```python
import asyncio
from datetime import datetime, timedelta
from services.data_feed.us_feed import USDataFeed

async def main():
    feed = USDataFeed()
    
    # 연결
    await feed.connect()
    
    # 과거 데이터 조회 (애플, 최근 30일)
    candles = await feed.get_historical_candles(
        symbol="AAPL",
        interval="1d",
        start_time=datetime.now() - timedelta(days=30),
        limit=30
    )
    
    print(f"조회된 캔들 수: {len(candles)}")
    for candle in candles[:5]:
        print(f"{candle.timestamp}: {candle.close} (거래량: {candle.volume})")
    
    # 실시간 스트리밍
    await feed.subscribe_candles(["AAPL", "MSFT", "TSLA"], interval="1m")
    
    async for candle in feed.stream_candles():
        print(f"실시간: {candle.symbol} - {candle.close}")
    
    await feed.disconnect()

asyncio.run(main())
```

### 암호화폐 시장 데이터 가져오기

```python
import asyncio
from datetime import datetime, timedelta
from services.data_feed.crypto_feed import CryptoDataFeed

async def main():
    feed = CryptoDataFeed()  # uses CRYPTO_EXCHANGE / CRYPTO_WS_URL
    
    # 연결
    await feed.connect()
    
    # 과거 데이터 조회 (비트코인, 최근 100개 1분봉)
    candles = await feed.get_historical_candles(
        symbol="BTCUSDT",
        interval="1m",
        start_time=datetime.now() - timedelta(hours=2),
        limit=100
    )
    
    print(f"조회된 캔들 수: {len(candles)}")
    
    # 실시간 스트리밍
    await feed.subscribe_candles(["BTCUSDT", "ETHUSDT"], interval="1m")
    
    async for candle in feed.stream_candles():
        print(f"실시간: {candle.symbol} - {candle.close}")
    
    await feed.disconnect()

asyncio.run(main())
```

---

## 주의사항

### 1. API 제한
- KIS API: 분당 요청 횟수 제한 있음 (정확한 제한은 API 문서 참조)
- Binance API: 가중치 기반 제한 (1분당 1200 가중치)

### 2. 인증 토큰
- KIS API 토큰은 24시간 유효
- 자동으로 갱신되지만, 장시간 사용 시 재연결 필요할 수 있음

### 3. WebSocket 연결
- 네트워크 끊김 시 자동 재연결 로직 필요
- 핑/퐁 메시지로 연결 유지 확인

### 4. 모의투자 vs 실전투자
- 개발 및 테스트 시 반드시 모의투자 모드 사용
- 실전투자 전환 시 충분한 테스트 필수

---

## 문제 해결

### KIS API 연결 실패
1. AppKey, AppSecret 확인
2. 모의투자/실전투자 설정 확인
3. API 사용 승인 상태 확인

### WebSocket 연결 끊김
1. 네트워크 상태 확인
2. 방화벽 설정 확인
3. 인증 토큰 만료 여부 확인

### 데이터가 수신되지 않음
1. 종목코드 형식 확인
2. 시장 개장 시간 확인
3. 로그 메시지 확인

---

## 참고 자료

- [한국투자증권 KIS API 문서](https://apiportal.koreainvestment.com)
- [Binance API 문서](https://binance-docs.github.io/apidocs/spot/en/)
- [VibeTrading 전체 README](../README.md)
