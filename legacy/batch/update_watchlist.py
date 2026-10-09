#!/usr/bin/env python3
"""
KOSPI200 + KOSDAQ150 + ETF 종목 자동 업데이트
NAVER Finance 스크레이핑 → 실패 시 기존 캐시 유지 (분기 4회 갱신 목적)

Usage:
    python batch/update_watchlist.py
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
import data.ma_store as ma_store

KST             = pytz.timezone("Asia/Seoul")
CACHE_PATH      = Path(__file__).parent.parent / "data" / "kospi200_cache.json"
KOSDAQ150_CACHE = Path(__file__).parent.parent / "data" / "kosdaq150_cache.json"
ETF_CACHE       = Path(__file__).parent.parent / "data" / "etf_cache.json"
MIN_STOCKS      = 100
MIN_ETFS        = 50

# NAVER Finance 데스크탑 헤더
_NAVER_STOCK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/sise/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
_NAVER_ETF_HEADERS = {
    **_NAVER_STOCK_HEADERS,
    "Referer": "https://finance.naver.com/fund/",
}

# ── 주식 업종명 → 섹터 매핑 ────────────────────────────────────────────────────
_SECTOR_KEYWORDS = [
    ("반도체",      "반도체/IT"),
    ("전기·전자",   "반도체/IT"),
    ("전기전자",    "반도체/IT"),
    ("디스플레이",  "반도체/IT"),
    ("2차전지",     "2차전지"),
    ("배터리",      "2차전지"),
    ("게임",        "플랫폼/게임"),
    ("인터넷",      "플랫폼/게임"),
    ("소프트웨어",  "플랫폼/게임"),
    ("IT서비스",    "플랫폼/게임"),
    ("미디어",      "엔터테인먼트"),
    ("엔터",        "엔터테인먼트"),
    ("방송",        "엔터테인먼트"),
    ("타이어",      "자동차/부품"),
    ("자동차",      "자동차/부품"),
    ("운수장비",    "자동차/부품"),
    ("항공우주",    "방산/우주"),
    ("방위산업",    "방산/우주"),
    ("조선",        "조선/해운"),
    ("해운",        "조선/해운"),
    ("항공",        "항공/물류"),
    ("운수·창고",   "항공/물류"),
    ("운수창고",    "항공/물류"),
    ("물류",        "항공/물류"),
    ("기계",        "중공업/기계"),
    ("중공업",      "중공업/기계"),
    ("전기장비",    "AI전력/설비"),
    ("전력기기",    "AI전력/설비"),
    ("전기설비",    "AI전력/설비"),
    ("의약품",      "바이오/제약"),
    ("제약",        "바이오/제약"),
    ("바이오",      "바이오/제약"),
    ("의료",        "바이오/제약"),
    ("건강관리",    "바이오/제약"),
    ("정유",        "화학/정유"),
    ("에너지화학",  "화학/정유"),
    ("화학",        "화학/정유"),
    ("철강",        "철강/소재"),
    ("금속",        "철강/소재"),
    ("비금속",      "철강/소재"),
    ("소재",        "철강/소재"),
    ("은행",        "금융/지주"),
    ("보험",        "금융/지주"),
    ("증권",        "금융/지주"),
    ("핀테크",      "금융/지주"),
    ("카드",        "금융/지주"),
    ("지주",        "금융/지주"),
    ("금융",        "금융/지주"),
    ("유통",        "유통/소비재"),
    ("음식료",      "유통/소비재"),
    ("식료품",      "유통/소비재"),
    ("담배",        "유통/소비재"),
    ("화장품",      "유통/소비재"),
    ("의류",        "유통/소비재"),
    ("생활",        "유통/소비재"),
    ("소비재",      "유통/소비재"),
    ("통신",        "통신/인프라"),
    ("전기가스",    "통신/인프라"),
    ("건설",        "건설/건자재"),
    ("건자재",      "건설/건자재"),
    ("시멘트",      "건설/건자재"),
]

_CODE_SECTOR_OVERRIDE: dict[str, str] = {
    "005490": "2차전지",
    "096770": "2차전지/정유",
    "010960": "건설/건자재",
    "028260": "금융/지주",
    "000150": "금융/지주",
    "034730": "금융/지주",
    "003550": "금융/지주",
    "000880": "금융/지주",
    "001040": "금융/지주",
    "004990": "금융/지주",
    "078930": "금융/지주",
    "002790": "금융/지주",
    "329180": "중공업/기계",
    "034020": "AI전력/설비",
    "267260": "AI전력/설비",
    "010120": "AI전력/설비",
    "006260": "AI전력/설비",
    "051600": "AI전력/설비",
}

# ── KOSDAQ150 검증용 종목 (KOSPI200에 없는 KOSDAQ 전용 종목) ──────────────────
# entryJongmok.naver 가 잘못된 코드를 받으면 KOSPI200 데이터를 기본값으로 반환함.
# 이 목록의 종목이 0개 매칭되면 KOSPI200 데이터로 판단, fetch 결과를 버림.
_KOSDAQ150_VALIDATORS = frozenset([
    "086520",  # 에코프로
    "247540",  # 에코프로비엠
    "196170",  # 알테오젠
    "028300",  # HLB
    "277810",  # 레인보우로보틱스 (두산로보틱스 2026-01 KOSPI 이전으로 교체)
    "141080",  # 리가켐바이오
    "357780",  # 솔브레인
    "403870",  # HPSP
])

# ── ETF 목록 URL (우선순위 순, 404 시 다음 URL 시도) ─────────────────────────
_ETF_LIST_URLS = [
    "https://finance.naver.com/fund/etfAllList.naver",
    "https://finance.naver.com/fund/etfTopCreator.naver",
    "https://finance.naver.com/fund/etf.naver",
]

# ── ETF 제외 키워드 (채권·금리·단기자금 관련) ─────────────────────────────────
_ETF_EXCLUDE = frozenset([
    "채권",    # bond
    "금리",    # interest rate
    "국채",    # government bond
    "회사채",  # corporate bond
    "단기채",  # short-term bond
    "통안",    # monetary stabilization bond
    "하이일드", # high yield bond
    "CP금리",  # commercial paper rate
    "CD금리",  # certificate of deposit rate
    "KOFR",   # Korea overnight financing rate
    "콜금리",  # call rate
    "TRF",    # target return fund (채권 혼합)
    "미국채",  # US treasury
])

# ── ETF 섹터 매핑 (앞에 있을수록 우선) ────────────────────────────────────────
_ETF_SECTOR_KEYWORDS = [
    # 레버리지/인버스 먼저 (섹터 오분류 방지)
    ("레버리지",   "ETF/레버리지"),
    ("인버스",     "ETF/인버스"),
    ("2X",         "ETF/레버리지"),
    ("3X",         "ETF/레버리지"),
    # 국내 섹터
    ("반도체",     "ETF/반도체"),
    ("2차전지",    "ETF/2차전지"),
    ("배터리",     "ETF/2차전지"),
    ("자동차",     "ETF/자동차"),
    ("모빌리티",   "ETF/자동차"),
    ("바이오",     "ETF/바이오"),
    ("헬스케어",   "ETF/바이오"),
    ("제약",       "ETF/바이오"),
    ("건강",       "ETF/바이오"),
    ("미디어",     "ETF/엔터"),
    ("엔터",       "ETF/엔터"),
    ("컨텐츠",     "ETF/엔터"),
    ("게임",       "ETF/엔터"),
    ("K-Culture",  "ETF/엔터"),
    ("방산",       "ETF/방산"),
    ("우주항공",   "ETF/방산"),
    ("조선",       "ETF/조선"),
    ("해운",       "ETF/조선"),
    ("로봇",       "ETF/4차산업"),
    ("AI",         "ETF/4차산업"),
    ("인공지능",   "ETF/4차산업"),
    ("4차산업",    "ETF/4차산업"),
    ("클라우드",   "ETF/4차산업"),
    ("전력",       "ETF/AI전력"),
    ("전기장비",   "ETF/AI전력"),
    ("기계",       "ETF/중공업"),
    ("중공업",     "ETF/중공업"),
    ("금융",       "ETF/금융"),
    ("은행",       "ETF/금융"),
    ("증권",       "ETF/금융"),
    ("보험",       "ETF/금융"),
    ("건설",       "ETF/건설"),
    ("에너지화학", "ETF/화학"),
    ("화학",       "ETF/화학"),
    ("정유",       "ETF/화학"),
    ("철강",       "ETF/철강소재"),
    ("소재",       "ETF/철강소재"),
    ("금속",       "ETF/철강소재"),
    ("통신",       "ETF/통신"),
    ("소비재",     "ETF/소비재"),
    ("소비",       "ETF/소비재"),
    ("유통",       "ETF/소비재"),
    ("식품",       "ETF/소비재"),
    ("생활",       "ETF/소비재"),
    # 해외
    ("필라델피아", "ETF/미국주식"),
    ("나스닥",     "ETF/미국주식"),
    ("S&P",        "ETF/미국주식"),
    ("빅테크",     "ETF/미국주식"),
    ("미국",       "ETF/미국주식"),
    ("차이나",     "ETF/해외"),
    ("중국",       "ETF/해외"),
    ("일본",       "ETF/해외"),
    ("유럽",       "ETF/해외"),
    ("신흥국",     "ETF/해외"),
    ("베트남",     "ETF/해외"),
    ("인도",       "ETF/해외"),
    ("글로벌",     "ETF/해외"),
    # 스타일/원자재
    ("배당",       "ETF/배당"),
    ("가치",       "ETF/배당"),
    ("달러",       "ETF/달러"),
    ("골드",       "ETF/원자재"),
    ("금선물",     "ETF/원자재"),
    ("원유",       "ETF/원자재"),
    ("원자재",     "ETF/원자재"),
    ("리츠",       "ETF/리츠"),
    ("부동산",     "ETF/리츠"),
    ("인프라",     "ETF/리츠"),
    # 지수 (가장 나중에)
    ("코스닥",     "ETF/지수"),
    ("코스피",     "ETF/지수"),
    ("200",        "ETF/지수"),
    ("150",        "ETF/지수"),
]


def _map_sector(bstp_name: str, code: str) -> str:
    if code in _CODE_SECTOR_OVERRIDE:
        return _CODE_SECTOR_OVERRIDE[code]
    for keyword, sector in _SECTOR_KEYWORDS:
        if keyword in bstp_name:
            return sector
    return "기타"


def _etf_sector(name: str) -> str:
    for keyword, sector in _ETF_SECTOR_KEYWORDS:
        if keyword in name:
            return sector
    return "ETF/기타"


def _load_old_sectors(cache_path: Path, key: str = "stocks") -> dict[str, str]:
    """기존 캐시에서 섹터 정보 로드 (새 목록 보강용)"""
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return {s["code"]: s.get("sector", "기타") for s in data.get(key, [])}
    except Exception:
        return {}


def _fetch_index(
    index_code: str,
    label: str,
    old_cache_path: Path,
    validators: frozenset[str] | None = None,
) -> list:
    """NAVER Finance 지수 구성 종목 페이지 HTML 스크레이핑.

    URL: https://finance.naver.com/sise/entryJongmok.naver?code={index_code}&page={n}
    NAVER 내부 코드: KOSPI200 → KPI200
    validators: 결과에 반드시 포함돼야 하는 종목 코드 집합. 하나도 없으면 잘못된 코드로 판단.
    """
    old_sectors = _load_old_sectors(old_cache_path)
    stocks: list[dict] = []
    seen: set[str] = set()

    for page in range(1, 30):
        url = "https://finance.naver.com/sise/entryJongmok.naver"
        try:
            resp = requests.get(
                url,
                headers=_NAVER_STOCK_HEADERS,
                params={"code": index_code, "page": page},
                timeout=30,
            )
            if not resp.ok:
                logger.error(f"[종목업데이트] {label} 페이지 {page} HTTP {resp.status_code}")
                break
            html = resp.text
        except Exception as e:
            logger.error(f"[종목업데이트] {label} 페이지 {page} 오류: {e}")
            break

        matches = re.findall(
            r'href="/item/main\.naver\?code=(\d{6})"[^>]*>\s*([^\s<][^<]*?)\s*</a>',
            html,
        )
        new = []
        for code, raw_name in matches:
            name = raw_name.strip()
            if not name or len(name) > 20 or code in seen:
                continue
            seen.add(code)
            sector = old_sectors.get(code) or _map_sector("", code)
            new.append({"code": code, "name": name, "sector": sector})

        if not new:
            logger.debug(f"[종목업데이트] {label} 페이지 {page}: 신규 없음 → 종료")
            break

        stocks.extend(new)
        logger.debug(f"[종목업데이트] {label} 페이지 {page}: +{len(new)}종목 (누적 {len(stocks)})")
        time.sleep(0.3)

    if len(stocks) < MIN_STOCKS:
        logger.error(f"[종목업데이트] {label} 결과 {len(stocks)}종목 — 최소 {MIN_STOCKS}개 미달")
        return []

    # 검증: 알려진 종목이 하나도 없으면 잘못된 코드 → 기본값(KOSPI200) 반환으로 판단
    if validators:
        found = {s["code"] for s in stocks}
        matched = found & validators
        if not matched:
            logger.error(
                f"[종목업데이트] {label} 검증 실패 — 코드 '{index_code}' 가 유효하지 않거나 "
                f"NAVER Finance가 기본값(KOSPI200) 반환 중. "
                f"검증 종목 {len(validators)}개 중 0개 매칭."
            )
            return []
        logger.debug(f"[종목업데이트] {label} 검증 OK: {len(matched)}개 매칭")

    sector_count: dict[str, int] = {}
    for s in stocks:
        sector_count[s["sector"]] = sector_count.get(s["sector"], 0) + 1
    logger.info(f"[종목업데이트] {label} {len(stocks)}종목  섹터:")
    for sector, cnt in sorted(sector_count.items(), key=lambda x: -x[1]):
        logger.info(f"  {sector:16s}: {cnt}종목")
    return stocks


def _try_fetch_etf(base_url: str) -> list:
    """단일 URL에서 ETF 목록 스크레이핑 시도. 실패 또는 MIN_ETFS 미달 시 [] 반환."""
    etfs: list[dict] = []
    seen: set[str] = set()
    excluded_cnt = 0

    for page in range(1, 60):
        try:
            resp = requests.get(
                base_url,
                headers=_NAVER_ETF_HEADERS,
                params={"page": page},
                timeout=30,
            )
            if not resp.ok:
                logger.debug(f"[ETF업데이트] {base_url} 페이지 {page} HTTP {resp.status_code}")
                break
            html = resp.text
        except Exception as e:
            logger.debug(f"[ETF업데이트] {base_url} 페이지 {page} 오류: {e}")
            break

        matches = re.findall(
            r'href="/item/main\.naver\?code=(\d{6})"[^>]*>\s*([^\s<][^<]*?)\s*</a>',
            html,
        )
        new = []
        for code, raw_name in matches:
            name = raw_name.strip()
            if not name or len(name) > 50 or code in seen:
                continue
            seen.add(code)
            if any(kw in name for kw in _ETF_EXCLUDE):
                excluded_cnt += 1
                continue
            new.append({"code": code, "name": name, "sector": _etf_sector(name)})

        if not new:
            break

        etfs.extend(new)
        logger.debug(f"[ETF업데이트] 페이지 {page}: +{len(new)}개 (누적 {len(etfs)})")
        time.sleep(0.3)

    if len(etfs) < MIN_ETFS:
        return []

    return etfs


def _fetch_etf() -> list:
    """NAVER Finance ETF 목록 스크레이핑. _ETF_LIST_URLS 순서대로 시도, 성공 시 반환.
    채권·금리·TRF 관련 ETF 자동 제외.
    """
    for url in _ETF_LIST_URLS:
        logger.debug(f"[ETF업데이트] 시도: {url}")
        etfs = _try_fetch_etf(url)
        if etfs:
            logger.info(f"[ETF업데이트] {url} → {len(etfs)}개 수집 성공")
            sector_count: dict[str, int] = {}
            for e in etfs:
                sector_count[e["sector"]] = sector_count.get(e["sector"], 0) + 1
            logger.info(f"[ETF업데이트] 섹터 분포:")
            for sector, cnt in sorted(sector_count.items(), key=lambda x: -x[1]):
                logger.info(f"  {sector:16s}: {cnt}개")
            return etfs
        logger.warning(f"[ETF업데이트] {url} 실패 (다음 URL 시도)")

    logger.warning("[ETF업데이트] 모든 URL 실패 — 정적 ETF_LIST 폴백 사용")
    return []


def run() -> None:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    logger.info("══════════════════════════════════════════")
    logger.info(f" KOSPI200 + KOSDAQ150 + ETF 종목 업데이트 [{today}]")
    logger.info("══════════════════════════════════════════")

    # KOSPI200: NAVER 내부 코드 KPI200
    kospi200  = _fetch_index("KPI200", "KOSPI200", CACHE_PATH)
    # KOSDAQ150: entryJongmok.naver 가 KOSDAQ150을 지원하지 않아 KOSPI200 기본값 반환.
    # 검증 실패 시 [] 반환 → 빌트인 폴백으로 덮어씀.
    kosdaq150 = _fetch_index("KDAQ150", "KOSDAQ150", KOSDAQ150_CACHE,
                             validators=_KOSDAQ150_VALIDATORS)
    etfs      = _fetch_etf()

    changed_files = []

    if kospi200:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps({"updated_at": today, "stocks": kospi200}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[종목업데이트] KOSPI200 {len(kospi200)}종목 저장 → {CACHE_PATH.name}")
        changed_files.append(str(CACHE_PATH))
    else:
        if CACHE_PATH.exists():
            logger.warning("[종목업데이트] KOSPI200 업데이트 실패 — 기존 캐시 유지")
        else:
            logger.error("[종목업데이트] KOSPI200 업데이트 실패 + 캐시 없음")

    if kosdaq150:
        KOSDAQ150_CACHE.parent.mkdir(parents=True, exist_ok=True)
        KOSDAQ150_CACHE.write_text(
            json.dumps({"updated_at": today, "stocks": kosdaq150}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[종목업데이트] KOSDAQ150 {len(kosdaq150)}종목 저장 → {KOSDAQ150_CACHE.name}")
        changed_files.append(str(KOSDAQ150_CACHE))
    else:
        # NAVER Finance가 KOSDAQ150 구성 종목 페이지를 지원하지 않음.
        # watchlist.py 빌트인 목록으로 캐시를 덮어써서 잘못된 KOSPI200 데이터를 교체.
        from data.watchlist import _KOSDAQ150_BUILTIN  # noqa: PLC0415
        kosdaq150 = _KOSDAQ150_BUILTIN
        KOSDAQ150_CACHE.parent.mkdir(parents=True, exist_ok=True)
        KOSDAQ150_CACHE.write_text(
            json.dumps({"updated_at": today, "stocks": kosdaq150}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[종목업데이트] KOSDAQ150 빌트인 폴백 {len(kosdaq150)}종목 저장 → {KOSDAQ150_CACHE.name}")
        changed_files.append(str(KOSDAQ150_CACHE))

    if etfs:
        ETF_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ETF_CACHE.write_text(
            json.dumps({"updated_at": today, "etfs": etfs}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[ETF업데이트] {len(etfs)}개 저장 → {ETF_CACHE.name}")
        changed_files.append(str(ETF_CACHE))
    else:
        if ETF_CACHE.exists():
            logger.warning("[ETF업데이트] 업데이트 실패 — 기존 캐시 유지")
        else:
            logger.warning("[ETF업데이트] 업데이트 실패 + 캐시 없음 (정적 ETF_LIST 폴백 사용)")

    # KOSPI200 캐시 없으면 치명적 실패
    if not kospi200 and not CACHE_PATH.exists():
        logger.error("[종목업데이트] KOSPI200 데이터 없음 — 캐시도 없어 종료")
        sys.exit(1)

    if changed_files:
        n_k = len(kospi200) if kospi200 else "캐시"
        n_q = len(kosdaq150) if kosdaq150 else "캐시"
        n_e = len(etfs) if etfs else "캐시"
        ma_store.git_commit_push(
            changed_files,
            f"data: 종목 업데이트 {today} "
            f"(KOSPI200:{n_k} KOSDAQ150:{n_q} ETF:{n_e})",
        )
    else:
        logger.info("[종목업데이트] 변경 없음 — 기존 캐시로 운영 계속")

    logger.info("══════════════════════════════════════════")
    logger.info(" 완료")
    logger.info("══════════════════════════════════════════")


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    logger.info("=== KOSPI200 + KOSDAQ150 + ETF 종목 업데이트 (NAVER Finance 스크레이핑) ===")
    run()
