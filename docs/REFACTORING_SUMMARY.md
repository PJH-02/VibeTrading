# Data Feed Refactoring Summary

## 변경 사항 개요

한국 시장(KR)과 미국 시장(US)의 데이터 피드를 실제 API 연동으로 리팩토링했습니다.

---

## 수정된 파일

### 1. `shared/config.py`
**추가된 내용:**
- `KISSettings`: 한국투자증권 API 설정 클래스
- `KiwoomSettings`: 키움증권 API 설정 클래스
- `TradingSettings`에 `kis`, `kiwoom` 필드 추가

**주요 기능:**
```python
# KIS API 설정
kis: KISSettings = Field(default_factory=KISSettings)
kiwoom: KiwoomSettings = Field(default_factory=KiwoomSettings)
```

---

### 2. `services/data_feed/kr_feed.py`
**변경 내용:** 스텁 구현 → 실제 KIS API 연동

**주요 기능:**
- ✅ 액세스 토큰 자동 관리 (24시간 유효, 자동 갱신)
- ✅ WebSocket을 통한 실시간 시세 스트리밍
- ✅ REST API를 통한 과거 데이터 조회
- ✅ 일별/주별/월별 OHLCV 데이터 지원
- ✅ 6자리 종목코드 자동 정규화

**API 엔드포인트:**
- 토큰 발급: `/oauth2/tokenP`
- 실시간 시세: WebSocket `ws://ops.koreainvestment.com:21000`
- 과거 데이터: `/uapi/domestic-stock/v1/quotations/inquire-daily-price`

---

### 3. `services/data_feed/us_feed.py`
**변경 내용:** 스텁 구현 → 실제 KIS API 연동 (해외주식)

**주요 기능:**
- ✅ KIS API를 통한 미국 주식 데이터 조회
- ✅ WebSocket 실시간 미국 주식 시세
- ✅ NASDAQ, NYSE, AMEX 지원
- ✅ 과거 데이터 조회 (일별/주별/월별)
- ✅ 티커 심볼 자동 정규화

**API 엔드포인트:**
- 토큰 발급: `/oauth2/tokenP` (KR과 동일)
- 실시간 시세: WebSocket `ws://ops.koreainvestment.com:21000`
- 과거 데이터: `/uapi/overseas-price/v1/quotations/dailyprice`

---

### 4. `requirements.txt`
**추가된 의존성:**
```txt
aiohttp>=3.9.0  # KIS API HTTP 요청에 사용
```

---

### 5. `.env.example` (신규 생성)
**목적:** 환경 변수 템플릿 제공

**포함된 설정:**
```env
# KIS API (한국 + 미국 시장)
KIS_APP_KEY=your_kis_app_key_here
KIS_APP_SECRET=your_kis_app_secret_here
KIS_ACCOUNT_NUMBER=your_account_number
KIS_ACCOUNT_PRODUCT_CODE=01
KIS_USE_MOCK=true

# Kiwoom API (대체 옵션)
KIWOOM_ACCOUNT_NUMBER=your_kiwoom_account_number
KIWOOM_ACCOUNT_PASSWORD=your_kiwoom_password
KIWOOM_CERT_PASSWORD=your_certificate_password
KIWOOM_USE_MOCK=true

# Binance API (암호화폐)
BINANCE_API_KEY=your_binance_api_key_here
BINANCE_API_SECRET=your_binance_api_secret_here
BINANCE_TESTNET=true
```

---

### 6. `docs/DATA_FEED_API_GUIDE.md` (신규 생성)
**목적:** API 통합 가이드 문서

**포함 내용:**
- 각 시장별 API 설명
- API 키 발급 방법
- 사용 예제 코드
- 주의사항 및 문제 해결

---

## 기술 스택

### 핵심 라이브러리
- **aiohttp**: 비동기 HTTP 클라이언트 (KIS API 호출)
- **websockets**: WebSocket 클라이언트 (실시간 데이터 스트리밍)
- **python-binance**: Binance API 클라이언트

### 아키텍처 패턴
- **Async/Await**: 모든 I/O 작업 비동기 처리
- **Queue 기반 스트리밍**: WebSocket 메시지를 asyncio.Queue로 전달
- **자동 토큰 관리**: 만료 5분 전 자동 갱신
- **에러 핸들링**: DataFeedError 예외를 통한 일관된 에러 처리

---

## 데이터 플로우

### 실시간 데이터 (WebSocket)
```
WebSocket 연결
    ↓
토큰 인증
    ↓
종목 구독 (subscribe_candles)
    ↓
메시지 수신 (비동기 루프)
    ↓
파싱 → Candle 객체 생성
    ↓
Queue에 추가
    ↓
stream_candles()로 전달
```

### 과거 데이터 (REST API)
```
REST API 요청
    ↓
토큰 인증 (헤더)
    ↓
파라미터 전달 (symbol, interval, date range)
    ↓
응답 파싱
    ↓
List[Candle] 반환
```

---

## 사용 방법

### 1. 환경 설정
```bash
# .env 파일 생성
cp .env.example .env

# API 키 입력 (에디터로 .env 파일 편집)
```

### 2. 의존성 설치
```bash
pip install -r requirements.txt
```

### 3. 코드 예제

#### 한국 시장 데이터
```python
from services.data_feed.kr_feed import KRDataFeed

feed = KRDataFeed()
await feed.connect()

# 과거 데이터
candles = await feed.get_historical_candles(
    symbol="005930",  # 삼성전자
    interval="1d",
    start_time=datetime.now() - timedelta(days=30)
)

# 실시간 데이터
await feed.subscribe_candles(["005930"])
async for candle in feed.stream_candles():
    print(candle)
```

#### 미국 시장 데이터
```python
from services.data_feed.us_feed import USDataFeed

feed = USDataFeed()
await feed.connect()

# 과거 데이터
candles = await feed.get_historical_candles(
    symbol="AAPL",
    interval="1d",
    start_time=datetime.now() - timedelta(days=30)
)

# 실시간 데이터
await feed.subscribe_candles(["AAPL", "MSFT"])
async for candle in feed.stream_candles():
    print(candle)
```

---

## 주요 개선사항

### Before (스텁 구현)
- ❌ 실제 데이터 없음
- ❌ API 연동 없음
- ❌ 빈 리스트 반환

### After (실제 구현)
- ✅ 실시간 데이터 스트리밍
- ✅ 과거 데이터 조회
- ✅ 토큰 자동 관리
- ✅ WebSocket 연결 관리
- ✅ 에러 핸들링
- ✅ 종목코드 정규화

---

## 테스트 방법

### 1. 모의투자 모드로 테스트
```env
KIS_USE_MOCK=true
```

### 2. 간단한 테스트 스크립트
```python
import asyncio
from services.data_feed.kr_feed import KRDataFeed

async def test():
    feed = KRDataFeed()
    try:
        await feed.connect()
        print("✅ 연결 성공")
        
        candles = await feed.get_historical_candles(
            symbol="005930",
            interval="1d",
            start_time=datetime.now() - timedelta(days=7)
        )
        print(f"✅ {len(candles)}개 캔들 조회 성공")
    except Exception as e:
        print(f"❌ 에러: {e}")
    finally:
        await feed.disconnect()

asyncio.run(test())
```

---

## 다음 단계

### 권장 작업
1. ✅ API 키 발급 및 `.env` 설정
2. ✅ 의존성 설치
3. ⏳ 간단한 테스트 실행
4. ⏳ 전체 시스템 통합 테스트
5. ⏳ 에러 핸들링 강화
6. ⏳ 로깅 개선
7. ⏳ 재연결 로직 추가

### 추가 고려사항
- Rate Limiting 처리
- WebSocket 재연결 로직
- 데이터 캐싱
- 메트릭 수집

---

## 참고 문서

- [API 통합 가이드](docs/DATA_FEED_API_GUIDE.md)
- [한국투자증권 API 문서](https://apiportal.koreainvestment.com)
- [전체 시스템 README](README.md)
