"""
DART OpenAPI 클라이언트
- 전체 상장법인 corp_code 조회 (corpCode.xml.zip)
- 분기/사업보고서 재무 데이터 (주당순이익·매출액) 조회

보고서 코드:
  11013 = 1분기(Q1)  11012 = 반기(H1/Q2)
  11014 = 3분기(Q3)  11011 = 사업보고서(Annual)

캐시 파일: data/dart_corpinfo.json
  형식: {stock_code: {"corp_code": "...", "name": "...회사명"}}
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

CORP_INFO_CACHE = Path("data/dart_corpinfo.json")

# 구버전 캐시 경로 (하위 호환 자동 마이그레이션용)
_LEGACY_CACHE   = Path("data/dart_corpcode.json")


class DARTClient:
    def __init__(self, api_key: str):
        self.api_key  = api_key
        self._info_map: Optional[dict] = None   # code → {corp_code, name}

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

    # ── corp_info 매핑 ────────────────────────────────────────────────
    def get_corp_info_map(self) -> dict:
        """stockCode → {corp_code, name} 매핑 딕셔너리 (캐시 재사용)"""
        if self._info_map is not None:
            return self._info_map
        if CORP_INFO_CACHE.exists():
            with open(CORP_INFO_CACHE, "r", encoding="utf-8") as f:
                self._info_map = json.load(f)
            logger.info(f"[DART] corpInfo 캐시 로드: {len(self._info_map):,}개")
            return self._info_map
        return self._download_corp_info()

    def get_corp_map(self) -> dict:
        """하위 호환: stockCode → corp_code (str) 반환"""
        return {k: v["corp_code"] for k, v in self.get_corp_info_map().items()}

    def _download_corp_info(self) -> dict:
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
            nm = (item.findtext("corp_name")  or "").strip()
            if sc and len(sc) == 6 and cc:
                result[sc] = {"corp_code": cc, "name": nm}

        CORP_INFO_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(CORP_INFO_CACHE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)

        self._info_map = result
        logger.info(f"[DART] corpInfo 다운로드 완료: {len(result):,}개 상장법인")
        return result

    def get_corp_code(self, stock_code: str) -> Optional[str]:
        info = self.get_corp_info_map().get(stock_code)
        return info["corp_code"] if info else None

    def get_all_listed_stocks(self) -> list:
        """DART에 등록된 전체 상장법인 목록
        Returns: [{"code": "005930", "name": "삼성전자"}, ...]
        """
        return [
            {"code": code, "name": info["name"]}
            for code, info in self.get_corp_info_map().items()
        ]

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
