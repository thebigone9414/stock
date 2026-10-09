"""
RSI 역추세 전략
- RSI < 30 (과매도) → 매수
- RSI > 70 (과매수) → 매도
"""
from typing import List

import pandas as pd
import numpy as np
from loguru import logger

from strategies.base import BaseStrategy, Signal, SignalType
from kis.market import KISMarket
from engine.portfolio import Portfolio


class RSIStrategy(BaseStrategy):
    """RSI 과매수/과매도 역추세 전략"""

    name = "RSI"

    def __init__(
        self,
        watchlist: List[str],
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
    ):
        self.watchlist = watchlist
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, market: KISMarket, portfolio: Portfolio) -> List[Signal]:
        signals = []
        for code in self.watchlist:
            try:
                sig = self._check_code(code, market, portfolio)
                if sig:
                    signals.append(sig)
            except Exception as e:
                logger.warning(f"[{self.name}] {code} 처리 오류: {e}")
        return signals

    def _check_code(self, code: str, market: KISMarket, portfolio: Portfolio) -> Signal | None:
        df = market.get_ohlcv(code, period="D")
        if df.empty or len(df) < self.period + 5:
            return None

        rsi = self._calc_rsi(df["close"], self.period)
        if rsi is None:
            return None

        if rsi < self.oversold and not portfolio.has_position(code):
            return Signal(
                code=code,
                signal_type=SignalType.BUY,
                strategy_name=self.name,
                reason=f"RSI 과매도 RSI={rsi:.1f} (기준<{self.oversold})",
                metadata={"rsi": rsi},
            )

        if rsi > self.overbought and portfolio.has_position(code):
            return Signal(
                code=code,
                signal_type=SignalType.SELL,
                strategy_name=self.name,
                reason=f"RSI 과매수 RSI={rsi:.1f} (기준>{self.overbought})",
                metadata={"rsi": rsi},
            )

        return None

    @staticmethod
    def _calc_rsi(series: pd.Series, period: int) -> float | None:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        valid = rsi.dropna()
        if valid.empty:
            return None
        return float(valid.iloc[-1])
