"""
리스크 관리 모듈
- 종목별 최대 투자 비율 제한
- 총 투자금액 제한
- 일일 손실 한도
- 포지션 수 제한
"""
from dataclasses import dataclass
from typing import Optional
from loguru import logger

from engine.portfolio import Portfolio


@dataclass
class RiskConfig:
    # 종목당 최대 투자 비율 (총자산 대비)
    max_position_ratio: float = 0.20      # 20%
    # 최대 보유 종목 수
    max_positions: int = 10
    # 일일 최대 손실 한도 (총자산 대비)
    daily_loss_limit_ratio: float = 0.05  # 5%
    # 종목당 최대 손실 (매수가 대비)
    stop_loss_ratio: float = 0.07         # 7%
    # 목표 수익률 (매수가 대비)
    take_profit_ratio: float = 0.15       # 15%


class RiskManager:
    def __init__(self, config: RiskConfig, portfolio: Portfolio):
        self.config = config
        self.portfolio = portfolio
        self._daily_realized_loss: int = 0
        self._daily_start_value: Optional[int] = None

    def set_daily_start_value(self, value: int) -> None:
        self._daily_start_value = value
        self._daily_realized_loss = 0
        logger.info(f"일일 기준 자산: {value:,}원")

    def can_buy(self, code: str, amount: int) -> tuple[bool, str]:
        """매수 가능 여부 체크"""
        balance = self.portfolio.balance
        if not balance:
            return False, "잔고 정보 없음"

        # 보유 종목 수 체크
        if not self.portfolio.has_position(code):
            if len(balance.positions) >= self.config.max_positions:
                return False, f"최대 보유 종목 수 초과 ({self.config.max_positions}개)"

        # 종목당 최대 투자 금액 체크
        total_asset = balance.total_eval or 1
        max_invest = int(total_asset * self.config.max_position_ratio)
        current_pos = self.portfolio.get_position(code)
        current_amount = current_pos.eval_amount if current_pos else 0
        if current_amount + amount > max_invest:
            return False, f"종목당 최대 투자 한도 초과 (한도: {max_invest:,}원)"

        # 일일 손실 한도 체크
        if self._daily_start_value:
            loss_limit = int(self._daily_start_value * self.config.daily_loss_limit_ratio)
            current_loss = self._daily_start_value - balance.total_eval
            if current_loss > loss_limit:
                return False, f"일일 손실 한도 도달 (손실: {current_loss:,}원, 한도: {loss_limit:,}원)"

        # 현금 체크
        if amount > balance.cash:
            return False, f"현금 부족 (보유: {balance.cash:,}원, 필요: {amount:,}원)"

        return True, "OK"

    def should_stop_loss(self, code: str, current_price: int) -> bool:
        """손절 여부 판단"""
        pos = self.portfolio.get_position(code)
        if not pos or pos.avg_price <= 0:
            return False
        loss_rate = (current_price - pos.avg_price) / pos.avg_price
        if loss_rate <= -self.config.stop_loss_ratio:
            logger.warning(
                f"[손절 트리거] {code} 손익률={loss_rate*100:.1f}% "
                f"(한도=-{self.config.stop_loss_ratio*100:.0f}%)"
            )
            return True
        return False

    def should_take_profit(self, code: str, current_price: int) -> bool:
        """익절 여부 판단"""
        pos = self.portfolio.get_position(code)
        if not pos or pos.avg_price <= 0:
            return False
        profit_rate = (current_price - pos.avg_price) / pos.avg_price
        if profit_rate >= self.config.take_profit_ratio:
            logger.info(
                f"[익절 트리거] {code} 손익률=+{profit_rate*100:.1f}% "
                f"(목표=+{self.config.take_profit_ratio*100:.0f}%)"
            )
            return True
        return False

    def calc_buy_quantity(self, code: str, price: int, ratio: float = 1.0) -> int:
        """매수 수량 계산 (허용 한도 내 최대)"""
        balance = self.portfolio.balance
        if not balance or price <= 0:
            return 0

        total_asset = balance.total_eval or 1
        max_invest = int(total_asset * self.config.max_position_ratio * ratio)
        current_pos = self.portfolio.get_position(code)
        current_amount = current_pos.eval_amount if current_pos else 0
        available_invest = min(max_invest - current_amount, balance.cash)
        if available_invest <= 0:
            return 0
        return available_invest // price
