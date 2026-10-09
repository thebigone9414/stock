"""
이동평균 골든크로스/데드크로스 전략
- 단기 MA(5일)가 장기 MA(20일)를 상향 돌파 → 매수 (골든크로스)
- 단기 MA(5일)가 장기 MA(20일)를 하향 돌파 → 매도 (데드크로스)
"""
from typing import List

import pandas as pd
from loguru import logger

from strategies.base import BaseStrategy, Signal, SignalType
from kis.market import KISMarket
from engine.portfolio import Portfolio


class MovingAverageCrossStrategy(BaseStrategy):
    """골든크로스/데드크로스 이동평균 전략"""

    name = "MA_Cross"

    def __init__(
        self,
        watchlist: List[str],
        short_period: int = 5,
        long_period: int = 20,
    ):
        self.watchlist = watchlist
        self.short = short_period
        self.long = long_period

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
        if df.empty or len(df) < self.long + 2:
            return None

        df["ma_short"] = df["close"].rolling(self.short).mean()
        df["ma_long"] = df["close"].rolling(self.long).mean()
        df = df.dropna().reset_index(drop=True)
        if len(df) < 2:
            return None

        prev = df.iloc[-2]
        curr = df.iloc[-1]

        # 골든크로스: 전봉에서 short < long, 현봉에서 short > long
        golden_cross = (prev["ma_short"] < prev["ma_long"]) and (curr["ma_short"] > curr["ma_long"])
        # 데드크로스: 전봉에서 short > long, 현봉에서 short < long
        dead_cross = (prev["ma_short"] > prev["ma_long"]) and (curr["ma_short"] < curr["ma_long"])

        if golden_cross and not portfolio.has_position(code):
            return Signal(
                code=code,
                signal_type=SignalType.BUY,
                strategy_name=self.name,
                reason=f"골든크로스 MA{self.short}={curr['ma_short']:.0f} > MA{self.long}={curr['ma_long']:.0f}",
                metadata={"ma_short": curr["ma_short"], "ma_long": curr["ma_long"]},
            )

        if dead_cross and portfolio.has_position(code):
            return Signal(
                code=code,
                signal_type=SignalType.SELL,
                strategy_name=self.name,
                reason=f"데드크로스 MA{self.short}={curr['ma_short']:.0f} < MA{self.long}={curr['ma_long']:.0f}",
                metadata={"ma_short": curr["ma_short"], "ma_long": curr["ma_long"]},
            )

        return None
