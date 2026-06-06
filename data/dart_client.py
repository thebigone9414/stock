"""
DART OpenAPI 클라이언트
- 기업 고유번호(corp_code) 조회 (corpCode.xml.zip)
- 분기/사업보고서 재무 데이터 (주당순이익·매출액) 조회

보고서 코드:
  11013 = 1분기(Q1)  11012 = 반기(H1/Q2)
  11014 = 3분기(Q3)  11011 = 사업보고서(Annual)
"""
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

DART_BASE       = "https://opendart.fss.or.kr/api"
REPRT_Q1        = "11013"
REPRT_H1        = "11012"
REPRT_Q3        = "11014"
REPRT_ANN       = "11011"

CORP_CODE_CACHE = Path("data/dart_corpcode.json")


class DARTClient:
    def __init__(self, api_key: str):
        self.api_key  = api_key
        self._corpmap: Optional[dict] = None

    # ── 내부 HTTP ─────────────────────────────────────────────────────
    def _get(self, endpoint: str, params: dict) -> dict:
        params = dict(params, crtfc_key=self.api_key)
        resp = requests.get(f"{DART_BASE}/{endpoint}", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        if status == "000":
            return data
        if status in ("010", "013"):          # 정상 조회 / 해당 없음
            return {"list": [], "status": status}
        raise ValueError(f"DART 오류 status={status}: {data.get('message', '')}")

    # ── corp_code 매핑 ────────────────────────────────────────────────
    def get_corp_map(self) -> dict:
        """stockCode → corpCode 매핑 딕셔너리 반환 (캐시 재사용)"""
        if self._corpmap is not None:
            return self._corpmap
        if CORP_CODE_CACHE.exists():
            with open(CORP_CODE_CACHE, "r", encoding="utf-8") as f:
                self._corpmap = json.load(f)
            logger.info(f"[DART] corpCode 캐시 로드: {len(self._corpmap):,}개")
            return self._corpmap
        return self._download_corp_map()

    def _download_corp_map(self) -> dict:
        logger.info("[DART] corpCode.xml 다운로드 중...")
        resp = requests.get(
            f"{DART_BASE}/corpCode.xml",
            params={"crtfc_key": self.api_key},
            timeout=60,
        )
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_bytes = zf.read("CORPCODE.xml")

        root   = ET.fromstring(xml_bytes)
        result = {}
        for item in root.findall("list"):
            sc = (item.findtext("stock_code") or "").strip()
            cc = (item.findtext("corp_code")  or "").strip()
            if sc and len(sc) == 6:
                result[sc] = cc

        CORP_CODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(CORP_CODE_CACHE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)

        self._corpmap = result
        logger.info(f"[DART] corpCode 다운로드 완료: {len(result):,}개")
        return result

    def get_corp_code(self, stock_code: str) -> Optional[str]:
        return self.get_corp_map().get(stock_code)

    # ── 재무제표 조회 ─────────────────────────────────────────────────
    def get_financial_statement(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str,
        fs_div: str = "CFS",   # CFS=연결, OFS=개별
    ) -> list:
        """단일회사 주요계정 (fnlttSinglAcnt) — 연결 없으면 개별 재시도"""
        try:
            data = self._get("fnlttSinglAcnt.json", {
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            })
            rows = data.get("list", [])
            if rows or fs_div == "OFS":
                return rows
            # 연결재무제표 없음 → 개별 재시도
            logger.debug(f"[DART] {corp_code} {bsns_year} {reprt_code} 연결 없음, 개별 재시도")
            return self.get_financial_statement(corp_code, bsns_year, reprt_code, "OFS")
        except Exception as e:
            logger.debug(f"[DART] {corp_code} {bsns_year} {reprt_code} {fs_div} 실패: {e}")
            if fs_div == "CFS":
                return self.get_financial_statement(corp_code, bsns_year, reprt_code, "OFS")
            return []

    def get_eps(
        self, corp_code: str, bsns_year: str, reprt_code: str
    ) -> Optional[int]:
        """손익계산서(IS)에서 주당순이익(EPS) 추출 → 원 단위 int. 없으면 None"""
        rows = self.get_financial_statement(corp_code, bsns_year, reprt_code)
        for row in rows:
            if row.get("sj_div") != "IS":
                continue
            nm = row.get("account_nm", "")
            if "주당순이익" in nm and "희석" not in nm:
                raw = row.get("thstrm_amount", "").replace(",", "").strip()
                try:
                    return int(raw)
                except (ValueError, TypeError):
                    return None
        return None

    def get_revenue(
        self, corp_code: str, bsns_year: str, reprt_code: str
    ) -> Optional[int]:
        """매출액 추출 → 원 단위 int. 없으면 None"""
        rows = self.get_financial_statement(corp_code, bsns_year, reprt_code)
        target_nms = {"매출액", "수익(매출액)", "영업수익", "매출"}
        for row in rows:
            if row.get("sj_div") not in ("IS", "CIS"):
                continue
            if row.get("account_nm", "").strip() in target_nms:
                raw = row.get("thstrm_amount", "").replace(",", "").strip()
                try:
                    return int(raw)
                except (ValueError, TypeError):
                    return None
        return None
