"""
거래량 돌파 전략
- 오늘 거래량이 20일 평균 거래량의 N배 이상 + 가격 상승 → 매수
- 강한 수급 시그널 포착
"""
from typing import List

from loguru import logger

from strategies.base import BaseStrategy, Signal, SignalType
from kis.market import KISMarket
from engine.portfolio import Portfolio


class VolumeBreakoutStrategy(BaseStrategy):
    """거래량 급증 + 가격 상승 돌파 전략"""

    name = "VolumeBreakout"

    def __init__(
        self,
        watchlist: List[str],
        volume_multiplier: float = 3.0,   # 평균 거래량 대비
        price_change_min: float = 2.0,     # 최소 상승률 (%)
        ma_period: int = 20,
    ):
        self.watchlist = watchlist
        self.volume_multiplier = volume_multiplier
        self.price_change_min = price_change_min
        self.ma_period = ma_period

    def generate_signals(self, market: KISMarket, portfolio: Portfolio) -> List[Signal]:
        signals = []

        # 거래량 상위 종목도 함께 체크 (watchlist 외 기회 포착)
        try:
            vol_rank = market.get_volume_rank(top_n=50)
            extra_codes = [r.get("mksc_shrn_iscd", "") for r in vol_rank if r.get("mksc_shrn_iscd")]
        except Exception:
            extra_codes = []

        targets = list(set(self.watchlist + extra_codes))

        for code in targets:
            if portfolio.has_position(code):
                continue
            try:
                sig = self._check_code(code, market)
                if sig:
                    signals.append(sig)
            except Exception as e:
                logger.warning(f"[{self.name}] {code} 처리 오류: {e}")

        return signals

    def _check_code(self, code: str, market: KISMarket) -> Signal | None:
        df = market.get_ohlcv(code, period="D")
        if df.empty or len(df) < self.ma_period + 2:
            return None

        avg_vol = df["volume"].rolling(self.ma_period).mean().iloc[-2]
        today = df.iloc[-1]

        vol_ratio = today["volume"] / avg_vol if avg_vol > 0 else 0
        prev_close = df.iloc[-2]["close"]
        price_change = (today["close"] - prev_close) / prev_close * 100 if prev_close > 0 else 0

        if vol_ratio >= self.volume_multiplier and price_change >= self.price_change_min:
            return Signal(
                code=code,
                signal_type=SignalType.BUY,
                strategy_name=self.name,
                reason=f"거래량 {vol_ratio:.1f}배 급증 + 가격 +{price_change:.1f}% 상승",
                metadata={"vol_ratio": vol_ratio, "price_change": price_change},
            )
        return None
