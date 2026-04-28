"""
KIS 계좌 정보 모듈
- 잔고 조회
- 주문가능금액 조회
- 손익 조회
"""
from dataclasses import dataclass, field
from typing import List

from loguru import logger
from .client import KISClient


@dataclass
class Position:
    code: str
    name: str
    quantity: int
    avg_price: float
    current_price: int
    eval_amount: int
    profit_loss: int
    profit_loss_rate: float


@dataclass
class Balance:
    cash: int
    total_eval: int
    total_purchase: int
    total_profit_loss: int
    total_profit_loss_rate: float
    positions: List[Position] = field(default_factory=list)


class KISAccount:
    def __init__(self, client: KISClient, account_no: str, is_paper: bool):
        self.client = client
        self.account_no = account_no
        self.is_paper = is_paper
        # 계좌번호 파싱 (XXXXXXXX-XX 형식)
        parts = account_no.replace("-", "")
        self.acnt_no = parts[:8]
        self.acnt_prdt_cd = parts[8:] if len(parts) > 8 else "01"

    def get_balance(self) -> Balance:
        """주식 잔고 조회 (국내주식-006)"""
        tr_id = "VTTC8434R" if self.is_paper else "TTTC8434R"
        data = self.client.get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=tr_id,
            params={
                "CANO": self.acnt_no,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        output1 = data.get("output1", [])
        output2 = data.get("output2", [{}])[0]

        positions = []
        for item in output1:
            qty = int(item.get("hldg_qty", 0))
            if qty <= 0:
                continue
            positions.append(
                Position(
                    code=item.get("pdno", ""),
                    name=item.get("prdt_name", ""),
                    quantity=qty,
                    avg_price=float(item.get("pchs_avg_pric", 0)),
                    current_price=int(item.get("prpr", 0)),
                    eval_amount=int(item.get("evlu_amt", 0)),
                    profit_loss=int(item.get("evlu_pfls_amt", 0)),
                    profit_loss_rate=float(item.get("evlu_pfls_rt", 0)),
                )
            )

        return Balance(
            cash=int(output2.get("dnca_tot_amt", 0)),
            total_eval=int(output2.get("tot_evlu_amt", 0)),
            total_purchase=int(output2.get("pchs_amt_smtl_amt", 0)),
            total_profit_loss=int(output2.get("evlu_pfls_smtl_amt", 0)),
            total_profit_loss_rate=float(output2.get("asst_icdc_erng_rt", 0)),
            positions=positions,
        )

    def get_available_cash(self) -> int:
        """주문가능금액 조회 (국내주식-007)"""
        tr_id = "VTTC8908R" if self.is_paper else "TTTC8908R"
        data = self.client.get(
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            tr_id=tr_id,
            params={
                "CANO": self.acnt_no,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "PDNO": "005930",  # 삼성전자 기준 (시장가 조회용)
                "ORD_UNPR": "0",
                "ORD_DVSN": "01",
                "CMA_EVLU_AMT_ICLD_YN": "Y",
                "OVRS_ICLD_YN": "N",
            },
        )
        output = data.get("output", {})
        return int(output.get("ord_psbl_cash", 0))

    def get_order_history(self, start_date: str, end_date: str) -> List[dict]:
        """일별 주문 체결 조회 (국내주식-005)"""
        tr_id = "VTTC8001R" if self.is_paper else "TTTC8001R"
        data = self.client.get(
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id=tr_id,
            params={
                "CANO": self.acnt_no,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "INQR_STRT_DT": start_date,
                "INQR_END_DT": end_date,
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN": "00",
                "PDNO": "",
                "CCLD_DVSN": "01",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        return data.get("output1", [])
