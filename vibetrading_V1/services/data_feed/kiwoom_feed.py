"""
Kiwoom Securities Data Feed Provider
키움증권 OpenAPI+를 통한 한국 주식 시세 데이터 수집.

키움 OpenAPI+는 실시간 시세(체결, 호가)와 과거 데이터(일봉, 분봉) 조회를 지원합니다.
Windows 전용이며 COM/OCX 기반입니다.

Requirements:
    - Windows OS
    - 키움 OpenAPI+ 모듈 설치
    - PyQt5 (pip install PyQt5)
"""

import asyncio
import logging
import sys
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from typing import AsyncIterator, Dict, List, Optional

from shared.config import get_settings
from shared.models import Candle, Market

from .base import DataFeedError, DataFeedProvider

logger = logging.getLogger(__name__)

_KIWOOM_AVAILABLE = False
try:
    from PyQt5.QAxContainer import QAxWidget
    from PyQt5.QtCore import QEventLoop, QTimer
    from PyQt5.QtWidgets import QApplication

    _KIWOOM_AVAILABLE = True
except ImportError:
    try:
        from PySide2.QtAxContainer import QAxWidget
        from PySide2.QtCore import QEventLoop, QTimer
        from PySide2.QtWidgets import QApplication

        _KIWOOM_AVAILABLE = True
    except ImportError:
        QAxWidget = None  # type: ignore
        QApplication = None  # type: ignore


class KiwoomDataFeed(DataFeedProvider):
    """
    키움증권 데이터 피드 프로바이더.
    
    키움 OpenAPI+를 통해 한국 주식 시세를 수집합니다:
    - 실시간 체결가 (opt10003: 체결정보요청)
    - 과거 분봉/일봉 데이터 (opt10080: 주식분봉차트조회, opt10081: 주식일봉차트조회)
    
    Note:
        키움 OpenAPI+는 초당 5회 TR 요청 제한이 있습니다.
        실시간 데이터는 별도 이벤트 기반으로 수신됩니다.
    """

    # 키움 FID 코드
    FID_CURRENT_PRICE = 10      # 현재가
    FID_VOLUME = 15             # 거래량
    FID_OPEN_PRICE = 16         # 시가
    FID_HIGH_PRICE = 17         # 고가
    FID_LOW_PRICE = 18          # 저가
    FID_TIME = 20               # 체결시간

    def __init__(self) -> None:
        super().__init__(Market.KR)
        self._settings = get_settings().kiwoom
        self._ocx: Optional[QAxWidget] = None
        self._candle_queue: asyncio.Queue[Candle] = asyncio.Queue()
        self._subscribed_symbols: set[str] = set()
        self._connected_event = threading.Event()
        self._tr_event = threading.Event()
        self._tr_result: List[dict] = []
        self._qt_app = None

    async def connect(self) -> None:
        """키움 OpenAPI+ 연결."""
        if not _KIWOOM_AVAILABLE:
            raise DataFeedError(
                "키움 OpenAPI+ 사용 불가: PyQt5를 설치하세요 (pip install PyQt5)",
                Market.KR,
            )
        if sys.platform != "win32":
            raise DataFeedError(
                "키움 OpenAPI+는 Windows에서만 사용할 수 있습니다",
                Market.KR,
            )

        logger.info("키움 OpenAPI+ 데이터 피드 연결 중...")

        loop = asyncio.get_event_loop()
        connected = await loop.run_in_executor(None, self._connect_sync)

        if not connected:
            raise DataFeedError("키움 OpenAPI+ 연결 실패", Market.KR)

        logger.info("키움 데이터 피드 연결 완료")

    def _connect_sync(self) -> bool:
        """동기적 연결."""
        if QApplication is None:
            return False

        if QApplication.instance() is None:
            self._qt_app = QApplication(sys.argv)

        self._ocx = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self._ocx.OnEventConnect.connect(self._on_event_connect)
        self._ocx.OnReceiveTrData.connect(self._on_receive_tr_data)
        self._ocx.OnReceiveRealData.connect(self._on_receive_real_data)

        self._connected_event.clear()
        self._ocx.dynamicCall("CommConnect()")

        if not self._connected_event.wait(timeout=90):
            return False

        return self._running_flag if hasattr(self, "_running_flag") else False

    def _on_event_connect(self, err_code: int) -> None:
        """로그인 이벤트."""
        self._running_flag = err_code == 0
        if err_code == 0:
            logger.info("키움 데이터 피드: 로그인 성공")
        else:
            logger.error(f"키움 데이터 피드: 로그인 실패 (에러: {err_code})")
        self._connected_event.set()

    def _on_receive_real_data(self, symbol: str, real_type: str, real_data: str) -> None:
        """실시간 데이터 수신 이벤트."""
        if real_type != "주식체결":
            return

        try:
            # 실시간 체결 데이터 파싱
            current_price = abs(int(self._get_comm_real_data(symbol, self.FID_CURRENT_PRICE)))
            volume = abs(int(self._get_comm_real_data(symbol, self.FID_VOLUME)))
            time_str = self._get_comm_real_data(symbol, self.FID_TIME)

            # 시/고/저가는 당일 기준
            try:
                open_price = abs(int(self._get_comm_real_data(symbol, self.FID_OPEN_PRICE)))
                high_price = abs(int(self._get_comm_real_data(symbol, self.FID_HIGH_PRICE)))
                low_price = abs(int(self._get_comm_real_data(symbol, self.FID_LOW_PRICE)))
            except (ValueError, TypeError):
                open_price = high_price = low_price = current_price

            normalized = symbol.strip().replace("A", "")

            candle = Candle(
                market=Market.KR,
                symbol=normalized,
                timestamp=datetime.now(),
                open=Decimal(str(open_price)),
                high=Decimal(str(high_price)),
                low=Decimal(str(low_price)),
                close=Decimal(str(current_price)),
                volume=Decimal(str(volume)),
                interval="tick",
                is_closed=False,
            )

            # asyncio 큐에 넣기 (스레드 안전)
            try:
                self._candle_queue.put_nowait(candle)
            except asyncio.QueueFull:
                pass  # 큐가 가득 차면 최신 데이터 우선

        except Exception as e:
            logger.error(f"실시간 데이터 파싱 오류: {e}")

    def _get_comm_real_data(self, symbol: str, fid: int) -> str:
        """실시간 데이터 FID 조회."""
        return self._ocx.dynamicCall(
            "GetCommRealData(QString, int)", symbol, fid
        ).strip()

    def _on_receive_tr_data(
        self,
        screen_no: str,
        rq_name: str,
        tr_code: str,
        record_name: str,
        prev_next: str,
        *args,
    ) -> None:
        """TR 응답 수신."""
        logger.debug(f"TR 수신: {tr_code} ({rq_name})")

        if tr_code in ("opt10080", "opt10081"):
            # 분봉/일봉 데이터 파싱
            count = self._ocx.dynamicCall(
                "GetRepeatCnt(QString, QString)", tr_code, rq_name
            )
            results = []
            for i in range(count):
                item = {}
                for field in ["체결시간", "시가", "고가", "저가", "현재가", "거래량"]:
                    val = self._ocx.dynamicCall(
                        "GetCommData(QString, QString, int, QString)",
                        tr_code, rq_name, i, field,
                    ).strip()
                    item[field] = val
                results.append(item)

            self._tr_result = results

        self._tr_event.set()

    async def disconnect(self) -> None:
        """연결 해제."""
        # 실시간 구독 해제
        if self._subscribed_symbols and self._ocx:
            for symbol in list(self._subscribed_symbols):
                try:
                    self._ocx.dynamicCall(
                        "SetRealRemove(QString, QString)", "ALL", symbol
                    )
                except Exception:
                    pass
        self._subscribed_symbols.clear()

        self._ocx = None
        self._running = False
        logger.info("키움 데이터 피드 연결 해제")

    async def subscribe_candles(
        self,
        symbols: List[str],
        interval: str = "1m",
    ) -> None:
        """실시간 시세 구독."""
        if not self._ocx:
            raise DataFeedError("키움에 연결되어 있지 않습니다", Market.KR)

        logger.info(f"키움 실시간 구독: {symbols}")

        loop = asyncio.get_event_loop()
        for symbol in symbols:
            normalized = self.normalize_symbol(symbol)
            if normalized in self._subscribed_symbols:
                continue

            # 실시간 등록
            await loop.run_in_executor(
                None,
                self._register_real_sync,
                normalized,
            )
            self._subscribed_symbols.add(normalized)

        logger.info(f"실시간 구독 완료: {len(self._subscribed_symbols)}개 종목")

    def _register_real_sync(self, symbol: str) -> None:
        """동기적 실시간 등록."""
        if not self._ocx:
            return
        # FID: 10(현재가), 15(거래량), 16(시가), 17(고가), 18(저가), 20(체결시간)
        fid_list = "10;15;16;17;18;20"
        screen_no = "1000"  # 화면번호

        self._ocx.dynamicCall(
            "SetRealReg(QString, QString, QString, QString)",
            screen_no,
            symbol,
            fid_list,
            "1",  # 0=기존 유지+추가, 1=신규등록
        )

    async def unsubscribe_candles(self, symbols: List[str]) -> None:
        """실시간 시세 구독 해제."""
        if not self._ocx:
            return

        loop = asyncio.get_event_loop()
        for symbol in symbols:
            normalized = self.normalize_symbol(symbol)
            if normalized not in self._subscribed_symbols:
                continue

            await loop.run_in_executor(
                None,
                lambda s=normalized: self._ocx.dynamicCall(
                    "SetRealRemove(QString, QString)", "ALL", s
                ),
            )
            self._subscribed_symbols.discard(normalized)

    async def stream_candles(self) -> AsyncIterator[Candle]:
        """실시간 캔들 스트리밍."""
        while self._running:
            try:
                candle = await asyncio.wait_for(
                    self._candle_queue.get(),
                    timeout=1.0,
                )
                yield candle
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"스트림 오류: {e}")
                break

    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Candle]:
        """
        과거 캔들 데이터 조회.
        
        키움 TR:
        - opt10080: 주식분봉차트조회 (1분, 3분, 5분, 10분, 15분, 30분, 60분)
        - opt10081: 주식일봉차트조회
        """
        if not self._ocx:
            raise DataFeedError("키움에 연결되어 있지 않습니다", Market.KR)

        normalized = self.normalize_symbol(symbol)
        end_time = end_time or datetime.now()

        logger.info(f"키움 과거 데이터 조회: {normalized} ({interval})")

        loop = asyncio.get_event_loop()

        if interval in ("1d", "1D", "daily"):
            results = await loop.run_in_executor(
                None,
                self._fetch_daily_sync,
                normalized,
                end_time,
            )
        else:
            # 분봉
            tick_unit = self._interval_to_tick_unit(interval)
            results = await loop.run_in_executor(
                None,
                self._fetch_minute_sync,
                normalized,
                tick_unit,
                end_time,
            )

        # dict → Candle 변환
        candles = []
        for item in results[:limit]:
            try:
                time_str = item.get("체결시간", "")
                if len(time_str) >= 12:
                    ts = datetime.strptime(time_str[:12], "%Y%m%d%H%M")
                elif len(time_str) >= 8:
                    ts = datetime.strptime(time_str[:8], "%Y%m%d")
                else:
                    continue

                if ts < start_time:
                    continue

                candle = Candle(
                    market=Market.KR,
                    symbol=normalized,
                    timestamp=ts,
                    open=Decimal(str(abs(int(item.get("시가", "0"))))),
                    high=Decimal(str(abs(int(item.get("고가", "0"))))),
                    low=Decimal(str(abs(int(item.get("저가", "0"))))),
                    close=Decimal(str(abs(int(item.get("현재가", "0"))))),
                    volume=Decimal(str(abs(int(item.get("거래량", "0"))))),
                    interval=interval,
                    is_closed=True,
                )
                candles.append(candle)
            except (ValueError, TypeError) as e:
                logger.warning(f"캔들 파싱 오류: {e}")

        # 시간순 정렬
        candles.sort(key=lambda c: c.timestamp)
        logger.info(f"키움 과거 데이터: {len(candles)}개 캔들")
        return candles

    def _fetch_daily_sync(self, symbol: str, end_time: datetime) -> List[dict]:
        """동기적 일봉 조회 (opt10081)."""
        if not self._ocx:
            return []

        self._tr_result = []
        self._tr_event.clear()

        self._ocx.dynamicCall(
            "SetInputValue(QString, QString)", "종목코드", symbol
        )
        self._ocx.dynamicCall(
            "SetInputValue(QString, QString)", "기준일자", end_time.strftime("%Y%m%d")
        )
        self._ocx.dynamicCall(
            "SetInputValue(QString, QString)", "수정주가구분", "1"
        )

        self._ocx.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "일봉조회", "opt10081", 0, "2000",
        )

        self._tr_event.wait(timeout=10)
        return self._tr_result

    def _fetch_minute_sync(self, symbol: str, tick_unit: int, end_time: datetime) -> List[dict]:
        """동기적 분봉 조회 (opt10080)."""
        if not self._ocx:
            return []

        self._tr_result = []
        self._tr_event.clear()

        self._ocx.dynamicCall(
            "SetInputValue(QString, QString)", "종목코드", symbol
        )
        self._ocx.dynamicCall(
            "SetInputValue(QString, QString)", "틱범위", str(tick_unit)
        )
        self._ocx.dynamicCall(
            "SetInputValue(QString, QString)", "수정주가구분", "1"
        )

        self._ocx.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "분봉조회", "opt10080", 0, "2001",
        )

        self._tr_event.wait(timeout=10)
        return self._tr_result

    @staticmethod
    def _interval_to_tick_unit(interval: str) -> int:
        """인터벌 문자열을 키움 틱 단위로 변환."""
        mapping = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "10m": 10,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "60m": 60,
        }
        return mapping.get(interval, 1)

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """종목코드 정규화 (6자리 숫자)."""
        symbol = symbol.upper().strip()
        for suffix in [".KS", ".KQ", ".KRX"]:
            if symbol.endswith(suffix):
                symbol = symbol[: -len(suffix)]
        symbol = symbol.replace("A", "")
        if symbol.isdigit():
            symbol = symbol.zfill(6)
        return symbol
