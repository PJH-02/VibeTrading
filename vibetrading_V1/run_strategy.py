"""
VibeTrading Strategy Runner
strategies/ 폴더의 전략을 선택하여 전체 파이프라인을 실행합니다.
Usage:
    python run_strategy.py                     # 대화형 메뉴
    python run_strategy.py --strategy turtle_breakout   # 바로 실행
    python run_strategy.py --list              # 전략 목록만 출력
"""

import argparse
import asyncio
import importlib
import inspect
import logging
import os
import signal
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional

# ── 프로젝트 루트 ──
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# standalone 모드 (NATS/DB 없이 동작)
os.environ["STANDALONE_MODE"] = "true"

from shared.config import apply_strategy_config, get_settings
from shared.models import Market, TeamType, TradingMode
from services.broker_factory import create_broker, create_data_feed
from services.signal_gen.engine import SignalGenerationEngine
from services.execution.order_manager import OrderManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_strategy")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 전략 스캔
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def scan_strategies() -> dict[str, dict]:
    """
    strategies/ 폴더를 스캔하여 STRATEGY_CONFIG가 있는 모듈을 찾습니다.

    Returns:
        {전략이름: STRATEGY_CONFIG dict} 매핑
    """
    strategies_dir = PROJECT_ROOT / "strategies"
    found: dict[str, dict] = {}

    for py_file in sorted(strategies_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = f"strategies.{py_file.stem}"
        try:
            mod: ModuleType = importlib.import_module(module_name)
            config = getattr(mod, "STRATEGY_CONFIG", None)
            if config and isinstance(config, dict) and "name" in config:
                found[config["name"]] = config
                logger.debug(f"전략 발견: {config['name']} ({py_file.name})")
        except Exception as e:
            logger.warning(f"전략 로드 실패: {py_file.name} → {e}")

    return found


def display_menu(strategies: dict[str, dict]) -> Optional[str]:
    """전략 선택 메뉴를 표시하고 선택된 전략 이름을 반환합니다."""
    print("\n" + "=" * 60)
    print("  VibeTrading — 전략 선택")
    print("=" * 60)

    items = list(strategies.items())
    for idx, (name, cfg) in enumerate(items, 1):
        desc = cfg.get("description", "")
        market = cfg.get("market", "?")
        mode = cfg.get("mode", "?")
        symbols = ", ".join(cfg.get("symbols", []))
        print(f"\n  [{idx}] {name}")
        if desc:
            print(f"      {desc}")
        print(f"      마켓={market}  모드={mode}  심볼={symbols}")

    print(f"\n  [0] 종료")
    print("-" * 60)

    while True:
        try:
            choice = input("전략 번호를 입력하세요: ").strip()
            num = int(choice)
            if num == 0:
                return None
            if 1 <= num <= len(items):
                return items[num - 1][0]
            print(f"  1~{len(items)} 또는 0을 입력하세요.")
        except (ValueError, EOFError):
            print("  숫자를 입력하세요.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 파이프라인 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class StrategyRunner:
    """
    전략의 STRATEGY_CONFIG를 받아 전체 파이프라인을 실행합니다.
      DataFeed(WebSocket) → SignalEngine(전략) → OrderManager(주문) → Broker(체결)
    """

    def __init__(self, strategy_config: dict) -> None:
        self._config = strategy_config
        self._running = False

        # 1) settings에 전략 설정 적용
        settings = apply_strategy_config(strategy_config)

        name = strategy_config["name"]
        market = Market(strategy_config.get("market", settings.market.value))
        mode = TradingMode(strategy_config.get("mode", settings.mode.value))
        team = TeamType(strategy_config.get("team", "trading"))
        symbols = strategy_config.get("symbols", ["BTCUSDT"])
        interval = strategy_config.get("interval", "1m")

        self._name = name
        self._market = market
        self._mode = mode
        self._symbols = symbols
        self._interval = interval

        # 2) 컴포넌트 생성
        self._data_feed = create_data_feed(market)
        self._signal_engine = SignalGenerationEngine(
            market=market,
            mode=mode,
            strategy_name=name,
            team=team,
        )
        self._broker = create_broker(market)
        self._order_manager = OrderManager(
            market=market,
            mode=mode,
            broker=self._broker,
        )

        # 3) 통계
        self._candle_count = 0
        self._signal_count = 0
        self._order_count = 0

    async def start(self) -> None:
        """전체 파이프라인 시작."""
        logger.info("=" * 60)
        logger.info("  VibeTrading Strategy Runner")
        logger.info(f"  전략: {self._name}")
        logger.info(f"  설명: {self._config.get('description', '-')}")
        logger.info(f"  심볼: {', '.join(self._symbols)}")
        logger.info(f"  마켓: {self._market.value}  모드: {self._mode.value}")
        logger.info(f"  인터벌: {self._interval}")
        logger.info("=" * 60)

        # 순서대로 시작
        await self._order_manager.start()
        logger.info("[OK] OrderManager 시작")

        await self._signal_engine.start()
        logger.info("[OK] SignalEngine 시작")

        await self._data_feed.connect()
        await self._data_feed.subscribe_candles(self._symbols, self._interval)
        logger.info("[OK] DataFeed 연결 + 구독 완료")

        self._running = True
        logger.info("파이프라인 가동! (Ctrl+C로 종료)")

        # 메인 루프: 캔들 → 시그널 → 주문
        try:
            async for candle in self._data_feed.stream_candles():
                if not self._running:
                    break

                self._candle_count += 1

                signals = self._signal_engine.process_candle_sync(candle)
                for sig in signals:
                    self._signal_count += 1
                    logger.info(
                        f"Signal #{self._signal_count}: "
                        f"{sig.action.value} {sig.symbol} @ {sig.price_at_signal}"
                    )
                    await self._order_manager.submit_signal_direct(sig)
                    self._order_count += 1

                if self._candle_count % 100 == 0:
                    logger.info(
                        f"통계: 캔들={self._candle_count}, "
                        f"시그널={self._signal_count}, 주문={self._order_count}"
                    )
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """파이프라인 종료."""
        self._running = False
        logger.info("파이프라인 종료 중...")

        await self._data_feed.disconnect()
        await self._order_manager.stop()
        await self._signal_engine.stop()

        logger.info(
            f"최종 통계: 캔들={self._candle_count}, "
            f"시그널={self._signal_count}, 주문={self._order_count}"
        )
        logger.info("파이프라인 종료 완료.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def run_async(strategy_config: dict) -> int:
    """비동기 메인."""
    runner = StrategyRunner(strategy_config)

    # Ctrl+C / SIGTERM 핸들링
    loop = asyncio.get_event_loop()
    start_task = asyncio.create_task(runner.start())

    try:
        await start_task
    except KeyboardInterrupt:
        pass
    finally:
        await runner.stop()
        start_task.cancel()
        try:
            await start_task
        except asyncio.CancelledError:
            pass

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="VibeTrading Strategy Runner — 전략 선택 후 전체 파이프라인 실행",
    )
    parser.add_argument(
        "--strategy", "-s",
        help="전략 이름 (생략 시 대화형 메뉴)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="전략 목록만 출력 후 종료",
    )
    args = parser.parse_args()

    # 전략 스캔
    strategies = scan_strategies()
    if not strategies:
        print("strategies/ 폴더에 STRATEGY_CONFIG가 있는 전략이 없습니다.")
        return 1

    # --list: 목록만 출력
    if args.list:
        print(f"\n사용 가능한 전략 ({len(strategies)}개):")
        for name, cfg in strategies.items():
            desc = cfg.get("description", "")
            print(f"  - {name}: {desc}")
        return 0

    # --strategy 또는 대화형 선택
    if args.strategy:
        if args.strategy not in strategies:
            print(f"전략 '{args.strategy}'를 찾을 수 없습니다.")
            print(f"사용 가능: {', '.join(strategies.keys())}")
            return 1
        selected = args.strategy
    else:
        selected = display_menu(strategies)
        if selected is None:
            print("종료합니다.")
            return 0

    strategy_config = strategies[selected]
    print(f"\n>>> 전략 '{selected}' 실행 <<<\n")

    return asyncio.run(run_async(strategy_config))


if __name__ == "__main__":
    sys.exit(main())
