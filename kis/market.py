"""
KIS 시장 데이터 모듈
- 주식 현재가 조회
- 일/주/월봉 OHLCV 조회
- 종목 검색
- 상한가/하한가/거래량 상위 조회
"""
from dataclasses import dataclass
from typing import List, Optional
import pandas as pd
from loguru import logger

from .client import KISClient


@dataclass
class Quote:
    code: str
    name: str
    price: int
    change: int
    change_rate: float
    volume: int
    open: int
    high: int
    low: int
    market_cap: int = 0


@dataclass
class OHLCV:
    date: str
    open: int
    high: int
    low: int
    close: int
    volume: int


class KISMarket:
    def __init__(self, client: KISClient):
        self.client = client

    # ── 현재가 조회 ─────────────────────────────────────────────
    def get_quote(self, code: str) -> Quote:
        """주식 현재가 시세 조회 (국내주식-009)"""
        data = self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
        )
        o = data["output"]
        return Quote(
            code=code,
            name=o.get("hts_kor_isnm", ""),
            price=int(o.get("stck_prpr", 0)),
            change=int(o.get("prdy_vrss", 0)),
            change_rate=float(o.get("prdy_ctrt", 0)),
            volume=int(o.get("acml_vol", 0)),
            open=int(o.get("stck_oprc", 0)),
            high=int(o.get("stck_hgpr", 0)),
            low=int(o.get("stck_lwpr", 0)),
            market_cap=int(o.get("hts_avls", 0)),
        )

    # ── 일봉/주봉/월봉 OHLCV ────────────────────────────────────
    def get_ohlcv(
        self,
        code: str,
        period: str = "D",  # D=일, W=주, M=월
        start_date: str = "",
        end_date: str = "",
        adj_price: bool = True,
    ) -> pd.DataFrame:
        """주식 기간별 시세 (국내주식-012)"""
        period_map = {"D": "FHKST03010100", "W": "FHKST03010200", "M": "FHKST03010300"}
        tr_id = period_map.get(period.upper(), "FHKST03010100")

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": code,
            "fid_input_date_1": start_date,
            "fid_input_date_2": end_date,
            "fid_period_div_code": period.upper(),
            "fid_org_adj_prc": "0" if adj_price else "1",
        }
        data = self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id=tr_id,
            params=params,
        )
        rows = data.get("output2", [])
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        col_map = {
            "stck_bsop_date": "date",
            "stck_oprc": "open",
            "stck_hgpr": "high",
            "stck_lwpr": "low",
            "stck_clpr": "close",
            "acml_vol": "volume",
            "acml_tr_pbmn": "amount",
        }
        df = df.rename(columns=col_map)
        numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
        return df

    # ── 거래량 상위 종목 ─────────────────────────────────────────
    def get_volume_rank(self, market: str = "0000", top_n: int = 30) -> List[dict]:
        """거래량 순위 (국내주식-047)"""
        data = self.client.get(
            "/uapi/domestic-stock/v1/ranking/volume",
            tr_id="FHPST01710000",
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20171",
                "fid_input_iscd": market,
                "fid_div_cls_code": "0",
                "fid_blng_cls_code": "0",
                "fid_trgt_cls_code": "111111111",
                "fid_trgt_exls_cls_code": "000000",
                "fid_input_price_1": "",
                "fid_input_price_2": "",
                "fid_vol_cnt": "",
                "fid_input_date_1": "",
            },
        )
        return data.get("output", [])[:top_n]

    # ── 상승률 상위 종목 ─────────────────────────────────────────
    def get_fluctuation_rank(self, rank_type: str = "1", top_n: int = 30) -> List[dict]:
        """등락률 순위 조회 (국내주식-048)  rank_type: 1=상승률, 2=하락률"""
        data = self.client.get(
            "/uapi/domestic-stock/v1/ranking/fluctuation",
            tr_id="FHPST01700000",
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20170",
                "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": rank_type,
                "fid_input_cnt_1": "0",
                "fid_prc_cls_code": "1",
                "fid_input_price_1": "",
                "fid_input_price_2": "",
                "fid_vol_cnt": "",
                "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_div_cls_code": "0",
                "fid_rsfl_rate1": "",
                "fid_rsfl_rate2": "",
            },
        )
        return data.get("output", [])[:top_n]

    # ── 호가 조회 ────────────────────────────────────────────────
    def get_orderbook(self, code: str) -> dict:
        """국내주식 호가 조회 (국내주식-011)"""
        data = self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            tr_id="FHKST01010200",
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
        )
        return data.get("output1", {})

    # ── 투자자별 매매동향 ─────────────────────────────────────────
    def get_investor_trend(self, code: str) -> dict:
        """투자자별 매매동향 (국내주식-061)
        output은 30일치 list. output[0]은 당일이지만 장중에는 투자자 필드가 빈 문자열.
        → 실제 값이 있는 가장 최근 레코드(통상 output[1] = 전거래일)를 반환.
        """
        data = self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            tr_id="FHKST01010900",
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
        )
        output = data.get("output", {})
        if isinstance(output, list):
            # 투자자 데이터가 실제로 채워진 가장 최근 레코드 반환
            for record in output:
                if isinstance(record, dict) and record.get("frgn_ntby_tr_pbmn", ""):
                    return record
            return output[0] if output else {}
        return output if isinstance(output, dict) else {}
