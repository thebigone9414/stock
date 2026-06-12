"""
KIS 주문 모듈
- 매수/매도 주문
- 정정/취소
- 미체결 조회
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List

from loguru import logger
from .client import KISClient


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "01"   # 시장가
    LIMIT = "00"    # 지정가
    CONDITIONAL_LIMIT = "05"  # 조건부지정가
    BEST_LIMIT = "06"  # 최유리지정가
    IMMEDIATE = "07"  # 최우선지정가


@dataclass
class OrderResult:
    order_no: str
    code: str
    side: OrderSide
    quantity: int
    price: int
    order_type: OrderType
    success: bool
    message: str = ""


class KISOrder:
    def __init__(self, client: KISClient, account_no: str, is_paper: bool):
        self.client = client
        self.account_no = account_no
        self.is_paper = is_paper
        parts = account_no.replace("-", "")
        self.acnt_no = parts[:8]
        self.acnt_prdt_cd = parts[8:] if len(parts) > 8 else "01"

    def _buy_tr_id(self) -> str:
        return "VTTC0802U" if self.is_paper else "TTTC0802U"

    def _sell_tr_id(self) -> str:
        return "VTTC0801U" if self.is_paper else "TTTC0801U"

    def buy(
        self,
        code: str,
        quantity: int,
        price: int = 0,
        order_type: OrderType = OrderType.MARKET,
    ) -> OrderResult:
        """매수 주문"""
        return self._place_order(OrderSide.BUY, code, quantity, price, order_type)

    def sell(
        self,
        code: str,
        quantity: int,
        price: int = 0,
        order_type: OrderType = OrderType.MARKET,
    ) -> OrderResult:
        """매도 주문"""
        return self._place_order(OrderSide.SELL, code, quantity, price, order_type)

    def _place_order(
        self,
        side: OrderSide,
        code: str,
        quantity: int,
        price: int,
        order_type: OrderType,
    ) -> OrderResult:
        tr_id = self._buy_tr_id() if side == OrderSide.BUY else self._sell_tr_id()
        body = {
            "CANO": self.acnt_no,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": code,
            "ORD_DVSN": order_type.value,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price) if order_type == OrderType.LIMIT else "0",
        }
        mode = "모의" if self.is_paper else "실전"
        side_str = "매수" if side == OrderSide.BUY else "매도"
        logger.info(f"[{mode}] {side_str} 주문: {code} {quantity}주 @ {price:,}원")

        try:
            data = self.client.post(
                "/uapi/domestic-stock/v1/trading/order-cash",
                tr_id=tr_id,
                body=body,
            )
            output = data.get("output", {})
            order_no = output.get("ODNO", "")
            logger.info(f"주문 성공: 주문번호={order_no}")
            return OrderResult(
                order_no=order_no,
                code=code,
                side=side,
                quantity=quantity,
                price=price,
                order_type=order_type,
                success=True,
            )
        except Exception as e:
            logger.error(f"주문 실패: {e}")
            return OrderResult(
                order_no="",
                code=code,
                side=side,
                quantity=quantity,
                price=price,
                order_type=order_type,
                success=False,
                message=str(e),
            )

    def buy_market(self, code: str, quantity: int) -> OrderResult:
        """시장가 매수"""
        return self.buy(code, quantity)

    def sell_market(self, code: str, quantity: int) -> OrderResult:
        """시장가 매도"""
        return self.sell(code, quantity)

    def cancel(self, order_no: str, code: str, quantity: int, price: int, order_type: str) -> bool:
        """주문 취소"""
        tr_id = "VTTC0803U" if self.is_paper else "TTTC0803U"
        body = {
            "CANO": self.acnt_no,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order_no,
            "ORD_DVSN": order_type,
            "RVSE_CNCL_DVSN_CD": "02",  # 취소
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
            "QTY_ALL_ORD_YN": "Y",
        }
        try:
            self.client.post("/uapi/domestic-stock/v1/trading/order-rvsecncl", tr_id=tr_id, body=body)
            logger.info(f"주문 취소 성공: {order_no}")
            return True
        except Exception as e:
            logger.error(f"주문 취소 실패: {e}")
            return False

    def get_pending_orders(self) -> List[dict]:
        """미체결 주문 조회"""
        tr_id = "VTTC8036R" if self.is_paper else "TTTC8036R"
        data = self.client.get(
            "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
            tr_id=tr_id,
            params={
                "CANO": self.acnt_no,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "INQR_DVSN_1": "0",
                "INQR_DVSN_2": "0",
            },
        )
        return data.get("output", [])
