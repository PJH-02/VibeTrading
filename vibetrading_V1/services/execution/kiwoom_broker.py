"""
Kiwoom Securities Broker Adapter
Order execution via Kiwoom OpenAPI+ (Windows COM/OCX).

키움증권 OpenAPI+를 통한 주문 실행 어댑터.
키움 OpenAPI+는 Windows 전용이며 COM/OCX 기반입니다.

Requirements:
    - Windows OS
    - 키움 OpenAPI+ 모듈 설치
    - PyQt5 또는 PySide2 (QAxWidget 사용)

Note:
    키움 OpenAPI+는 동기적 COM 이벤트 모델을 사용하므로
    asyncio와 통합하기 위해 별도 스레드에서 QApplication을 실행합니다.
"""

import asyncio
import logging
import sys
import threading
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from shared.config import get_settings
from shared.models import (
    Fill,
    Market,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TradingMode,
)

from .base import BrokerAdapter, OrderError

logger = logging.getLogger(__name__)

# Kiwoom OpenAPI+ 사용을 위한 조건부 임포트
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
        logger.warning(
            "PyQt5/PySide2가 설치되지 않았습니다. "
            "키움 OpenAPI+를 사용하려면 pip install PyQt5 를 실행하세요."
        )


class KiwoomOpenAPI:
    """
    키움 OpenAPI+ COM 래퍼.
    QAxWidget을 통해 키움 OCX 컨트롤과 통신합니다.
    
    이 클래스는 Qt 이벤트 루프가 동작하는 스레드에서 실행되어야 합니다.
    """

    CYCLED_EVENTS = {
        "OnReceiveTrData",
        "OnReceiveRealData",
        "OnReceiveMsg",
        "OnReceiveChejanData",
        "OnEventConnect",
        "OnReceiveRealCondition",
    }

    def __init__(self) -> None:
        if not _KIWOOM_AVAILABLE:
            raise OrderError(
                "키움 OpenAPI+ 사용 불가: PyQt5/PySide2를 설치하세요. "
                "(pip install PyQt5)"
            )

        self._ocx: Optional[QAxWidget] = None
        self._connected = False
        self._login_event = threading.Event()
        self._tr_event = threading.Event()
        self._order_event = threading.Event()

        # 체결 데이터 콜백
        self._chejan_callback = None

        # TR 응답 저장
        self._tr_data: Dict[str, list] = {}

    def create_control(self) -> None:
        """OCX 컨트롤 생성 및 이벤트 연결."""
        self._ocx = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self._ocx.OnEventConnect.connect(self._on_event_connect)
        self._ocx.OnReceiveTrData.connect(self._on_receive_tr_data)
        self._ocx.OnReceiveChejanData.connect(self._on_receive_chejan_data)
        self._ocx.OnReceiveMsg.connect(self._on_receive_msg)
        logger.info("키움 OpenAPI+ OCX 컨트롤 생성 완료")

    def login(self, timeout: int = 60) -> bool:
        """
        키움 로그인 (CommConnect).
        로그인 창이 뜨며 사용자가 직접 로그인해야 합니다.
        """
        if self._ocx is None:
            raise OrderError("OCX 컨트롤이 생성되지 않았습니다.")

        self._login_event.clear()
        self._ocx.dynamicCall("CommConnect()")

        # 로그인 완료 대기
        if not self._login_event.wait(timeout=timeout):
            raise OrderError("키움 로그인 시간 초과")

        return self._connected

    def _on_event_connect(self, err_code: int) -> None:
        """로그인 완료 이벤트."""
        if err_code == 0:
            self._connected = True
            logger.info("키움 OpenAPI+ 로그인 성공")
        else:
            self._connected = False
            logger.error(f"키움 로그인 실패 (에러코드: {err_code})")
        self._login_event.set()

    def _on_receive_msg(self, screen_no: str, rq_name: str, tr_code: str, msg: str) -> None:
        """서버 메시지 수신."""
        logger.info(f"키움 메시지: [{tr_code}] {msg}")

    def _on_receive_tr_data(
        self,
        screen_no: str,
        rq_name: str,
        tr_code: str,
        record_name: str,
        prev_next: str,
        *args,
    ) -> None:
        """TR 데이터 수신 이벤트."""
        logger.debug(f"TR 수신: {tr_code} ({rq_name})")
        self._tr_event.set()

    def _on_receive_chejan_data(self, gubun: str, item_cnt: int, fid_list: str) -> None:
        """
        체결/잔고 데이터 수신 이벤트.
        
        gubun: "0" = 주문체결, "1" = 잔고변경, "4" = 파생잔고
        """
        if gubun == "0":
            # 주문 체결 데이터 파싱
            order_no = self._get_chejan_data(9203)    # 주문번호
            symbol = self._get_chejan_data(9001)       # 종목코드
            order_status = self._get_chejan_data(913)  # 주문상태
            filled_qty = self._get_chejan_data(911)    # 체결량
            filled_price = self._get_chejan_data(910)  # 체결가
            side_code = self._get_chejan_data(905)     # 매도수구분 (1:매도, 2:매수)

            logger.info(
                f"체결: {symbol} {order_no} 상태={order_status} "
                f"체결량={filled_qty} 체결가={filled_price}"
            )

            if self._chejan_callback and filled_qty and filled_price:
                try:
                    fill_data = {
                        "order_no": order_no.strip() if order_no else "",
                        "symbol": symbol.strip().replace("A", "") if symbol else "",
                        "status": order_status.strip() if order_status else "",
                        "filled_qty": filled_qty.strip() if filled_qty else "0",
                        "filled_price": filled_price.strip() if filled_price else "0",
                        "side": "sell" if side_code and side_code.strip() == "1" else "buy",
                    }
                    self._chejan_callback(fill_data)
                except Exception as e:
                    logger.error(f"체결 콜백 처리 오류: {e}")

    def _get_chejan_data(self, fid: int) -> str:
        """체결 데이터 FID 값 조회."""
        return self._ocx.dynamicCall("GetChejanData(int)", fid)

    def get_comm_data(self, tr_code: str, record_name: str, index: int, item_name: str) -> str:
        """TR 응답에서 데이터 조회."""
        return self._ocx.dynamicCall(
            "GetCommData(QString, QString, int, QString)",
            tr_code,
            record_name,
            index,
            item_name,
        ).strip()

    def get_repeat_cnt(self, tr_code: str, record_name: str) -> int:
        """TR 반복 횟수 조회."""
        return self._ocx.dynamicCall(
            "GetRepeatCnt(QString, QString)", tr_code, record_name
        )

    def send_order(
        self,
        rq_name: str,
        screen_no: str,
        account: str,
        order_type: int,
        symbol: str,
        qty: int,
        price: int,
        hoga_type: str,
        org_order_no: str = "",
    ) -> int:
        """
        주문 발주.
        
        order_type: 1=신규매수, 2=신규매도, 3=매수취소, 4=매도취소, 5=매수정정, 6=매도정정
        hoga_type: "00"=지정가, "03"=시장가, "05"=조건부지정가, "06"=최유리지정가
        """
        ret = self._ocx.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            [rq_name, screen_no, account, order_type, symbol, qty, price, hoga_type, org_order_no],
        )
        if ret != 0:
            logger.error(f"주문 전송 실패: 에러코드 {ret}")
        return ret

    def get_login_info(self, tag: str) -> str:
        """로그인 정보 조회 (ACCNO, USER_ID, USER_NAME 등)."""
        return self._ocx.dynamicCall("GetLoginInfo(QString)", tag)

    def get_account_list(self) -> list[str]:
        """계좌 목록 조회."""
        accounts = self.get_login_info("ACCNO")
        return [a for a in accounts.split(";") if a.strip()]

    @property
    def is_connected(self) -> bool:
        return self._connected


class KiwoomBrokerAdapter(BrokerAdapter):
    """
    키움증권 주문 실행 어댑터.
    
    BrokerAdapter 인터페이스를 구현하여
    한국 주식 시장에서의 주문 실행을 처리합니다.
    
    특이사항:
    - Windows 전용 (COM/OCX)
    - Qt 이벤트 루프 필요 (별도 스레드에서 실행)
    - 동시 접속 제한 (1 OCX per process)
    """

    def __init__(self) -> None:
        super().__init__(Market.KR)
        self._settings = get_settings().kiwoom
        self._api: Optional[KiwoomOpenAPI] = None
        self._qt_app: Optional[QApplication] = None
        self._qt_thread: Optional[threading.Thread] = None
        self._account: str = ""
        self._order_map: Dict[str, Order] = {}  # order_no -> Order

    async def connect(self) -> None:
        """키움 OpenAPI+ 연결 (별도 Qt 스레드에서 로그인)."""
        if not _KIWOOM_AVAILABLE:
            raise OrderError(
                "키움 OpenAPI+ 사용 불가: PyQt5를 설치하세요. "
                "(pip install PyQt5)"
            )

        if sys.platform != "win32":
            raise OrderError("키움 OpenAPI+는 Windows에서만 사용할 수 있습니다.")

        logger.info("키움 OpenAPI+ 연결 중...")

        # Qt 스레드에서 OCX 생성 및 로그인
        loop = asyncio.get_event_loop()
        connected = await loop.run_in_executor(None, self._connect_sync)

        if not connected:
            raise OrderError("키움 OpenAPI+ 연결 실패")

        # 계좌번호 설정
        self._account = self._settings.account_number
        if not self._account and self._api:
            accounts = self._api.get_account_list()
            if accounts:
                self._account = accounts[0]
                logger.info(f"자동 선택된 계좌: {self._account}")

        self._connected = True
        logger.info(f"키움 OpenAPI+ 연결 완료 (계좌: {self._account})")

    def _connect_sync(self) -> bool:
        """동기적 연결 (Qt 스레드용)."""
        if QApplication is None:
            return False

        # QApplication이 없으면 생성
        if QApplication.instance() is None:
            self._qt_app = QApplication(sys.argv)

        self._api = KiwoomOpenAPI()
        self._api.create_control()
        self._api._chejan_callback = self._handle_chejan_sync

        # 로그인 (블로킹)
        return self._api.login(timeout=90)

    def _handle_chejan_sync(self, fill_data: dict) -> None:
        """체결 데이터 처리 (동기 콜백)."""
        try:
            order_no = fill_data.get("order_no", "")
            symbol = fill_data.get("symbol", "")
            filled_qty = fill_data.get("filled_qty", "0")
            filled_price = fill_data.get("filled_price", "0")
            side_str = fill_data.get("side", "buy")

            if not filled_qty or filled_qty == "0":
                return

            side = OrderSide.BUY if side_str == "buy" else OrderSide.SELL
            mode = TradingMode.PAPER if self._settings.use_mock else TradingMode.LIVE

            fill = Fill(
                market=Market.KR,
                order_id=self._order_map.get(order_no, Order(
                    market=Market.KR, mode=mode, symbol=symbol,
                    side=side, order_type=OrderType.MARKET,
                    quantity=Decimal(filled_qty), strategy_name="unknown",
                )).id,
                external_id=order_no,
                mode=mode,
                symbol=symbol,
                side=side,
                quantity=Decimal(filled_qty),
                price=Decimal(filled_price),
                commission=Decimal("0"),  # 키움에서 별도 조회 필요
                commission_asset="KRW",
                slippage_bps=Decimal("0"),
                latency_ms=0,
            )

            if self._on_fill_callback:
                self._on_fill_callback(fill)

            # 주문 상태 업데이트
            if order_no in self._order_map:
                order = self._order_map[order_no]
                order.filled_quantity += Decimal(filled_qty)
                if order.filled_quantity >= order.quantity:
                    order.status = OrderStatus.FILLED
                    order.filled_at = datetime.utcnow()
                else:
                    order.status = OrderStatus.PARTIAL
                order.updated_at = datetime.utcnow()

                if self._on_order_update_callback:
                    self._on_order_update_callback(order)

        except Exception as e:
            logger.error(f"체결 처리 오류: {e}")

    async def disconnect(self) -> None:
        """키움 OpenAPI+ 연결 해제."""
        self._connected = False
        self._api = None
        self._order_map.clear()
        logger.info("키움 OpenAPI+ 연결 해제")

    async def submit_order(self, order: Order) -> Order:
        """키움을 통해 주문 제출."""
        if not self._api or not self._api.is_connected:
            raise OrderError("키움에 연결되어 있지 않습니다.", order)

        try:
            # 주문 유형 매핑
            if order.side == OrderSide.BUY:
                kiwoom_order_type = 1  # 신규매수
            else:
                kiwoom_order_type = 2  # 신규매도

            # 호가 유형 매핑
            hoga_map = {
                OrderType.MARKET: "03",  # 시장가
                OrderType.LIMIT: "00",   # 지정가
            }
            hoga_type = hoga_map.get(order.order_type, "03")

            price = int(order.price) if order.price and order.order_type == OrderType.LIMIT else 0
            qty = int(order.quantity)

            # 주문 발주 (별도 스레드에서)
            loop = asyncio.get_event_loop()
            ret = await loop.run_in_executor(
                None,
                self._api.send_order,
                f"order_{order.id}",
                "0101",  # 화면번호
                self._account,
                kiwoom_order_type,
                order.symbol,
                qty,
                price,
                hoga_type,
                "",
            )

            if ret != 0:
                order.status = OrderStatus.REJECTED
                order.error_message = f"키움 주문 오류: 코드 {ret}"
                raise OrderError(f"키움 주문 실패 (에러코드: {ret})", order)

            order.status = OrderStatus.SUBMITTED
            order.submitted_at = datetime.utcnow()
            order.updated_at = datetime.utcnow()

            logger.info(f"키움 주문 전송: {order.side.value} {order.quantity} {order.symbol}")

            return order

        except OrderError:
            raise
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.error_message = str(e)
            raise OrderError(f"키움 주문 실패: {e}", order)

    async def cancel_order(self, order: Order) -> Order:
        """키움에서 주문 취소."""
        if not self._api or not self._api.is_connected:
            raise OrderError("키움에 연결되어 있지 않습니다.", order)

        try:
            # 취소 주문 유형: 3=매수취소, 4=매도취소
            cancel_type = 3 if order.side == OrderSide.BUY else 4

            loop = asyncio.get_event_loop()
            ret = await loop.run_in_executor(
                None,
                self._api.send_order,
                f"cancel_{order.id}",
                "0102",
                self._account,
                cancel_type,
                order.symbol,
                0,  # qty (취소시 0)
                0,  # price
                "00",
                order.external_id or "",
            )

            if ret != 0:
                raise OrderError(f"키움 취소 실패 (에러코드: {ret})", order)

            order.status = OrderStatus.CANCELLED
            order.cancelled_at = datetime.utcnow()
            order.updated_at = datetime.utcnow()

            if self._on_order_update_callback:
                self._on_order_update_callback(order)

            return order

        except OrderError:
            raise
        except Exception as e:
            raise OrderError(f"키움 취소 실패: {e}", order)

    async def get_order_status(self, order: Order) -> Order:
        """주문 상태 조회."""
        # 체결 이벤트에서 실시간 업데이트되므로
        # _order_map에서 최신 상태를 반환
        if order.external_id and order.external_id in self._order_map:
            cached = self._order_map[order.external_id]
            order.status = cached.status
            order.filled_quantity = cached.filled_quantity
            order.updated_at = cached.updated_at
        return order

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """미체결 주문 목록 조회."""
        open_orders = [
            o for o in self._order_map.values()
            if not o.is_terminal and (symbol is None or o.symbol == symbol)
        ]
        return open_orders

    async def get_account_balance(self) -> Decimal:
        """예수금 조회."""
        if not self._api or not self._api.is_connected:
            return Decimal("0")

        try:
            loop = asyncio.get_event_loop()
            balance = await loop.run_in_executor(None, self._get_balance_sync)
            return balance
        except Exception as e:
            logger.error(f"예수금 조회 실패: {e}")
            return Decimal("0")

    def _get_balance_sync(self) -> Decimal:
        """동기적 예수금 조회 (opw00001 TR)."""
        if not self._api:
            return Decimal("0")

        # 간단히 로그인 정보로부터 조회
        # 실제로는 opw00001 TR을 호출해야 하지만,
        # TR 요청은 더 복잡한 구현이 필요합니다.
        # 여기서는 기본값을 반환합니다.
        logger.warning("키움 예수금 조회: TR 기반 조회 미구현 - 기본값 반환")
        return Decimal("0")
