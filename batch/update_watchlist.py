#!/usr/bin/env python3
"""
KOSPI200 + KOSDAQ150 구성 종목 자동 업데이트
NAVER Finance 구성 종목 페이지 스크레이핑 → 실패 시 기존 캐시 유지 (분기 4회 갱신 목적)

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
MIN_STOCKS      = 100

# NAVER Finance 데스크탑 헤더 (GitHub Actions에서 접근 가능)
_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/sise/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ── KIS업종명 → 우리 섹터명 매핑 ─────────────────────────────────────────────
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


def _map_sector(bstp_name: str, code: str) -> str:
    if code in _CODE_SECTOR_OVERRIDE:
        return _CODE_SECTOR_OVERRIDE[code]
    for keyword, sector in _SECTOR_KEYWORDS:
        if keyword in bstp_name:
            return sector
    return "기타"


def _load_old_sectors(cache_path: Path) -> dict[str, str]:
    """기존 캐시에서 섹터 정보 로드 (새 목록 보강용)"""
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return {s["code"]: s.get("sector", "기타") for s in data.get("stocks", [])}
    except Exception:
        return {}


def _fetch_index(index_code: str, label: str, old_cache_path: Path) -> list:
    """NAVER Finance 구성 종목 페이지 HTML 스크레이핑.

    URL: https://finance.naver.com/sise/entryJongmok.naver?code={index_code}&page={n}
    한 페이지당 ~10종목, KOSPI200은 ~20페이지, KOSDAQ150은 ~15페이지.
    """
    old_sectors = _load_old_sectors(old_cache_path)
    stocks: list[dict] = []
    seen: set[str] = set()

    for page in range(1, 30):
        url = "https://finance.naver.com/sise/entryJongmok.naver"
        try:
            resp = requests.get(
                url,
                headers=_NAVER_HEADERS,
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

        # href="/item/main.naver?code=XXXXXX">종목명</a> 패턴
        matches = re.findall(
            r'href="/item/main\.naver\?code=(\d{6})"[^>]*>([^<\n\r]+)</a>',
            html,
        )
        new = []
        for code, raw_name in matches:
            name = raw_name.strip()
            if not name or code in seen:
                continue
            seen.add(code)
            # 기존 캐시 섹터 우선, 없으면 코드 오버라이드, 없으면 기타
            sector = old_sectors.get(code) or _map_sector("", code)
            new.append({"code": code, "name": name, "sector": sector})

        if not new:
            logger.debug(f"[종목업데이트] {label} 페이지 {page}: 신규 없음 → 종료")
            break

        stocks.extend(new)
        logger.debug(f"[종목업데이트] {label} 페이지 {page}: +{len(new)}종목 (누적 {len(stocks)})")
        time.sleep(0.3)

    if len(stocks) < MIN_STOCKS:
        logger.error(
            f"[종목업데이트] {label} 결과 {len(stocks)}종목 — 최소 {MIN_STOCKS}개 미달"
        )
        if stocks:
            logger.debug(f"[종목업데이트] 첫 3종목: {stocks[:3]}")
        return []

    sector_count: dict[str, int] = {}
    for s in stocks:
        sector_count[s["sector"]] = sector_count.get(s["sector"], 0) + 1
    logger.info(f"[종목업데이트] {label} {len(stocks)}종목  섹터:")
    for sector, cnt in sorted(sector_count.items(), key=lambda x: -x[1]):
        logger.info(f"  {sector:16s}: {cnt}종목")
    return stocks


def run() -> None:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    logger.info("══════════════════════════════════════════")
    logger.info(f" KOSPI200 + KOSDAQ150 구성 종목 업데이트 [{today}]")
    logger.info("══════════════════════════════════════════")

    kospi200  = _fetch_index("KOSPI200",  "KOSPI200",  CACHE_PATH)
    kosdaq150 = _fetch_index("KOSDAQ150", "KOSDAQ150", KOSDAQ150_CACHE)

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

    # API 실패해도 캐시 있으면 정상 종료 (분기 업데이트 특성)
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
        logger.info("[종목업데이트] 변경 없음 (스크레이핑 실패) — 기존 캐시로 운영 계속")

    logger.info("══════════════════════════════════════════")
    logger.info(" 완료")
    logger.info("══════════════════════════════════════════")


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    logger.info("=== KOSPI200+KOSDAQ150 종목 업데이트 (NAVER Finance 스크레이핑) ===")
    run()
