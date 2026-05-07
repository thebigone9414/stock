"""
한국 증시 휴장일 관리

[자동 처리]  holidays 라이브러리 — 국경일·대체공휴일 매년 자동 계산
[수동 추가]  EXTRA_CLOSURES — 선거일, 연말 임시 휴장 등 비정기 휴장

연말 업데이트 불필요. 비정기 휴장(선거일 등)만 EXTRA_CLOSURES에 추가.
"""
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Optional

import pytz

KST = pytz.timezone("Asia/Seoul")

# ── 비정기 시장 휴장 (국경일 아니지만 KRX 휴장) ────────────────────────────
# 선거일, 연말 임시 휴장 등 holidays 라이브러리로 처리 안 되는 날만 수동 추가
EXTRA_CLOSURES: set = {
    date(2025, 3,  3),   # 대통령선거일
    date(2025, 12, 31),  # 연말 임시 휴장
    # date(2026, 12, 31),  # 향후 연말 임시 휴장 발표 시 추가
}

# ── holidays 라이브러리에 있지만 KRX가 개장하는 날 ─────────────────────────
# 제헌절(7/17): 2008년 법정 공휴일 제외, KRX 정상 개장
_KRX_EXCLUDED_MONTHDAY: set = {(7, 17)}


@lru_cache(maxsize=10)
def _kr_market_holidays(year: int) -> frozenset:
    """연도별 KRX 휴장일 세트 (캐시됨)"""
    try:
        import holidays as _lib
        base = _lib.country_holidays("KR", years=year)
        result = set()
        for d in base.keys():
            if (d.month, d.day) not in _KRX_EXCLUDED_MONTHDAY:
                result.add(d)
    except ImportError:
        # holidays 라이브러리 미설치 시 하드코딩 폴백
        result = set(d for d in _FALLBACK if d.year == year)

    # 근로자의날 (5/1): 법정 공휴일 아니지만 KRX 휴장, holidays 라이브러리 미포함
    labor_day = date(year, 5, 1)
    if labor_day.weekday() < 5:      # 평일인 경우만 (주말이면 대체공휴일 없음)
        result.add(labor_day)

    return frozenset(result)


def is_market_holiday(check_date: Optional[date] = None) -> bool:
    """오늘(또는 지정일)이 증시 휴장일이면 True"""
    if check_date is None:
        check_date = datetime.now(KST).date()
    if check_date.weekday() >= 5:               # 주말
        return True
    if check_date in EXTRA_CLOSURES:            # 비정기 휴장
        return True
    return check_date in _kr_market_holidays(check_date.year)


def is_trading_day(check_date: Optional[date] = None) -> bool:
    return not is_market_holiday(check_date)


def next_trading_day(from_date: Optional[date] = None) -> date:
    """다음 거래일 반환"""
    if from_date is None:
        from_date = datetime.now(KST).date()
    d = from_date + timedelta(days=1)
    while is_market_holiday(d):
        d += timedelta(days=1)
    return d


# ── 하드코딩 폴백 (holidays 라이브러리 미설치 시) ─────────────────────────────
# 설날/추석은 음력 기반이므로 매년 날짜가 다름. 라이브러리 사용 강력 권장.
_FALLBACK: set = {
    # 2025
    date(2025, 1,  1), date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30),
    date(2025, 3,  1), date(2025, 5,  5), date(2025, 5,  6),
    date(2025, 5, 13), date(2025, 6,  6), date(2025, 8, 15),
    date(2025, 10, 3), date(2025, 10, 5), date(2025, 10, 6),
    date(2025, 10, 7), date(2025, 10, 8), date(2025, 10, 9), date(2025, 12, 25),
    # 2026 (설날 2/16~18, holidays 라이브러리 기준)
    date(2026, 1,  1), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 3,  1), date(2026, 3,  2), date(2026, 5,  5), date(2026, 5, 24),
    date(2026, 5, 25), date(2026, 6,  3), date(2026, 8, 15), date(2026, 8, 17),
    date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26),
    date(2026, 10, 3), date(2026, 10, 5), date(2026, 10, 9), date(2026, 12, 25),
}
