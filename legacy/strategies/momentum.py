"""
모멘텀 전략
- 최근 N일 수익률 상위 종목 매수
- 일정 보유 기간 후 청산
- 거래량 급증 + 가격 상승 조합 필터
"""
from typing import List
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from strategies.base import BaseStrategy, Signal, SignalType
from kis.market import KISMarket
from engine.portfolio import Portfolio


class MomentumStrategy(BaseStrategy):
    """N일 모멘텀 전략"""

    name = "Momentum"

    def __init__(
        self,
        watchlist: List[str],
        lookback_days: int = 20,
        top_n: int = 5,
        hold_days: int = 10,
        min_volume_ratio: float = 1.5,  # 평균 거래량 대비 배율
    ):
        self.watchlist = watchlist
        self.lookback_days = lookback_days
        self.top_n = top_n
        self.hold_days = hold_days
        self.min_volume_ratio = min_volume_ratio
        self._entry_dates: dict = {}  # code → 매수일

    def generate_signals(self, market: KISMarket, portfolio: Portfolio) -> List[Signal]:
        signals = []
        today = datetime.today()

        # 1. 보유 중인 종목 청산 조건 체크 (보유기간 초과)
        for code, entry_date in list(self._entry_dates.items()):
            if portfolio.has_position(code):
                days_held = (today - entry_date).days
                if days_held >= self.hold_days:
                    signals.append(
                        Signal(
                            code=code,
                            signal_type=SignalType.SELL,
                            strategy_name=self.name,
                            reason=f"보유기간 {days_held}일 초과 (기준: {self.hold_days}일)",
                        )
                    )
                    self._entry_dates.pop(code, None)

        # 2. 모멘텀 스코어 계산 → 상위 종목 매수 시도
        scores = []
        for code in self.watchlist:
            if portfolio.has_position(code):
                continue
            try:
                score = self._calc_momentum_score(code, market)
                if score is not None:
                    scores.append((code, score))
            except Exception as e:
                logger.warning(f"[{self.name}] {code} 스코어 계산 오류: {e}")

        scores.sort(key=lambda x: x[1], reverse=True)
        for code, score in scores[: self.top_n]:
            signals.append(
                Signal(
                    code=code,
                    signal_type=SignalType.BUY,
                    strategy_name=self.name,
                    reason=f"모멘텀 스코어={score:.2f}%",
                    metadata={"momentum_score": score},
                )
            )
            self._entry_dates[code] = today

        return signals

    def _calc_momentum_score(self, code: str, market: KISMarket) -> float | None:
        df = market.get_ohlcv(code, period="D")
        if df.empty or len(df) < self.lookback_days + 5:
            return None

        recent = df.tail(self.lookback_days)
        start_price = recent.iloc[0]["close"]
        end_price = recent.iloc[-1]["close"]
        if start_price <= 0:
            return None

        # 수익률 (%)
        return_pct = (end_price - start_price) / start_price * 100

        # 거래량 필터: 최근 5일 평균 거래량 vs 이전 기간 평균
        recent_vol = recent.tail(5)["volume"].mean()
        prev_vol = recent.head(self.lookback_days - 5)["volume"].mean()
        if prev_vol > 0 and recent_vol / prev_vol < self.min_volume_ratio:
            return None  # 거래량 미충족

        return return_pct
