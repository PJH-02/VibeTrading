"""
Turtle Breakout Strategy
Simple trend-following strategy for system validation.

Entry: Buy when price breaks 20-day high
Exit: Sell when price breaks 10-day low

This is a test strategy to validate the trading system.
No strategy-specific code exists in the core system.
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Deque, Dict, List, Optional

from shared.models import (
    Candle,
    Signal,
    SignalAction,
    StrategyContext,
    StrategyResult,
    TeamType,
)

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 전략 설정 (Strategy Config)
# run_strategy.py 가 이 설정을 읽어 파이프라인을 구성합니다.
# 새 전략을 만들 때 이 블록을 복사하여 값만 바꾸면 됩니다.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STRATEGY_CONFIG = {
    # ── 기본 정보 ──
    "name": "turtle_breakout",
    "description": "Turtle Breakout: 20일 고가 돌파 매수, 10일 저가 이탈 매도",

    # ── 마켓 / 모드 ──
    "market": "crypto",              # crypto | kr | us
    "mode": "paper",                 # backtest | paper | live
    "symbols": ["BTCUSDT"],          # 거래 심볼 목록
    "interval": "1m",                # 캔들 인터벌

    # ── 엔진 / 팀 ──
    "team": "trading",               # trading | portfolio | arbitrage

    # ── 자본금 & 포지션 ──
    "initial_balance": "100000",     # 초기 자본금
    "position_size_pct": "10.0",     # 포지션 비율 (% of 자본금)

    # ── 거래소 / 브로커 ──
    "crypto_exchange": "binance",    # binance | bybit
    "kr_broker": "kis",              # kis | kiwoom | both (kr 마켓일 때만)

    # ── API 키 (비워두면 .env에서 읽음) ──
    "binance_api_key": "",
    "binance_api_secret": "",
    "binance_testnet": True,
    "kis_app_key": "",
    "kis_app_secret": "",
    "kis_account_number": "",
    "kis_use_mock": True,

    # ── 슬리피지 & 수수료 ──
    "slippage_bps": 10,              # 슬리피지 (basis points)
    "commission_rate": "0.001",      # 수수료율 (0.1% = 10 bps)
    "min_latency_ms": 50,            # 최소 지연 (ms)

    # ── 리스크 ──
    "max_drawdown_pct": "10.0",
    "max_position_size_pct": "5.0",
    "daily_loss_limit_pct": "3.0",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 전략 구현
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class SymbolState:
    """Per-symbol strategy state."""
    highs: Deque[Decimal] = field(default_factory=lambda: deque(maxlen=20))
    lows: Deque[Decimal] = field(default_factory=lambda: deque(maxlen=10))
    in_position: bool = False
    entry_price: Optional[Decimal] = None
    entry_time: Optional[datetime] = None


class Strategy:
    """
    Turtle Breakout Strategy
    
    Rules:
    - Long Entry: Close > 20-day high
    - Long Exit: Close < 10-day low
    
    Simple trend-following system used to validate
    the trading infrastructure.
    """
    
    name = "turtle_breakout"
    TEAM_TYPE = TeamType.TRADING
    
    def __init__(self) -> None:
        """Initialize strategy."""
        self._state: Dict[str, SymbolState] = {}
        self._lookback_entry = 20
        self._lookback_exit = 10
    
    def initialize(self) -> None:
        """Initialize strategy state."""
        self._state.clear()
        logger.info(f"Strategy '{self.name}' initialized")
    
    def reset(self) -> None:
        """Reset strategy state for backtesting."""
        self._state.clear()
        logger.debug(f"Strategy '{self.name}' reset")
    
    def _get_state(self, symbol: str) -> SymbolState:
        """Get or create symbol state."""
        if symbol not in self._state:
            self._state[symbol] = SymbolState()
        return self._state[symbol]
    
    def on_candle(
        self,
        candle: Candle,
        context: StrategyContext,
    ) -> StrategyResult:
        """
        Process new candle and generate signals.
        
        Args:
            candle: New candle data
            context: Strategy execution context
            
        Returns:
            StrategyResult with any generated signals
        """
        state = self._get_state(candle.symbol)
        signals: List[Signal] = []
        
        # Update price history
        state.highs.append(candle.high)
        state.lows.append(candle.low)
        
        # Need full lookback before trading
        if len(state.highs) < self._lookback_entry:
            return StrategyResult(signals=[])
        
        # Calculate breakout levels
        # Note: We exclude current bar to prevent look-ahead
        entry_high = max(list(state.highs)[:-1]) if len(state.highs) > 1 else candle.high
        exit_low = min(list(state.lows)[:-1]) if len(state.lows) > 1 else candle.low
        
        # Check for entry signal (not in position)
        if not state.in_position:
            if candle.close > entry_high:
                signal = Signal(
                    market=candle.market,
                    mode=context.mode,
                    symbol=candle.symbol,
                    action=SignalAction.ENTER_LONG,
                    strength=Decimal("1.0"),
                    price_at_signal=candle.close,
                    strategy_name=self.name,
                    metadata={
                        "entry_level": str(entry_high),
                        "trigger": "20_day_high_breakout",
                    },
                )
                signals.append(signal)
                
                # Update state
                state.in_position = True
                state.entry_price = candle.close
                state.entry_time = candle.timestamp
                
                logger.debug(f"ENTER_LONG {candle.symbol} @ {candle.close}")
        
        # Check for exit signal (in position)
        else:
            if candle.close < exit_low:
                signal = Signal(
                    market=candle.market,
                    mode=context.mode,
                    symbol=candle.symbol,
                    action=SignalAction.EXIT_LONG,
                    strength=Decimal("1.0"),
                    price_at_signal=candle.close,
                    strategy_name=self.name,
                    metadata={
                        "exit_level": str(exit_low),
                        "entry_price": str(state.entry_price),
                        "trigger": "10_day_low_breakdown",
                    },
                )
                signals.append(signal)
                
                # Update state
                state.in_position = False
                
                logger.debug(f"EXIT_LONG {candle.symbol} @ {candle.close}")
        
        return StrategyResult(signals=signals)


# Strategy instance for direct import
strategy = Strategy()
