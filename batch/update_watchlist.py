#!/usr/bin/env python3
"""
KOSPI200 + KOSDAQ150 구성 종목 자동 업데이트
KRX 공개 데이터(pykrx)로 구성 종목을 가져와 data/kospi200_cache.json 등에 저장.
분기 리밸런싱(3·6·9·12월) 후 자동 반영되도록 매월 1회 cron 실행.

Usage:
    python batch/update_watchlist.py
"""
import json
import sys
from datetime import datetime, date as dt_date
from pathlib import Path

import pytz
from pykrx import stock as krx

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
import data.ma_store as ma_store

KST                = pytz.timezone("Asia/Seoul")
CACHE_PATH         = Path(__file__).parent.parent / "data" / "kospi200_cache.json"
KOSDAQ150_CACHE    = Path(__file__).parent.parent / "data" / "kosdaq150_cache.json"
# pykrx 지수 티커 코드 (get_index_ticker_list(market='KOSPI/KOSDAQ') 로 확인 가능)
KOSPI200_TICKER    = "1028"   # 코스피 200
KOSDAQ150_TICKER   = "2203"   # 코스닥 150
MIN_STOCKS         = 100   # 이 수 미만이면 조회 오류로 간주하고 저장하지 않음

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
    """KIS 업종명 + 코드 → 우리 섹터명"""
    if code in _CODE_SECTOR_OVERRIDE:
        return _CODE_SECTOR_OVERRIDE[code]
    for keyword, sector in _SECTOR_KEYWORDS:
        if keyword in bstp_name:
            return sector
    return "기타"


def _fetch_index(index_ticker: str, krx_market: str, label: str) -> list:
    """pykrx로 KRX에서 지수 구성 종목 직접 조회 → [{"code", "name", "sector"}] 반환."""
    today = dt_date.today().strftime("%Y%m%d")

    try:
        codes = krx.get_index_portfolio_deposit_file(index_ticker)
    except Exception as e:
        logger.error(f"[종목업데이트] {label} KRX 지수 구성 종목 조회 오류: {e}")
        return []

    if len(codes) < MIN_STOCKS:
        logger.error(
            f"[종목업데이트] {label} 조회 결과 {len(codes)}종목 — "
            f"최소 {MIN_STOCKS}개 미달, 업데이트 중단"
        )
        return []

    # 업종명(섹터 분류용) + 종목명 일괄 조회
    sector_df = None
    try:
        sector_df = krx.get_market_sector_classifications(today, market=krx_market)
    except Exception as e:
        logger.warning(f"[종목업데이트] {label} 업종 정보 조회 실패 — 섹터 '기타' 적용: {e}")

    stocks = []
    sector_count: dict[str, int] = {}
    for code in codes:
        name = code
        bstp = ""
        if sector_df is not None and code in sector_df.index:
            row  = sector_df.loc[code]
            name = str(row.get("종목명", code)).strip() or code
            bstp = str(row.get("업종명", "")).strip()
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

    kospi200  = _fetch_index(KOSPI200_TICKER,  "KOSPI",  "KOSPI200")
    kosdaq150 = _fetch_index(KOSDAQ150_TICKER, "KOSDAQ", "KOSDAQ150")

    changed_files = []

    if kospi200:
        CACHE_PATH.write_text(
            json.dumps({"updated_at": today, "stocks": kospi200}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[종목업데이트] KOSPI200 {len(kospi200)}종목 저장 → {CACHE_PATH.name}")
        changed_files.append(str(CACHE_PATH))
    else:
        logger.warning("[종목업데이트] KOSPI200 업데이트 실패 — 기존 캐시 유지")

    if kosdaq150:
        KOSDAQ150_CACHE.parent.mkdir(parents=True, exist_ok=True)
        KOSDAQ150_CACHE.write_text(
            json.dumps({"updated_at": today, "stocks": kosdaq150}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[종목업데이트] KOSDAQ150 {len(kosdaq150)}종목 저장 → {KOSDAQ150_CACHE.name}")
        changed_files.append(str(KOSDAQ150_CACHE))
    else:
        logger.warning("[종목업데이트] KOSDAQ150 업데이트 실패 — 기존 캐시 유지")

    if not changed_files:
        logger.error("[종목업데이트] 양쪽 모두 실패")
        sys.exit(1)

    ma_store.git_commit_push(
        changed_files,
        f"data: KOSPI200+KOSDAQ150 구성 종목 업데이트 {today} "
        f"(KOSPI200:{len(kospi200)} KOSDAQ150:{len(kosdaq150)}종목)",
    )
    logger.info(f"══════════════════════════════════════════")
    logger.info(f" 완료")
    logger.info(f"══════════════════════════════════════════")


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    logger.info("=== KOSPI200+KOSDAQ150 종목 업데이트 (KRX pykrx) ===")
    run()
