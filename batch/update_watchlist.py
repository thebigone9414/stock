#!/usr/bin/env python3
"""
KOSPI200 구성 종목 자동 업데이트
KIS API로 현재 KOSPI200 구성 종목을 가져와 data/kospi200_cache.json에 저장.
분기 리밸런싱(6월·12월) 후 자동 반영되도록 매월 1회 cron 실행.

Usage:
    python batch/update_watchlist.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from config.settings import get_settings
from utils.logger import setup_logger
from kis.factory import KIS
import data.ma_store as ma_store

KST        = pytz.timezone("Asia/Seoul")
CACHE_PATH = Path(__file__).parent.parent / "data" / "kospi200_cache.json"
MIN_STOCKS = 150   # 이 수 미만이면 API 오류로 간주하고 저장하지 않음

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


def run(market) -> None:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    logger.info(f"══════════════════════════════════════════")
    logger.info(f" KOSPI200 구성 종목 업데이트 [{today}]")
    logger.info(f"══════════════════════════════════════════")

    try:
        components = market.get_index_components("0028")
    except Exception as e:
        logger.error(f"[종목업데이트] KIS API 오류: {e}")
        sys.exit(1)

    if len(components) < MIN_STOCKS:
        logger.error(
            f"[종목업데이트] 조회 결과 {len(components)}종목 — "
            f"최소 {MIN_STOCKS}개 미달, 업데이트 중단 (API 오류 의심)"
        )
        sys.exit(1)

    stocks = []
    sector_count: dict[str, int] = {}
    for c in components:
        code   = c["code"]
        name   = c["name"]
        bstp   = c.get("bstp_name", "")
        sector = _map_sector(bstp, code)
        stocks.append({"code": code, "name": name, "sector": sector})
        sector_count[sector] = sector_count.get(sector, 0) + 1

    cache = {"updated_at": today, "stocks": stocks}
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"[종목업데이트] {len(stocks)}종목 저장 완료 → {CACHE_PATH.name}")
    logger.info("[종목업데이트] 섹터별 분포:")
    for sector, cnt in sorted(sector_count.items(), key=lambda x: -x[1]):
        logger.info(f"  {sector:16s}: {cnt}종목")

    ma_store.git_commit_push(
        [str(CACHE_PATH)],
        f"data: KOSPI200 구성 종목 업데이트 {today} ({len(stocks)}종목)",
    )
    logger.info(f"══════════════════════════════════════════")
    logger.info(f" 완료")
    logger.info(f"══════════════════════════════════════════")


if __name__ == "__main__":
    settings = get_settings()
    setup_logger(settings.log_level)
    logger.info(
        f"=== KOSPI200 종목 업데이트 "
        f"[{'모의' if settings.kis_is_paper_trading else '실전'}투자] ==="
    )
    kis = KIS(settings)
    run(kis.market)
