"""
Broker Factory
설정(KR_BROKER)에 따라 적절한 브로커 어댑터와 데이터 피드를 생성합니다.

Usage:
    from services.broker_factory import create_broker, create_data_feed
    
    broker = create_broker()       # 현재 설정에 맞는 브로커
    feed   = create_data_feed()    # 현재 설정에 맞는 데이터 피드
"""

import logging
from typing import Optional

from shared.config import get_settings
from shared.models import Market
from services.data_feed.base import DataFeedProvider
from services.execution.base import BrokerAdapter

logger = logging.getLogger(__name__)


def create_broker(market: Optional[Market] = None) -> BrokerAdapter:
    """
    설정에 기반하여 브로커 어댑터를 생성합니다.
    
    Args:
        market: 마켓 지정 (None이면 설정에서 읽음)
    
    Returns:
        적절한 BrokerAdapter 인스턴스
    """
    settings = get_settings()
    market = market or settings.market

    if market == Market.CRYPTO:
        from services.execution.crypto_binance import CryptoBinanceAdapter
        testnet = settings.binance.testnet
        logger.info(f"Binance 브로커 생성 ({'테스트넷' if testnet else '메인넷'})")
        return CryptoBinanceAdapter(testnet=testnet)

    elif market == Market.KR:
        kr_broker = settings.kr_broker

        if kr_broker == "kiwoom":
            from services.execution.kiwoom_broker import KiwoomBrokerAdapter
            logger.info("키움증권 브로커 생성")
            return KiwoomBrokerAdapter()
        else:
            # kis (기본값) 또는 both (KIS 우선)
            from services.execution.broker_stub import BrokerStub
            logger.info("한국투자증권 브로커 생성 (BrokerStub)")
            # 참고: KIS 실 주문 어댑터는 별도 구현 필요
            # 현재는 BrokerStub(Paper)을 반환
            return BrokerStub(market=Market.KR)

    elif market == Market.US:
        from services.execution.broker_stub import BrokerStub
        logger.info("미국 주식 브로커 생성 (BrokerStub)")
        return BrokerStub(market=Market.US)

    else:
        raise ValueError(f"지원하지 않는 마켓: {market}")


def create_data_feed(market: Optional[Market] = None) -> DataFeedProvider:
    """
    설정에 기반하여 데이터 피드를 생성합니다.
    
    Args:
        market: 마켓 지정 (None이면 설정에서 읽음)
    
    Returns:
        적절한 DataFeedProvider 인스턴스
    """
    settings = get_settings()
    market = market or settings.market

    if market == Market.CRYPTO:
        from services.data_feed.crypto_feed import CryptoDataFeed
        exchange = settings.crypto_exchange
        logger.info(f"암호화폐 데이터 피드 생성 ({exchange})")
        return CryptoDataFeed(exchange=exchange)

    elif market == Market.KR:
        kr_broker = settings.kr_broker

        if kr_broker == "kiwoom":
            from services.data_feed.kiwoom_feed import KiwoomDataFeed
            logger.info("키움증권 데이터 피드 생성")
            return KiwoomDataFeed()
        else:
            # kis 또는 both (KIS 데이터 피드 사용)
            from services.data_feed.kr_feed import KRDataFeed
            logger.info("한국투자증권(KIS) 데이터 피드 생성")
            return KRDataFeed()

    elif market == Market.US:
        from services.data_feed.us_feed import USDataFeed
        logger.info("미국 주식 데이터 피드 생성 (KIS)")
        return USDataFeed()

    else:
        raise ValueError(f"지원하지 않는 마켓: {market}")


def create_all_kr_brokers() -> dict[str, BrokerAdapter]:
    """
    KR_BROKER=both 설정일 때 사용.
    KIS와 키움 브로커를 모두 생성하여 반환합니다.
    
    Returns:
        {"kis": BrokerAdapter, "kiwoom": BrokerAdapter}
    """
    brokers = {}

    try:
        from services.execution.broker_stub import BrokerStub
        brokers["kis"] = BrokerStub(market=Market.KR)
        logger.info("KIS 브로커 생성 완료")
    except Exception as e:
        logger.error(f"KIS 브로커 생성 실패: {e}")

    try:
        from services.execution.kiwoom_broker import KiwoomBrokerAdapter
        brokers["kiwoom"] = KiwoomBrokerAdapter()
        logger.info("키움 브로커 생성 완료")
    except Exception as e:
        logger.error(f"키움 브로커 생성 실패: {e}")

    return brokers
