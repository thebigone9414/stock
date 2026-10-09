"""
포트폴리오 상태 추적 및 관리
실계좌 잔고와 동기화하여 포지션을 추적
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime

from loguru import logger
from kis.account import Balance, KISAccount


@dataclass
class PortfolioSnapshot:
    timestamp: datetime
    cash: int
    total_eval: int
    total_purchase: int
    total_profit_loss: int
    total_profit_loss_rate: float
    positions: list


class Portfolio:
    def __init__(self, account: KISAccount):
        self._account = account
        self._balance: Optional[Balance] = None
        self._snapshots: List[PortfolioSnapshot] = []

    def refresh(self) -> Balance:
        """계좌 잔고 최신화"""
        self._balance = self._account.get_balance()
        snap = PortfolioSnapshot(
            timestamp=datetime.now(),
            cash=self._balance.cash,
            total_eval=self._balance.total_eval,
            total_purchase=self._balance.total_purchase,
            total_profit_loss=self._balance.total_profit_loss,
            total_profit_loss_rate=self._balance.total_profit_loss_rate,
            positions=[p for p in self._balance.positions],
        )
        self._snapshots.append(snap)
        # 최근 1000개 스냅샷만 유지
        if len(self._snapshots) > 1000:
            self._snapshots = self._snapshots[-1000:]
        return self._balance

    @property
    def balance(self) -> Optional[Balance]:
        return self._balance

    def get_position(self, code: str):
        if not self._balance:
            return None
        for pos in self._balance.positions:
            if pos.code == code:
                return pos
        return None

    def has_position(self, code: str) -> bool:
        return self.get_position(code) is not None

    def get_position_quantity(self, code: str) -> int:
        pos = self.get_position(code)
        return pos.quantity if pos else 0

    def summary(self) -> str:
        if not self._balance:
            return "잔고 미조회"
        b = self._balance
        lines = [
            f"현금: {b.cash:,}원",
            f"총평가: {b.total_eval:,}원",
            f"총손익: {b.total_profit_loss:,}원 ({b.total_profit_loss_rate:.2f}%)",
            f"보유종목: {len(b.positions)}개",
        ]
        for pos in b.positions:
            lines.append(
                f"  [{pos.code}] {pos.name} {pos.quantity}주 "
                f"평균단가:{pos.avg_price:,.0f} 현재:{pos.current_price:,} "
                f"손익:{pos.profit_loss:,}원({pos.profit_loss_rate:.2f}%)"
            )
        return "\n".join(lines)
