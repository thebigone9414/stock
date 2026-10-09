"""
전략 베이스 클래스
모든 투자 전략은 BaseStrategy를 상속하여 generate_signals() 구현
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any

from kis.market import KISMarket
from engine.portfolio import Portfolio


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    code: str
    signal_type: SignalType
    strategy_name: str
    reason: str
    price: Optional[int] = None      # None이면 시장가
    quantity: Optional[int] = None   # None이면 리스크 모듈이 산출
    confidence: float = 1.0          # 0~1 신뢰도
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """
    전략 베이스 클래스

    새 전략 작성법:
        class MyStrategy(BaseStrategy):
            name = "MyStrategy"
            def generate_signals(self, market, portfolio) -> List[Signal]:
                ...
    """

    name: str = "BaseStrategy"

    @abstractmethod
    def generate_signals(
        self,
        market: KISMarket,
        portfolio: Portfolio,
    ) -> List[Signal]:
        """시그널 생성. 매수/매도 Signal 리스트 반환"""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name})"
