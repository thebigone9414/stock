"""
KIS 시장 데이터 모듈
- 주식 현재가 조회 + 프로그램 매매 데이터
- 일/주/월봉 OHLCV 조회
- 종목 검색, 거래량/등락률 순위
"""
from dataclasses import dataclass


def _safe_int(val) -> int:
    """빈 문자열 / None / 숫자 문자열을 안전하게 int 변환"""
    try:
        return int(str(val).replace(",", "").strip() or "0")
    except (ValueError, TypeError):
        return 0


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

    def get_program_trade(self, code: str) -> dict:
        """실시간 프로그램 매매 데이터 조회
        FHKST01010100 (현재가시세) output 내 프로그램매매 필드 추출.

        [필드 가용성 확인 결과]
        pgtr_ntby_qty      — 존재 O, 실시간 순매수 수량 (음수=순매도)
        pgtr_ntby_tr_pbmn  — 존재 X (API output에 없음) → 항상 0
        pgtr_est_amt       — 내부 계산: pgtr_ntby_qty × 현재가 (원 단위 추정치)
                             종목간 단순 수량 비교는 주가 차이로 왜곡되므로 금액 환산
        """
        data = self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
        )
        o     = data.get("output", {})
        price = _safe_int(o.get("stck_prpr"))
        qty   = _safe_int(o.get("pgtr_ntby_qty"))   # 순매수 수량 (실제 존재하는 필드)
        return {
            "code":              code,
            "price":             price,
            "change_rate":       float(o.get("prdy_ctrt", 0) or 0),
            "volume":            _safe_int(o.get("acml_vol")),
            # 프로그램 매매 — 수량 (API 제공)
            "pgtr_ntby_qty":     qty,
            # 프로그램 매매 — 추정 금액 (수량 × 현재가, 종목 간 비교용)
            "pgtr_est_amt":      qty * price,
            # 전체 output (디버그용)
            "_raw": o,
        }

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

    def get_ohlcv_long(
        self,
        code: str,
        days: int = 800,
        throttler=None,
    ) -> pd.DataFrame:
        """최근 N 영업일 일봉 조회 (페이지네이션 자동 처리)

        744일 이평선 계산용. days=820 권장 (744 + 여유 76일).
        KIS API 1회 약 100행 반환 → 최대 12청크 반복으로 충분.
        """
        from datetime import timedelta

        chunks: list = []
        end_dt = pd.Timestamp.now()

        for _ in range(12):
            start_dt = end_dt - timedelta(days=150)  # 150 달력일 ≈ 100 영업일
            if throttler:
                throttler.acquire()

            df = self.get_ohlcv(
                code,
                period="D",
                start_date=start_dt.strftime("%Y%m%d"),
                end_date=end_dt.strftime("%Y%m%d"),
                adj_price=True,   # 수정주가 (분배락/권리락 반영)
            )
            if df.empty:
                break

            chunks.append(df)
            if sum(len(c) for c in chunks) >= days + 50:
                break

            oldest: pd.Timestamp = df["date"].min()
            end_dt = oldest - timedelta(days=1)

        if not chunks:
            return pd.DataFrame()

        return (
            pd.concat(chunks)
            .drop_duplicates("date")
            .sort_values("date")
            .reset_index(drop=True)
            .tail(days)
            .reset_index(drop=True)
        )

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

    # ── 지수 구성 종목 조회 ─────────────────────────────────────
    def get_index_components(self, index_code: str = "0028") -> list:
        """지수 구성 종목 조회 (FHPUP02100000)

        Args:
            index_code: KIS 지수 코드 (KOSPI200 = "0028")
        Returns:
            [{"code", "name", "bstp_name"}, ...] 형태의 리스트
        """
        result = []
        ctx_fk = ""
        ctx_nk = ""

        for _ in range(5):  # 200종목 기준 최대 3페이지면 충분, 5로 여유
            data = self.client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-index-stockinfo",
                tr_id="FHPUP02100000",
                params={
                    "fid_cond_mrkt_div_code": "U",
                    "fid_input_iscd":         index_code,
                    "CTX_AREA_FK100":         ctx_fk,
                    "CTX_AREA_NK100":         ctx_nk,
                },
            )
            rows = data.get("output2", [])
            for r in rows:
                # 응답 필드명이 버전마다 다를 수 있어 두 가지 모두 시도
                code = (r.get("mksc_shrn_iscd") or r.get("stck_shrn_iscd", "")).strip()
                name = r.get("hts_kor_isnm", "").strip()
                bstp = r.get("bstp_kor_isnm", "").strip()
                if code and name:
                    result.append({"code": code, "name": name, "bstp_name": bstp})

            ctx_fk = (data.get("ctx_area_fk100") or "").strip()
            ctx_nk = (data.get("ctx_area_nk100") or "").strip()
            if not ctx_fk:
                break

        # 중복 코드 제거
        seen: set = set()
        unique = []
        for item in result:
            if item["code"] not in seen:
                seen.add(item["code"])
                unique.append(item)

        logger.info(f"[지수조회] {index_code} → {len(unique)}종목")
        return unique

    # ── 지수 현재가 / 등락률 조회 ───────────────────────────────────
    def get_index_change_rate(self, index_code: str = "0001") -> float:
        """KOSPI/KOSDAQ 지수 전일대비 등락률 조회 (FHKUP03500100)

        Args:
            index_code: "0001"=KOSPI, "1001"=KOSDAQ
        Returns:
            등락률(%) — 예: -2.35, +1.10. 조회 실패 시 0.0
        """
        data = self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            tr_id="FHKUP03500100",
            params={
                "fid_cond_mrkt_div_code": "U",
                "fid_input_iscd": index_code,
            },
        )
        o = data.get("output", {})
        raw = o.get("bstp_nmix_prdy_ctrt", "0") or "0"
        try:
            return float(str(raw).replace(",", "").strip())
        except (ValueError, TypeError):
            return 0.0

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

    def get_investor_trend_history(self, code: str, days: int = 5) -> list:
        """투자자별 매매동향 최근 N거래일 반환
        Returns list of {"date": "YYYYMMDD", "frgn_net": int, "orgn_net": int}
        sorted most-recent first.
        """
        data = self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            tr_id="FHKST01010900",
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
        )
        output = data.get("output", [])
        if not isinstance(output, list):
            return []
        result = []
        for record in output:
            if not (isinstance(record, dict) and record.get("frgn_ntby_tr_pbmn", "")):
                continue
            result.append({
                "date":     record.get("stnd_isit", ""),
                "frgn_net": _safe_int(record.get("frgn_ntby_tr_pbmn", "0")),
                "orgn_net": _safe_int(record.get("orgn_ntby_tr_pbmn", "0")),
            })
            if len(result) >= days:
                break
        return result
