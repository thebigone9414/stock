"""
메인 트레이더 - 전략 실행 및 주문 처리 통합
"""
from datetime import datetime
from typing import List, Type

from loguru import logger

from kis.factory import KIS
from kis.order import OrderType
from engine.portfolio import Portfolio
from engine.risk import RiskManager, RiskConfig
from strategies.base import BaseStrategy, Signal, SignalType
from utils.notifier import Notifier


class Trader:
    def __init__(
        self,
        kis: KIS,
        strategies: List[BaseStrategy],
        risk_config: RiskConfig = None,
        notifier: Notifier = None,
    ):
        self.kis = kis
        self.strategies = strategies
        self.portfolio = Portfolio(kis.account)
        self.risk = RiskManager(risk_config or RiskConfig(), self.portfolio)
        self.notifier = notifier or Notifier()
        self._running = False

    def initialize(self) -> None:
        """트레이더 초기화 - 잔고 조회 및 기준값 설정"""
        logger.info(f"=== 트레이더 초기화 [{self.kis.mode_label}] ===")
        balance = self.portfolio.refresh()
        self.risk.set_daily_start_value(balance.total_eval or balance.cash)
        logger.info(f"\n{self.portfolio.summary()}")
        self.notifier.notify(
            f"[{self.kis.mode_label}] 트레이더 시작\n{self.portfolio.summary()}"
        )

    def run_cycle(self) -> None:
        """단일 매매 사이클 실행 (스케줄러에서 주기적 호출)"""
        if not self._is_market_open():
            logger.debug("장 마감 시간 - 사이클 스킵")
            return

        self.portfolio.refresh()

        for strategy in self.strategies:
            try:
                signals = strategy.generate_signals(self.kis.market, self.portfolio)
                for signal in signals:
                    self._execute_signal(signal)
            except Exception as e:
                logger.error(f"전략 [{strategy.name}] 실행 오류: {e}")

        # 리스크 기반 자동 손절/익절
        self._check_risk_exits()

    def _execute_signal(self, signal: Signal) -> None:
        price = signal.price or self._get_current_price(signal.code)
        if price <= 0:
            return

        if signal.signal_type == SignalType.BUY:
            qty = signal.quantity or self.risk.calc_buy_quantity(signal.code, price)
            if qty <= 0:
                return
            ok, reason = self.risk.can_buy(signal.code, price * qty)
            if not ok:
                logger.warning(f"매수 거부 [{signal.code}]: {reason}")
                return
            result = self.kis.order.buy(signal.code, qty, price, OrderType.MARKET)
            if result.success:
                msg = f"[매수] {signal.code} {qty}주 @시장가 | 전략:{signal.strategy_name} | 사유:{signal.reason}"
                logger.info(msg)
                self.notifier.notify(msg)

        elif signal.signal_type == SignalType.SELL:
            pos = self.portfolio.get_position(signal.code)
            if not pos:
                return
            qty = signal.quantity or pos.quantity
            result = self.kis.order.sell(signal.code, qty, price, OrderType.MARKET)
            if result.success:
                msg = f"[매도] {signal.code} {qty}주 @시장가 | 전략:{signal.strategy_name} | 사유:{signal.reason}"
                logger.info(msg)
                self.notifier.notify(msg)

    def _check_risk_exits(self) -> None:
        """손절/익절 자동 체크"""
        balance = self.portfolio.balance
        if not balance:
            return
        for pos in balance.positions:
            price = self._get_current_price(pos.code)
            if price <= 0:
                continue
            if self.risk.should_stop_loss(pos.code, price):
                result = self.kis.order.sell(pos.code, pos.quantity, 0, OrderType.MARKET)
                if result.success:
                    self.notifier.notify(f"[손절] {pos.code} {pos.quantity}주 @시장가")
            elif self.risk.should_take_profit(pos.code, price):
                result = self.kis.order.sell(pos.code, pos.quantity, 0, OrderType.MARKET)
                if result.success:
                    self.notifier.notify(f"[익절] {pos.code} {pos.quantity}주 @시장가")

    def _get_current_price(self, code: str) -> int:
        try:
            quote = self.kis.market.get_quote(code)
            return quote.price
        except Exception as e:
            logger.warning(f"현재가 조회 실패 [{code}]: {e}")
            return 0

    def _is_market_open(self) -> bool:
        """장 운영 시간 체크 (평일 09:00~15:30 KST)"""
        import pytz
        kst = pytz.timezone("Asia/Seoul")
        now = datetime.now(kst)
        if now.weekday() >= 5:  # 토/일
            return False
        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now <= market_close

    def end_of_day(self) -> None:
        """장 종료 후 일일 결산"""
        self.portfolio.refresh()
        summary = self.portfolio.summary()
        logger.info(f"=== 일일 결산 ===\n{summary}")
        self.notifier.notify(f"[일일결산]\n{summary}")
