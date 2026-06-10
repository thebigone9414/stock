#!/usr/bin/env python3
"""
KOSPI200 + KOSDAQ150 구성 종목 자동 업데이트
NAVER Finance 모바일 API → 실패 시 기존 캐시 유지 (분기 4회 갱신 목적)

Usage:
    python batch/update_watchlist.py
"""
import json
import sys
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
MIN_STOCKS      = 100

# NAVER Finance 모바일 API — 인증 불필요, GitHub Actions에서 접근 가능
_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; SM-G975F) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json, text/plain, */*",
}

# ── KIS업종명 → 우리 섹터명 매핑 ─────────────────────────────────────────────
# 앞에 있을수록 우선순위 높음 (키워드 포함 여부로 체크)
_SECTOR_KEYWORDS = [
    # 세부 업종 먼저 (오탐 방지)
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

# 수동 오버라이드: KIS 업종 분류보다 우선 (잘못 분류되기 쉬운 종목)
_CODE_SECTOR_OVERRIDE: dict[str, str] = {
    "005490": "2차전지",      # POSCO홀딩스 (2차전지 소재 핵심)
    "096770": "2차전지/정유", # SK이노베이션
    "010960": "건설/건자재",  # 삼호개발
    "028260": "금융/지주",    # 삼성물산 (건설+지주)
    "000150": "금융/지주",    # 두산
    "034730": "금융/지주",    # SK
    "003550": "금융/지주",    # LG
    "000880": "금융/지주",    # 한화
    "001040": "금융/지주",    # CJ
    "004990": "금융/지주",    # 롯데지주
    "078930": "금융/지주",    # GS
    "002790": "금융/지주",    # 아모레G
    "329180": "중공업/기계",  # HD현대 (중공업 지주)
    "034020": "AI전력/설비",  # 두산에너빌리티
    "267260": "AI전력/설비",  # HD현대일렉트릭
    "010120": "AI전력/설비",  # LS ELECTRIC
    "006260": "AI전력/설비",  # LS
    "051600": "AI전력/설비",  # 한전KPS
}


def _map_sector(bstp_name: str, code: str) -> str:
    """업종명 + 코드 → 우리 섹터명"""
    if code in _CODE_SECTOR_OVERRIDE:
        return _CODE_SECTOR_OVERRIDE[code]
    for keyword, sector in _SECTOR_KEYWORDS:
        if keyword in bstp_name:
            return sector
    return "기타"


def _fetch_index(index_code: str, label: str, page_size: int = 300) -> list:
    """NAVER Finance 모바일 API로 지수 구성 종목 조회.

    https://m.stock.naver.com/api/index/{index_code}/constituent?page=1&pageSize=N
    인증 불필요, GitHub Actions IP에서 접근 가능.
    """
    url = f"https://m.stock.naver.com/api/index/{index_code}/constituent"
    params = {"page": 1, "pageSize": page_size}

    try:
        resp = requests.get(url, headers=_NAVER_HEADERS, params=params, timeout=30)
        if not resp.ok:
            logger.error(f"[종목업데이트] {label} HTTP {resp.status_code}: {resp.text[:400]}")
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"[종목업데이트] {label} NAVER API 조회 오류: {e}")
        return []

    # 응답 구조 탐색: stocks / list / constituents / 배열 직접
    items = (
        data.get("stocks")
        or data.get("list")
        or data.get("constituents")
        or data.get("result", {}).get("stocks")
        or (data if isinstance(data, list) else [])
    )

    if len(items) < MIN_STOCKS:
        logger.error(
            f"[종목업데이트] {label} 결과 {len(items)}종목 — "
            f"최소 {MIN_STOCKS}개 미달"
        )
        logger.debug(f"[종목업데이트] 응답 샘플: {str(data)[:500]}")
        return []

    stocks = []
    sector_count: dict[str, int] = {}
    for item in items:
        code = (
            item.get("itemCode")
            or item.get("code")
            or item.get("shrtCd")
            or item.get("stockCode")
            or ""
        ).strip()
        name = (
            item.get("stockName")
            or item.get("name")
            or item.get("hname")
            or item.get("itemName")
            or ""
        ).strip()
        bstp = (
            item.get("industryName")
            or item.get("industry")
            or item.get("upjong")
            or item.get("sectorName")
            or ""
        ).strip()
        if not code:
            continue
        sector = _map_sector(bstp, code)
        stocks.append({"code": code, "name": name, "sector": sector})
        sector_count[sector] = sector_count.get(sector, 0) + 1

    logger.info(f"[종목업데이트] {label} {len(stocks)}종목  섹터:")
    for sector, cnt in sorted(sector_count.items(), key=lambda x: -x[1]):
        logger.info(f"  {sector:16s}: {cnt}종목")
    return stocks


def run() -> None:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    logger.info(f"══════════════════════════════════════════")
    logger.info(f" KOSPI200 + KOSDAQ150 구성 종목 업데이트 [{today}]")
    logger.info(f"══════════════════════════════════════════")

    kospi200  = _fetch_index("KOSPI200",  "KOSPI200",  300)
    kosdaq150 = _fetch_index("KOSDAQ150", "KOSDAQ150", 200)

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
        if KOSDAQ150_CACHE.exists():
            logger.warning("[종목업데이트] KOSDAQ150 업데이트 실패 — 기존 캐시 유지")
        else:
            logger.error("[종목업데이트] KOSDAQ150 업데이트 실패 + 캐시 없음")

    # API 실패여도 캐시가 있으면 정상 종료 (분기 업데이트 특성상 이전 캐시로 운영 가능)
    if not kospi200 and not CACHE_PATH.exists():
        logger.error("[종목업데이트] KOSPI200 데이터 없음 — 캐시도 없어 종료")
        sys.exit(1)
    if not kosdaq150 and not KOSDAQ150_CACHE.exists():
        logger.error("[종목업데이트] KOSDAQ150 데이터 없음 — 캐시도 없어 종료")
        sys.exit(1)

    if changed_files:
        ma_store.git_commit_push(
            changed_files,
            f"data: KOSPI200+KOSDAQ150 구성 종목 업데이트 {today} "
            f"(KOSPI200:{len(kospi200)} KOSDAQ150:{len(kosdaq150)}종목)",
        )
    else:
        logger.info("[종목업데이트] 변경 없음 (API 실패) — 기존 캐시로 운영 계속")

    logger.info(f"══════════════════════════════════════════")
    logger.info(f" 완료")
    logger.info(f"══════════════════════════════════════════")


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    logger.info("=== KOSPI200+KOSDAQ150 종목 업데이트 (NAVER Finance) ===")
    run()
